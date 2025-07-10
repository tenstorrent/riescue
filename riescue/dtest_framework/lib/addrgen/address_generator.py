# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from collections import defaultdict

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.lib.addrgen.exceptions import AddrGenError
from riescue.dtest_framework.lib.addrgen.address_space import AddressSpace
from riescue.dtest_framework.lib.addrgen.types import AddressConstraint
from riescue.dtest_framework.lib.memory import Memory

log = logging.getLogger(__name__)


class AddrGen:
    """
    Address Generator, Facade interface for generating and managing AddressSpace objects.
    Handles physical and linear address spaces, validates constraints, manages memory reservations

    `generate_address` is the main method to generate an address based on the constraints.
    `reserve_memory` can also be used to reserve memory for fixed addresses.

    :param rng: Random number generator
    :param mem: Memory object
    :param limit_indices: Whether to limit the number of addresses with the same index
    :param limit_way_predictor_multihit: Whether to limit the number of addresses with the same way predictor multihit
    """

    def __init__(self, rng: RandNum, mem: Memory, limit_indices: bool = False, limit_way_predictor_multihit: bool = False):
        self._mem = mem
        self._linear_addr_space = AddressSpace(rng, RV.AddressType.LINEAR)
        self._physical_addr_space = AddressSpace(rng, RV.AddressType.PHYSICAL)

        self.limit_indices = limit_indices
        self.limit_way_predictor_multihit = limit_way_predictor_multihit

        self.restricted_indices = defaultdict(int)  # ; tracks number of times address with same index was generated

        # Setting up DRAM, IO, Secure, and Reserved ranges
        for range in self._mem.dram_ranges:
            # FIXME: Is this check needed?
            # Needed until PMA and PMP config are fixed to support non-zero DRAM start
            # PMP should be fixed
            if range.start == 0:
                start = 0x80000000
            else:
                start = range.start
            log.info(f"Adding DRAM range: 0x{start:016x} - 0x{range.end:016x}")
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, start, range.end)

        for range in self._mem.io_ranges:
            log.info(f"Adding IO range: 0x{range.start:016x} - 0x{range.end:016x}")
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_MMIO, range.start, range.end)

        for range in self._mem.secure_ranges:
            log.info(f"Adding Secure range: 0x{range.start:016x} - 0x{range.end:016x}")
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_SECURE, range.start, range.end)

        for range in self._mem.reserved_ranges:
            # Reserve physical and linear address space
            self._physical_addr_space.reserve_memory(range.start, range.end - 1)
            self._linear_addr_space.reserve_memory(range.start, range.end - 1)
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_RESERVED, range.start, range.end)

        # Setting up Linear address space
        self._linear_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_LINEAR, 0, pow(2, 57) - 1)

    def generate_address(self, constraint: AddressConstraint) -> int:
        """Generate a random address based on provided constraints and feature manager settings.

        :param featmgr: Feature manager instance containing address generation settings
        :type featmgr: object
        :param constraint: AddressConstraint object specifying address requirements
        :type constraint: AddressConstraint
        :return: Generated address value
        :rtype: int
        :raises AddrGenError: If address generation fails or restrictions are violated
        """

        constraint.validate_constraints()
        log.debug(f"Generating address with constraints: {constraint}")
        log.debug(f"Constraints: {constraint}")

        # Generate address
        if constraint.type == RV.AddressType.MEMORY:
            address = self._generate_and_reserve_memory(constraint)
        else:
            addr_space = self._address_space(constraint.type)
            for addr_space in self._address_space(constraint.type):
                address = addr_space.generate_address(constraint)
                if address:
                    break

        # Check restrictions ; physical addresses are not restricted
        # If linear address, check restriction. If not restricted, address is generated and count is incremented in restricted_indices
        if constraint.type != RV.AddressType.PHYSICAL:
            if not self._check_linear_addr_restrictions(address):
                log.debug(f"unique address: {address:x}, index: {common.bits(address, 15, 6)}")
                if self.limit_indices:
                    self.restricted_indices[common.bits(address, 15, 6)] += 1
                    log.warning(f"restricted_indices: {self.restricted_indices}")
            else:
                raise AddrGenError(f"Restricted address limit reached: {address:x}, index: {self.restricted_indices[common.bits(address, 15, 6)]:x}")

        if address is None:
            raise AddrGenError(f"Failed to generate address, {address=}")

        log.debug(f"Generated address: {address:x}")
        return address

    def reserve_memory(self, address_type: RV.AddressType, start_address: int, size: int, interesting_address: bool = False):
        """
        reserve_memory() is used to allocate memory for fixed addresses into a given
        address space
        """
        end_address = start_address + size - 1
        addr_space = self._address_space(address_type)

        # Check linear address restrictions
        # TODO
        log.debug(f"Call to reserving memory: {start_address:x} - {end_address:x}")

        for _ in addr_space:
            _.reserve_memory(start_address, end_address, interesting_address)

    def _address_space(self, address_type: RV.AddressType) -> list[AddressSpace]:
        """
        Help method to decide which address space to call.
        Returns a list
        """
        addr_space = []
        if address_type == RV.AddressType.LINEAR:
            addr_space = [self._linear_addr_space]
        elif address_type == RV.AddressType.PHYSICAL:
            addr_space = [self._physical_addr_space]
        elif address_type == RV.AddressType.MEMORY:
            addr_space = [self._linear_addr_space]
            addr_space.append(self._physical_addr_space)
        else:
            raise TypeError(f"Unknown address type {address_type}")

        return addr_space

    def _generate_and_reserve_memory(self, constraint: AddressConstraint) -> int:
        """
        Find an address that is compatible with both linear and physical address spaces.
        Skips memory reservation if dont_allocate is True. Raises AddrGenError if address generation fails.

        Assumes that constraint.type is MEMORY

        :param constraint: AddressConstraint object specifying address requirements
        :type constraint: AddressConstraint
        :return: Generated address value
        :rtype: int
        :raises AddrGenError: If address generation fails or restrictions are violated
        """
        size = constraint.size
        try_times = 10
        for _ in range(try_times):
            address = self._linear_addr_space.generate_address(constraint=constraint)

            if not self._physical_addr_space.check_overlap(address, address + size) and not self._linear_addr_space.check_overlap(address, address + size):
                if constraint.dont_allocate:
                    pass
                else:
                    constraint.dont_allocate = False
                    self._linear_addr_space.reserve_memory(address, address + size)
                    self._physical_addr_space.reserve_memory(address, address + size)
                return address
        raise AddrGenError(f"Failed to generate physical addresswith constraints: {constraint}")

    def _check_linear_addr_restrictions(self, address: int) -> bool:
        """
        1. Currently, we can't generate more than 4 addresses which has same index
        2. We can't have a multi-hit in the way predictor
        """
        if self.limit_indices:
            # FIXME
            return False  # TODO: remove this line once we have a fix for the issue index restrictions
            # 1. Currently, we can't generate more than 4 addresses which has same index
            # Check if address index 15:6 exists in the restricted_indices dict
            if common.bits(address, 15, 6) in self.restricted_indices:
                # If so, make sure it is less than 4
                if self.restricted_indices[common.bits(address, 15, 6)] >= 4:
                    return True
        elif self.limit_way_predictor_multihit:
            # 2. We can't have a multi-hit in the way predictor
            # Check for following expression is correct
            # va[26:16] ^ va[37:27] ^ va[48:38] ^ {va[18:16], va[56], va[19], va[54], va[21], va[52:49]
            way_p_hash = (
                common.bits(address, 26, 16)
                ^ common.bits(address, 37, 27)
                ^ common.bits(address, 48, 38)
                ^ (
                    (common.bits(address, 18, 16) << 8)
                    | (common.bitn(address, 56) << 7)
                    | (common.bitn(address, 19) << 6)
                    | (common.bitn(address, 54) << 5)
                    | (common.bitn(address, 21) << 4)
                    | common.bits(address, 52, 49)
                )
            )
            if way_p_hash == 0:
                return True

        return False
