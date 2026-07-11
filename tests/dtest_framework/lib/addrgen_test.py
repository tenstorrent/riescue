# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

import riescue.dtest_framework.lib.addrgen as addrgen
import riescue.lib.enums as RV
from riescue.dtest_framework.config import Memory
from riescue.dtest_framework.lib.pma import PmaInfo
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


class AddrGenPmaExclusionTest(unittest.TestCase):
    """
    Test AddrGen avoidance of excluded (randomized decoy) PMA regions.
    """

    def setUp(self):
        self.rng = RandNum(0)
        self.mem = Memory()

    def physical_constraint(self, size=0x1000):
        return addrgen.AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,
            size=size,
            mask=0xFFFFFFFFFFFFF000,
        )

    def test_unmasked_exclusion_avoided(self):
        """Generated physical addresses never land inside an unmasked excluded region"""
        decoy = PmaInfo(pma_name="pma_rand_0", pma_address=0x8000_0000, pma_size=0x1000_0000, pma_randomized=True)
        generator = addrgen.AddrGen(self.rng, self.mem, excluded_pma_regions=[decoy])
        for _ in range(50):
            addr = generator.generate_address(self.physical_constraint())
            self.assertIsNone(generator._hits_excluded_pma(addr, 0x1000), f"0x{addr:x} landed in the decoy")

    def test_masked_exclusion_avoided(self):
        """Generated physical addresses never satisfy a masked decoy's congruence"""
        # Region matches any page whose bits [31:13] equal the base's (bit 12 is don't-care)
        decoy = PmaInfo(pma_name="pma_rand_1", pma_address=0x9000_0000, pma_size=0x1000, pma_mask=1 << 12, pma_randomized=True)
        generator = addrgen.AddrGen(self.rng, self.mem, excluded_pma_regions=[decoy])
        mask = decoy.effective_match_mask()
        for _ in range(50):
            addr = generator.generate_address(self.physical_constraint())
            self.assertNotEqual(addr & mask, decoy.pma_address & mask, f"0x{addr:x} matches masked decoy window")

    def test_dense_masked_exclusion_avoided(self):
        """A masked decoy excluding 50% of pages (bit 12 clear) is always avoided on the PHYSICAL path"""
        decoy = PmaInfo(pma_name="pma_rand_2", pma_address=0x0, pma_size=0x1000, pma_mask=PmaInfo.PMAMASK_ADDR_BITS & ~(1 << 12), pma_randomized=True)
        self.assertEqual(decoy.effective_match_mask(), 1 << 12)
        generator = addrgen.AddrGen(self.rng, self.mem, excluded_pma_regions=[decoy])
        for _ in range(30):
            addr = generator.generate_address(self.physical_constraint())
            self.assertTrue(addr & (1 << 12), f"0x{addr:x} lies in the masked decoy window (bit 12 clear)")

    def test_exclusion_exhaustion_raises(self):
        """A decoy covering the whole 32-bit DRAM window makes generation fail loudly"""
        decoy = PmaInfo(pma_name="pma_rand_3", pma_address=0x0, pma_size=1 << 32, pma_randomized=True)
        generator = addrgen.AddrGen(self.rng, self.mem, excluded_pma_regions=[decoy])
        with self.assertRaises(addrgen.AddrGenError):
            generator.generate_address(self.physical_constraint())

    def test_no_exclusions_no_behavior_change(self):
        """Without exclusions the generation stream matches the legacy expectation"""
        generator = addrgen.AddrGen(self.rng, self.mem, excluded_pma_regions=[])
        legacy_constraint = addrgen.AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,
            size=0x400,
            mask=0xFFFFFFFFFFFFF,
        )
        addr = generator.generate_address(legacy_constraint)
        self.assertEqual(addr, 0xB5D54872, "legacy first-address expectation changed")

    def test_allocated_physical_intervals_roundtrip(self):
        """Reserved physical spans come back as [start, end) intervals"""
        generator = addrgen.AddrGen(self.rng, self.mem)
        generator.reserve_memory(RV.AddressType.PHYSICAL, 0x8000_0000, 0x2000)
        generator.reserve_memory(RV.AddressType.PHYSICAL, 0x9000_0000, 0x1000)
        intervals = generator.allocated_physical_intervals()
        self.assertIn((0x8000_0000, 0x8000_2000), intervals)
        self.assertIn((0x9000_0000, 0x9000_1000), intervals)

    def test_extra_excluded_regions_avoided(self):
        """Regions registered via exclude_pma_region are avoided like constructor exclusions"""
        generator = addrgen.AddrGen(self.rng, self.mem)
        carveout = PmaInfo(pma_name="pma_carve", pma_address=0x8000_0000, pma_size=0x1000_0000, pma_valid=True)
        generator.exclude_pma_region(carveout)
        for _ in range(20):
            addr = generator.generate_address(self.physical_constraint())
            self.assertIsNone(generator._hits_excluded_pma(addr, 0x1000), f"0x{addr:x} landed in the excluded carve-out")


if __name__ == "__main__":
    unittest.main(verbosity=2)
