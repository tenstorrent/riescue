# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from riescue.dtest_framework.generator.generator import Generator
from riescue.dtest_framework.generator.pt_request_builder import build_page_tables
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.config import FeatMgr
from riescue.riemap.memory import Memory, DramRange
from riescue.dtest_framework.parser import ParsedPmaHint, ParsedRandomAddress, Parser, PmaInfo
from riescue.dtest_framework.lib.discrete_test import DiscreteTest
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
import riescue.riemap.addrgen as addrgen

# Minimal parseable test source with a single in_pma member: one page mapping under real
# paging (so the builder allocates real PT-node frames), plus one ;#random_addr(in_pma=1)
# so a concrete PmaInfo region lands in translation.region_pma. Kept deliberately small --
# the repo's full test_pma_hint.s exercises hint-region sharing/growth that is orthogonal
# to what this test checks (real PT-node frames vs. a late carve-out mask).
_MINIMAL_IN_PMA_TEST = """
;#test.name       carveout_pt_probe
;#test.author     test_author
;#test.arch       rv64
;#test.priv       machine
;#test.cpus       1
;#test.paging     sv39
;#test.class      pma

;#random_addr(name=lin_page, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys_page, type=physical, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=memory, pma_cacheability=cacheable, pma_read=1, pma_write=1, pma_execute=1)  # noqa: E501
;#page_mapping(lin_name=lin_page, phys_name=phys_page, v=1, r=1, w=1, x=1, a=1, d=1)

.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    ;#test_passed()

test_cleanup:
    ;#test_passed()
"""


def _anchor_region(generator, region) -> PmaInfo:
    """Give a PMA region a pow2 size, a reserved size-aligned DRAM base, and a pool entry.

    Test setup only: production anchors in_pma regions through riemap, not the generator.
    """
    region.pma_size = 1 << max(12, (region.pma_size - 1).bit_length())
    constraint = addrgen.AddressConstraint(
        type=RV.AddressType.PHYSICAL,
        qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
        bits=44,
        size=region.pma_size,
        mask=(~(region.pma_size - 1)) & 0xFFFFFFFFFFFFFFFF,
    )
    region.pma_address = generator.addrgen.generate_address(constraint=constraint)
    generator.pool.pma_regions.add_entry(region)
    return region


class GeneratorFixture(unittest.TestCase):
    """Shared rng/pool/featmgr-mock fixture for generator tests (no tests of its own)."""

    def setUp(self):
        """Set up test fixtures"""
        self.rng = RandNum(seed=42)
        self.pool = Pool()
        # Add a dummy discrete_test to pool to prevent IndexError in Loader
        # This is needed when bringup_pagetables is True
        dummy_test = DiscreteTest(name="dummy_test", priv=RV.RiscvPrivileges.MACHINE)
        self.pool.discrete_tests["dummy_test"] = dummy_test

        # Use MagicMock with spec_set=False to allow attribute assignment
        # Then explicitly configure all needed attributes
        self.featmgr = MagicMock(spec=FeatMgr)
        self.featmgr.memory = Memory(dram_ranges=(DramRange(start=0x80000000, size=0x10000000),))
        self.featmgr.cpu_config = None
        self.featmgr.addrgen_limit_indices = False
        self.featmgr.addrgen_limit_way_predictor_multihit = False
        # Mock feature attribute for Runtime initialization
        self.featmgr.feature = MagicMock()
        self.featmgr.feature.is_enabled = MagicMock(return_value=True)
        self.featmgr.num_cpus = 1
        # Default contiguous hart IDs (0..num_cpus-1) for the runtime's VariableManager.
        self.featmgr.hart_ids = None
        self.featmgr.get_hart_ids.return_value = [0]
        self.featmgr.discontiguous_hartids.return_value = False
        # Set paging_mode to a valid enum value (required by Generator.__init__)
        self.featmgr.paging_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.paging_g_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.env = RV.RiscvTestEnv.TEST_ENV_BARE_METAL
        # PMA randomization defaults off (MagicMock attributes are truthy otherwise)
        self.featmgr.enable_pma_randomization = False
        self.featmgr.pma_random_regions = 8
        self.featmgr.pma_random_mask_pct = 25
        self.featmgr.pma_carveout_mask_pct = 0
        self.featmgr.user_programmable_pmacfg = 0
        # Disable bringup_pagetables to avoid Loader requiring discrete_tests
        # Use configure_mock to ensure it's properly set
        self.featmgr.configure_mock(bringup_pagetables=False)


class PmaIntegrationTest(GeneratorFixture):
    """Test PMA hint integration with generator"""

    def test_generator_with_pma_hint(self):
        """Test generator processes PMA hints"""
        # Add a PMA hint to pool
        hint = ParsedPmaHint(name="test_hint", combinations=[{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}])
        self.pool.add_parsed_pma_hint(hint)

        # Create generator
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Check that PMA regions were generated
        consolidated = generator.pool.pma_regions.consolidated_entries()
        # Should have at least the generated region (plus default DRAM/IO regions)
        self.assertGreater(len(consolidated), 0)

        # Check that our generated region exists
        generated_regions = [r for r in consolidated if r.pma_name.startswith("pma_test_hint")]
        self.assertGreater(len(generated_regions), 0)

    def test_generator_with_pma_config(self):
        """Test generator processes PMA config from cpu_config"""
        from riescue.dtest_framework.config.pma_config import PmaConfig, PmaRegionConfig, PmaAttributes

        # Create PMA config
        region_cfg = PmaRegionConfig(name="config_region", base=0x90000000, size=0x1000000, attributes=PmaAttributes(memory_type="memory", cacheability="cacheable"))
        pma_config = PmaConfig(regions=[region_cfg])

        # Mock cpu_config with PMA config
        self.featmgr.cpu_config = MagicMock()
        self.featmgr.cpu_config.pma_config = pma_config

        # Create generator
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Check that configured region exists
        consolidated = generator.pool.pma_regions.consolidated_entries()
        config_regions = [r for r in consolidated if r.pma_name == "config_region"]
        self.assertEqual(len(config_regions), 1)
        self.assertEqual(config_regions[0].pma_address, 0x90000000)

    def test_generator_with_hints_and_config(self):
        """Test generator with both hints and config"""
        from riescue.dtest_framework.config.pma_config import PmaConfig, PmaRegionConfig, PmaAttributes

        # Add hint
        hint = ParsedPmaHint(name="hint1", combinations=[{"memory_type": "memory", "cacheability": "noncacheable", "rwx": "rwx"}])
        self.pool.add_parsed_pma_hint(hint)

        # Add config
        region_cfg = PmaRegionConfig(name="config_region", base=0x90000000, size=0x1000000, attributes=PmaAttributes(memory_type="memory"))
        pma_config = PmaConfig(regions=[region_cfg])
        self.featmgr.cpu_config = MagicMock()
        self.featmgr.cpu_config.pma_config = pma_config

        # Create generator
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Check both exist
        consolidated = generator.pool.pma_regions.consolidated_entries()
        config_regions = [r for r in consolidated if r.pma_name == "config_region"]
        hint_regions = [r for r in consolidated if r.pma_name.startswith("pma_hint1")]

        self.assertEqual(len(config_regions), 1)
        self.assertGreater(len(hint_regions), 0)

    def test_generator_without_pma(self):
        """Test generator works without PMA hints/config"""
        # Create generator without hints or config
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Should still work - just uses default PMA regions
        consolidated = generator.pool.pma_regions.consolidated_entries()
        self.assertGreater(len(consolidated), 0)  # Should have default DRAM/IO regions

    def test_no_decoys_when_disabled(self):
        """Flag off leaves pma_random_regions empty"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        self.assertEqual(generator.pool.pma_random_regions, [])


class PmaRandomizationFixture(unittest.TestCase):
    """Shared randomization-enabled fixture (no tests of its own)."""

    def setUp(self):
        self.rng = RandNum(seed=42)
        self.pool = Pool()
        dummy_test = DiscreteTest(name="dummy_test", priv=RV.RiscvPrivileges.MACHINE)
        self.pool.discrete_tests["dummy_test"] = dummy_test

        self.featmgr = MagicMock(spec=FeatMgr)
        self.featmgr.memory = Memory(dram_ranges=(DramRange(start=0x80000000, size=0x10000000),))
        self.featmgr.cpu_config = None
        self.featmgr.addrgen_limit_indices = False
        self.featmgr.addrgen_limit_way_predictor_multihit = False
        self.featmgr.feature = MagicMock()
        self.featmgr.feature.is_enabled = MagicMock(return_value=True)
        self.featmgr.num_cpus = 1
        self.featmgr.hart_ids = None
        self.featmgr.get_hart_ids.return_value = [0]
        self.featmgr.discontiguous_hartids.return_value = False
        self.featmgr.paging_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.paging_g_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.env = RV.RiscvTestEnv.TEST_ENV_BARE_METAL
        self.featmgr.enable_pma_randomization = True
        self.featmgr.pma_random_regions = 6
        self.featmgr.pma_random_mask_pct = 50
        self.featmgr.pma_carveout_mask_pct = 0
        self.featmgr.user_programmable_pmacfg = 0
        # A concrete int, not the MagicMock default: _project_decoy_budget/decoy_pma_budget
        # do real arithmetic (num_pmas - essential) against it before RieMap ever runs.
        self.featmgr.num_pmas = 64
        self.featmgr.reset_pc = 0x8000_0000
        self.featmgr.io_htif_addr = None
        self.featmgr.io_imsic_mfile_addr = None
        self.featmgr.io_imsic_sfile_addr = None
        self.featmgr.io_imsic_vsfile_addr = None
        self.featmgr.io_maplic_addr = None
        self.featmgr.io_maplic_size = None
        self.featmgr.io_saplic_addr = None
        self.featmgr.io_saplic_size = None
        self.featmgr.debug_rom_address = None
        self.featmgr.debug_rom_size = None
        self.featmgr.configure_mock(bringup_pagetables=False)


class PmaRandomizationIntegrationTest(PmaRandomizationFixture):
    """Test randomized decoy PMA regions and strict in_pma anchoring in the generator"""

    def test_decoys_generated_and_isolated(self):
        """Decoys are generated, avoid the reset_pc window, and stay out of the consolidated pool"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        decoys = generator.pool.pma_random_regions
        self.assertEqual(len(decoys), 6)
        consolidated_ids = {id(r) for r in generator.pool.pma_regions.consolidated_entries()}
        for decoy in decoys:
            self.assertTrue(decoy.pma_randomized)
            self.assertNotIn(id(decoy), consolidated_ids, "decoys must not enter PmaRegion consolidation")
            self.assertFalse(decoy.pma_address < 0x8100_0000 and 0x8000_0000 < decoy.get_end_address(), f"decoy {decoy} overlaps the reset_pc window")

    def test_decoys_deterministic_per_seed(self):
        """Same seed produces the same decoys"""
        generator_a = Generator(RandNum(seed=7), Pool(), self.featmgr)
        generator_b = Generator(RandNum(seed=7), Pool(), self.featmgr)
        self.assertEqual(
            [(r.pma_address, r.pma_size, r.pma_mask) for r in generator_a.pool.pma_random_regions],
            [(r.pma_address, r.pma_size, r.pma_mask) for r in generator_b.pool.pma_random_regions],
        )

    def test_find_matching_pma_region_uses_unmasked_decoys_only(self):
        """in_pma attribute matching may target unmasked decoys; masked decoys are excluded"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        unmasked = PmaInfo(
            pma_name="pma_rand_u",
            pma_address=0x2000_0000,
            pma_size=0x10000,
            pma_memory_type="io",
            pma_read=True,
            pma_write=False,
            pma_execute=False,
            pma_amo_type="none",
            pma_routing_to="noncoherent",
            pma_valid=True,
            pma_randomized=True,
        )
        masked = PmaInfo(
            pma_name="pma_rand_m",
            pma_address=0x3000_0000,
            pma_size=0x10000,
            pma_memory_type="io",
            pma_read=True,
            pma_write=False,
            pma_execute=False,
            pma_amo_type="none",
            pma_routing_to="noncoherent",
            pma_valid=True,
            pma_randomized=True,
            pma_mask=1 << 30,
        )
        generator.pool.set_pma_random_regions([masked, unmasked])
        request = PmaInfo(pma_memory_type="io", pma_read=True, pma_write=False, pma_execute=False, pma_amo_type="none", pma_routing_to="noncoherent", pma_size=0x1000)
        self.assertIs(generator._find_matching_pma_region(request), unmasked)
        generator.pool.set_pma_random_regions([masked])
        self.assertIsNone(generator._find_matching_pma_region(request))


class CarveoutMaskingTest(PmaRandomizationFixture):
    """Test the carve-out masking pass (_apply_carveout_masks) and pma_masked placement."""

    def _make_io_request(self, masked: bool = False) -> ParsedRandomAddress:
        info = PmaInfo(pma_memory_type="io", pma_read=True, pma_write=False, pma_execute=False, pma_amo_type="none", pma_routing_to="noncoherent")
        return ParsedRandomAddress(name=f"req_{'m' if masked else 'u'}", type=RV.AddressType.PHYSICAL, size=0x1000, in_pma=True, pma_info=info, pma_masked=int(masked))

    def _anchored_carveout(self, generator, name="pma_carve") -> PmaInfo:
        region = PmaInfo(pma_name=name, pma_size=0x2000, pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="none", pma_valid=True)
        return _anchor_region(generator, region)

    def test_carveout_masking_disabled_by_default(self):
        """pct=0 leaves every carve-out unmasked"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        region = self._anchored_carveout(generator)
        generator._apply_carveout_masks()
        self.assertEqual(region.pma_mask, 0)

    def test_carveout_mask_avoids_all_allocations(self):
        """pct=100 masks the carve-out; scattered windows miss every foreign allocation"""
        self.featmgr.pma_carveout_mask_pct = 100
        generator = Generator(self.rng, self.pool, self.featmgr)
        region = self._anchored_carveout(generator)
        generator.addrgen.reserve_memory(RV.AddressType.PHYSICAL, 0x8800_0000, 0x2000)
        generator._apply_carveout_masks()
        self.assertNotEqual(region.pma_mask, 0, "pct=100 must mask the carve-out")
        # The region still matches its own base window (pages inside keep their attributes)
        self.assertTrue(region.matches_phys_range(region.pma_address, region.pma_size))
        span = (region.pma_address, region.get_end_address())
        for start, end in generator.addrgen.allocated_physical_intervals():
            if start >= span[0] and end <= span[1]:
                continue  # the region's own reservation
            self.assertFalse(region.matches_phys_range(start, end - start), f"mask window covers allocation [{start:#x}, {end:#x})")

    def test_masked_carveout_registered_with_addrgen(self):
        """Masked carve-outs join the addrgen exclusion list so later allocations avoid their windows"""
        self.featmgr.pma_carveout_mask_pct = 100
        generator = Generator(self.rng, self.pool, self.featmgr)
        region = self._anchored_carveout(generator)
        generator._apply_carveout_masks()
        self.assertTrue(any(r.overlaps(region.pma_address, region.pma_size) for r in generator.addrgen._extra_excluded_regions))
        window = region.pma_address ^ (region.pma_mask & -region.pma_mask)  # flip lowest masked bit
        self.assertIsNotNone(generator.addrgen._hits_excluded_region(window, 0x1000))

    def test_pma_masked_request_forces_mask(self):
        """A pma_masked=1 in_pma request produces a flagged region that the pass force-masks"""
        self.featmgr.pma_carveout_mask_pct = 0
        self.pool.add_parsed_addr(self._make_io_request(masked=True))
        generator = Generator(self.rng, self.pool, self.featmgr)
        region = generator._pre_allocated_pma_regions["req_m"]
        self.assertTrue(region.pma_mask_requested)
        _anchor_region(generator, region)
        generator._apply_carveout_masks()
        self.assertNotEqual(region.pma_mask, 0, "pma_masked request must be force-masked even at pct=0")

    def test_forced_mask_survives_containing_allocation_cluster(self):
        """A blocked interval containing the carve-out's own span must not veto every mask"""
        self.featmgr.pma_carveout_mask_pct = 0
        self.pool.add_parsed_addr(self._make_io_request(masked=True))
        generator = Generator(self.rng, self.pool, self.featmgr)
        region = generator._pre_allocated_pma_regions["req_m"]
        _anchor_region(generator, region)
        # Simulate an addrgen cluster run that merged the carve-out's own page with its
        # neighbors: every mask matches its own base window inside this interval, so
        # without clipping no candidate mask could ever be accepted
        containing = (region.pma_address - 0x10_0000, region.get_end_address() + 0x10_0000)
        real_intervals = generator.addrgen.allocated_physical_intervals
        generator.addrgen.allocated_physical_intervals = lambda: real_intervals() + [containing]
        generator._apply_carveout_masks()
        self.assertNotEqual(region.pma_mask, 0, "containing cluster run must not block forced masking")
        # Scattered windows must still avoid the cluster parts outside the region's own span
        for start, end in ((containing[0], region.pma_address), (region.get_end_address(), containing[1])):
            self.assertFalse(region.matches_phys_range(start, end - start), f"mask window covers foreign cluster part [{start:#x}, {end:#x})")

    def test_masked_and_unmasked_requests_get_distinct_regions(self):
        """Identical attributes with different pma_masked must never share a region"""
        self.pool.add_parsed_addr(self._make_io_request(masked=False))
        self.pool.add_parsed_addr(self._make_io_request(masked=True))
        generator = Generator(self.rng, self.pool, self.featmgr)
        region_u = generator._pre_allocated_pma_regions["req_u"]
        region_m = generator._pre_allocated_pma_regions["req_m"]
        self.assertIsNot(region_u, region_m)
        self.assertFalse(region_u.pma_mask_requested)
        self.assertTrue(region_m.pma_mask_requested)

    def test_find_matching_skips_decoys_for_masked_requests(self):
        """require_masked never matches decoys, even attribute-matching unmasked ones"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        decoy = PmaInfo(
            pma_name="pma_rand_u",
            pma_address=0x2000_0000,
            pma_size=0x10000,
            pma_memory_type="io",
            pma_read=True,
            pma_write=False,
            pma_execute=False,
            pma_amo_type="none",
            pma_routing_to="noncoherent",
            pma_valid=True,
            pma_randomized=True,
        )
        generator.pool.set_pma_random_regions([decoy])
        request = PmaInfo(pma_memory_type="io", pma_read=True, pma_write=False, pma_execute=False, pma_amo_type="none", pma_routing_to="noncoherent", pma_size=0x1000)
        self.assertIs(generator._find_matching_pma_region(request), decoy)
        self.assertIsNone(generator._find_matching_pma_region(request, require_masked=True))

    def test_find_matching_separates_masked_pool_regions(self):
        """Pool regions match by require_masked: to-be-masked regions only serve masked requests"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        flagged = PmaInfo(
            pma_name="pma_flagged",
            pma_address=0x9000_0000,
            pma_size=0x10000,
            pma_memory_type="io",
            pma_read=True,
            pma_write=False,
            pma_execute=False,
            pma_amo_type="none",
            pma_routing_to="noncoherent",
            pma_valid=True,
            pma_mask_requested=True,
        )
        generator.pool.pma_regions.add_entry(flagged)
        request = PmaInfo(pma_memory_type="io", pma_read=True, pma_write=False, pma_execute=False, pma_amo_type="none", pma_routing_to="noncoherent", pma_size=0x1000)
        self.assertIs(generator._find_matching_pma_region(request, require_masked=True), flagged)
        self.assertIsNone(generator._find_matching_pma_region(request, require_masked=False))


def _fixed_window(start: int, name: str, tags: tuple) -> DramRange:
    """A memory-map window a config declares with tags and pma_randomization: false."""
    return DramRange(start=start, size=0x1_0000, name=name, tags=tags, pma_randomization=False)


class FixedWindowTest(PmaRandomizationFixture):
    """DRAM ranges with pma_randomization: false become named PMA windows excluded from randomization."""

    # Names are deliberately unrelated to the tag: neither the name nor the tag classifies, the flag does
    FIXED_WINDOWS = (("pma_poison_window", 0xA000_0000), ("pma_scrub_window", 0xB000_0000), ("pma_tee_window", 0xC000_0000))

    @staticmethod
    def _memory(dram_size: int = 0x1000_0000) -> Memory:
        return Memory(
            dram_ranges=(DramRange(start=0x8000_0000, size=dram_size),),
            pma_fixed_ranges=(
                _fixed_window(0xA000_0000, "poison_window", ("derr",)),
                _fixed_window(0xB000_0000, "scrub_window", ("nderr",)),
                _fixed_window(0xC000_0000, "tee_window", ("stee", "spare")),
            ),
        )

    def setUp(self):
        super().setUp()
        self.featmgr.memory = self._memory()

    def test_fixed_ranges_become_named_pma_regions(self):
        """Each range lands in pool.pma_regions as pma_<range name> with default cacheable-RWX attributes"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        entries = {r.pma_name: r for r in generator.pool.pma_regions.consolidated_entries(merge_named=False)}
        for name, base in self.FIXED_WINDOWS:
            self.assertIn(name, entries)
            region = entries[name]
            self.assertEqual(region.pma_address, base)
            self.assertEqual(region.pma_size, 0x1_0000)
            self.assertEqual(region.pma_memory_type, "memory")
            self.assertEqual(region.pma_cacheability, "cacheable")
            self.assertTrue(region.pma_read and region.pma_write and region.pma_execute)

    def test_decoys_avoid_fixed_windows(self):
        """No decoy NAPOT span or scattered mask window may cover a fixed range"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        self.assertEqual(len(generator.pool.pma_random_regions), 6)
        for decoy in generator.pool.pma_random_regions:
            for _, base in self.FIXED_WINDOWS:
                self.assertFalse(decoy.pma_address < base + 0x1_0000 and base < decoy.get_end_address(), f"decoy {decoy} overlaps window at {base:#x}")
                self.assertFalse(decoy.matches_phys_range(base, 0x1_0000), f"decoy {decoy} mask window covers {base:#x}")

    def test_fixed_windows_exempt_from_mask_stress(self):
        """pct=100 masks ordinary carve-outs but never fixed windows"""
        self.featmgr.pma_carveout_mask_pct = 100
        generator = Generator(self.rng, self.pool, self.featmgr)
        generator._apply_carveout_masks()
        fixed_names = {name for name, _ in self.FIXED_WINDOWS}
        for region in generator.pool.pma_regions.consolidated_entries(merge_named=False):
            if region.pma_name in fixed_names:
                self.assertEqual(region.pma_mask, 0, f"{region.pma_name} must stay unmasked")

    def test_fixed_windows_are_targetable_by_range_name(self):
        """A test can place an address inside a window on purpose via custom_region=<range name>"""
        generator = Generator(self.rng, self.pool, self.featmgr)
        regions = generator.addrgen._custom_regions
        self.assertIn("poison_window", regions, "fixed window must be addressable by its memory-map name")
        self.assertEqual(regions["poison_window"], (0xA000_0000, 0xA000_0000 + 0x1_0000 - 1))
        self.assertIn("scrub_window", regions)
        self.assertIn("tee_window", regions)

    def test_fixed_windows_punched_out_of_enclosing_dram_range(self):
        """
        The realistic map: one big DRAM range with the windows inside it.

        A fixed window must be subtracted from ADDRESS_DRAM, or an ordinary random_addr can land in
        it by chance - which is exactly what pma_randomization: false is supposed to prevent.
        """
        self.featmgr.memory = self._memory(dram_size=0x1_0000_0000)  # spans every window
        generator = Generator(self.rng, self.pool, self.featmgr)
        phys_space = generator.addrgen._physical_addr_space
        dram_spans = phys_space.sub_clusters[RV.AddressQualifiers.ADDRESS_DRAM]
        self.assertTrue(dram_spans, "DRAM must still be allocatable outside the windows")
        for _, base in self.FIXED_WINDOWS:
            for idx in dram_spans:
                for lo, hi in phys_space.clusters[idx].super_cluster[RV.AddressQualifiers.ADDRESS_DRAM]:
                    self.assertFalse(lo <= base <= hi, f"fixed window {base:#x} still inside DRAM span [{lo:#x}, {hi:#x}]")

    def test_tagged_randomizable_range_stays_in_the_pool(self):
        """A tag alone only makes a range selectable; without pma_randomization: false it stays ordinary DRAM"""
        self.featmgr.memory = Memory(dram_ranges=(DramRange(start=0x8000_0000, size=0x1000_0000, name="low_bank", tags=("bank0",)),))
        generator = Generator(self.rng, self.pool, self.featmgr)
        entries = {r.pma_name for r in generator.pool.pma_regions.consolidated_entries(merge_named=False)}
        self.assertNotIn("pma_low_bank", entries, "a randomizable range gets no fixed-window PMA entry")
        phys_space = generator.addrgen._physical_addr_space
        dram_spans = phys_space.sub_clusters[RV.AddressQualifiers.ADDRESS_DRAM]
        covered = [(lo, hi) for idx in dram_spans for lo, hi in phys_space.clusters[idx].super_cluster[RV.AddressQualifiers.ADDRESS_DRAM]]
        self.assertTrue(any(lo <= 0x8000_0000 <= hi for lo, hi in covered), "tagged range must stay allocatable as DRAM")


class PmaMaskedValidationTest(GeneratorFixture):
    """pma_masked=1 is rejected without --enable_pma_randomization (legacy loader emits pmacfg=0)."""

    def test_pma_masked_without_randomization_raises(self):
        info = PmaInfo(pma_memory_type="io", pma_read=True, pma_write=False, pma_execute=False, pma_amo_type="none", pma_routing_to="noncoherent")
        parsed = ParsedRandomAddress(name="legacy_m", type=RV.AddressType.PHYSICAL, size=0x1000, in_pma=True, pma_info=info, pma_masked=1)
        self.pool.add_parsed_addr(parsed)
        with self.assertRaises(ValueError):
            Generator(self.rng, self.pool, self.featmgr)


class PmaMaskedParserTest(unittest.TestCase):
    """;#random_addr parsing of the pma_masked key."""

    def _parse(self, line: str) -> ParsedRandomAddress:
        from riescue.dtest_framework.parser import Parser

        pool = Pool()
        parser = Parser(filename=Path("dummy.s"), pool=pool)
        parser.parse_random_addr(line)
        return pool.get_parsed_addrs()[next(iter(pool.get_parsed_addrs()))]

    def test_pma_masked_lands_on_parsed_addr_not_pma_info(self):
        parsed = self._parse(";#random_addr(name=mpage, type=physical, size=0x1000, in_pma=1, pma_masked=1, pma_memory_type=io, pma_read=1, pma_write=0)")
        self.assertEqual(parsed.pma_masked, 1)
        self.assertTrue(parsed.in_pma)
        assert parsed.pma_info is not None
        self.assertFalse(hasattr(parsed.pma_info, "pma_masked") and getattr(parsed.pma_info, "pma_masked", None), "pma_masked must not become a PmaInfo attribute")
        self.assertEqual(parsed.pma_info.pma_memory_type, "io")

    def test_pma_masked_defaults_to_zero(self):
        parsed = self._parse(";#random_addr(name=upage, type=physical, size=0x1000, in_pma=1, pma_memory_type=io)")
        self.assertEqual(parsed.pma_masked, 0)

    def test_pma_masked_without_in_pma_raises(self):
        with self.assertRaises(ValueError):
            self._parse(";#random_addr(name=bad, type=physical, size=0x1000, pma_masked=1)")


class CarveoutMasksAgainstRealPageTablesTest(unittest.TestCase):
    """The carve-out masking pass runs after ``allocate_via_riemap`` (master timing:
    ``generate()`` calls ``allocate_via_riemap()`` before ``_apply_carveout_masks()``), so
    its scattered windows are chosen against *real* already-placed PT-node frames -- not
    just addrgen's own bookkeeping. Drive an actual page-table build (paging enabled) so
    ``SpaceResult.tables()`` gives independent, real frame addresses to check against.
    """

    def setUp(self):
        self.rng = RandNum(seed=3)
        self.pool = Pool()
        self.pool.discrete_tests["dummy"] = DiscreteTest(name="dummy", priv=RV.RiscvPrivileges.MACHINE)
        self._test_file = tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False)
        self._test_file.write(_MINIMAL_IN_PMA_TEST)
        self._test_file.close()
        self.addCleanup(lambda: Path(self._test_file.name).unlink(missing_ok=True))
        Parser(Path(self._test_file.name), self.pool).parse()

        self.featmgr = MagicMock(spec=FeatMgr)
        self.featmgr.memory = Memory(dram_ranges=(DramRange(start=0x80000000, size=0x100000000),))
        self.featmgr.cpu_config = None
        self.featmgr.addrgen_limit_indices = False
        self.featmgr.addrgen_limit_way_predictor_multihit = False
        self.featmgr.feature = MagicMock()
        self.featmgr.feature.is_enabled = MagicMock(return_value=True)
        self.featmgr.num_cpus = 1
        self.featmgr.hart_ids = None
        self.featmgr.get_hart_ids.return_value = [0]
        self.featmgr.discontiguous_hartids.return_value = False
        self.featmgr.paging_mode = RV.RiscvPagingModes.SV39
        self.featmgr.paging_g_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.env = RV.RiscvTestEnv.TEST_ENV_BARE_METAL
        self.featmgr.enable_pma_randomization = True
        self.featmgr.pma_random_regions = 0
        self.featmgr.pma_random_mask_pct = 0
        self.featmgr.pma_carveout_mask_pct = 100
        self.featmgr.user_programmable_pmacfg = 0
        self.featmgr.num_pmas = 64
        self.featmgr.reset_pc = 0x8000_0000
        self.featmgr.io_htif_addr = None
        self.featmgr.io_imsic_mfile_addr = None
        self.featmgr.io_imsic_sfile_addr = None
        self.featmgr.io_imsic_vsfile_addr = None
        self.featmgr.io_maplic_addr = None
        self.featmgr.io_maplic_size = None
        self.featmgr.io_saplic_addr = None
        self.featmgr.io_saplic_size = None
        self.featmgr.debug_rom_address = None
        self.featmgr.debug_rom_size = None
        self.featmgr.configure_mock(bringup_pagetables=False)
        self.featmgr.private_maps = False
        self.featmgr.reserve_partial_phys_memory = False
        self.featmgr.physical_addr_bits = 56
        self.featmgr.priv_mode = RV.RiscvPrivileges.SUPER
        self.featmgr.pbmt_ncio = False
        self.featmgr.all_4kb_pages = False
        self.featmgr.svadu = False
        self.featmgr.secure_mode = RV.RiscvSecureModes.NON_SECURE
        self.featmgr.secure_pt_probability = 0
        self.featmgr.secure_access_probability = 0

    def test_masked_carveout_misses_every_real_pt_frame_but_not_its_own_span(self):
        gen = Generator(self.rng, self.pool, self.featmgr)
        gen.process_raw_parsed_page_mappings()
        gen.add_page_maps()
        for ppm in self.pool.get_parsed_page_mappings().values():
            gen.randomize_pagesize(ppm)

        result, translation = build_page_tables(self.pool, self.featmgr, RandNum(seed=3), pma_region_bindings=gen._pma_region_bindings)
        gen._readback_allocation(result, translation)

        # An intentional in_pma member from the parsed test: its region must still be
        # tracked (registered on read-back) and resolve to a concrete base -- carve-out
        # masking elsewhere in the pool must never perturb it.
        member_regions = [binding.info for binding in translation.region_pma.values()]
        self.assertTrue(member_regions, "expected in_pma regions tracked in region_pma")
        for info in member_regions:
            self.assertNotEqual(info.pma_address, 0, f"{info.pma_name} must resolve to a real base")

        carveout = PmaInfo(pma_name="pma_carve", pma_size=0x2000, pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="none", pma_valid=True)
        _anchor_region(gen, carveout)

        gen._apply_carveout_masks()
        self.assertNotEqual(carveout.pma_mask, 0, "pct=100 must mask the late carve-out")

        # The carveout's own declared span is an intentional member: masking a region
        # never makes it stop matching its own base window.
        self.assertTrue(carveout.matches_phys_range(carveout.pma_address, carveout.pma_size), "a masked region must still match its own declared span")

        frame_addrs = {table.addr for space in result.spaces() for table in space.tables()}
        self.assertTrue(frame_addrs, "expected at least one auto-allocated PT-node frame")
        for addr in frame_addrs:
            self.assertFalse(carveout.matches_phys_range(addr, 0x1000), f"masked carve-out window covers real PT-node frame 0x{addr:x}")


class AdoptedDecoyFixture(unittest.TestCase):
    """Seeds one high-PA randomized PMA decoy for an ``in_pma`` test to adopt.

    Subclasses supply ``_TEST`` (the parsed assembly) and ``_DECOY_SIZE``.
    """

    _DECOY_BASE = 0x6147_DB84_00000
    _DECOY_SIZE = 0x40_0000
    _DECOY_TYPE = "io"

    _TEST = """
;#test.name       io_decoy_above_mmio
;#test.author     test_author
;#test.arch       rv64
;#test.priv       machine
;#test.cpus       1
;#test.paging     sv39
;#test.class      pma

;#random_addr(name=lin_page, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys_page, type=physical56, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=io, pma_cacheability=cacheable, pma_combining=combining, pma_amo_type=none, pma_routing_to=noncoherent, pma_read=1, pma_write=1, pma_execute=0)  # noqa: E501
;#page_mapping(lin_name=lin_page, phys_name=phys_page, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    ;#test_passed()

test_cleanup:
    ;#test_passed()
"""

    def setUp(self):
        self._test_file = tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False)
        self._test_file.write(self._TEST)
        self._test_file.flush()
        self._test_file.close()
        self.addCleanup(lambda: Path(self._test_file.name).unlink(missing_ok=True))

        self.pool = Pool()
        self.pool.discrete_tests["dummy_test"] = DiscreteTest(name="dummy_test", priv=RV.RiscvPrivileges.MACHINE)
        Parser(Path(self._test_file.name), self.pool).parse()

        # Seed the high-PA io decoy before Generator so pre-allocation can adopt it.
        # pma_random_regions=0 prevents additional random decoys from being generated.
        decoy = PmaInfo(
            pma_name="pma_rand_4",
            pma_address=self._DECOY_BASE,
            pma_size=self._DECOY_SIZE,
            pma_memory_type=self._DECOY_TYPE,
            pma_cacheability="cacheable",
            pma_combining="combining",
            pma_amo_type="none",
            pma_routing_to="noncoherent",
            pma_read=True,
            pma_write=True,
            pma_execute=False,
            pma_valid=True,
            pma_randomized=True,
            pma_mask=0,
        )
        self.pool.set_pma_random_regions([decoy])

        self.featmgr = MagicMock(spec=FeatMgr)
        # Default Memory.io_ranges top out at 0x8000_0000 -- below the decoy window.
        self.featmgr.memory = Memory(dram_ranges=(DramRange(start=0x80000000, size=0x100000000),))
        self.featmgr.cpu_config = None
        self.featmgr.addrgen_limit_indices = False
        self.featmgr.addrgen_limit_way_predictor_multihit = False
        self.featmgr.feature = MagicMock()
        self.featmgr.feature.is_enabled = MagicMock(return_value=True)
        self.featmgr.num_cpus = 1
        self.featmgr.hart_ids = None
        self.featmgr.get_hart_ids.return_value = [0]
        self.featmgr.discontiguous_hartids.return_value = False
        self.featmgr.paging_mode = RV.RiscvPagingModes.SV39
        self.featmgr.paging_g_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.env = RV.RiscvTestEnv.TEST_ENV_BARE_METAL
        self.featmgr.enable_pma_randomization = True
        self.featmgr.pma_random_regions = 0
        self.featmgr.pma_random_mask_pct = 0
        self.featmgr.pma_carveout_mask_pct = 0
        self.featmgr.user_programmable_pmacfg = 0
        self.featmgr.num_pmas = 64
        self.featmgr.reset_pc = 0x8000_0000
        self.featmgr.io_htif_addr = None
        self.featmgr.io_imsic_mfile_addr = None
        self.featmgr.io_imsic_sfile_addr = None
        self.featmgr.io_imsic_vsfile_addr = None
        self.featmgr.io_maplic_addr = None
        self.featmgr.io_maplic_size = None
        self.featmgr.io_saplic_addr = None
        self.featmgr.io_saplic_size = None
        self.featmgr.debug_rom_address = None
        self.featmgr.debug_rom_size = None
        self.featmgr.configure_mock(bringup_pagetables=False)
        self.featmgr.private_maps = False
        self.featmgr.reserve_partial_phys_memory = False
        self.featmgr.physical_addr_bits = 56
        self.featmgr.priv_mode = RV.RiscvPrivileges.SUPER
        self.featmgr.pbmt_ncio = False
        self.featmgr.all_4kb_pages = False
        self.featmgr.svadu = False
        self.featmgr.secure_mode = RV.RiscvSecureModes.NON_SECURE
        self.featmgr.secure_pt_probability = 0
        self.featmgr.secure_access_probability = 0


class TestIoDecoyAboveMmioPlaces(AdoptedDecoyFixture):
    """Adopted io-typed decoys outside AddrGen MMIO ranges must still place (master parity).

    The voyager2 seed-2 failure stamped ADDRESS_MMIO onto a fixed decoy whose window
    sits above every ``memory.io_ranges`` segment, emptying the allocator draw.
    """

    def test_adopted_io_decoy_outside_mmio_builds(self):
        mmio_top = max((r.end for r in self.featmgr.memory.io_ranges), default=0)
        self.assertGreater(self._DECOY_BASE, mmio_top, "decoy must sit above every AddrGen MMIO segment")

        gen = Generator(RandNum(seed=2), self.pool, self.featmgr)
        phys = self.pool.get_parsed_addr("phys_page")
        self.assertIsNotNone(phys.pma_info)
        assert phys.pma_info is not None
        self.assertEqual(phys.pma_info.pma_address, self._DECOY_BASE, "in_pma must adopt the seeded io decoy")
        self.assertTrue(phys.pma_info.pma_randomized)

        gen.process_raw_parsed_page_mappings()
        gen.add_page_maps()
        for ppm in self.pool.get_parsed_page_mappings().values():
            gen.randomize_pagesize(ppm)

        result, translation = build_page_tables(self.pool, self.featmgr, RandNum(seed=2), pma_region_bindings=gen._pma_region_bindings)
        member_pages = [page for page, region in translation.page_regions.items() if region.base == self._DECOY_BASE]
        self.assertTrue(member_pages, "expected a page constrained to the adopted decoy region")
        for page in member_pages:
            addr = result.address(page)
            self.assertTrue(
                self._DECOY_BASE <= addr < self._DECOY_BASE + self._DECOY_SIZE,
                f"member 0x{addr:x} outside decoy [0x{self._DECOY_BASE:x}, 0x{self._DECOY_BASE + self._DECOY_SIZE:x})",
            )


class TestManyMembersInLargePmaRegion(AdoptedDecoyFixture):
    """A large PMA region with many members must build, and must not cost per-address work.

    This is the shape the PMA CLI tests generate: several members constrained to a
    region big enough that enumerating its aligned slots (262144 of them per GiB of
    4 KiB pages, re-walked for every member) dominates the whole build.
    """

    _DECOY_SIZE = 0x4000_0000
    _DECOY_TYPE = "memory"  # must match the attributes the in_pma directives ask for, or nothing adopts it
    _MEMBERS = 24

    _HEADER = """
;#test.name       many_members_large_pma
;#test.author     test_author
;#test.arch       rv64
;#test.priv       machine
;#test.cpus       1
;#test.paging     sv39
;#test.class      pma
"""

    _FOOTER = """
.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    ;#test_passed()

test_cleanup:
    ;#test_passed()
"""

    @property
    def _TEST(self):
        directives = []
        for index in range(self._MEMBERS):
            directives.append(f";#random_addr(name=lin_{index}, type=linear, size=0x1000, and_mask=0xfffffffffffff000)")
            directives.append(
                f";#random_addr(name=phys_{index}, type=physical56, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, "
                "pma_size=0x1000, pma_memory_type=memory, pma_cacheability=cacheable, pma_combining=combining, "
                "pma_amo_type=none, pma_routing_to=noncoherent, pma_read=1, pma_write=1, pma_execute=0)"
            )
            directives.append(f";#page_mapping(lin_name=lin_{index}, phys_name=phys_{index}, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])")
        return self._HEADER + "\n".join(directives) + "\n" + self._FOOTER

    def test_many_members_build_in_bounded_time(self):
        gen = Generator(RandNum(seed=2), self.pool, self.featmgr)
        gen.process_raw_parsed_page_mappings()
        gen.add_page_maps()
        for ppm in self.pool.get_parsed_page_mappings().values():
            gen.randomize_pagesize(ppm)

        started = time.monotonic()
        result, translation = build_page_tables(self.pool, self.featmgr, RandNum(seed=2), pma_region_bindings=gen._pma_region_bindings)
        elapsed = time.monotonic() - started

        member_pages = [page for page, region in translation.page_regions.items() if region.base == self._DECOY_BASE]
        self.assertEqual(len(member_pages), self._MEMBERS, "every in_pma request should adopt the one decoy whose attributes it asked for")
        spans = sorted((result.address(page), result.address(page) + 0x1000) for page in member_pages)
        for start, end in spans:
            self.assertTrue(self._DECOY_BASE <= start and end <= self._DECOY_BASE + self._DECOY_SIZE, f"member [0x{start:x}, 0x{end:x}) outside the decoy region")
        for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(first_end, second_start, "members overlap inside the region")
        self.assertTrue({table.addr for space in result.spaces() for table in space.tables()}, "expected auto-allocated PT-node frames")
        self.assertLess(elapsed, 30.0, "page-table build scaled with PMA region size, not occupancy")


if __name__ == "__main__":
    unittest.main()
