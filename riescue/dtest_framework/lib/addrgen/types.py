# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.addrgen.exceptions import AddrGenError

log = logging.getLogger(__name__)


@dataclass
class AddressConstraint:
    """
    Used to specify the constraints for the address generation.
    """

    type: RV.AddressType = RV.AddressType.NONE
    bits: int = 64
    size: int = 0x1000
    mask: int = 0xFFFFFFFFFFFFF000
    or_mask: int = 0  #: Optional OR mask for the address generation
    start: int = 0
    end: int = 0
    dont_allocate: bool = False
    qualifiers: set[RV.AddressQualifiers] = field(default_factory=set)

    def __str__(self) -> str:
        str = "Constraints: \n"

        str += f"\taddress_type: {self.type}\n"
        str += f"\taddress_bits: {self.bits}\n"
        str += f"\taddress_size: 0x{self.size:x}\n"
        str += f"\taddress_qual: {self.qualifiers}\n"
        str += f"\taddress_mask (and_mask): 0x{self.mask:016x}\n"
        str += f"\taddress_or_mask: 0x{self.or_mask:0x}\n"
        str += f"\taddress_start: {self.start}\n"
        str += f"\taddress_end:  {self.end}\n"
        str += f"\tallocate: {not self.dont_allocate}\n"

        return str

    def validate_constraints(self) -> None:
        """
        Validate the constraints for the address generation.

        :raises AddrGenError: If the constraints are invalid.
        """
        if self.type == RV.AddressType.NONE:
            raise AddrGenError("Address type cannot be NONE")
        if self.size == 0:
            raise AddrGenError("Address size cannot be 0")
        if self.mask == 0:
            raise AddrGenError("Address mask cannot be 0")
        if self.start > self.end:
            raise AddrGenError("Address start cannot be greater than address end")

        if self.or_mask != 0 and self.or_mask.bit_length() > self.mask.bit_length():
            # AND mask should include or_mask, or_mask is used to set specific bits.
            raise AddrGenError("Address or_mask cannot be greater than address and_mask")


@dataclass
class ClusterFlags:
    dram_starts: bool = False
    dram_ends: bool = False
    mmio_starts: bool = False
    mmio_ends: bool = False

    def __repr__(self):
        return f"ClusterFlags(dram_starts={self.dram_starts}, dram_ends={self.dram_ends}, mmio_starts={self.mmio_starts}, mmio_ends={self.mmio_ends})"
