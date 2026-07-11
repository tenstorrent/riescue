# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from dataclasses import dataclass, field, replace
import riescue.lib.common as common


@dataclass
class PmaInfo:
    pma_name: str = ""
    pma_valid: bool = False  # TODO: Evaluate need for this valid.
    pma_read: bool = True
    pma_write: bool = True
    pma_execute: bool = True
    pma_memory_type: str = "memory"  # 'io' | 'memory' | 'ch0' | 'ch1'
    pma_amo_type: str = "arithmetic"  # 'none' | 'logical' | 'swap' | 'arithmetic'
    pma_cacheability: str = "cacheable"  # 'cacheable' | 'noncacheable'
    pma_combining: str = "noncombining"  # 'combining' | 'noncombining'
    pma_routing_to: str = "coherent"  # 'coherent' | 'noncoherent'
    pma_address: int = 0
    pma_size: int = 0
    pma_mask: int = 0  # raw pmamask CSR value, bits [51:12]; 0 = match the whole NAPOT region
    pma_randomized: bool = False  # True for randomized decoy regions (never consolidated/merged)
    pma_mask_requested: bool = False  # pma_masked=1 in_pma request; mask forced in _apply_carveout_masks

    #: pmamask address-compare field covers physical address bits [51:12]
    PMAMASK_ADDR_BITS = 0x000F_FFFF_FFFF_F000

    _memory_type_map = {"memory": 0, "io": 1, "ch0": 2, "ch1": 3}

    _amo_type_map = {"none": 0, "logical": 1, "swap": 2, "arithmetic": 3}

    _cacheability_map = {"cacheable": 1, "noncacheable": 0}

    _combining_map = {"combining": 1, "noncombining": 0}

    _routing_to_map = {"coherent": 1, "noncoherent": 0}

    def __repr__(self) -> str:
        desc = ""
        if self.pma_name:
            desc += f"name={self.pma_name}, "
        desc += f"type={self.pma_memory_type}, "
        desc += f"base:0x{self.pma_address:x}, size:0x{self.pma_size:x}, "
        desc += f"routing_to={self.pma_routing_to}, "
        desc += f"combining={self.pma_combining}, "
        desc += f"cacheability={self.pma_cacheability}, "
        desc += f"amo_type={self.pma_amo_type}, "

        desc += "rwx="
        desc += "r" if self.pma_read else "-"
        desc += "w" if self.pma_write else "-"
        desc += "x" if self.pma_execute else "-"

        return desc

    def generate_pma_value(self, force: bool = False):
        # pmacfg CSR format looks like this
        # 2:0 - Permission, 0: read, 1: write, 2: execute
        # 4:3 - memory type, 0: memory, 1: io, 2: ch0, 3: ch1
        # 6:5 - amo type, 0: none, 1: logical, 2: swap, 3: arithmetic
        # 7 (memory) - cacheability, 1: cacheabl1, 0: noncacheable
        # 7 (io) - combining, 1: combining, 0: noncombining
        # 8 - routing to, 1: coherent, 0: noncoherent
        # 11:9 - reserved 0
        # 51:12 - address
        # 57:52 - reserved 0
        # 63:58 - size (if 0, then pma is invalid)
        # NOTE: legacy quirk skips encoding when pma_valid=True (hint/in_pma regions emit 0); force=True
        # bypasses it so PMA-randomization runs program real values without changing legacy output.
        pma_value = 0
        if force or not self.pma_valid:
            pma_value |= self.pma_read << 0
            pma_value |= self.pma_write << 1
            if self.pma_execute:
                pma_value |= self.pma_execute << 2
            pma_value |= self._memory_type_map[self.pma_memory_type] << 3
            pma_value |= self._amo_type_map[self.pma_amo_type] << 5
            if self.pma_memory_type == "memory":
                pma_value |= self._cacheability_map[self.pma_cacheability] << 7
            else:  # io
                pma_value |= self._combining_map[self.pma_combining] << 7
            pma_value |= self._routing_to_map[self.pma_routing_to] << 8
            pma_value |= (self.pma_address >> 12) << 12
            pma_value |= self._encoded_size_bits(force) << 58
            # print(f'pma_size: bits: {common.msb(self.pma_size)}, size: {self.pma_size:x}')

        return pma_value

    def _encoded_size_bits(self, force: bool) -> int:
        """Whisper decodes bits[63:58] as exact log2(size): force mode encodes pow2 sizes exactly.

        Legacy encoding (msb+1) doubles every region's whisper-effective size with an align-down
        base; kept for byte-identical legacy output and as ceil-log2 for non-pow2 sizes.
        """
        if force and self.pma_size > 0 and self.pma_size & (self.pma_size - 1) == 0:
            return common.msb(self.pma_size)
        return common.msb(self.pma_size) + 1

    def generate_pma_mask_value(self) -> int:
        return self.pma_mask & self.PMAMASK_ADDR_BITS

    def effective_match_mask(self) -> int:
        """Whisper processPmamaskChange: compare mask = (~pmamask & bits[51:12]) & bits above region size."""
        size_bits = common.msb(self.pma_size) if self.pma_size > 0 else 12
        size_mask = self.PMAMASK_ADDR_BITS & ~((1 << size_bits) - 1)
        return ~self.pma_mask & size_mask

    def matches_phys_range(self, start: int, size: int) -> bool:
        """Whisper regionMatches over [start, start+size): masked regions ignore the NAPOT interval."""
        end = start + max(size, 1) - 1
        if self.pma_mask == 0:
            return start < self.get_end_address() and self.pma_address <= end
        mask = self.effective_match_mask()
        if mask == 0:
            return True  # degenerate mask matches everything; randomizer forbids this by construction
        tag = self.pma_address & mask
        first = self._first_masked_match_at_or_above(start & ~0xFFF, mask, tag)
        return first is not None and first <= end

    @staticmethod
    def _first_masked_match_at_or_above(addr: int, mask: int, tag: int) -> int | None:
        """
        Smallest page-aligned A >= addr with A & mask == tag, or None (O(64), no page walking).

        Beyond a direct hit, the minimal match agrees with addr above some bit p, has 1 at p where
        addr has 0 (so A > addr), and is minimal below (free bits 0, compare bits = tag); the lowest
        legal p gives the smallest such A.
        """
        if (addr & mask) == tag:
            return addr
        for p in range(12, 64):
            if (addr >> p) & 1:
                continue  # A must gain a 1 at p where addr has 0
            if (mask >> p) & 1 and not ((tag >> p) & 1):
                continue  # compare bit forced to 0 here
            above = ~((1 << (p + 1)) - 1)
            if (addr & mask & above) != (tag & above):
                continue  # addr's prefix above p conflicts with the tag
            return (addr & above) | (1 << p) | (tag & ((1 << p) - 1))
        return None

    def attrib_matches(self, other: PmaInfo) -> bool:
        if self.pma_memory_type != other.pma_memory_type:
            return False
        if self.pma_amo_type != other.pma_amo_type:
            return False
        if self.pma_cacheability != other.pma_cacheability:
            return False
        if self.pma_combining != other.pma_combining:
            return False
        if self.pma_routing_to != other.pma_routing_to:
            return False
        if self.pma_read != other.pma_read:
            return False
        if self.pma_write != other.pma_write:
            return False
        if self.pma_execute != other.pma_execute:
            return False
        return True

    def get_end_address(self) -> int:
        return self.pma_address + self.pma_size

    def contains_address(self, address: int) -> bool:
        """Check if an address falls within this PMA region."""
        return self.pma_address <= address < self.get_end_address()

    def contains(self, other: PmaInfo) -> bool:
        return self.pma_address <= other.pma_address and self.get_end_address() >= other.get_end_address()

    def is_io(self) -> bool:
        return self.pma_memory_type == "io"


class PmaRegion:
    """
    Incrementally build PMA configuration and generate CSR values

    :param bool pad_napot: if True, then the region will be padded to the pervious region or 0

    Usage:
    .. code-block:: python

        builder = PmaRegion()
        builder.add_region(0x80000000, 0x1000, "memory")

        for reg in builder.consolidated_entries():
            print(f"pmacfg{reg.cfg.name}: 0x{reg.cfg.value:x}")

    """

    def __init__(self) -> None:
        self._entries: list[PmaInfo] = []

    def add_region(self, base: int, size: int, type: str, **kwargs) -> None:
        params = {
            "pma_address": base,
            "pma_size": size,
            "pma_memory_type": type,
            "pma_read": kwargs.get("read", True),
            "pma_write": kwargs.get("write", True),
            "pma_execute": kwargs.get("execute", True),
            "pma_routing_to": kwargs.get("routing_to", "coherent"),
            "pma_combining": kwargs.get("combining", "noncombining"),
            "pma_cacheability": kwargs.get("cacheability", "cacheable"),
            "pma_amo_type": kwargs.get("amo_type", "arithmetic"),
        }
        self.add_entry(PmaInfo(**params))

    def add_entry(self, pma_info: PmaInfo) -> None:
        self._entries.append(pma_info)

    def consolidated_entries(self, merge_named: bool = True) -> list[PmaInfo]:
        if not self._entries:
            return []
        c_entries = []
        # First sort by address
        # If attributes match, then we can attempt consolidating regions
        # For memory the regions must be adjacent.
        # For IO we will add uninterrupted IO regions
        # merge_named=False keeps every named pma_* carve-out intact (gap-merging io carve-outs would
        # produce giant regions whose NAPOT base aligns below the test); legacy callers keep merging.
        self._entries.sort(key=lambda entry: entry.pma_address)
        c_entries.append(self._entries[0])
        for entry in self._entries[1:]:
            if not merge_named and (entry.pma_name.startswith("pma_") or c_entries[-1].pma_name.startswith("pma_")):
                c_entries.append(entry)
            elif not c_entries[-1].attrib_matches(entry):
                # attributes do not match, so we add a new region
                c_entries.append(entry)
            elif c_entries[-1].contains(entry):
                # last region already includes this region
                # However, preserve named hint regions (pma_*) even if contained
                if entry.pma_name and entry.pma_name.startswith("pma_"):
                    c_entries.append(entry)
                else:
                    pass
            elif c_entries[-1].get_end_address() == entry.pma_address:
                # last region is adjacent to this region (merge into a copy; stored entries stay intact)
                c_entries[-1] = replace(c_entries[-1], pma_size=c_entries[-1].pma_size + entry.pma_size)
            elif c_entries[-1].is_io():
                # we merge io regions even if they are not directly adjacent
                c_entries[-1] = replace(c_entries[-1], pma_size=c_entries[-1].pma_size + (entry.pma_address - c_entries[-1].get_end_address()) + entry.pma_size)
            else:
                c_entries.append(entry)
        return c_entries

    def find_region_for_address(self, address: int) -> PmaInfo | None:
        """Find the PMA region that contains the given address.

        :param address: Address to check
        :return: PmaInfo if address is within a region, None otherwise
        """
        # Check consolidated entries (final PMA regions)
        for region in self.consolidated_entries():
            if region.contains_address(address):
                return region
        return None
