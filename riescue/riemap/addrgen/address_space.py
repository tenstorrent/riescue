# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import copy
from collections import defaultdict
from typing import Dict, DefaultDict, Iterable, List, MutableSet, Optional, Tuple

from sortedcontainers import SortedSet

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.address_cluster import AddressCluster
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.addrgen.types import AddressConstraint
from riescue.riemap.masked_bases import MaskedBases, choose_in_windows, free_windows

log = logging.getLogger(__name__)


def _merge_inclusive(spans: Iterable[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Coalesce inclusive spans, joining ones that touch as well as ones that overlap."""
    merged: List[Tuple[int, int]] = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _intersect_inclusive(left: List[Tuple[int, int]], right: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Intersect two lists of disjoint, ascending inclusive spans."""
    out: List[Tuple[int, int]] = []
    i = j = 0
    while i < len(left) and j < len(right):
        lo = max(left[i][0], right[j][0])
        hi = min(left[i][1], right[j][1])
        if lo <= hi:
            out.append((lo, hi))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return out


class AddressSpace:
    def __init__(self, rng: RandNum, address_type: RV.AddressType):
        self.rng = rng
        self.address_type = address_type
        self.total_allocated_address = 0
        self.all_valid_clusters: MutableSet[int] = SortedSet()
        self.clusters: Dict[int, AddressCluster] = dict()
        self.sub_clusters: DefaultDict[RV.AddressQualifiers, SortedSet] = defaultdict(SortedSet)
        self._shared_clusters: set[int] = set()

        # Create clusters
        for i in range(64):
            cluster_instance = AddressCluster(self.rng, i)
            if cluster_instance.start_address == cluster_instance.end_address:
                log.debug(f"Cluster {i} has no address range (start==end)")
            self.clusters[i] = cluster_instance

    def define_segment(self, qualifier: RV.AddressQualifiers, start: int, end: int) -> None:
        """
        Given a (start, end) and qualifier, assign the address to its respective
        cluster(s)
        """
        log.debug(f"{self.address_type}: Setting {qualifier} range: " f"0x{start:016x} - 0x{end:016x}")
        if start > end:
            log.error(f"Cannot define a segment with start address after end address: start=0x{start:x} > end=0x{end:x}")

        start_cluster, end_cluster = self._address_to_cluster(start, end)
        for i in range(start_cluster, end_cluster + 1):
            cluster_instance = self.clusters[i]

            start_address = cluster_instance.start_address
            end_address = cluster_instance.end_address
            if i == start_cluster:
                start_address = start
            if i == end_cluster:
                end_address = end

            super_clster = cluster_instance.super_cluster[qualifier]
            super_clster.add((start_address, end_address))
            self.all_valid_clusters.add(i)
            self.sub_clusters[qualifier].add(i)
            size = end_address - start_address + 1
            allocated = []
            for allocated_start, allocated_end in cluster_instance.allocated_addresses.overlap((start_address, end_address)):
                allocated.append((max(start_address, allocated_start), min(end_address, allocated_end)))
            allocated.sort()
            merged = []
            for lo, hi in allocated:
                if merged and lo <= merged[-1][1] + 1:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                else:
                    merged.append((lo, hi))
            unavailable = sum(hi - lo + 1 for lo, hi in merged)
            cluster_instance.qualifier_size[qualifier] += size - unavailable

    def clone(self, rng: RandNum) -> "AddressSpace":
        """Create a copy-on-write speculative allocation branch."""
        result = copy.copy(self)
        result.rng = rng
        # Segment declarations never change after AddrGen construction. Allocation
        # state lives in clusters; share those until either branch mutates one.
        result.clusters = dict(self.clusters)
        shared = set(self.clusters)
        self._shared_clusters.update(shared)
        result._shared_clusters = shared
        return result

    def _mutable_cluster(self, cluster_id: int) -> AddressCluster:
        """Return this branch's private copy of a cluster before mutation."""
        cluster = self.clusters[cluster_id]
        if cluster_id in self._shared_clusters:
            cluster = cluster.clone(self.rng)
            self.clusters[cluster_id] = cluster
            self._shared_clusters.remove(cluster_id)
        else:
            cluster.rng = self.rng
        return cluster

    def check_overlap(self, start: int, end: int) -> bool:
        """
        Given (start, end) for a given address_type, check if it overlaps
        with other addresses
        Return:
            True: if overlaps
            False: else
        """
        cl_start, cl_end = self._address_to_cluster(start, end)
        for i in range(cl_start, cl_end + 1):
            cl = self.clusters[i]
            if len(cl.allocated_addresses.overlap((start, end))) != 0:
                return True

        return False

    def declares_span(self, start: int, end: int, qualifiers: Optional[Iterable[RV.AddressQualifiers]] = None) -> bool:
        """Whether the inclusive span lies wholly inside segments of the given kind.

        Allocation state says nothing about whether an address exists: a physical
        address outside every declared range is unallocated but also unbacked, so a
        caller drawing in one pool and checking membership in another needs this.
        ``qualifiers`` defaults to every declared kind.
        """
        wanted = set(qualifiers) if qualifiers is not None else None
        spans: List[Tuple[int, int]] = []
        cl_start, cl_end = self._address_to_cluster(start, end)
        for cluster_id in range(cl_start, cl_end + 1):
            for qualifier, ranges in self.clusters[cluster_id].super_cluster.items():
                if wanted is not None and qualifier not in wanted:
                    continue
                spans.extend(ranges.overlap((start, end)))
        cursor = start
        for lo, hi in _merge_inclusive(spans):
            if lo > cursor:
                return False
            cursor = max(cursor, hi + 1)
            if cursor > end:
                return True
        return cursor > end

    def reserve_memory(self, start: int, end: int, interesting_address: bool = False) -> None:
        """
        Interface to reserve memory
        """
        cl_start, cl_end = self._address_to_cluster(start, end)
        for i in range(cl_start, cl_end + 1):
            cluster = self._mutable_cluster(i)
            start_addr = cluster.start_address
            end_addr = cluster.end_address
            if i == cl_start:
                start_addr = start
            if i == cl_end:
                end_addr = end

            log.debug(f"reserving memory: {start_addr:x} - {end_addr:x} in cluster {i}, available: {cluster.available_memory:x}")
            newly_reserved = cluster.reserve_address(start_addr, end_addr, interesting_address)
            log.debug(f"available memory after: {cluster.available_memory:x}")
            if newly_reserved:
                self.total_allocated_address += 1

    def generate_address(self, constraint: AddressConstraint) -> int:
        """
        Generate a non-overlapping address and allocate it
        Currently, the logic goes like:
        1. Select a valid cluster randomly
        2. Allocate address in that cluster
        Generating address across cluster is NOT supported yet
        """
        constraint = copy.deepcopy(constraint)
        log.debug(f"constraint: {constraint}")
        # 1. Step 1
        _list = self.find_clusters(constraint)
        log.debug(f"clusters: {_list}")
        if not _list:
            spanning = self._generate_spanning_address(constraint)
            if spanning is not None:
                return spanning
            raise AddrGenError("No address range satisfies the requested qualifiers, bounds, " f"size, and mask.\n{constraint}")

        self.rng.shuffle(_list)
        for i in _list:
            cluster = self.clusters[i]
            if log.isEnabledFor(logging.DEBUG):
                log.debug("Trying to generate address with cluster: %s", cluster.cluster_id)
                log.debug("Pre-allocation cluster details:\n%s", cluster)

            # 2. Step 2
            uclusters = cluster.find_ucluster(constraint)
            if len(uclusters) != 0:
                # 2a. Allocate address
                cluster = self._mutable_cluster(i)
                addr = cluster.allocate_address(constraint, uclusters)
                # log.debug(f'addrgen: addr: {addr:x}')
                if addr is None:
                    log.debug(f"Address generation failed for cluster {i}")
                    continue
                log.debug(f"allocated: 0x{addr:016x} - " f"0x{addr+constraint.size-1:016x} " f"in cluster {cluster.cluster_id}")
                if not constraint.dont_allocate:
                    self.total_allocated_address += 1
                if log.isEnabledFor(logging.DEBUG):
                    log.debug(f"Post allocation cluster details:\n {cluster}")

                return addr
            else:
                log.warning(f"Address generation failed for cluster {i}")
                continue

        spanning = self._generate_spanning_address(constraint)
        if spanning is not None:
            return spanning

        raise AddrGenError(f"AddrGen could not pick a cluster for {constraint}")

    def _generate_spanning_address(self, constraint: AddressConstraint) -> Optional[int]:
        """Place a request whose span crosses a cluster boundary.

        Clusters partition the address space by magnitude, so a contiguous memory
        segment straddling a power of two is split into pieces that may each be too
        small for a request the whole segment could hold. Reassemble the per-cluster
        qualifier windows into the segments they came from and place only in the ones
        that still cross a boundary -- everything inside a single cluster has already
        been tried by the per-cluster path.
        """
        windows = self._segment_windows(constraint)
        if not windows:
            return None

        size = constraint.size
        free: List[Tuple[int, int]] = []
        for lo, hi in windows:
            if self._address_to_cluster(lo)[0] == self._address_to_cluster(hi)[0]:
                continue
            occupied = _merge_inclusive(self._allocated_spans(lo, hi))
            free.extend(free_windows(lo, hi + 1, [(span_lo, span_hi + 1) for span_lo, span_hi in occupied]))
        if not free:
            return None

        domain = MaskedBases.for_span(and_mask=constraint.mask, or_mask=constraint.or_mask, bits=constraint.bits)
        if domain is None:
            return None
        base = choose_in_windows(domain, free, size, self.rng)
        if base is None:
            return None
        log.debug(f"cluster-spanning allocation: 0x{base:016x} - 0x{base + size - 1:016x}")
        if not constraint.dont_allocate:
            self.reserve_memory(base, base + size - 1)
        return base

    def _segment_windows(self, constraint: AddressConstraint) -> List[Tuple[int, int]]:
        """Declared windows satisfying every requested qualifier, merged across clusters."""
        qualifiers = list(constraint.qualifiers)
        if not qualifiers:
            return []
        windows = self._qualifier_windows(qualifiers[0])
        for qualifier in qualifiers[1:]:
            windows = _intersect_inclusive(windows, self._qualifier_windows(qualifier))
        # bounds() widens an unbound end to the address width, so this clamps the
        # request's window and its declared bit width in the same step.
        windows = _intersect_inclusive(windows, [constraint.bounds()])
        return [(lo, hi) for lo, hi in windows if hi - lo + 1 >= constraint.size]

    def _qualifier_windows(self, qualifier: RV.AddressQualifiers) -> List[Tuple[int, int]]:
        spans: List[Tuple[int, int]] = []
        for cluster_id in self.sub_clusters[qualifier]:
            spans.extend(self.clusters[cluster_id].super_cluster[qualifier])
        return _merge_inclusive(spans)

    def _allocated_spans(self, start: int, end: int) -> List[Tuple[int, int]]:
        cl_start, cl_end = self._address_to_cluster(start, end)
        spans: List[Tuple[int, int]] = []
        for cluster_id in range(cl_start, cl_end + 1):
            spans.extend(self.clusters[cluster_id].allocated_addresses.overlap((start, end)))
        return spans

    def find_clusters(self, constraint: AddressConstraint) -> List[int]:
        """
        Return clusters that can accomodate the given constraint(s)
        """
        # 1. Filter the clusters based on size and mask
        # 2. Filter the clusters based on qualifiers
        # 3. Filter them on available memory

        log.debug(f"Finding clusters for {constraint}")
        qualifiers = constraint.qualifiers
        log.debug(f"qualifiers: {qualifiers}")

        # Use random qualifier if no qualifier specified
        sub_clusters = list(self.sub_clusters.keys())
        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"sub_clusters: {sub_clusters}")

        if len(qualifiers) == 0:
            if constraint.type == RV.AddressType.PHYSICAL:
                rnd_qualifier = RV.AddressQualifiers.ADDRESS_DRAM
            else:
                rnd_qualifier = self.rng.random_entry_in(sub_clusters)
            log.debug(f"Assigning default qualifier: {rnd_qualifier}")
            qualifiers.add(rnd_qualifier)

        mask, bits = constraint.mask, constraint.bits
        size = constraint.size

        # STEE uses physical bit 55 as its secure marker. For non-secure draws the
        # bit must be forced low in every cluster, including clusters above 55.
        if constraint.type == RV.AddressType.PHYSICAL and RV.AddressQualifiers.ADDRESS_SECURE not in qualifiers:
            secure_bit = 1 << 55
            if constraint.or_mask & secure_bit:
                raise AddrGenError("Non-secure physical constraint forces secure bit 55")
            mask &= ~secure_bit
            constraint.mask = mask

        # 1. Filter based on size and mask
        log.debug(f"all valid clusters: {self.all_valid_clusters}")
        clusters = self._possible_clusters(mask, bits, constraint.or_mask)
        clusters = clusters.intersection(self.all_valid_clusters)
        log.debug(f"allowed clusters: {clusters}")

        # Cluster 55 itself cannot contain an address with bit 55 clear.
        if constraint.type == RV.AddressType.PHYSICAL and RV.AddressQualifiers.ADDRESS_SECURE not in qualifiers:
            clusters.discard(55)

        # 2. Filter based on qualifiers
        q = set(qualifiers) - set(sub_clusters)
        if len(q) != 0:
            msg = "Below sub_clusters are not setup yet:\n"
            for i in q:
                msg += f"{i}, "
            msg += f"qualifiers: {qualifiers=} sub_clusters: {sub_clusters=}"
            raise AddrGenError(msg)

        for q in qualifiers:
            clusters = clusters.intersection(self.sub_clusters[q])
            if len(clusters) == 0:
                raise AddrGenError(f"No compatible clusters found for {constraint}")

        # 3b. If constraint has explicit start/end bounds, keep only clusters that overlap
        if constraint.is_bounded():
            bounds_lo, bounds_hi = constraint.bounds()
            bounds_start_cluster, bounds_end_cluster = self._address_to_cluster(bounds_lo, bounds_hi)
            bounds_clusters = SortedSet(range(bounds_start_cluster, bounds_end_cluster + 1))
            clusters = clusters.intersection(bounds_clusters)
            log.debug(f"filtered clusters with bounds [{bounds_lo:#x}, {bounds_hi:#x}]: {clusters}")

        log.debug(f"filtered clusters with qualifiers {qualifiers}: {clusters}")
        cluster_list2 = []
        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"clusters: {clusters}")
        for i in clusters:
            cluster = self.clusters[i]
            if log.isEnabledFor(logging.DEBUG):
                log.debug(f"find cluster: {cluster}")
                log.debug(f"cluster {i} available_memory: {cluster.available_memory:x}, size: {size:x}")
            if cluster.available_memory < size:
                log.debug(f"cluster {i} available_memory: {cluster.available_memory:x}, size: {size:x}")
                log.debug(f"not enough memory in cluster {i}")
                continue
            for qualifier in qualifiers:
                log.debug(f"cluster {i} qualifier_size: {cluster.qualifier_size[qualifier]:x}, size: {size:x}")
            if min(cluster.qualifier_size[qualifier] for qualifier in qualifiers) < size:
                log.debug(f"Not adding cluster {i} since qualifier size is less than requested size: {size:x}")
                continue
            log.debug(f"Adding cluster {i} to the list")
            cluster_list2.append(i)

        return cluster_list2

    def _address_to_cluster(self, *args: int) -> List[int]:
        """
        Provide the address(es) as a list.
        The method returns the clusters in which they belong
        """
        ret_list = []
        for i in args:
            if i == 0:
                ret_list.append(0)
            else:
                ret_list.append(i.bit_length() - 1)

        return ret_list

    def _possible_clusters(
        self,
        address_mask: int,
        address_bits: int,
        or_mask: int = 0,
    ) -> SortedSet:
        """
        Convert address mask to possible clusters
        """
        possible_clusters = SortedSet()
        log.debug(f"address_mask: {address_mask:x}, address_bits: {address_bits}")
        if or_mask >> address_bits:
            return possible_clusters
        forced_high_bit = min(or_mask.bit_length(), address_bits) - 1
        if forced_high_bit >= 0:
            possible_clusters.add(forced_high_bit)
        bitlen = min(address_mask.bit_length(), address_bits)
        for i in range(forced_high_bit + 1, bitlen):
            if common.bitn(address_mask, i):
                possible_clusters.add(i)

        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"possible_clusters: {possible_clusters}")
        return possible_clusters
