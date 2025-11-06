# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from dataclasses import dataclass, field
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

    def generate_pma_value(self):
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
        pma_value = 0
        if not self.pma_valid:
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
            pma_value |= (common.msb(self.pma_size) + 1) << 58
            # print(f'pma_size: bits: {common.msb(self.pma_size)}, size: {self.pma_size:x}')

        return pma_value

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

    def consolidated_entries(self) -> list[PmaInfo]:
        c_entries = []
        # First sort by address
        # If attributes match, then we can attempt consolidating regions
        # For memory the regions must be adjacent.
        # For IO we will add uninterrupted IO regions
        self._entries.sort(key=lambda entry: entry.pma_address)
        c_entries.append(self._entries[0])
        for entry in self._entries[1:]:
            if not c_entries[-1].attrib_matches(entry):
                # attributes do not match, so we add a new region
                c_entries.append(entry)
            elif c_entries[-1].contains(entry):
                # last region already includes this region
                pass
            elif c_entries[-1].get_end_address() == entry.pma_address:
                # last region is adjacent to this region
                c_entries[-1].pma_size += entry.pma_size
            elif c_entries[-1].is_io():
                # we merge io regions even if they are not directly adjacent
                c_entries[-1].pma_size += (entry.pma_address - c_entries[-1].get_end_address()) + entry.pma_size
            else:
                c_entries.append(entry)
        return c_entries
