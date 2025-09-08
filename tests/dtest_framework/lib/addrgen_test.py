# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

import riescue.dtest_framework.lib.addrgen as addrgen
import riescue.lib.enums as RV
from riescue.dtest_framework.config import Memory
from riescue.lib.rand import RandNum


class AddrGenTest(unittest.TestCase):
    """
    Test the addrgen module. Target AddrGen
    """

    def setUp(self):
        self.rng = RandNum(0)
        self.mem = Memory()
        self.address_size = 0x400
        self.start_addr = 0x80000000

    def test_address_constraint(self):
        phys_addr_constraint = addrgen.AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,
            size=self.address_size,
            mask=0xFFFFFFFFFFFFF,
        )
        self.assertIn("address_type: PHYSICAL", str(phys_addr_constraint))
        self.assertIn("address_bits: 32", str(phys_addr_constraint))
        self.assertIn("address_size: 0x", str(phys_addr_constraint))

    def test_addrgen(self):
        "Basic addrgen.AddrGen test, default Memory"
        generator = addrgen.AddrGen(self.rng, self.mem)

        physical_constraint = addrgen.AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,
            size=self.address_size,
            mask=0xFFFFFFFFFFFFF,
        )
        # Grab first address, check that rng is still generating expected first address
        addr = generator.generate_address(physical_constraint)
        expected_addr = 0xB5D54872
        self.assertIsInstance(addr, int, "Expecting addresses to be integers")  # Just for checking type helps
        self.assertEqual(addr, expected_addr, f"Expected {expected_addr:0x}, got {addr:0x}. Change in AddrGen.generate_address algorithm")

        # Check reserving memory at same address as previously generated address
        # This should probably cause an error but currently doesn't
        generator.reserve_memory(
            address_type=RV.AddressType.PHYSICAL,
            start_address=expected_addr & 0xFFFFF400,
            size=self.address_size,
        )

    def test_addrgen_memory_conflicts(self):
        "Test that reserving memory then generating at same address will generate different address and not fail"
        generator = addrgen.AddrGen(self.rng, self.mem)
        collide_addr = 0xB5D54872
        expected_addr = 0x8DF03372

        generator.reserve_memory(
            address_type=RV.AddressType.PHYSICAL,
            start_address=collide_addr,
            size=self.address_size,
        )
        physical_constraint = addrgen.AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,
            size=0x400,
            mask=0xFFFFFFFFFFFFF,
        )
        addr = generator.generate_address(physical_constraint)
        self.assertEqual(addr, expected_addr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
