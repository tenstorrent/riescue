# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from dataclasses import dataclass, field


from riescue.lib.enums import Xlen, RiscvPmpAddressMatchingModes

"""
Package containing PMP related classes. Used to generate ``pmpcfg`` and ``pmpaddr`` CSRs.
"""

log = logging.getLogger(__name__)


@dataclass
class PmpCfg:
    """
    Model of a pmpcfg register.

    :param Xlen xlen: the XLEN of the PMP configuration, used to determine number of entries
    """

    name: str
    value: int
    xlen: Xlen = Xlen.XLEN64

    def __post_init__(self):
        self.num_entries = self.xlen // 8


@dataclass
class PmpAddr:
    """
    Model of a pmpaddr register.

    :param int address: the address of the PMP entry
    :param Xlen xlen:  XLEN used to validate address bits
    """

    name: str
    address: int
    xlen: Xlen = Xlen.XLEN64
    addr_matching: RiscvPmpAddressMatchingModes = RiscvPmpAddressMatchingModes.OFF

    def __str__(self):
        return f"{self.name}: 0x{self.address:x} (A={self.addr_matching.name}, )"

    def range(self) -> tuple[int, int]:
        "Return range for debug and testing"
        if self.addr_matching == RiscvPmpAddressMatchingModes.NAPOT:
            mask = self.address
            ones = 0
            while mask & 1:
                ones += 1
                mask >>= 1
            size = 1 << (ones + 3)  # 4-byte granule × 2^{ones}
            base = (self.address & ~((1 << ones) - 1)) << 2
            return base, size
        elif self.addr_matching == RiscvPmpAddressMatchingModes.NA4:
            base = (self.address & ~0b11) * PmpEntry.PMP_GRANULE
            return base, 4
        elif self.addr_matching == RiscvPmpAddressMatchingModes.OFF:
            return 0, 0
        elif self.addr_matching == RiscvPmpAddressMatchingModes.TOR:
            raise ValueError("TOR mode not supported for PmpAddr.range()")
        else:
            raise ValueError(f"unsupported mode {self.addr_matching}")


@dataclass
class PmpRegisters:
    """
    Holds the concrete CSR values for a single PMP entry.

    :param PmpCfg cfg: the PMP configuration
    :param list[PmpAddr] addr: the PMP address registers
    """

    cfg: PmpCfg
    addr: list[PmpAddr]


@dataclass
class PmpEntry:
    """
    A single logical PMP entry. Contains logic to generate pmpaddr and pmpcfg values.

    Can be expanded later to optimize PMP region overlaps.

    :param int base: region start address
    :param int size: region size in bytes, must be a power of two ≥ PMP_GRANULE
    :param str perms: combination of ``"r"``, ``"w"``, ``"x"``
    :param PmpAddrMatching addr_matching: the address matching mode
    """

    PMP_GRANULE = 4  # bytes

    base: int
    size: int
    perms: str
    addr_matching: RiscvPmpAddressMatchingModes = RiscvPmpAddressMatchingModes.OFF

    def to_pmpaddr(self, idx: int) -> PmpAddr:
        """
        generate a PmpAddr object from this entry

        :param int idx: the index of the PMP entry
        """
        return PmpAddr(
            name=f"pmpaddr{idx}",
            address=self.encode_addr(),
            xlen=Xlen.XLEN64,
            addr_matching=self.addr_matching,
        )

    def encode_addr(self) -> int:
        """
        Encode *pmpaddrN* for this entry.

        :returns: encoded address field
        :rtype: int
        :raises ValueError: if ``base``, ``size`` or ``addr_matching`` are inconsistent
        """
        gran = self.PMP_GRANULE

        if self.addr_matching == RiscvPmpAddressMatchingModes.OFF:
            return 0
        elif self.addr_matching == RiscvPmpAddressMatchingModes.TOR:
            return (self.base + self.size) // gran  # field holds (top-of-range >> 2)
        elif self.addr_matching == RiscvPmpAddressMatchingModes.NA4:
            if self.size != 4:
                raise ValueError("NA4 requires size == 4 bytes")
            return (self.base // gran) | 0b01
        elif self.addr_matching == RiscvPmpAddressMatchingModes.NAPOT:
            if self.size < 8 or (self.size & (self.size - 1)):
                raise ValueError("NAPOT size must be power-of-two ≥ 8 bytes")
            size_gran = self.size // gran
            mask = (size_gran - 1) >> 1
            return (self.base // gran) | mask
        else:
            raise ValueError(f"unsupported mode {self.addr_matching}")

    def to_pmpcfg(self, idx: int) -> PmpCfg:
        """
        generate a PmpCfg object from this entry

        :param int idx: the index of the PMP entry
        """
        return PmpCfg(
            name=f"pmpcfg{idx}",
            value=self.encode_cfg(),
            xlen=Xlen.XLEN64,
        )

    def encode_cfg(self) -> int:
        """
        Encode the entry into the eight-bit chunk written to *pmpcfgN*.

        :returns: encoded cfg byte
        :rtype: int
        """
        perm_bits = (1 if "r" in self.perms else 0) | (1 if "w" in self.perms else 0) << 1 | (1 if "x" in self.perms else 0) << 2
        return perm_bits | self.addr_matching.encode()


class PmpRegion:
    """
    Incrementally build PMP configuration and generate CSR values

    :param bool pad_napot: if True, then the region will be padded to the pervious region or 0

    Usage:
    .. code-block:: python

        builder = PmpRegion()
        builder.add_region(0x80000000, 0x1000, "rx")

        for reg in builder.encode():
            print(f"pmpcfg{reg.cfg.name}: 0x{reg.cfg.value:x}")
            for addr in reg.addr:
                print(f"pmpaddr{addr.name}: 0x{addr.address:x}")

    """

    PMP_NAPOT_GRANULE = 8  # smallest NAPOT region size
    NUM_ENTRIES = 64

    def __init__(self, pad_napot: bool = True, xlen: Xlen = Xlen.XLEN64) -> None:
        self.pad_napot = pad_napot
        self._entries: list[PmpEntry] = []
        self.xlen = xlen

    def is_napot(self, base: int, size: int) -> bool:
        """
        Return ``True`` if the region [base, base + size) can be encoded as a
        NAPOT PMP entry.

        A NAPOT region must satisfy two constraints:

            -  ``size`` is a power of two not smaller than the hardware granule
            - ``base`` is naturally aligned to that ``size``

        :param int base: region start address
        :param int size: region length in bytes
        """
        if size < self.PMP_NAPOT_GRANULE:
            return False
        if size & (size - 1):  # not a power of two
            return False
        return (base & (size - 1)) == 0  # naturally aligned

    def align_napot(self, base: int, size: int) -> list[tuple[int, int]]:
        """
        Strategies for aligning a region to a NAPOT region
        if ``pad_napot`` is set, then the region will be padded to the pervious region or 0
        otherwise will attempt to cover the region NAPOT regions. This can raise an error if all regions cannot be covered by NAPOT regions.

        """
        if self.pad_napot:
            return self.napot_pad(base, size)
        else:
            return self.napot_cover(base, size)

    def napot_pad(self, base: int, size: int) -> list[tuple[int, int]]:
        # expand down to the previous aligned boundary and up to the next
        # power-of-two so that the whole interval fits into one NAPOT entry
        cover_size = 1 << (base + size - 1).bit_length()
        if cover_size < self.PMP_NAPOT_GRANULE:
            raise ValueError("cover_size is less than PMP_NAPOT_GRANULE")
        cover_base = base & ~(cover_size - 1)
        return [(cover_base, cover_size)]

    @staticmethod
    def napot_cover(base: int, size: int) -> list[tuple[int, int]]:
        """
        Return a list of (base, size) NAPOT regions that exactly cover
        ``[base, base + size)`` with the fewest entries.

        Breaks down the range into the largest power-of-two chunks that are naturally aligned.
        Throws an error if any chunks have a size less than PMP_NAPOT_GRANULE.

        :param int base: range start
        :param int size: range length in bytes
        :returns: list of (addr, length) pairs
        :rtype: list[tuple[int, int]]
        :raises ValueError: if any of the chunks are smaller than PMP_NAPOT_GRANULE
        """
        out: list[tuple[int, int]] = []
        addr, rem = base, size
        while rem:
            align = addr & -addr  # largest power-of-two divisor of addr
            span = 1 << (rem.bit_length() - 1)  # largest power-of-two ≤ rem
            if span <= align or align == 0:
                chunk = span
            else:
                chunk = align
            if chunk < PmpRegion.PMP_NAPOT_GRANULE:
                raise ValueError(f"chunk size {chunk} is less than PMP_NAPOT_GRANULE {PmpRegion.PMP_NAPOT_GRANULE}")
            out.append((addr, chunk))
            addr += chunk
            rem -= chunk
        return out

    def add_region(self, base: int, size: int, perms: str = "rwx") -> PmpRegion:
        """
        Append a region. Adds regions to PmpEntry list.

        .. warning::
            This does not optimize regions. Overlaps are not merged, they will not be merged. Only adds to list in order

        :returns: self to allow chaining
        """
        log.info(f"Adding region {base:x} {size:x} {perms}")
        if len(self._entries) >= self.NUM_ENTRIES:
            raise ValueError("no PMP slots left. Consolidate regions before adding more")

        if not self.is_napot(base, size):
            aligned_napot = self.align_napot(base, size)
            log.debug(f"Split region into {len(aligned_napot)} NAPOT regions")

            for napot_start, napot_size in aligned_napot:
                log.debug(f"Adding NAPOT region {napot_start:x} {napot_size:x} {perms}")
                self._entries.append(PmpEntry(napot_start, napot_size, perms, addr_matching=RiscvPmpAddressMatchingModes.NAPOT))

        else:
            self._entries.append(PmpEntry(base, size, perms, addr_matching=RiscvPmpAddressMatchingModes.NAPOT))

        return self

    def encode(self) -> list[PmpRegisters]:
        """
        Produce the CSR register values.

        :returns: concrete register values
        :rtype: PmpRegisters
        """
        registers: list[PmpRegisters] = []

        max_registers_per_cfg = self.xlen // 8
        addr_registers: list[PmpAddr] = []
        cfg_value = 0
        cfg_idx = None

        # generate all cfg and addr registe
        for cfg_idx, entry in enumerate(self._entries):
            cfg_value = (cfg_value << 8) | entry.encode_cfg()
            addr_registers.append(entry.to_pmpaddr(cfg_idx))

            if len(addr_registers) == max_registers_per_cfg:
                next_cfg_idx = cfg_idx // max_registers_per_cfg
                if self.xlen == Xlen.XLEN64:
                    next_cfg_idx *= 2
                cfg = PmpCfg(
                    name=f"pmpcfg{next_cfg_idx}",
                    value=cfg_value,
                    xlen=self.xlen,
                )
                registers.append(PmpRegisters(cfg=cfg, addr=addr_registers))
                addr_registers = []
                cfg_value = 0

        # adding last cfg if any
        if cfg_idx is None:
            raise ValueError("no PMP entries but Pmp requsted")
        if addr_registers:
            next_cfg_idx = cfg_idx // max_registers_per_cfg
            if self.xlen == Xlen.XLEN64:
                next_cfg_idx *= 2
            cfg = PmpCfg(
                name=f"pmpcfg{next_cfg_idx}",
                value=cfg_value,
                xlen=self.xlen,
            )
            registers.append(PmpRegisters(cfg=cfg, addr=addr_registers))
        return registers
