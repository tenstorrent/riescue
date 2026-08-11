# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import time
import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.address_cluster import AddressCluster
from riescue.riemap.addrgen.address_range import address_range_set
from riescue.riemap.addrgen.types import AddressConstraint


class AddressClusterTest(unittest.TestCase):
    """
    Test the addrgen module. Target AddressRangeSet
    """

    def setUp(self):
        self.rng = RandNum(0)
        self.cluster = AddressCluster(self.rng, 0)

    def test_address_cluster_zero_cluster(self):
        """
        basic API test for public methods
        """
        # address_range_set should return an AddressRangeSet
        self.cluster = AddressCluster(self.rng, 0)
        constraint = AddressConstraint(size=0x1, qualifiers={RV.AddressQualifiers.ADDRESS_DRAM})
        ucluster = self.cluster.find_ucluster(constraint)
        new_addr = self.cluster.allocate_address(constraint, ucluster)
        self.assertIsNone(new_addr)

    def test_address_cluster_non_zero_cluster(self):
        """
        Test the addrgen module with a non-zero cluster
        """

        self.cluster = AddressCluster(self.rng, 11)
        start_addr = 1 << 11
        self.assertEqual(self.cluster.start_address, start_addr)
        self.assertEqual(self.cluster.end_address, start_addr + (1 << 11) - 1)
        self.assertEqual(self.cluster.total_memory, 1 << 11)
        self.assertEqual(len(self.cluster.allocated_addresses), 0)
        self.cluster.super_cluster[RV.AddressQualifiers.ADDRESS_DRAM].add((self.cluster.start_address, self.cluster.end_address))

        constraint = AddressConstraint(
            size=0x10,
            mask=0xFFFF_FFFF_FFFF_FFFF,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
        )
        ucluster = self.cluster.find_ucluster(constraint)
        new_addr = self.cluster.allocate_address(constraint, ucluster)
        self.assertIsNotNone(new_addr)
        self.assertGreaterEqual(new_addr, self.cluster.start_address)
        self.assertLessEqual(new_addr + constraint.size - 1, self.cluster.end_address)

    def test_boundary_bias_returns_the_cluster_start(self):
        cluster = AddressCluster(RandNum(seed=1), 12)
        qualifier = RV.AddressQualifiers.ADDRESS_DRAM
        cluster.super_cluster[qualifier].add((cluster.start_address, cluster.end_address))
        cluster.qualifier_size[qualifier] = cluster.total_memory
        constraint = AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            bits=16,
            size=0x100,
            mask=0xFFFF,
            qualifiers={qualifier},
        )
        uclusters = cluster.find_ucluster(constraint)
        cluster.rng.percent = lambda: 0
        cluster.rng.random_entry_in = lambda entries: entries[0]
        cluster.rng.random_in_range = lambda start, end: start + 0x200

        self.assertEqual(
            cluster.allocate_address(constraint, uclusters),
            cluster.start_address,
        )


class ReachableBaseInWindowTest(unittest.TestCase):
    """``_reachable_base_in_window`` is MaskedBases over AddrGen's inclusive windows."""

    def setUp(self):
        self.cluster = AddressCluster(RandNum(seed=1), 20)

    def test_empty_domain_returns_none(self):
        # or_mask forces a bit the window cannot satisfy.
        self.assertIsNone(self.cluster._reachable_base_in_window(0x1000, 0x1FFF, 0x1000, 0xFFFFFFFFFFFFF000, 0x2000))

    def test_window_too_small_for_size_returns_none(self):
        self.assertIsNone(self.cluster._reachable_base_in_window(0x1000, 0x17FF, 0x1000, 0xFFFFFFFFFFFFF000, 0))

    def test_selection_satisfies_the_mask(self):
        mask, or_mask = 0xFFFFFFFFFFFFF000, 0x8000
        for seed in range(16):
            self.cluster.rng = RandNum(seed=seed)
            base = self.cluster._reachable_base_in_window(0x10000, 0x3FFFF, 0x1000, mask, or_mask)
            self.assertIsNotNone(base)
            self.assertEqual(base, (base & mask) | or_mask)
            self.assertTrue(0x10000 <= base <= 0x3FFFF - 0x1000 + 1)

    def test_many_fragmented_windows_stay_first_viable(self):
        """Per-draw cost must not scale with window count (shuffle + first hit)."""
        mask = 0xFFFFFFFFFFFFF000
        # Many empty-looking windows followed by one viable: shuffle still tries one at a time.
        windows = [(i * 0x2000, i * 0x2000 + 0x7FF) for i in range(2000)]  # 0x800 bytes each: too small for size 0x1000
        windows.append((0x10_0000, 0x10_1FFF))
        constraint = AddressConstraint(size=0x1000, mask=mask, or_mask=0, bits=64)
        uclusters = address_range_set(windows)
        started = time.monotonic()
        for seed in range(20):
            self.cluster.rng = RandNum(seed=seed)
            base = self.cluster._allocate_reachable(constraint, uclusters)
            self.assertIsNotNone(base)
            self.assertTrue(0x10_0000 <= base <= 0x10_1000)
        self.assertLess(time.monotonic() - started, 5.0)


if __name__ == "__main__":
    unittest.main()
