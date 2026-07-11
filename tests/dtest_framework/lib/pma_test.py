# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.dtest_framework.lib.pma import PmaRegion, PmaInfo


class PmaTest(unittest.TestCase):
    """
    Test the PMP module.
    """

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
        self.assertEqual(entries[0].pma_routing_to, "coherent", "Expected coherent")
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
        self.assertEqual(entries[2].pma_routing_to, "coherent", "Expected coherent")
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
