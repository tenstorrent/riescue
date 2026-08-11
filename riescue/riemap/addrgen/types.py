# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import riescue.lib.enums as RV
from riescue.riemap.addrgen.exceptions import AddrGenError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExcludedRegion:
    """A physical-address set excluded from automatic allocation.

    A region is either one contiguous half-open interval or a masked set
    ``address & mask == value``.  The masked form operates on page-aligned
    addresses in the 64-bit physical-address domain.
    """

    _interval: Optional[Tuple[int, int]] = None
    mask: Optional[int] = None
    value: int = 0

    def __post_init__(self) -> None:
        if (self._interval is None) == (self.mask is None):
            raise ValueError("ExcludedRegion requires exactly one of interval or mask")
        if self._interval is not None:
            start, end = self._interval
            if end < start:
                raise ValueError("ExcludedRegion interval end must not precede start")

    @classmethod
    def from_interval(cls, start: int, end: int) -> "ExcludedRegion":
        """Construct a contiguous half-open ``[start, end)`` exclusion."""
        return cls(_interval=(start, end))

    @classmethod
    def from_mask(cls, mask: int, value: int) -> "ExcludedRegion":
        """Construct the masked set ``address & mask == value & mask``."""
        return cls(mask=mask, value=value & mask)

    def interval(self) -> Optional[Tuple[int, int]]:
        """Return the contiguous half-open span, or ``None`` for a masked set."""
        return self._interval

    def overlaps(self, start: int, size: int) -> bool:
        """Return whether this set intersects ``[start, start + max(size, 1))``."""
        end = start + max(size, 1) - 1
        if self._interval is not None:
            region_start, region_end = self._interval
            return start < region_end and region_start <= end

        assert self.mask is not None
        if self.mask == 0:
            return True
        first = self._first_masked_match_at_or_above(start & ~0xFFF, self.mask, self.value)
        return first is not None and first <= end

    @staticmethod
    def _first_masked_match_at_or_above(addr: int, mask: int, value: int) -> Optional[int]:
        """Return the smallest page-aligned matching address at or above ``addr``."""
        if (addr & mask) == value:
            return addr
        for bit in range(12, 64):
            if (addr >> bit) & 1:
                continue
            if (mask >> bit) & 1 and not ((value >> bit) & 1):
                continue
            above = ~((1 << (bit + 1)) - 1)
            if (addr & mask & above) != (value & above):
                continue
            return (addr & above) | (1 << bit) | (value & ((1 << bit) - 1))
        return None

    def __str__(self) -> str:
        if self._interval is not None:
            start, end = self._interval
            return f"[0x{start:x}, 0x{end:x})"
        assert self.mask is not None
        return f"address & 0x{self.mask:x} == 0x{self.value:x}"


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
    start: Optional[int] = None  #: inclusive lower bound; None is unbounded
    end: Optional[int] = None  #: inclusive upper bound; None is unbounded
    dont_allocate: bool = False
    qualifiers: set[RV.AddressQualifiers] = field(default_factory=set)
    custom_region: Optional[str] = None  #: Named custom region; bounds/qualifier resolved by AddrGen.generate_address()
    pinned: bool = False  #: the mask/or_mask pin specific index bits; select from the reachable slot set instead of probe-and-mask
    exclude: Optional[Tuple[ExcludedRegion, ...]] = None  #: per-draw override of the builder-level exclusion set; None keeps the default, () disables exclusions

    def __str__(self) -> str:
        str = "Constraints: \n"

        str += f"\taddress_type: {self.type}\n"
        str += f"\taddress_bits: {self.bits}\n"
        str += f"\taddress_size: 0x{self.size:x}\n"
        str += f"\taddress_qual: {self.qualifiers}\n"
        str += f"\taddress_mask (and_mask): 0x{self.mask:016x}\n"
        str += f"\taddress_or_mask: 0x{self.or_mask:0x}\n"
        str += f"\taddress_start: {self.start if self.start is not None else 'unbounded'}\n"
        str += f"\taddress_end:  {self.end if self.end is not None else 'unbounded'}\n"
        str += f"\tallocate: {not self.dont_allocate}\n"

        return str

    def bounds(self) -> Tuple[int, int]:
        """The inclusive bounds with unbounded ends widened to the address width."""
        start = 0 if self.start is None else self.start
        end = (1 << self.bits) - 1 if self.end is None else self.end
        return start, end

    def is_bounded(self) -> bool:
        """Whether either end of the address window was given explicitly."""
        return self.start is not None or self.end is not None

    def validate_constraints(self) -> None:
        """
        Validate the constraints for the address generation.

        :raises AddrGenError: If the constraints are invalid.
        """
        if self.type == RV.AddressType.NONE:
            raise AddrGenError("Address type cannot be NONE")
        if self.size <= 0:
            raise AddrGenError(f"Address size must be positive, got {self.size}")
        if not 1 <= self.bits <= 64:
            raise AddrGenError(f"Address bits must be in 1..64, got {self.bits}")
        # mask == 0 is legal: it pins the draw to or_mask, which is how a fixed
        # address is expressed. A negative mask is a caller bug -- Python has no
        # width, so ~(page - 1) never truncates on its own.
        if self.mask < 0:
            raise AddrGenError(f"Address mask cannot be negative, got {self.mask}")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise AddrGenError("Address start cannot be greater than address end")

        if self.or_mask != 0:
            # AND mask should include or_mask, or_mask is used to set specific bits. A
            # *pinned* constraint clears colored index fields from the and_mask
            # (so the draw takes the OR'd value there); its or_mask legitimately sets index
            # bits above the (shrunken) and_mask top, bounded only by the address width.
            limit = self.bits if self.pinned else self.mask.bit_length()
            if self.or_mask.bit_length() > limit:
                raise AddrGenError("Address or_mask cannot be greater than address and_mask")


@dataclass
class ClusterFlags:
    dram_starts: bool = False
    dram_ends: bool = False
    mmio_starts: bool = False
    mmio_ends: bool = False

    def __repr__(self):
        return f"ClusterFlags(dram_starts={self.dram_starts}, dram_ends={self.dram_ends}, mmio_starts={self.mmio_starts}, mmio_ends={self.mmio_ends})"
