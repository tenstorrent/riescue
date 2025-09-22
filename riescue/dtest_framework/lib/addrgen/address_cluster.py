# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from collections import defaultdict
from typing import Optional, DefaultDict, Literal, Tuple

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.lib.addrgen.types import AddressConstraint, ClusterFlags
from riescue.dtest_framework.lib.addrgen.address_range import AddressRange, AddressRangeSet, address_range_set
from riescue.dtest_framework.lib.addrgen.exceptions import AddrGenError

log = logging.getLogger(__name__)


class AddressCluster:
    """
    Represents a cluster of address ranges within a specific power-of-2 range.

    The cluster_id defines the power-of-2 boundary for this address range cluster:
    - start_address = 1 << cluster_id
    - end_address = (1 << (cluster_id + 1)) - 1

    This clustering strategy serves two key purposes:
    1. Ensures that addresses are grouped by magnitude, allowing more controlled allocation
    2. Creates a distribution of addresses across the entire address space instead of
       generating only huge numbers, producing more interesting test scenarios

    Each cluster contains multiple AddressRangeSet collections for tracking:
    - Allocated address ranges
    - Interesting address ranges (special cases)
    - Qualifier-specific ranges (DRAM, MMIO, etc.)

    :param rng: Random number generator for address selection within the cluster
    :param cluster_id: Power-of-2 exponent that defines this cluster's address boundaries
    """

    def __init__(self, rng: RandNum, cluster_id: int):
        self.rng = rng
        self.cluster_id = cluster_id
        self.start_address = 1 << cluster_id
        self.end_address = (1 << (cluster_id + 1)) - 1
        self.total_memory = self.end_address - self.start_address + 1
        self.available_memory = self.total_memory
        self.flags = ClusterFlags()
        self.start_allocated = False
        self.end_allocated = False
        self.qualifier_size: DefaultDict[RV.AddressQualifiers, int] = defaultdict(int)
        self.super_cluster: DefaultDict[RV.AddressQualifiers, AddressRangeSet] = defaultdict(address_range_set)

        self.allocated_addresses = address_range_set()
        self.interesting_addresses = address_range_set()

        for q in RV.AddressQualifiers:
            self.super_cluster[q] = address_range_set()
            self.qualifier_size[q] = 0

    def __str__(self) -> str:
        cluster = self.cluster_id
        s = f"cluster: {cluster}:\n"
        s += f"\tstart_address: 0x{self.start_address:016x}\n"
        s += f"\tend_address: 0x{self.end_address:016x}\n"
        s += f"\ttotal_memory: 0x{self.total_memory:016x}\n"
        s += f"\tavailable_memory: 0x{self.available_memory:016x}\n"
        s += "\tuclusters:\n"
        for qualifier, super_cluster in self.super_cluster.items():
            if len(super_cluster) == 0:
                continue
            s += f"\t\t{qualifier}: ["
            s += str(super_cluster)
            s += "]\n"
        s += "\tsize_available per qualifier:\n"
        for q, size in self.qualifier_size.items():
            if size > 0:
                s += f"\t\t{q} = 0x{size:x}\n"
        s += "\tallocated_addresses: ["
        for r in self.allocated_addresses:
            s += f"(0x{r[0]:016x}, 0x{r[1]:016x}), "
        s += "]\n"
        s += "\tinteresting_addresses: ["
        for r in self.interesting_addresses:
            s += f"(0x{r[0]:016x}, 0x{r[1]:016x}), "
        s += "]\n"
        s += f"\tallocated_num = {hex(len(self.allocated_addresses))}\n"
        return s

    def find_ucluster(self, constraint: AddressConstraint) -> AddressRangeSet:
        """
        Returns a sorted set of addresses overalapping all the qualifiers
        """
        qualifiers = list(constraint.qualifiers)
        # 1) Figure out common overlapping addresses of all qualifiers
        # 2) Remove addresses which overlap with allocated_addresses
        # 3) Remove addresses which are less than constraint.size
        # 4) Remove addresses which don't comply with mask

        # Do (1)
        cluster_range = self.super_cluster[qualifiers[0]]
        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"qualifiers: {qualifiers}, super_clusters inside this cluster: {str(cluster_range)}")

        qualifiers.pop(0)
        for q in qualifiers:
            if log.isEnabledFor(logging.DEBUG):
                log.debug(f"Calling get_intersection for {cluster_range} and {self.super_cluster[q]}")
            cluster_range = self._get_intersection(cluster_range, self.super_cluster[q])

        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"common overlapping addresses: {common.format_hex_list(cluster_range)}")  # Should this be str()?

        # Do (2)
        new_list = address_range_set()
        for i in cluster_range:
            overlap = self.allocated_addresses.overlap(i)
            if len(overlap) == 0:
                new_list.add(i)
            else:
                start, end = i[0], i[1]
                for j in overlap:
                    # (1, 100) overlaps with (20,20), (70,100), but (1,20)
                    # and (50, 70) are possible
                    if start < j[0]:
                        new_list.add((start, j[0] - 1))
                    start = j[1] + 1
                if start < end:
                    new_list.add((start, end))

        # Do (3)
        new_addr_range = address_range_set()
        size = constraint.size
        for start, end in new_list:
            cluster_size = end - start + 1
            if cluster_size >= size:
                new_addr_range.add((start, end))
        return new_addr_range

    def allocate_address(self, constraint: AddressConstraint, uclusters: AddressRangeSet) -> Optional[int]:
        """
        allocate_address() will actually allocate the adddress near an
        existing or interesting address already present in the cluster
        """
        # 1) Allocate anywhere (pure random)
        # 2) Allocate next to allocated addresses
        # 3) Allocate next to interesting addresses
        # 4) Allocate next to either

        if len(self.allocated_addresses) == 0 or (self.rng.percent() < 90):
            start_addr = self._allocate_anywhere(constraint, uclusters)
        elif self.rng.percent() <= 100:
            # allocate near
            addresses = self.allocated_addresses
            start_addr = self._allocate_near(constraint, uclusters, addresses)
        else:
            # FIXME: Interesting addresses are not yet being filled
            addresses = self.interesting_addresses
            start_addr = self._allocate_near(constraint, uclusters, addresses)

        # Allocate near can fail in some situations where allocate_anywhere will still work
        if start_addr is None:
            start_addr = self._allocate_anywhere(constraint, uclusters)

        if (constraint.dont_allocate is False) and (start_addr is not None):
            size = constraint.size
            qualifiers = constraint.qualifiers
            self.allocated_addresses.add((start_addr, start_addr + size - 1))
            self.available_memory -= size
            for q in qualifiers:
                self.qualifier_size[q] -= size

        return start_addr

    def overlap_level(self, entry: AddressRange, ucluster: AddressRangeSet) -> Literal[0, 1, 2]:
        """
        Return
            0: no overlap
            1: complete overlap
            2: partial overlap
        """
        level = 0
        overlap = ucluster.overlap(entry)
        for start, end in overlap:
            level = 2
            if (entry[0] >= start) and (entry[1] <= end):
                level = 1
                break

        return level

    def _allocate_near(self, constraint: AddressConstraint, uclusters: AddressRangeSet, addresses: AddressRangeSet) -> Optional[int]:
        """
        _allocate_near() will allocate an address of given size, near one of the
        existing limits inside the given array and inflate the limit after
        allocating the address
        """
        if len(addresses) == 0:
            return self._allocate_anywhere(constraint, uclusters)

        size, mask = constraint.size, constraint.mask

        # Need to allocate near "addresses"
        address_list_shuffle = list(addresses)
        self.rng.shuffle(address_list_shuffle)
        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"trying NEAR other addresses in cluster: {self.cluster_id}")

        for entry in address_list_shuffle:
            start_entry, end_entry = entry[0], entry[1]
            if self.rng.percent() < 50:
                # case (1) -> allocate before the entry
                start = (start_entry - size) & mask
            else:
                # case (2) -> allocate after the entry
                start = (end_entry + 1) & mask
            end = start + size - 1

            # Look for reasons why this selection will not work
            if len(self.allocated_addresses.overlap((start, end))) or start < self.start_address or end > self.end_address:
                # Invalid address, try near next allocation
                continue
            if not any(start >= u[0] and end <= u[1] for u in uclusters):
                # Address is not in any of the constrained valid super clusters
                continue

            # Region is good to use
            return start

        log.warning(f"could not generate NEAR address in cluster: {self.cluster_id}")

        return None

    def _allocate_anywhere(self, constraint: AddressConstraint, uclusters: AddressRangeSet) -> Optional[int]:
        """
        allocates memory anywhere in the cluster. Since the allocation is random,
        we need to make sure we are not overlappgin with existing allocated addresses

        :param constraint: AddressConstraint
        :param uclusters: banyan.SortedSet
        :param addresses: banyan.SortedSet
        :return: int
        """
        size, mask = constraint.size, constraint.mask
        allocating_near_start, allocating_near_end = False, False

        # A small number of times, allocate near the limits of the micro-cluster
        if (not self.start_allocated or not self.end_allocated) and self.rng.percent() < 10:
            start = None
            if not self.start_allocated:
                start = self.start_address & mask
                end = start + size + 1
                allocating_near_start = True

            elif not self.end_allocated:
                start = (self.end_address - size + 1) & mask
                end = start + size + 1
                allocating_near_end = True

            if start is None:
                raise AddrGenError("Failed to allocate near the limits of the micro-cluster")

            level = self.overlap_level((start, end), uclusters)
            if level == 1:
                if allocating_near_start:
                    self.start_allocated = True
                else:
                    self.end_allocated = True

        # Randomly allocate anywhere
        ucluster_list = list(uclusters)
        for _ in range(len(ucluster_list)):
            rnd_ucluster = self.rng.random_entry_in(ucluster_list)
            cluster_start, cluster_end = rnd_ucluster[0], rnd_ucluster[1]

            for _ in range(10):
                address = self.rng.random_in_range(cluster_start, (cluster_end - size + 1) + 1)
                start = address & mask
                end = start + size - 1
                level = self.overlap_level((start, end), uclusters)
                if level == 1:
                    return start
            ucluster_list.remove(rnd_ucluster)

        return None

    def _get_intersection(self, cluster1: AddressRangeSet, cluster2: AddressRangeSet) -> AddressRangeSet:
        """
        Get the overlap/intersection of two SortedSet lists
        """
        ilist = address_range_set()
        if log.isEnabledFor(logging.DEBUG):

            log.debug(f"get_intersection: cluster1: {cluster1}, cluster2: {cluster2}")

        for i in cluster1:
            for j in cluster2:
                # Compute the actual intersection
                start, end = max(i[0], j[0]), min(i[1], j[1])

                # Only add to ilist if it's a valid non-empty intersection
                if start < end:
                    ilist.add((start, end))
        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"get_intersection: {ilist}")
        return ilist
