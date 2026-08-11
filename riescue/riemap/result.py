# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""The result of a build: the read-back surface a consumer reads addresses and
page tables out of.

:class:`AllocationResult` is the *only* thing a caller reads after
:meth:`~riescue.riemap.builder.PageTableBuilder.build`. It exposes allocated
addresses, placed region bases, per-space roots (sptbr) and paging modes, and a
neutral traversal of the built page-table trees. Every accessor is keyed by the
same declaration OBJECT the consumer passed to the builder (a :class:`~riescue.riemap.request.Space`,
:class:`~riescue.riemap.request.Page`, or :class:`~riescue.riemap.request.MemoryRegion`)
-- RieMap assigns no names or ids of its own, so the records this surface yields carry
structured data (addresses, levels, the originating declaration) never pre-formatted
assembly symbols; the consumer chooses naming (its own ``object -> symbol`` map). A consumer
never reaches into ``PageMap`` / ``PTTable`` internals -- it goes through this object.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Dict, Iterator, List, Mapping, Optional, Tuple

import riescue.lib.enums as RV
from riescue.lib import common

if TYPE_CHECKING:
    from riescue.riemap.pagetables import PTAttrs, PTTable
    from riescue.riemap.page_map import PageMap
    from riescue.riemap.request import MemoryRegion, Page, Space


class EntryAttrs:
    """The attribute bits one emitted PTE carries, as a read-only snapshot.

    Read-back hands out a copy, not the live ``PTAttrs`` the walker packed the PTE value
    from. Writing to the live object changed nothing about the already-packed ``value`` a
    consumer emits, so the two silently disagreed; assignment raises here instead. Access
    is unchanged: ``attrs.x`` and ``attrs.get("x")`` both answer, and ``str()`` renders the
    same ``v=1, r=1, ...`` line RiescueD writes as an assembly comment.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Optional[int]]):
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    @classmethod
    def of(cls, attrs: "PTAttrs") -> "EntryAttrs":
        """Snapshot the PTE's own fields (the bits it encodes) out of a live ``PTAttrs``."""
        return cls({name: getattr(attrs, name) for name in type(attrs).own_attrs})

    def get(self, name: str) -> Optional[int]:
        try:
            return self._values[name]
        except KeyError:
            raise ValueError(f"{type(self).__name__} has no member {name}") from None

    def items(self) -> Iterator[Tuple[str, Optional[int]]]:
        return iter(self._values.items())

    def __getattr__(self, name: str) -> Optional[int]:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(f"{type(self).__name__} has no member {name}") from None

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is a read-only snapshot of an emitted PTE: cannot set {name}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is a read-only snapshot of an emitted PTE: cannot delete {name}")

    def __reduce__(self) -> Tuple[type, Tuple[Dict[str, Optional[int]]]]:
        # Copying and pickling both rebuild through __init__: blocking __setattr__ otherwise
        # breaks the default protocols, which restore state by assignment.
        return (type(self), (dict(self._values),))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EntryAttrs):
            return dict(self._values) == dict(other._values)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(sorted(self._values.items(), key=lambda item: item[0])))

    def __str__(self) -> str:
        return ", ".join(f"{name}={int(value)}" for name, value in self._values.items() if value is not None)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict(self._values)!r})"


@dataclass
class PageTableEntry:
    """One PTE, ready to emit: its index in the table, packed value, and attrs."""

    index: int
    value: int
    level: int
    leaf: bool
    attrs: EntryAttrs


@dataclass
class TableView:
    """One non-leaf page-table node: the space it belongs to, its base address,
    PTE stride, and entries. Structured only -- the consumer formats any linker
    section name (e.g. ``__pagetable_{name}_{addr}``) from its own ``space -> name``
    map plus ``addr``.

    ``addr`` (also :attr:`backing_addr`) is where the frame's bytes live, so it is what
    a consumer builds a memory image or a linker section from. ``input_addr`` is the
    address the parent PTE encodes for this frame -- a GPA for a VS table under a
    non-identity g-stage -- and so is what :meth:`SpaceResult.walk` reports. The two are
    equal for a single-stage tree and for an identity-backed frame."""

    space: "Space"
    addr: int
    entry_size: int
    input_addr: Optional[int] = None
    entries: List[PageTableEntry] = field(default_factory=list)

    @property
    def backing_addr(self) -> int:
        return self.addr

    @property
    def input_base(self) -> int:
        """The frame address the parent PTE encodes (== :attr:`addr` when identity-backed)."""
        return self.addr if self.input_addr is None else self.input_addr


@dataclass
class WalkStep:
    """One PTE visited while walking a tree for an address: its address and level.

    ``ptg_page`` / ``ptg_gpa`` expose the unambiguous
    :class:`~riescue.riemap.request.PTGPage` declaration for this VS node and its resolved
    GPA. ``ptg_page`` is ``None`` for an auto node, a non-VS walk, or an interned frame
    shared by multiple declaration identities; ``ptg_gpa`` remains available for a shared
    emitted frame. Use :meth:`SpaceResult.gstage_identity` on the g-stage space for
    authoritative emitted geometry."""

    pte_addr: int
    level: int
    leaf: bool
    pte_backing_addr: Optional[int] = None
    ptg_page: Optional["object"] = None
    ptg_gpa: Optional[int] = None


@dataclass
class PageMeta:
    """Per-page metadata a consumer needs for ``;#read_pte``/``;#write_pte``."""

    pagesize: RV.RiscvPageSizes
    gstage_vs_leaf_pagesize: Optional[RV.RiscvPageSizes] = None
    gstage_vs_nonleaf_pagesize: Optional[RV.RiscvPageSizes] = None
    gstage_node_pagesizes: Dict[int, RV.RiscvPageSizes] = field(default_factory=dict)


@dataclass(frozen=True)
class GStageIdentityView:
    """Canonical geometry and PTE inputs actually emitted for one structural identity."""

    gpa: int
    hpa: int
    pagesize: RV.RiscvPageSizes
    attrs: Dict[str, Optional[int]]
    pinned_frame_bases: Dict[int, int]


class SpaceResult:
    """Read-back for one address space (one satp/vsatp/hgatp root)."""

    def __init__(self, space: "Space", page_map: "PageMap"):
        self._space = space
        self._page_map = page_map

    @property
    def space(self) -> "Space":
        return self._space

    @property
    def root_addr(self) -> int:
        return self._page_map.sptbr

    @property
    def paging_mode(self) -> RV.RiscvPagingModes:
        return self._page_map.paging_mode

    @property
    def is_gstage(self) -> bool:
        return self._page_map.g_map

    def gstage_identity(self, gpa: int) -> GStageIdentityView:
        """Return the synthesized identity actually accepted at ``gpa``.

        This is emitted-frame metadata, not a declaration-object view, and therefore remains
        authoritative when several VS declarations intern to the same frame.
        """
        identity = self._page_map.emitted_gstage_identities[gpa]
        return GStageIdentityView(
            gpa=identity.gpa,
            hpa=identity.hpa,
            pagesize=identity.pagesize,
            attrs=dict(identity.attrs),
            pinned_frame_bases=dict(identity.pinned_frame_bases),
        )

    def gstage_identities(self) -> Iterator[GStageIdentityView]:
        """Yield synthesized identities in GPA order."""
        for gpa in sorted(self._page_map.emitted_gstage_identities):
            yield self.gstage_identity(gpa)

    def tables(self) -> Iterator[TableView]:
        """Yield every non-leaf table in the tree ONCE, root first (pre-order).

        The tree is a DAG, not a tree: tables are interned by base address
        (``PageMap.tables_by_base``), so one frame is reachable from every pointer PTE that
        targets it -- a pinned/shared PT node, or two VAs whose walks converge. A plain
        pre-order recursion yields such a frame once per path, which multiplies
        exponentially through a diamond, so dedupe by base address here (the identity a
        consumer keys on: RiescueD names the emitted linker section
        ``__pagetable_{map}_{addr}``, and a section emitted twice replays its ``.org``
        directives from 0 and fails to assemble with "attempt to move .org backwards").
        """
        entry_size = RV.RiscvPagingModes.pt_entry_size(mode=self._page_map.paging_mode)
        yield from self._walk(self._page_map.basetable, entry_size, set())

    def pte_entries(self) -> Iterator[Tuple[int, int]]:
        """Yield ``(pte_addr, pte_value)`` for every PTE in the tree, in the WALK domain.

        Each address is keyed the way :meth:`walk` reports it -- off the frame's
        :attr:`TableView.input_base`, the address its parent PTE encodes. That is what makes
        ``dict(space.pte_entries())[step.pte_addr]`` resolve for every step of a walk,
        including a VS table whose GPA differs from the host frame holding its bytes.

        A consumer building a MEMORY IMAGE wants the other domain -- where the bytes live --
        and must iterate :meth:`tables` and key off :attr:`TableView.backing_addr` instead.
        The two agree for a single-stage tree and for identity-backed frames.
        """
        for view in self.tables():
            for entry in view.entries:
                yield (view.input_base + view.entry_size * entry.index, entry.value)

    def walk(self, addr: int) -> Tuple[List[WalkStep], Optional[int]]:
        """Structurally walk the emitted tree for ``addr``.

        Returns ``(steps, translated_addr)`` where ``steps`` is the ordered list of
        PTEs visited from the root down to the leaf, and ``translated_addr`` is the
        address encoded by that leaf (``None`` if the walk does not reach one). This
        helper intentionally does not evaluate validity, permission, or reserved-encoding
        faults; negative tests can inspect entries that hardware would reject. This
        is the neutral read-back a consumer uses for ``;#read_pte``/``;#write_pte``
        level resolution and for reconstructing a two-stage walk.
        """
        mode = self._page_map.paging_mode
        entry_size = RV.RiscvPagingModes.pt_entry_size(mode=mode)
        max_levels = RV.RiscvPagingModes.max_levels(mode)
        steps: List[WalkStep] = []
        table = self._page_map.basetable
        leaf_entry = None
        leaf_level = 0
        for level in range(max_levels - 1, -1, -1):
            if table is None:
                break
            pte_idx = common.bits(addr, *RV.RiscvPagingModes.index_bits(mode, level))
            pte_addr = table.input_addr + entry_size * pte_idx
            step = WalkStep(
                pte_addr=pte_addr,
                pte_backing_addr=table.base_addr + entry_size * pte_idx,
                level=level,
                leaf=False,
            )
            steps.append(step)
            if pte_idx not in table.table:
                break
            entry = table.table[pte_idx]
            step.ptg_page = entry.ptg_page
            step.ptg_gpa = entry.ptg_gpa
            if entry.basetable and not entry.leaf:
                table = entry.basetable
            else:
                leaf_entry = entry
                leaf_level = level
                step.leaf = True
                break

        translated = None
        if leaf_entry is not None and leaf_entry.basetable is not None:
            leaf_base = leaf_entry.basetable.base_addr
            # Recognize the architecturally defined 64 KiB Svnapot encoding.
            # RieMap also permits callers to construct reserved N=1 encodings
            # for negative tests; those retain the ordinary literal-PPN
            # read-back behavior.
            if leaf_entry.pt_attr.n and leaf_level == 0 and ((leaf_base >> 12) & 0xF) == 0x8:
                leaf_base = (leaf_base & ~0xFFFF) | (addr & 0xF000)
            # Page-offset width = the low index bit of the leaf level (12 for a 4KB leaf,
            # the level's low bit for a superpage). Derived from the mode's index bits so
            # sv32's 10-bit levels (4MB superpage -> bit 21) are handled, not just 9-bit modes.
            offset_bits = RV.RiscvPagingModes.index_bits(mode, leaf_level)[1]
            offset_mask = (1 << offset_bits) - 1
            translated = (leaf_base & ~offset_mask) | (addr & offset_mask)
        return steps, translated

    def _walk(self, table: "Optional[PTTable]", entry_size: int, seen: set) -> Iterator[TableView]:
        if table is None or table.leaf or table.base_addr in seen:
            return
        seen.add(table.base_addr)
        view = TableView(
            space=self._space,
            addr=table.base_addr,
            input_addr=table.input_addr,
            entry_size=entry_size,
        )
        for index, entry in sorted(table.table.items()):
            view.entries.append(PageTableEntry(index=index, value=entry.get_value(), level=entry.level, leaf=entry.leaf, attrs=EntryAttrs.of(entry.pt_attr)))
        yield view
        for entry in table.table.values():
            if not entry.leaf and entry.basetable is not None:
                yield from self._walk(entry.basetable, entry_size, seen)


class AllocationResult:
    """Everything a consumer reads back after ``build()``. Every accessor takes the
    declaration object the consumer originally passed to the builder."""

    def __init__(
        self,
        spaces: "Dict[Space, SpaceResult]",
        region_bases: "Dict[MemoryRegion, int]",
        addresses: "Optional[Dict[Page, int]]" = None,
        physical_intervals: Optional[List[Tuple[int, int]]] = None,
        linear_intervals: Optional[List[Tuple[int, int]]] = None,
        page_addrs: "Optional[Dict[Page, Tuple[int, int]]]" = None,
        page_metas: "Optional[Dict[Page, PageMeta]]" = None,
    ):
        self._spaces = spaces
        self._region_bases = region_bases
        self._addresses = addresses or {}
        self._physical_intervals = list(physical_intervals or [])
        self._linear_intervals = list(linear_intervals or [])
        self._page_addrs = dict(page_addrs or {})
        self._page_metas = dict(page_metas or {})

    def space(self, space: "Space") -> SpaceResult:
        """The read-back for ``space``. Raises when that space has no page table.

        Not every declared space has one: a leaf (physical) space is never a mapping
        source, and a space whose paging mode is DISABLE originates no walk. Asking either
        for its page table is a wiring bug in the consumer, not a condition to handle --
        a consumer always knows which of its spaces it declared paging for -- so this
        raises rather than returning ``None``, which would invite silently skipping the
        space. The message says which of the two reasons applies; the bare dict lookup
        raised a ``KeyError`` naming a ``Space`` object, which said neither."""
        result = self._spaces.get(space)
        if result is None:
            raise KeyError(f"space has no page table: no mapping originates from it, or its paging mode is disabled: {space!r}")
        return result

    def spaces(self) -> Iterator[SpaceResult]:
        return iter(self._spaces.values())

    def region_base(self, region: "MemoryRegion") -> int:
        return self._region_bases[region]

    def address(self, page: "Page") -> int:
        """Return the allocated address for any declared page."""
        return self._addresses[page]

    def physical_intervals(self) -> List[Tuple[int, int]]:
        """Every physical ``[start, end)`` span RieMap placed (roots, page-table
        nodes, page frames). Reported so a caller that still allocates other things
        in its own space can mark these occupied -- it is output *data*, not a shared
        allocator."""
        return list(self._physical_intervals)

    def linear_intervals(self) -> List[Tuple[int, int]]:
        """Every linear ``[start, end)`` span RieMap placed (see :meth:`physical_intervals`)."""
        return list(self._linear_intervals)

    def address_of(self, page: "Page") -> Tuple[int, int]:
        """Return ``(va, pa)`` for a page."""
        return self._page_addrs[page]

    def page_meta(self, page: "Page") -> PageMeta:
        return self._page_metas[page]
