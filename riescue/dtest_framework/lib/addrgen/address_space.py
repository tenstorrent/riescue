# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import copy
from collections import defaultdict
from typing import Dict, DefaultDict, List, MutableSet

from sortedcontainers import SortedSet

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.lib.addrgen.address_cluster import AddressCluster
from riescue.dtest_framework.lib.addrgen.exceptions import AddrGenError
from riescue.dtest_framework.lib.addrgen.types import AddressConstraint

log = logging.getLogger(__name__)


class AddressSpace:
    def __init__(self, rng: RandNum, address_type: RV.AddressType):
        self.rng = rng
        self.address_type = address_type
        self.total_allocated_address = 0
        self.all_valid_clusters: MutableSet[int] = SortedSet()
        self.clusters: Dict[int, AddressCluster] = dict()
        self.sub_clusters: DefaultDict[RV.AddressQualifiers, SortedSet] = defaultdict(SortedSet)

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
            cluster_instance.qualifier_size[qualifier] += size

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

    def reserve_memory(self, start: int, end: int, interesting_address: bool = False) -> None:
        """
        Interface to reserve memory
        """
        cl_start, cl_end = self._address_to_cluster(start, end)
        for i in range(cl_start, cl_end + 1):
            # 1) reserve partially in starting cluster
            # 2) reserve comletely in intermediate clusters
            # 3) reserve partially in end cluster

            # TODO: This is setting the initial address ranges for the cluster, without this it looks like clusters cant have addreses added
            # Should this be in the constructor?
            cluster = self.clusters[i]
            start_addr = cluster.start_address
            end_addr = cluster.end_address
            if i == cl_start:
                start_addr = start
            if i == cl_end:
                end_addr = end

            size = end_addr - start_addr + 1
            # TODO: This directly modifies the set in the cluster, should address cluster be doing this initialization?
            cluster.allocated_addresses.add((start_addr, end_addr))
            if interesting_address:
                cluster.interesting_addresses.add((start_addr, end_addr))
            log.debug(f"reserving memory: {start_addr:x} - {end_addr:x} in cluster {i}, size: {size:x}, available: {cluster.available_memory:x}")
            cluster.available_memory -= size
            log.debug(f"available memory after: {cluster.available_memory:x}")
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
            # FIXME: This needs better debug and error messages. Should be able to point to exact problem
            raise AddrGenError(f"No matching clusters found. Likely out of memory.\n{constraint}")

        self.rng.shuffle(_list)
        for i in _list:
            cluster = self.clusters[i]
            if log.isEnabledFor(logging.DEBUG):
                log.debug(f"Trying to generate address with cluster: {cluster.cluster_id}")
                log.debug(f"Pre allocation cluster details:\n {cluster}")
                log.debug(f"Trying to generate address with cluster: {cluster.cluster_id}")
                log.debug(f"Pre allocation cluster details:\n {cluster}")

            # 2. Step 2
            uclusters = cluster.find_ucluster(constraint)
            if len(uclusters) != 0:
                # 2a. Allocate address
                addr = cluster.allocate_address(constraint, uclusters)
                # log.debug(f'addrgen: addr: {addr:x}')
                if addr is None:
                    log.debug(f"Address generation failed for cluster {i}")
                    continue
                log.debug(f"allocated: 0x{addr:016x} - " f"0x{addr+constraint.size-1:016x} " f"in cluster {cluster.cluster_id}")
                self.total_allocated_address += 1
                if log.isEnabledFor(logging.DEBUG):
                    log.debug(f"Post allocation cluster details:\n {cluster}")

                return addr
            else:
                log.warning(f"Address generation failed for cluster {i}")
                continue

        raise AddrGenError(f"AddrGen could not pick a cluster for {constraint}")

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

        # 1. Filter based on size and mask
        log.debug(f"all valid clusters: {self.all_valid_clusters}")
        clusters = self._possible_clusters(mask, bits)
        clusters = clusters.intersection(self.all_valid_clusters)
        log.debug(f"allowed clusters: {clusters}")

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

    def _possible_clusters(self, address_mask, address_bits) -> SortedSet:
        """
        Convert address mask to possible clusters
        """
        possible_clusters = SortedSet()
        log.debug(f"address_mask: {address_mask:x}, address_bits: {address_bits}")
        bitlen = min(address_mask.bit_length(), address_bits)
        for i in range(bitlen):
            if common.bitn(address_mask, i):
                possible_clusters.add(i)

        if log.isEnabledFor(logging.DEBUG):
            log.debug(f"possible_clusters: {possible_clusters}")
        return possible_clusters
