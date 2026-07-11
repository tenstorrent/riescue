# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from riescue.dtest_framework.generator.generator import Generator
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.config.memory import Memory, DramRange
from riescue.dtest_framework.parser import ParsedPmaHint, ParsedRandomAddress, PmaInfo
from riescue.dtest_framework.lib.discrete_test import DiscreteTest
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum


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
        self.featmgr.memory = Memory(dram_ranges=[DramRange(start=0x80000000, size=0x10000000)])
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
        self.featmgr.memory = Memory(dram_ranges=[DramRange(start=0x80000000, size=0x10000000)])
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
        generator.pool.pma_random_regions[:] = [masked, unmasked]
        request = PmaInfo(pma_memory_type="io", pma_read=True, pma_write=False, pma_execute=False, pma_amo_type="none", pma_routing_to="noncoherent", pma_size=0x1000)
        self.assertIs(generator._find_matching_pma_region(request), unmasked)
        generator.pool.pma_random_regions[:] = [masked]
        self.assertIsNone(generator._find_matching_pma_region(request))

    def test_anchor_and_placement(self):
        """Anchoring reserves the whole pow2-aligned region; placements never collide"""
        import riescue.dtest_framework.lib.addrgen as addrgen

        generator = Generator(self.rng, self.pool, self.featmgr)
        region = PmaInfo(pma_name="pma_strict", pma_size=0x1800, pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="none", pma_valid=True)
        constraint = addrgen.AddressConstraint(type=RV.AddressType.PHYSICAL, qualifiers={RV.AddressQualifiers.ADDRESS_DRAM}, bits=44, size=0x1000, mask=0xFFFFFFFFFFFFF000)
        generator._anchor_pma_region(region, constraint)
        self.assertEqual(region.pma_size, 0x2000, "size rounds up to power of two")
        self.assertEqual(region.pma_address % region.pma_size, 0, "anchor is size-aligned")
        self.assertTrue(generator.addrgen.physical_overlap(region.pma_address, region.pma_size), "whole region reserved")

        first = generator._place_in_pma_region(region, constraint)
        second = generator._place_in_pma_region(region, constraint)
        assert first is not None and second is not None, "in-region placement failed"
        self.assertNotEqual(first, second, "same-region placements must not collide")
        for addr in (first, second):
            self.assertGreaterEqual(addr, region.pma_address)
            self.assertLessEqual(addr + 0x1000, region.get_end_address())
        self.assertIsNone(generator._place_in_pma_region(region, constraint), "region full")


class CarveoutMaskingTest(PmaRandomizationFixture):
    """Test the carve-out masking pass (_apply_carveout_masks) and pma_masked placement."""

    def _make_io_request(self, masked: bool = False) -> ParsedRandomAddress:
        info = PmaInfo(pma_memory_type="io", pma_read=True, pma_write=False, pma_execute=False, pma_amo_type="none", pma_routing_to="noncoherent")
        return ParsedRandomAddress(name=f"req_{'m' if masked else 'u'}", type=RV.AddressType.PHYSICAL, size=0x1000, in_pma=True, pma_info=info, pma_masked=int(masked))

    def _anchored_carveout(self, generator, name="pma_carve") -> PmaInfo:
        import riescue.dtest_framework.lib.addrgen as addrgen

        region = PmaInfo(pma_name=name, pma_size=0x2000, pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="none", pma_valid=True)
        constraint = addrgen.AddressConstraint(type=RV.AddressType.PHYSICAL, qualifiers={RV.AddressQualifiers.ADDRESS_DRAM}, bits=44, size=0x1000, mask=0xFFFFFFFFFFFFF000)
        generator._anchor_pma_region(region, constraint)
        generator.pool.pma_regions.add_entry(region)
        return region

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
        self.assertIn(region, generator.addrgen._extra_excluded_pma_regions)
        window = region.pma_address ^ (region.pma_mask & -region.pma_mask)  # flip lowest masked bit
        self.assertIsNotNone(generator.addrgen._hits_excluded_pma(window, 0x1000))

    def test_pma_masked_request_forces_mask(self):
        """A pma_masked=1 in_pma request produces a flagged region that the pass force-masks"""
        self.featmgr.pma_carveout_mask_pct = 0
        self.pool.add_parsed_addr(self._make_io_request(masked=True))
        generator = Generator(self.rng, self.pool, self.featmgr)
        region = generator._pre_allocated_pma_regions["req_m"]
        self.assertTrue(region.pma_mask_requested)
        # Anchor it (normally done by handle_random_addr) then run the pass
        import riescue.dtest_framework.lib.addrgen as addrgen

        constraint = addrgen.AddressConstraint(type=RV.AddressType.PHYSICAL, qualifiers={RV.AddressQualifiers.ADDRESS_DRAM}, bits=44, size=0x1000, mask=0xFFFFFFFFFFFFF000)
        generator._anchor_pma_region(region, constraint)
        generator.pool.pma_regions.add_entry(region)
        generator._apply_carveout_masks()
        self.assertNotEqual(region.pma_mask, 0, "pma_masked request must be force-masked even at pct=0")

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
        generator.pool.pma_random_regions[:] = [decoy]
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


class DerivedAddressResolutionTest(GeneratorFixture):
    """Derived (derive_from) linear addresses resolve deterministically relative to their source."""

    def _resolve(self, parsed_addrs):
        for parsed in parsed_addrs:
            self.pool.add_parsed_addr(parsed)
        generator = Generator(self.rng, self.pool, self.featmgr)
        generator.generate_addr()
        width_mask = (1 << generator.linear_addr_bits) - 1
        return {p.name: self.pool.get_random_addrs()[p.name].address & width_mask for p in parsed_addrs}

    def test_buddy_derive_is_source_plus_size(self):
        """not_mask == size with a 2*size-aligned source pins the buddy page directly above."""
        src = ParsedRandomAddress(name="src", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFE000)
        buddy = ParsedRandomAddress(name="buddy", type=RV.AddressType.LINEAR, size=0x1000, derive_from="src", derive_not_mask=0x1000)
        addrs = self._resolve([src, buddy])
        self.assertEqual(addrs["src"] & 0x1FFF, 0, "source must honor its 8KB and_mask")
        self.assertEqual(addrs["buddy"], addrs["src"] + 0x1000)

    def test_general_pinned_derive_is_source_xor_not_mask(self):
        """Multi-bit not_mask (non-buddy shape) still resolves to exactly source ^ not_mask."""
        src = ParsedRandomAddress(name="gsrc", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFC000)
        rel = ParsedRandomAddress(name="grel", type=RV.AddressType.LINEAR, size=0x1000, derive_from="gsrc", derive_not_mask=0x3000)
        addrs = self._resolve([src, rel])
        self.assertEqual(addrs["gsrc"] & 0x3FFF, 0)
        self.assertEqual(addrs["grel"], addrs["gsrc"] ^ 0x3000)

    def test_mixed_deriveds_from_one_source(self):
        """A buddy-shaped and a general derived off the same source both resolve deterministically."""
        src = ParsedRandomAddress(name="msrc", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFC000)
        buddy = ParsedRandomAddress(name="mbuddy", type=RV.AddressType.LINEAR, size=0x1000, derive_from="msrc", derive_not_mask=0x1000)
        far = ParsedRandomAddress(name="mfar", type=RV.AddressType.LINEAR, size=0x1000, derive_from="msrc", derive_not_mask=0x2000)
        addrs = self._resolve([src, buddy, far])
        self.assertEqual(addrs["mbuddy"], addrs["msrc"] + 0x1000)
        self.assertEqual(addrs["mfar"], addrs["msrc"] ^ 0x2000)

    def test_derive_from_undeclared_source_raises(self):
        orphan = ParsedRandomAddress(name="orphan", type=RV.AddressType.LINEAR, size=0x1000, derive_from="ghost", derive_not_mask=0x1000)
        self.pool.add_parsed_addr(orphan)
        generator = Generator(self.rng, self.pool, self.featmgr)
        with self.assertRaises(ValueError):
            generator.generate_addr()


if __name__ == "__main__":
    unittest.main()
