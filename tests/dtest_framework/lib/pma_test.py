# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.dtest_framework.config.pma_config import PmaConfig
from riescue.dtest_framework.lib.pma import PMACFG_ROUTING_BIT, PmaRegion, PmaInfo, legacy_pma, set_legacy_pma, allow_amos_in_pma_ncio, set_allow_amos_in_pma_ncio


class PmaPolicyFixture(unittest.TestCase):
    """Restores the process-wide PMA policy latches, so a target cannot leak between tests."""

    def setUp(self):
        self.addCleanup(set_legacy_pma, legacy_pma())
        self.addCleanup(set_allow_amos_in_pma_ncio, allow_amos_in_pma_ncio())

    def encode(self, **kwargs):
        return PmaInfo(pma_address=0x8000_0000, pma_size=0x1000, **kwargs).generate_pma_value(force=True)


class LegacyPmaRoutingBitTest(PmaPolicyFixture):
    """legacy_pma target: pmacfg bit 8 carries routing, defaulting to coherent."""

    def setUp(self):
        super().setUp()
        set_legacy_pma(True)

    def test_unrequested_routing_defaults_to_coherent(self):
        """A region that never names a routing still sets bit 8, exactly as before the policy was configurable"""
        self.assertTrue(self.encode(pma_memory_type="memory") & PMACFG_ROUTING_BIT)
        self.assertEqual(PmaInfo().effective_routing_to, "coherent")

    def test_explicit_routing_round_trips(self):
        self.assertTrue(self.encode(pma_memory_type="memory", pma_routing_to="coherent") & PMACFG_ROUTING_BIT)
        self.assertFalse(self.encode(pma_memory_type="io", pma_execute=False, pma_routing_to="noncoherent") & PMACFG_ROUTING_BIT)

    def test_invalid_routing_raises(self):
        with self.assertRaises(ValueError):
            PmaInfo(pma_routing_to="bogus")


class RoutingBitReservedTest(PmaPolicyFixture):
    """Off a legacy_pma target: bit 8 is reserved RO-zero and any routing request is an error."""

    def setUp(self):
        super().setUp()
        set_legacy_pma(False)

    def test_bit8_is_clear_for_every_memory_type(self):
        """Bit 8 is never written: no memory type, cacheability or amo type may set it"""
        self.assertFalse(self.encode(pma_memory_type="memory") & PMACFG_ROUTING_BIT)
        self.assertFalse(self.encode(pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="swap") & PMACFG_ROUTING_BIT)
        for io_type in ("io", "ch0", "ch1"):
            self.assertFalse(self.encode(pma_memory_type=io_type, pma_execute=False) & PMACFG_ROUTING_BIT, io_type)

    def test_any_routing_request_raises(self):
        """Even the legacy default value is an error: routing is not a configurable attribute off a legacy target"""
        for routing in ("coherent", "noncoherent"):
            with self.assertRaises(ValueError, msg=routing) as ctx:
                PmaInfo(pma_routing_to=routing)
            self.assertIn("legacy_pma", str(ctx.exception))


class AmoTypeFieldTest(PmaPolicyFixture):
    """pmacfg[6:5] is one 2-bit amo type: 0b11 on cacheable main memory, 0b00-0b11 anywhere else."""

    def setUp(self):
        super().setUp()
        set_allow_amos_in_pma_ncio(True)  # the NC/IO range below only exists on a target that permits it

    def amo_bits(self, **kwargs):
        return (self.encode(**kwargs) >> 5) & 3

    def test_cacheable_memory_encodes_0b11(self):
        self.assertEqual(self.amo_bits(pma_memory_type="memory", pma_cacheability="cacheable"), 3)

    def test_cacheable_memory_rejects_other_amo_types(self):
        for amo in ("none", "swap", "logical"):
            with self.assertRaises(ValueError, msg=amo):
                PmaInfo(pma_memory_type="memory", pma_cacheability="cacheable", pma_amo_type=amo)

    def test_amocasq_encodes_like_arithmetic(self):
        """amocasq is an input alias of arithmetic: both write pmacfg[6:5]=0b11"""
        self.assertEqual(self.amo_bits(pma_amo_type="amocasq"), 3)
        self.assertEqual(self.amo_bits(pma_amo_type="arithmetic"), self.amo_bits(pma_amo_type="amocasq"))
        self.assertEqual(self.amo_bits(pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="amocasq"), 3)

    def test_cacheable_memory_accepts_amocasq(self):
        pma = PmaInfo(pma_memory_type="memory", pma_cacheability="cacheable", pma_amo_type="amocasq")
        self.assertEqual(pma.pma_amo_type, "amocasq")
        self.assertEqual((pma.generate_pma_value(force=True) >> 5) & 3, 3)

    def test_cacheable_memory_still_rejects_swap(self):
        with self.assertRaises(ValueError):
            PmaInfo(pma_memory_type="memory", pma_cacheability="cacheable", pma_amo_type="swap")

    def test_attrib_matches_arithmetic_and_amocasq(self):
        arith = PmaInfo(pma_address=0x8000_0000, pma_size=0x1000, pma_amo_type="arithmetic")
        amocasq = PmaInfo(pma_address=0x8000_1000, pma_size=0x1000, pma_amo_type="amocasq")
        self.assertTrue(arith.attrib_matches(amocasq))
        self.assertTrue(amocasq.attrib_matches(arith))
        swap = PmaInfo(pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="swap")
        arith_nc = PmaInfo(pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="arithmetic")
        self.assertFalse(arith_nc.attrib_matches(swap))

    def test_full_range_outside_cacheable_memory(self):
        """NC/IO [6:5] is Babylon AMO×Rsrv packing (none/swap/logical/arithmetic = 00/01/10/11)"""
        expected = {"none": 0, "swap": 1, "logical": 2, "arithmetic": 3, "amocasq": 3}
        for amo, bits in expected.items():
            self.assertEqual(self.amo_bits(pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type=amo), bits, amo)
            for io_type in ("io", "ch0", "ch1"):
                self.assertEqual(self.amo_bits(pma_memory_type=io_type, pma_execute=False, pma_amo_type=amo), bits, f"{io_type}/{amo}")

    def test_explicit_rsrv_controls_bit_6_independently(self):
        self.assertEqual(self.amo_bits(pma_cacheability="noncacheable", pma_amo_type="arithmetic", pma_rsrv="none"), 0b01)
        self.assertEqual(self.amo_bits(pma_cacheability="noncacheable", pma_amo_type="none", pma_rsrv="non_eventual"), 0b10)
        self.assertEqual(self.amo_bits(pma_cacheability="noncacheable", pma_amo_type="arithmetic", pma_rsrv="non_eventual"), 0b11)

    def test_rsrv_eventual_is_cacheable_only(self):
        self.assertEqual(PmaInfo(pma_rsrv="eventual").effective_rsrv, "eventual")
        with self.assertRaisesRegex(ValueError, "eventual"):
            PmaInfo(pma_cacheability="noncacheable", pma_rsrv="eventual")
        with self.assertRaisesRegex(ValueError, "requires pma_rsrv='eventual'"):
            PmaInfo(pma_rsrv="none")

    def test_explicit_rsrv_uses_babylon_amo_choices(self):
        for amo_type in ("swap", "logical"):
            with self.assertRaisesRegex(ValueError, "Babylon"):
                PmaInfo(pma_cacheability="noncacheable", pma_amo_type=amo_type, pma_rsrv="none")


class NcioAmoClampTest(PmaPolicyFixture):
    """allow_amos_in_pma_ncio off forces pmacfg[6:5]=0b00 everywhere but cacheable main memory."""

    NCIO_SHAPES = (
        {"pma_memory_type": "memory", "pma_cacheability": "noncacheable"},
        {"pma_memory_type": "io", "pma_execute": False},
        {"pma_memory_type": "ch0", "pma_execute": False},
        {"pma_memory_type": "ch1", "pma_execute": False},
    )

    def setUp(self):
        super().setUp()
        set_allow_amos_in_pma_ncio(False)

    def amo_bits(self, **kwargs):
        return (self.encode(**kwargs) >> 5) & 3

    def test_default_is_off(self):
        """The knob has to default off, or existing targets silently gain NC/IO atomicity"""
        self.assertFalse(PmaConfig().allow_amos_in_pma_ncio)

    def test_every_ncio_shape_clamps_to_amonone(self):
        for shape in self.NCIO_SHAPES:
            for amo in ("swap", "logical", "arithmetic", "amocasq"):
                self.assertEqual(self.amo_bits(pma_amo_type=amo, **shape), 0, f"{shape}/{amo}")

    def test_clamp_zeroes_the_rsrv_bit_too(self):
        """AMONone is the whole field: an explicit rsrv must not leave bit 6 set"""
        self.assertEqual(self.amo_bits(pma_cacheability="noncacheable", pma_amo_type="none", pma_rsrv="non_eventual"), 0)
        self.assertEqual(self.amo_bits(pma_memory_type="io", pma_execute=False, pma_amo_type="arithmetic", pma_rsrv="non_eventual"), 0)
        self.assertEqual(PmaInfo(pma_cacheability="noncacheable", pma_amo_type="none", pma_rsrv="non_eventual").effective_rsrv, "none")

    def test_cacheable_memory_is_untouched(self):
        self.assertEqual(self.amo_bits(pma_memory_type="memory", pma_cacheability="cacheable"), 3)
        self.assertEqual(PmaInfo().effective_rsrv, "eventual")
        self.assertEqual(PmaInfo().effective_amo_type, "arithmetic")

    def test_effective_amo_type_reports_what_is_programmed(self):
        nc = PmaInfo(pma_memory_type="memory", pma_cacheability="noncacheable", pma_amo_type="arithmetic")
        self.assertEqual(nc.pma_amo_type, "arithmetic")  # the request is preserved
        self.assertEqual(nc.effective_amo_type, "none")  # what actually reaches pmacfg
        set_allow_amos_in_pma_ncio(True)
        self.assertEqual(nc.effective_amo_type, "arithmetic")

    def test_clamped_shapes_share_one_region(self):
        """Two requests that now encode identically must compare equal and share one entry"""
        swap = PmaInfo(pma_memory_type="io", pma_execute=False, pma_amo_type="swap")
        arith = PmaInfo(pma_memory_type="io", pma_execute=False, pma_amo_type="arithmetic")
        self.assertTrue(swap.attrib_matches(arith))
        set_allow_amos_in_pma_ncio(True)
        self.assertFalse(swap.attrib_matches(arith))

    def test_knob_on_restores_the_full_range(self):
        set_allow_amos_in_pma_ncio(True)
        expected = {"none": 0, "swap": 1, "logical": 2, "arithmetic": 3}
        for amo, bits in expected.items():
            self.assertEqual(self.amo_bits(pma_memory_type="io", pma_execute=False, pma_amo_type=amo), bits, amo)


class PmaTest(PmaPolicyFixture):
    """
    Test the PMP module. Routing takes part in region equality only on a legacy_pma target, and these
    consolidation cases assert routing-aware entry counts, so they pin the latch on.
    """

    def setUp(self):
        super().setUp()
        set_legacy_pma(True)

    def test_pma_mem_consolidation(self):
        """
        Tests that memory regions are consolidated correctly and only if consecutive
        """
        pma_region = PmaRegion()
        pma_region.add_region(0x80000000, 0x1000, "memory")
        pma_region.add_region(0x100001000, 0x1000, "memory")
        pma_region.add_region(0x100000000, 0x1000, "memory")
        pma_region.add_region(0x80001000, 0x1000, "memory")
        pma_region.add_region(0x80003000, 0x1000, "memory")

        entries = pma_region.consolidated_entries()

        self.assertEqual(len(entries), 3, "Expected 3 PmaInfo")

        self.assertEqual(entries[0].pma_address, 0x80000000, "Expected 0x80000000")
        self.assertEqual(entries[0].pma_size, 0x2000, "Expected 0x1010")
        self.assertEqual(entries[0].pma_memory_type, "memory", "Expected memory")
        self.assertEqual(entries[0].pma_read, True, "Expected read")
        self.assertEqual(entries[0].pma_write, True, "Expected write")
        self.assertEqual(entries[0].pma_execute, True, "Expected execute")
        self.assertEqual(entries[0].effective_routing_to, "coherent", "Expected coherent")
        self.assertEqual(entries[0].pma_combining, "noncombining", "Expected noncombining")
        self.assertEqual(entries[0].pma_cacheability, "cacheable", "Expected cacheable")
        self.assertEqual(entries[0].pma_amo_type, "arithmetic", "Expected arithmetic")

        self.assertEqual(entries[1].pma_address, 0x80003000, "Expected 0x80003000")
        self.assertEqual(entries[1].pma_size, 0x1000, "Expected 0x1000")

        self.assertEqual(entries[2].pma_address, 0x100000000, "Expected 0x100001000")
        self.assertEqual(entries[2].pma_size, 0x2000, "Expected 0x1000")

    def test_pma_mem_consolidation_with_diff_attributes(self):
        """
        Tests that memory regions are consolidated correctly and only if consecutive and attribs match
        """
        pma_region = PmaRegion()
        pma_region.add_region(0x80000000, 0x1000, "memory")
        pma_region.add_region(0x100001000, 0x1000, "memory")
        pma_region.add_region(0x100000000, 0x1000, "memory")
        pma_region.add_region(0x80001000, 0x1000, "memory", read=False)
        pma_region.add_region(0x80002000, 0x1000, "memory", read=False)
        pma_region.add_region(0x100002000, 0x1000, "memory", combining="combining")
        pma_region.add_region(0x100003000, 0x1000, "memory", routing_to="noncoherent")

        entries = pma_region.consolidated_entries()

        self.assertEqual(len(entries), 5, "Expected 5 PmaInfo")

        self.assertEqual(entries[0].pma_address, 0x80000000, "Expected 0x80000000")
        self.assertEqual(entries[0].pma_size, 0x1000, "Expected 0x1000")
        self.assertEqual(entries[1].pma_address, 0x80001000, "Expected 0x80001000")
        self.assertEqual(entries[1].pma_size, 0x2000, "Expected 0x1000")
        self.assertEqual(entries[1].pma_read, False, "Expected no read")
        self.assertEqual(entries[2].pma_address, 0x100000000, "Expected 0x100000000")
        self.assertEqual(entries[2].pma_size, 0x2000, "Expected 0x1000")
        self.assertEqual(entries[2].pma_combining, "noncombining", "Expected noncombining")
        self.assertEqual(entries[2].effective_routing_to, "coherent", "Expected coherent")
        self.assertEqual(entries[3].pma_address, 0x100002000, "Expected 0x100000000")
        self.assertEqual(entries[3].pma_size, 0x1000, "Expected 0x1000")
        self.assertEqual(entries[3].pma_combining, "combining", "Expected combining")
        self.assertEqual(entries[4].pma_address, 0x100003000, "Expected 0x100000000")
        self.assertEqual(entries[4].pma_size, 0x1000, "Expected 0x1000")
        self.assertEqual(entries[4].pma_routing_to, "noncoherent", "Expected noncoherent")

    def test_pma_io_consolidation(self):
        """
        Tests that IO regions are consolidated correctly and even if not consecutive
        """
        pma_region = PmaRegion()
        pma_region.add_region(0x80000000, 0x1000, "io")
        pma_region.add_region(0x100001000, 0x1000, "io")
        pma_region.add_region(0x90000000, 0x1000, "memory")
        pma_region.add_region(0x100002000, 0x1000, "io")
        pma_region.add_region(0x80003000, 0x1000, "io")
        pma_region.add_region(0x8000A000, 0x1000, "io")

        entries = pma_region.consolidated_entries()

        self.assertEqual(len(entries), 3, "Expected 3 PmaInfo")

        self.assertEqual(entries[0].pma_address, 0x80000000, "Expected 0x80000000")
        self.assertEqual(entries[0].pma_size, 0xB000, "Expected 0xb000")
        self.assertEqual(entries[0].pma_memory_type, "io", "Expected io")

        self.assertEqual(entries[2].pma_address, 0x100001000, "Expected 0x80000000")
        self.assertEqual(entries[2].pma_size, 0x2000, "Expected 0x2000")
        self.assertEqual(entries[2].pma_memory_type, "io", "Expected io")

        self.assertEqual(entries[1].pma_address, 0x90000000, "Expected 0x90000000")
        self.assertEqual(entries[1].pma_size, 0x1000, "Expected 0x1000")
        self.assertEqual(entries[1].pma_memory_type, "memory", "Expected memory")


class PmaMaskTest(unittest.TestCase):
    """
    Tests for PmaInfo pmamask support (effective_match_mask / matches_phys_range).
    """

    def test_generate_pma_mask_value_clamps_to_addr_bits(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000, pma_mask=0xFFFF_FFFF_FFFF_FFFF)
        self.assertEqual(pma.generate_pma_mask_value(), PmaInfo.PMAMASK_ADDR_BITS)

    def test_effective_match_mask_no_mask(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000)
        self.assertEqual(pma.effective_match_mask(), PmaInfo.PMAMASK_ADDR_BITS)

    def test_effective_match_mask_excludes_size_bits(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x4000)
        expected = PmaInfo.PMAMASK_ADDR_BITS & ~0x3000  # bits 13:12 fall below region size
        self.assertEqual(pma.effective_match_mask(), expected)

    def test_effective_match_mask_removes_pmamask_bits(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000, pma_mask=1 << 13)
        expected = PmaInfo.PMAMASK_ADDR_BITS & ~(1 << 13)
        self.assertEqual(pma.effective_match_mask(), expected)

    def test_matches_phys_range_unmasked_interval(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000)
        self.assertTrue(pma.matches_phys_range(0x80000000, 0x1000))
        self.assertTrue(pma.matches_phys_range(0x80000800, 0x8))
        self.assertTrue(pma.matches_phys_range(0x7FFFF000, 0x2000), "span overlapping region start")
        self.assertFalse(pma.matches_phys_range(0x80001000, 0x1000), "adjacent above must not match")
        self.assertFalse(pma.matches_phys_range(0x7FFFF000, 0x1000), "adjacent below must not match")

    def test_matches_phys_range_masked_scattered_windows(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000, pma_mask=1 << 13)
        self.assertTrue(pma.matches_phys_range(0x80000000, 0x1000), "home window")
        self.assertTrue(pma.matches_phys_range(0x80002000, 0x1000), "bit-13 alias window outside interval")
        self.assertFalse(pma.matches_phys_range(0x80001000, 0x1000), "bit-12 differs, still compared")
        self.assertFalse(pma.matches_phys_range(0x00000000, 0x1000), "distant address mismatch")

    def test_matches_phys_range_masked_span_hits_window(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000, pma_mask=1 << 13)
        self.assertTrue(pma.matches_phys_range(0x80001000, 0x2000), "span [0x1000,0x3000) covers alias at 0x2000")

    def test_matches_phys_range_bit55_alias(self):
        secure_addr = 0x80000000 | (1 << 55)
        masked = PmaInfo(pma_address=0x80000000, pma_size=0x1000, pma_mask=1 << 13)
        unmasked = PmaInfo(pma_address=0x80000000, pma_size=0x1000)
        self.assertTrue(masked.matches_phys_range(secure_addr, 0x1000), "masked compare ignores bits >= 52")
        self.assertFalse(unmasked.matches_phys_range(secure_addr, 0x1000), "interval compare sees bit 55")

    def test_generate_pma_value_unaffected_by_mask(self):
        base = PmaInfo(pma_address=0x80000000, pma_size=0x1000)
        masked = PmaInfo(pma_address=0x80000000, pma_size=0x1000, pma_mask=1 << 20)
        self.assertEqual(base.generate_pma_value(), masked.generate_pma_value())

    def test_generate_pma_value_force_bypasses_valid_quirk(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000, pma_valid=True)
        self.assertEqual(pma.generate_pma_value(), 0, "legacy quirk: pma_valid=True regions emit 0")
        self.assertNotEqual(pma.generate_pma_value(force=True), 0)

    def test_size_encoding_exact_log2_under_force(self):
        pma = PmaInfo(pma_address=0x80000000, pma_size=0x1000)
        self.assertEqual(pma.generate_pma_value() >> 58, 13, "legacy encoding doubles the region (msb+1)")
        self.assertEqual(pma.generate_pma_value(force=True) >> 58, 12, "force encodes exact log2 like whisper decodes")
        non_pow2 = PmaInfo(pma_address=0x80000000, pma_size=0x3000)
        self.assertEqual(non_pow2.generate_pma_value(force=True) >> 58, 14, "non-pow2 sizes use ceil log2")

    def test_masked_match_closed_form_equals_page_walk(self):
        """The O(64) masked-match successor must agree with a brute-force page walk everywhere."""
        import random

        prng = random.Random(1234)
        for _ in range(200):
            size_bits = prng.randint(12, 16)
            base = prng.randrange(0, 1 << 24, 1 << size_bits)
            mask_bits = prng.sample(range(size_bits, 22), prng.randint(1, 4))
            pma = PmaInfo(pma_address=base, pma_size=1 << size_bits, pma_mask=sum(1 << b for b in mask_bits))
            start = prng.randrange(0, 1 << 24, 0x1000)
            size = prng.choice([0x1000, 0x3000, 0x10000])
            mask = pma.effective_match_mask()
            tag = base & mask
            walk = any((page << 12) & mask == tag for page in range(start >> 12, ((start + size - 1) >> 12) + 1))
            self.assertEqual(pma.matches_phys_range(start, size), walk, f"mismatch: base={base:#x} mask={pma.pma_mask:#x} start={start:#x} size={size:#x}")
