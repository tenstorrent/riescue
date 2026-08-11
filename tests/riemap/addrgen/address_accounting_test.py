# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.address_generator import AddrGen
from riescue.riemap.addrgen.address_space import AddressSpace
from riescue.riemap.addrgen.types import AddressConstraint, ExcludedRegion
from riescue.riemap.memory import DramRange, Memory


class TestReservationAccounting(unittest.TestCase):
    def test_cluster_zero_accounts_for_addresses_zero_and_one(self):
        space = AddressSpace(RandNum(seed=1), RV.AddressType.LINEAR)
        space.define_segment(
            RV.AddressQualifiers.ADDRESS_LINEAR,
            0,
            1,
        )
        cluster = space.clusters[0]

        self.assertEqual(cluster.total_memory, 2)
        self.assertEqual(cluster.available_memory, 2)
        self.assertEqual(
            cluster.qualifier_size[RV.AddressQualifiers.ADDRESS_LINEAR],
            2,
        )

        space.reserve_memory(0, 1)
        self.assertEqual(cluster.available_memory, 0)
        self.assertEqual(
            cluster.qualifier_size[RV.AddressQualifiers.ADDRESS_LINEAR],
            0,
        )

    def test_overlapping_and_idempotent_reservations_count_only_new_bytes(self):
        base = 0x80000000
        space = AddressSpace(RandNum(seed=1), RV.AddressType.PHYSICAL)
        space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, base, base + 0xFFF)
        cluster = space.clusters[base.bit_length() - 1]
        initial_available = cluster.available_memory
        initial_dram = cluster.qualifier_size[RV.AddressQualifiers.ADDRESS_DRAM]

        space.reserve_memory(base, base + 0xFF)
        space.reserve_memory(base + 0x80, base + 0x17F)
        space.reserve_memory(base, base + 0x17F)

        self.assertEqual(cluster.available_memory, initial_available - 0x180)
        self.assertEqual(cluster.qualifier_size[RV.AddressQualifiers.ADDRESS_DRAM], initial_dram - 0x180)

    def test_segment_defined_after_reservation_counts_only_free_bytes(self):
        base = 0x80000000
        space = AddressSpace(RandNum(seed=1), RV.AddressType.PHYSICAL)
        space.reserve_memory(base, base + 0xFF)
        space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, base, base + 0xFFF)

        cluster = space.clusters[base.bit_length() - 1]
        self.assertEqual(cluster.qualifier_size[RV.AddressQualifiers.ADDRESS_DRAM], 0xF00)

    def test_clone_copies_only_a_touched_cluster(self):
        base = 0x80000000
        space = AddressSpace(RandNum(seed=1), RV.AddressType.PHYSICAL)
        space.define_segment(
            RV.AddressQualifiers.ADDRESS_DRAM,
            base,
            base + 0x3FFF,
        )
        cluster_id = base.bit_length() - 1
        original_cluster = space.clusters[cluster_id]
        original_available = original_cluster.available_memory

        cloned = space.clone(RandNum(seed=2))

        self.assertIs(cloned.clusters[cluster_id], original_cluster)
        cloned.reserve_memory(base, base + 0xFFF)
        self.assertIsNot(cloned.clusters[cluster_id], original_cluster)
        self.assertEqual(
            original_cluster.available_memory,
            original_available,
        )


class TestCandidateCommit(unittest.TestCase):
    _LO = 0x80100000
    _SIZE = 0x1000
    _MASK = 0xFFFFFFFFFFF00000
    _OR_MASK = 0x1000

    @classmethod
    def _constraint(cls, address_type=RV.AddressType.PHYSICAL):
        return AddressConstraint(
            type=address_type,
            bits=64,
            size=cls._SIZE,
            mask=cls._MASK,
            or_mask=cls._OR_MASK,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM} if address_type == RV.AddressType.PHYSICAL else set(),
        )

    def test_rejected_excluded_candidates_do_not_leak(self):
        saw_rejection = False
        for seed in range(40):
            hits = {"n": 0}
            base = ExcludedRegion.from_interval(0x80101000, 0x80102000)

            class Counted:
                def overlaps(self, start, size):
                    matched = base.overlaps(start, size)
                    if matched:
                        hits["n"] += 1
                    return matched

                def interval(self):
                    return base.interval()

                def __str__(self):
                    return str(base)

            excluded = Counted()
            addrgen = AddrGen(
                RandNum(seed=seed),
                Memory(dram_ranges=[DramRange(start=self._LO, size=0x400000)]),
                excluded_regions=[excluded],
            )
            address = addrgen.generate_address(self._constraint())
            self.assertNotEqual(address, 0x80101000)
            allocated = addrgen.allocated_physical_intervals()
            self.assertEqual(sum(end - start for start, end in allocated), self._SIZE)
            self.assertEqual(addrgen._physical_addr_space.total_allocated_address, 1)
            saw_rejection |= hits["n"] > 0
        self.assertTrue(saw_rejection, "seed sweep did not exercise a rejected candidate")

    def test_memory_commit_uses_exact_half_open_size(self):
        addrgen = AddrGen(
            RandNum(seed=3),
            Memory(dram_ranges=[DramRange(start=self._LO, size=0x400000)]),
        )
        constraint = self._constraint(RV.AddressType.MEMORY)
        constraint.start = 0x80201000
        constraint.end = 0x80201FFF
        address = addrgen.generate_address(constraint)

        self.assertEqual(address, 0x80201000)
        self.assertEqual(addrgen.allocated_physical_intervals(), [(address, address + self._SIZE)])


if __name__ == "__main__":
    unittest.main()
