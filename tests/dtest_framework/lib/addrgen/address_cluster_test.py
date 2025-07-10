# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.lib.addrgen.address_cluster import AddressCluster
from riescue.dtest_framework.lib.addrgen.types import AddressConstraint


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

        # TODO: Fixup these tests later. It seems like the caller has to do a lot of work on the state of the cluster
        # like adding the starting ranges, setting the size of the cluster, decrementing the size/available memory, total_allocated_address

        self.cluster = AddressCluster(self.rng, 11)
        start_addr = 1 << 11
        self.assertEqual(self.cluster.start_address, start_addr)
        self.assertEqual(self.cluster.end_address, start_addr + (1 << 11) - 1)
        self.assertEqual(self.cluster.total_memory, 1 << 11)
        self.assertEqual(len(self.cluster.allocated_addresses), 0)
        self.cluster.allocated_addresses.add((self.cluster.start_address, self.cluster.end_address))
        self.assertEqual(len(self.cluster.allocated_addresses), 1)

        constraint = AddressConstraint(size=0x10, qualifiers={RV.AddressQualifiers.ADDRESS_DRAM})
        ucluster = self.cluster.find_ucluster(constraint)
        new_addr = self.cluster.allocate_address(constraint, ucluster)
