# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import copy
from collections import defaultdict
from typing import Optional, DefaultDict, Literal

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.types import AddressConstraint, ClusterFlags
from riescue.riemap.addrgen.address_range import AddressRange, AddressRangeSet, address_range_set
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.masked_bases import MaskedBases

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
        # Address 0 has no bit set, so _address_to_cluster maps it to cluster 0 along
        # with address 1. Cluster 0 therefore covers [0, 1] rather than [1, 1]; without
        # this, address 0 belongs to no cluster and can never be drawn or reserved.
        self.start_address = 0 if cluster_id == 0 else 1 << cluster_id
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

    def clone(self, rng: RandNum) -> "AddressCluster":
        """Copy mutable allocation state without rebuilding interval-tree internals."""
        result = copy.copy(self)
        result.rng = rng
        result.flags = copy.copy(self.flags)
        result.qualifier_size = defaultdict(int, self.qualifier_size)
        # Segment definitions are immutable after AddrGen construction.
        result.super_cluster = defaultdict(address_range_set, self.super_cluster)
        result.allocated_addresses = address_range_set(self.allocated_addresses)
        result.interesting_addresses = address_range_set(self.interesting_addresses)
        return result

    def find_ucluster(self, constraint: AddressConstraint) -> AddressRangeSet:
        """
        Returns a sorted set of addresses overalapping all the qualifiers
        """
        qualifiers = list(constraint.qualifiers)
        # 1) Figure out common overlapping addresses of all qualifiers
        # 1b) If constraint has explicit start/end bounds, intersect with those
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

        # Do (1b) - Intersect with explicit bounds when set (used by custom regions)
        if constraint.is_bounded():
            bounds_lo, bounds_hi = constraint.bounds()
            bounds: AddressRangeSet = address_range_set()
            bounds.add((bounds_lo, bounds_hi))
            cluster_range = self._get_intersection(cluster_range, bounds)
            if log.isEnabledFor(logging.DEBUG):
                log.debug(f"After bounds intersection [{bounds_lo:#x}, {bounds_hi:#x}]: {cluster_range}")

        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"common overlapping addresses: {str(cluster_range)}")

        # Keep the qualifier/bounds windows broad. Normal allocation probes those
        # windows and checks the allocated interval tree directly; materializing every
        # free hole on every draw is quadratic for large sparse request sets.
        size = constraint.size
        return address_range_set((start, end) for start, end in cluster_range if end - start + 1 >= size)

    def _free_uclusters(self, cluster_range: AddressRangeSet, size: int) -> AddressRangeSet:
        """Materialize exact free windows for the completeness fallback."""
        free_ranges = []
        for i in cluster_range:
            overlap = self.allocated_addresses.overlap(i)
            if len(overlap) == 0:
                free_ranges.append(i)
            else:
                start, end = i[0], i[1]
                for j in overlap:
                    # (1, 100) overlaps with (20,20), (70,100), but (1,20)
                    # and (50, 70) are possible
                    if start < j[0]:
                        free_ranges.append((start, j[0] - 1))
                    start = max(start, j[1] + 1)
                if start <= end:
                    free_ranges.append((start, end))
        return address_range_set((start, end) for start, end in free_ranges if end - start + 1 >= size)

    def allocate_address(self, constraint: AddressConstraint, uclusters: AddressRangeSet) -> Optional[int]:
        """
        allocate_address() will actually allocate the adddress near an
        existing or interesting address already present in the cluster
        """
        if constraint.pinned:
            # Coloring-pinned draw: pick from the reachable slot set directly (see _allocate_pinned).
            start_addr = self._allocate_pinned(constraint, self._free_uclusters(uclusters, constraint.size))
        else:
            if len(self.allocated_addresses) == 0 or (self.rng.percent() < 90):
                start_addr = self._allocate_anywhere(constraint, uclusters)
            else:
                # The proximity path has a separate policy draw. Its only
                # supported source is the allocated-address set.
                self.rng.percent()
                addresses = self.allocated_addresses
                start_addr = self._allocate_near(constraint, uclusters, addresses)

            # Allocate near can fail in some situations where allocate_anywhere will still work
            if start_addr is None:
                start_addr = self._allocate_anywhere(constraint, uclusters)

        if (constraint.dont_allocate is False) and (start_addr is not None):
            self.reserve_address(start_addr, start_addr + constraint.size - 1)

        return start_addr

    def reserve_address(self, start: int, end: int, interesting_address: bool = False) -> int:
        """Reserve an inclusive span and return the number of newly covered bytes."""
        uncovered = [(start, end)]
        for allocated_start, allocated_end in self.allocated_addresses.overlap((start, end)):
            next_uncovered = []
            for lo, hi in uncovered:
                if allocated_end < lo or allocated_start > hi:
                    next_uncovered.append((lo, hi))
                    continue
                if lo < allocated_start:
                    next_uncovered.append((lo, allocated_start - 1))
                if allocated_end < hi:
                    next_uncovered.append((allocated_end + 1, hi))
            uncovered = next_uncovered

        newly_reserved = sum(hi - lo + 1 for lo, hi in uncovered)
        for span in uncovered:
            self.allocated_addresses.add(span)
            if interesting_address:
                self.interesting_addresses.add(span)

        self.available_memory -= newly_reserved
        for qualifier, ranges in self.super_cluster.items():
            covered = []
            for lo, hi in uncovered:
                for range_lo, range_hi in ranges.overlap((lo, hi)):
                    covered.append((max(lo, range_lo), min(hi, range_hi)))
            if covered:
                covered.sort()
                merged = [covered[0]]
                for lo, hi in covered[1:]:
                    if lo <= merged[-1][1] + 1:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                    else:
                        merged.append((lo, hi))
                self.qualifier_size[qualifier] -= sum(hi - lo + 1 for lo, hi in merged)
        return newly_reserved

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
                start = ((start_entry - size) & mask) | constraint.or_mask
            else:
                # case (2) -> allocate after the entry
                start = ((end_entry + 1) & mask) | constraint.or_mask
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
        if (not self.start_allocated or not self.end_allocated) and self.rng.percent() < 10:
            start = None
            allocating_near_start = False
            if not self.start_allocated:
                start = (self.start_address & mask) | constraint.or_mask
                allocating_near_start = True
            elif not self.end_allocated:
                start = ((self.end_address - size + 1) & mask) | constraint.or_mask
            if start is None:
                raise AddrGenError("failed to construct a cluster-boundary allocation")
            end = start + size - 1
            if start >= self.start_address and end <= self.end_address and self.overlap_level((start, end), uclusters) == 1 and len(self.allocated_addresses.overlap((start, end))) == 0:
                if allocating_near_start:
                    self.start_allocated = True
                else:
                    self.end_allocated = True
                return start

        ucluster_list = list(uclusters)
        for _ in range(len(ucluster_list)):
            rnd_ucluster = self.rng.random_entry_in(ucluster_list)
            cluster_start, cluster_end = rnd_ucluster
            for _ in range(10):
                address = self.rng.random_in_range(
                    cluster_start,
                    (cluster_end - size + 1) + 1,
                )
                start = (address & mask) | constraint.or_mask
                end = start + size - 1
                if self.overlap_level((start, end), uclusters) == 1 and len(self.allocated_addresses.overlap((start, end))) == 0:
                    return start
            ucluster_list.remove(rnd_ucluster)

        # The compatibility probes above preserve established seeded layouts. Exact
        # enumeration is the completeness fallback when those probes miss sparse slots.
        return self._allocate_reachable(constraint, self._free_uclusters(uclusters, size))

    def _allocate_pinned(self, constraint: AddressConstraint, uclusters: AddressRangeSet) -> Optional[int]:
        """
        Reachable-slot allocation for coloring-pinned constraints.

        A tight and_mask with interior bits cleared plus a big or_mask forces a specific
        index congruence class. Probe-and-mask (_allocate_anywhere) almost always masks the
        random probe back out of the free window, so it spuriously fails on a near-empty
        pool. Instead, select directly from the reachable set -- the bases b for which
        b == (b & mask) | or_mask -- inside each free ucluster window, by randomizing only
        the free bits (set in mask, clear in or_mask) that keep b in range.
        """
        return self._allocate_reachable(constraint, uclusters)

    def _allocate_reachable(self, constraint: AddressConstraint, uclusters: AddressRangeSet) -> Optional[int]:
        """Choose exactly from masked bases that fit one of the free windows."""
        size, mask, or_mask = constraint.size, constraint.mask, constraint.or_mask
        windows = list(uclusters)
        self.rng.shuffle(windows)
        for lo, hi in windows:
            base = self._reachable_base_in_window(lo, hi, size, mask, or_mask)
            if base is None:
                continue
            end = base + size - 1
            if self.overlap_level((base, end), uclusters) == 1 and len(self.allocated_addresses.overlap((base, end))) == 0:
                return base
        return None

    def _reachable_base_in_window(self, lo: int, hi: int, size: int, mask: int, or_mask: int) -> Optional[int]:
        """Pick a random base ``b`` in ``[lo, hi - size + 1]`` with ``b == (b & mask) | or_mask``.

        ``lo`` / ``hi`` are inclusive free-window bounds (AddrGen's interval convention).
        The reachable set is counted and un-ranked in ``O(address bits)``; an empty set
        returns ``None`` so the caller can try another window.
        """
        hmax = hi - size + 1
        if hmax < lo:
            return None
        domain = MaskedBases(and_mask=mask, or_mask=or_mask)
        count = domain.count_in(lo, hmax)
        if count == 0:
            return None
        return domain.nth_in(lo, hmax, self.rng.randrange(0, count))

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
                if start <= end:
                    ilist.add((start, end))
        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"get_intersection: {ilist}")
        return ilist
