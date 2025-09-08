# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.dtest_framework.lib.pmp import PmpRegisters, PmpRegion, PmpCfg, PmpAddr, RiscvPmpAddressMatchingModes


class PmpTest(unittest.TestCase):
    """
    Test the PMP module.
    """

    def test_pmp_region_napot(self):
        "Tests a PMP range that is NAPOT"
        start = 0x8000_0000
        size = 0x8000_0000
        pmp_region = PmpRegion()
        pmp_region.add_region(start, size, "rwx")

        registers = pmp_region.encode()
        self.assertEqual(len(registers), 1)
        for pmp in registers:
            self.assertIsInstance(pmp.cfg, PmpCfg)
            self.assertIsInstance(pmp.addr, list)
            for addr in pmp.addr:
                self.assertIsInstance(addr, PmpAddr)
                addr_start, addr_size = addr.range()
                self.assertEqual(addr.addr_matching, RiscvPmpAddressMatchingModes.NAPOT)
                self.assertEqual(addr_start, start, f"Mismatch in start address: {start:x} != {addr_start:x}")
                self.assertEqual(addr_size, size, f"Mismatch in size: {size:x} != {addr_size:x}")

        # self.assertEqual(regs.cfg[0], 0x1F)
        # self.assertEqual(regs.addr[0], 0x80000000)
        # self.assertEqual(regs.cfg[1], 0x1F)

    def test_pmp_builder_not_napot(self):
        """
        Tests a PMP range that is not NAPOT. This should split cleanly into 2 regions
        0x8000_0000 - 0x10_0000_0000
        0x10_0000_0000 - 0x14_0000_0000
        """
        start = 0x8000_0000
        size = 0xC000_0000

        pmp_region = PmpRegion(pad_napot=False)
        pmp_region.add_region(start, size)
        registers = pmp_region.encode()
        self.assertEqual(len(registers), 1, "Expected 1 PmpRegister with 2 PmpAddr and 1 PmpCfg")
        for pmp in registers:
            self.assertIsInstance(pmp.cfg, PmpCfg)
            self.assertIsInstance(pmp.addr, list)
            self.assertEqual(len(pmp.addr), 2, "Expected 2 PmpAddr")

            pmpaddr0 = pmp.addr[0]
            pmpaddr1 = pmp.addr[1]
            addr_start, addr_size = pmpaddr0.range()
            self.assertEqual(pmpaddr0.name, "pmpaddr0")
            self.assertEqual(addr_start, 0x80000000)
            self.assertEqual(addr_size, 0x80000000)
            addr_start, addr_size = pmpaddr1.range()
            self.assertEqual(pmpaddr1.name, "pmpaddr1")
            self.assertEqual(addr_start, 0x100000000)
            self.assertEqual(addr_size, 0x40000000)

            self.assertEqual(pmp.cfg.name, "pmpcfg0")
            self.assertEqual(pmp.cfg.value, (0x1F << 8) | 0x1F, "Expected 2 NAPOT regions")

    def test_multiple_napot_regions(self):
        """
        Tests that 16 NAPOT regions for XLEN=64 generates 16 pmpaddr and 2 pmpcfg registers
        """
        one_gb = 0x4000_0000
        two_gb = 0x8000_0000
        pmp_region = PmpRegion(pad_napot=False)
        for i in range(16):
            pmp_region.add_region(two_gb + i * one_gb, one_gb, "rwx")
        registers = pmp_region.encode()
        self.assertEqual(len(registers), 2, "Expected 2 PmpRegister with 1 PmpAddr and 1 PmpCfg")

        total_addr_regs = 0
        total_cfg_regs = 0
        for pmp in registers:
            self.assertIsInstance(pmp.cfg, PmpCfg)
            self.assertIsInstance(pmp.addr, list)
            self.assertEqual(len(pmp.addr), 8, "Expected 8 PmpAddr")
            total_addr_regs += len(pmp.addr)
            total_cfg_regs += 1
        self.assertEqual(total_addr_regs, 16, "Expected 16 PmpAddr")  # 16 NAPOT regions
        self.assertEqual(total_cfg_regs, 2, "Expected 2 PmpCfg")  # 2 PmpCfg

    def test_pmp_region_napot_4_byte_offset(self):
        """
        Tests a PMP range that is not NAPOT. Naturally aligned, but >= 8 bytes

        Currently raises an error because the range is not NAPOT.
        In the future the PmpBuilder can instead increase the samllest region to a size of 8 bytes
        """

        with self.assertRaises(ValueError):
            pmp_region = PmpRegion(pad_napot=False)
            pmp_region.add_region(0x8000_0000, 0x24, "rwx")

    def test_pmp_region_large_range(self):
        """
        Tests a handful of large regions that are not all NAPOT
        """

        regions = [
            (0x8000_0000, 0x3_8000_0000_0000),  # 128GB, unaligneed
            (0x4_0000_0000_0000, 0x4_0000_0000_0000),  # 16GB, aligned
            (0x8_0000_0000_0000, 0x4_0000_0000_0000),  # 16GB, aligned
            (0xC_0000_0000_0000, 0x4_0000_0000_0000),  # 16GB, aligned
        ]

        pmp_region = PmpRegion(pad_napot=False)
        for start, size in regions:
            pmp_region.add_region(start, size, "rwx")
        registers = pmp_region.encode()
        self.assertEqual(len(registers), 3, "Expected 3 PmpRegisters")
        for reg in registers:
            self.assertNotEqual(reg.cfg.name, "pmpcfg1", "Odd pmcfg csr names are illegal in XLEN=6")
