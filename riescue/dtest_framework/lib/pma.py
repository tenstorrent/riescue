# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
from typing import Optional
import logging
from dataclasses import dataclass, field, replace
import riescue.lib.common as common

log = logging.getLogger(__name__)

#: pmacfg bit 8, the routing/coherency bit (1 coherent, 0 noncoherent). Live only on a legacy_pma target
PMACFG_ROUTING_BIT = 1 << 8

#: Default routing when a legacy_pma region does not name one; matches the historical pma_routing_to="coherent"
DEFAULT_ROUTING_TO = "coherent"

#: pmacfg[6:5] value that cacheable main memory is pinned to (AMOCASQ + RsrvEventual).
CACHEABLE_MEMORY_AMO_TYPE = "arithmetic"

#: Error text shared by every surface that can be handed an explicit routing request off a legacy_pma target
NO_ROUTING_ERROR = "pmacfg bit 8 (routing/coherency) is reserved read-only-zero unless the target sets legacy_pma; drop the routing request or set legacy_pma"

#: Advice appended to every NC/IO amo clamp warning; the knob is cpuconfig-only
NCIO_AMO_CLAMP_ADVICE = "set mmap.pma.allow_amos_in_pma_ncio to keep it"

# All latched once per FeatMgr / Voyager2 build so a rerun cannot inherit the last target
_legacy_pma = False
_legacy_pbmt = True
_allow_amos_in_pma_ncio = False

# Warnings are deduped by text: one PmaSpec default can reach many pages
_ncio_amo_warned: set[str] = set()


def set_legacy_pma(enabled: bool) -> None:
    "Latch the cpuconfig mmap.pma.legacy_pma field (or its --legacy_pma/--no_legacy_pma override)"
    global _legacy_pma
    _legacy_pma = bool(enabled)


def legacy_pma() -> bool:
    "True on a pre-Babylon target: atomics only on cacheable main memory, and pmacfg bit 8 carries routing"
    return _legacy_pma


def set_legacy_pbmt(enabled: bool) -> None:
    "Latch the cpuconfig mmap.pma.legacy_pbmt field (or its --legacy_pbmt/--no_legacy_pbmt override)"
    global _legacy_pbmt
    _legacy_pbmt = bool(enabled)


def legacy_pbmt() -> bool:
    "True when a PBMT=NC/IO leaf revokes AMO and LR/SC on that page whatever the underlying PMA grants"
    return _legacy_pbmt


def set_allow_amos_in_pma_ncio(enabled: bool) -> None:
    "Latch the cpuconfig mmap.pma.allow_amos_in_pma_ncio field; there is no CLI override for it"
    global _allow_amos_in_pma_ncio
    _allow_amos_in_pma_ncio = bool(enabled)
    _ncio_amo_warned.clear()


def allow_amos_in_pma_ncio() -> bool:
    "True when NC/IO may carry a non-AMONone pmacfg[6:5]; mirrors whisper's allow_amo_in_*"
    return _allow_amos_in_pma_ncio


def no_routing_on_pma() -> bool:
    "True when pmacfg bit 8 is reserved read-only-zero and routing may not be requested anywhere"
    return not legacy_pma()


def ncio_amo_clamped(memory_type: str, cacheability: Optional[str]) -> bool:
    "True when this shape must program pmacfg[6:5]=0b00: NC/IO with the ncio knob off"
    if allow_amos_in_pma_ncio():
        return False
    return not (memory_type == "memory" and cacheability == "cacheable")


def warn_ncio_amo_clamp(context: str, memory_type: str, cacheability: Optional[str], amo_type: str, rsrv: Optional[str] = None) -> None:
    "Warn once per distinct message that an explicit NC/IO atomicity request was forced to AMONone"
    if not ncio_amo_clamped(memory_type, cacheability):
        return
    if _amo_type_is_none(amo_type) and rsrv in (None, "none"):
        return  # already AMONone, nothing was taken away
    shape = memory_type if memory_type != "memory" else f"{cacheability} memory"
    rsrv_desc = "" if rsrv is None else f", rsrv={rsrv!r}"
    message = f"{context}: {shape} requested amo_type={amo_type!r}{rsrv_desc}; programming AMONone (pmacfg[6:5]=0b00) - {NCIO_AMO_CLAMP_ADVICE}"
    if message in _ncio_amo_warned:
        return
    _ncio_amo_warned.add(message)
    log.warning(message)


def _amo_type_is_none(amo_type: str) -> bool:
    "PmaInfo._amo_type_map is defined below this point, so resolve the name lazily"
    return PmaInfo._amo_type_map.get(amo_type, 0) == 0


@dataclass
class PmaInfo:
    pma_name: str = ""
    pma_valid: bool = False  # TODO: Evaluate need for this valid.
    pma_read: bool = True
    pma_write: bool = True
    pma_execute: bool = True
    pma_memory_type: str = "memory"  # 'io' | 'memory' | 'ch0' | 'ch1'
    pma_amo_type: str = "arithmetic"  # Legacy pmacfg[6:5] hierarchy; with pma_rsrv, bit 5 independently selects AMO
    pma_rsrv: Optional[str] = None  # None = legacy encoding; otherwise 'none' | 'non_eventual' | 'eventual'
    pma_cacheability: str = "cacheable"  # 'cacheable' | 'noncacheable'
    pma_combining: str = "noncombining"  # 'combining' | 'noncombining'
    pma_routing_to: Optional[str] = None  # pmacfg bit 8: 'coherent' | 'noncoherent'; None = unrequested, defaults to coherent
    pma_address: int = 0
    pma_size: int = 0
    pma_mask: int = 0  # raw pmamask CSR value, bits [51:12]; 0 = match the whole NAPOT region
    pma_randomized: bool = False  # True for randomized decoy regions (never consolidated/merged)
    pma_mask_requested: bool = False  # pma_masked=1 in_pma request; mask forced in _apply_carveout_masks

    #: pmamask address-compare field covers physical address bits [51:12]
    PMAMASK_ADDR_BITS = 0x000F_FFFF_FFFF_F000

    _memory_type_map = {"memory": 0, "io": 1, "ch0": 2, "ch1": 3}

    #: Legacy pmacfg[6:5] AMO hierarchy. Explicit pma_rsrv switches NC/IO to Babylon bit packing.
    #: amocasq is an input-only alias of arithmetic (same 0b11); reverse-decode keeps the canonical name
    _amo_type_map = {"none": 0, "swap": 1, "logical": 2, "arithmetic": 3, "amocasq": 3}
    _rsrv_map = {"none": 0, "non_eventual": 1, "eventual": 1}

    _cacheability_map = {"cacheable": 1, "noncacheable": 0}

    _combining_map = {"combining": 1, "noncombining": 0}

    _routing_to_map = {"coherent": 1, "noncoherent": 0}

    def __post_init__(self):
        "Cacheable main memory is the only shape whose amo type is pinned (0b11); every other region may use 0b00-0b11"
        if self.pma_amo_type not in self._amo_type_map:
            raise ValueError(f"Invalid pma_amo_type: {self.pma_amo_type!r}. Must be one of {sorted(self._amo_type_map)}")
        if self.pma_rsrv is not None and self.pma_rsrv not in self._rsrv_map:
            raise ValueError(f"Invalid pma_rsrv: {self.pma_rsrv!r}. Must be one of {sorted(self._rsrv_map)}")
        if self.pma_rsrv is not None and self.pma_amo_type not in ("none", "arithmetic", "amocasq"):
            raise ValueError(f"Illegal PMA: explicit pma_rsrv uses Babylon AMONone/AMOCASQ packing; got pma_amo_type={self.pma_amo_type!r}")
        if self.is_cacheable_memory() and self._amo_type_map[self.pma_amo_type] != self._amo_type_map[CACHEABLE_MEMORY_AMO_TYPE]:
            raise ValueError(f"Illegal PMA: cacheable memory requires pma_amo_type={CACHEABLE_MEMORY_AMO_TYPE!r} or 'amocasq' (pmacfg[6:5]=0b11), got {self.pma_amo_type!r}")
        if self.is_cacheable_memory() and self.pma_rsrv not in (None, "eventual"):
            raise ValueError(f"Illegal PMA: cacheable memory requires pma_rsrv='eventual', got {self.pma_rsrv!r}")
        if not self.is_cacheable_memory() and self.pma_rsrv == "eventual":
            raise ValueError("Illegal PMA: pma_rsrv='eventual' requires cacheable memory")
        if self.pma_routing_to is not None:
            if no_routing_on_pma():
                raise ValueError(f"{NO_ROUTING_ERROR} (got pma_routing_to={self.pma_routing_to!r} on {self.pma_name or 'unnamed region'})")
            if self.pma_routing_to not in self._routing_to_map:
                raise ValueError(f"Invalid pma_routing_to: {self.pma_routing_to!r}. Must be one of {sorted(self._routing_to_map)}")

    @property
    def effective_routing_to(self) -> str:
        "The routing an unrequested region falls back to; only meaningful while pmacfg bit 8 is live"
        return self.pma_routing_to if self.pma_routing_to is not None else DEFAULT_ROUTING_TO

    def is_cacheable_memory(self) -> bool:
        "True for main memory (pmacfg[4:3]=0) that is also cacheable (pmacfg bit 7=1) - the shape that pins amo type to 0b11"
        return self.pma_memory_type == "memory" and self.pma_cacheability == "cacheable"

    def __repr__(self) -> str:
        desc = ""
        if self.pma_name:
            desc += f"name={self.pma_name}, "
        desc += f"type={self.pma_memory_type}, "
        desc += f"base:0x{self.pma_address:x}, size:0x{self.pma_size:x}, "
        if not no_routing_on_pma():
            desc += f"routing_to={self.effective_routing_to}, "
        desc += f"combining={self.pma_combining}, "
        desc += f"cacheability={self.pma_cacheability}, "
        desc += f"amo_type={self.effective_amo_type}, "
        desc += f"rsrv={self.effective_rsrv}, "

        desc += "rwx="
        desc += "r" if self.pma_read else "-"
        desc += "w" if self.pma_write else "-"
        desc += "x" if self.pma_execute else "-"

        return desc

    def generate_pma_value(self, force: bool = False):
        # pmacfg CSR format looks like this
        # 2:0 - Permission, 0: read, 1: write, 2: execute
        # 4:3 - memory type, 0: memory, 1: io, 2: ch0, 3: ch1
        # 6:5 - legacy AMO hierarchy, or Babylon Rsrv×AMO when pma_rsrv is explicit.
        # 7 (memory) - cacheability, 1: cacheable, 0: noncacheable
        # 7 (io) - combining, 1: combining, 0: noncombining
        # 8 - routing to, 1: coherent, 0: noncoherent; reserved read-only-zero unless the target sets legacy_pma
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
            pma_value |= self.atomicity_bits << 5
            if self.pma_memory_type == "memory":
                pma_value |= self._cacheability_map[self.pma_cacheability] << 7
            else:  # io
                pma_value |= self._combining_map[self.pma_combining] << 7
            if not no_routing_on_pma():
                pma_value |= self._routing_to_map[self.effective_routing_to] << 8
            pma_value |= (self.pma_address >> 12) << 12
            pma_value |= self._encoded_size_bits(force) << 58
            # print(f'pma_size: bits: {common.msb(self.pma_size)}, size: {self.pma_size:x}')

        return pma_value

    @property
    def amo_clamped(self) -> bool:
        """True when this region is forced to AMONone: NC/IO with allow_amos_in_pma_ncio off."""
        return ncio_amo_clamped(self.pma_memory_type, self.pma_cacheability)

    @property
    def effective_amo_type(self) -> str:
        """The amo type actually programmed: the request, or AMONone where the clamp applies."""
        return "none" if self.amo_clamped else self.pma_amo_type

    @property
    def atomicity_bits(self) -> int:
        """Return pmacfg[6:5], preserving legacy AMO encoding unless pma_rsrv is explicit."""
        # The clamp zeroes bit 6 (Rsrv) too: AMONone is the whole field, as whisper enforces
        if self.amo_clamped:
            return 0
        amo_bits = self._amo_type_map[self.pma_amo_type]
        if self.pma_rsrv is None or self.is_cacheable_memory():
            return amo_bits
        if self.pma_amo_type not in ("none", "arithmetic", "amocasq"):
            raise ValueError(f"Explicit pma_rsrv cannot be combined with pma_amo_type={self.pma_amo_type!r}")
        return (self._rsrv_map[self.pma_rsrv] << 1) | (amo_bits & 1)

    @property
    def effective_rsrv(self) -> str:
        """Return the LR/SC level represented by pmacfg bit 6."""
        if self.is_cacheable_memory():
            return "eventual"
        if self.amo_clamped:
            return "none"
        if self.pma_rsrv is not None:
            return self.pma_rsrv
        return "non_eventual" if self._amo_type_map[self.pma_amo_type] & 2 else "none"

    def _encoded_size_bits(self, force: bool) -> int:
        """Whisper decodes bits[63:58] as exact log2(size): force mode encodes pow2 sizes exactly.

        The msb+1 fallback doubles a region's whisper-effective size with an align-down base, so it
        now serves only as ceil-log2 for non-pow2 sizes; every emission site force-encodes.
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

    def excluded_region(self):
        """Translate this PMA into a generic RieMap :class:`~riescue.riemap.addrgen.types.ExcludedRegion`."""
        from riescue.riemap.addrgen.types import ExcludedRegion

        if self.pma_mask == 0:
            return ExcludedRegion.from_interval(self.pma_address, self.get_end_address())
        return ExcludedRegion.from_mask(self.effective_match_mask(), self.pma_address)

    def matches_phys_range(self, start: int, size: int) -> bool:
        """Whisper regionMatches over [start, start+size): masked regions ignore the NAPOT interval."""
        return self.excluded_region().overlaps(start, size)

    def attrib_matches(self, other: PmaInfo) -> bool:
        if self.pma_memory_type != other.pma_memory_type:
            return False
        if self.atomicity_bits != other.atomicity_bits:
            return False
        if self.pma_cacheability != other.pma_cacheability:
            return False
        if self.pma_combining != other.pma_combining:
            return False
        if not no_routing_on_pma() and self.effective_routing_to != other.effective_routing_to:
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
            "pma_routing_to": kwargs.get("routing_to"),
            "pma_combining": kwargs.get("combining", "noncombining"),
            "pma_cacheability": kwargs.get("cacheability", "cacheable"),
            "pma_amo_type": kwargs.get("amo_type", "arithmetic"),
            "pma_rsrv": kwargs.get("rsrv"),
        }
        self.add_entry(PmaInfo(**params))

    def add_entry(self, pma_info: PmaInfo) -> None:
        self._entries.append(pma_info)

    def entries(self) -> list[PmaInfo]:
        "Every stored entry, unconsolidated - identity-stable, unlike consolidated_entries() which may merge into copies"
        return list(self._entries)

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
