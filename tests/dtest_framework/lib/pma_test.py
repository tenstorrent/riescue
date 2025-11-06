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
