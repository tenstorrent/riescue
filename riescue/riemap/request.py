# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Declarations: the description of what to allocate.

A caller passes the :class:`~riescue.riemap.builder.PageTableBuilder` a set of
:class:`Space` s (address domains), :class:`Page` s (allocations in a space),
:class:`Mapping` s (leaf translations between pages), and any :class:`MemoryRegion` s.

These are **immutable, identity-based declarations**. They carry no build state and no
name/id -- the object itself is the identity. The builder produces an
:class:`~riescue.riemap.result.AllocationResult` and never mutates a declaration.
Relationships are typed **object references**, not id strings: ``Page.space`` is a
:class:`Space`, ``Mapping.src``/``dst`` are :class:`Page` s, and a relation's ``target``
is the referenced :class:`Page`. The builder navigates this graph by attribute access.

Each type is ``@dataclass(frozen=True, eq=False)``: frozen so a declaration cannot change
after construction, and identity-based (``eq=False``) so equality and hashing are by
object identity. A consumer may therefore use any declaration as a dict key or set member
-- e.g. its own ``object -> assembly symbol`` table -- and read results back keyed by the
same object (``result.address_of(page)``). RieMap does not define symbol names; naming is
the consumer's concern.

Declarations describe spaces, pages, mappings, and address constraints.
Any declaration order yields a valid (constraint-satisfying) result; with a fixed
seed, exact addresses may still differ if declaration order differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Generic, Iterable, Mapping as TypingMapping, Optional, Set, Tuple, TypeVar, Union

import riescue.lib.enums as RV


# --- solver choices -------------------------------------------------------


T = TypeVar("T")


def _freeze_mapping(values: TypingMapping[Any, Any]) -> TypingMapping[Any, Any]:
    """Copy a declaration mapping into a read-only view."""
    return MappingProxyType(dict(values))


def _require_positive_int(value: Any, label: str) -> int:
    """Reject anything that is not a positive ``int``.

    ``bool`` is excluded explicitly: it is an ``int`` subclass, so ``True`` would
    otherwise pass as the size 1.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer, got {value!r}")
    return value


@dataclass(frozen=True)
class Choice(Generic[T]):
    """A caller-owned choice domain with a deterministic preferred value.

    A scalar declaration is a hard constraint.  ``Choice`` instead says that every
    value in :attr:`options` is legal while :attr:`preferred` is the caller's seeded
    policy choice.  RieMap may select an alternative to preserve sharing or satisfy
    geometry, but never chooses a value outside this domain.

    Choices are generic so paging policy remains outside RieMap: callers can use the
    same declaration for PBMT bits, page sizes, or a correlated whole-node variant.
    """

    preferred: T
    alternatives: Tuple[T, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.preferred, TypingMapping):
            object.__setattr__(self, "preferred", _freeze_mapping(self.preferred))
        object.__setattr__(
            self,
            "alternatives",
            tuple(_freeze_mapping(value) if isinstance(value, TypingMapping) else value for value in self.alternatives),
        )
        if any(value == self.preferred for value in self.alternatives):
            raise ValueError("Choice.alternatives must not repeat preferred")
        for index, value in enumerate(self.alternatives):
            if any(value == prior for prior in self.alternatives[:index]):
                raise ValueError("Choice.alternatives must be unique")

    @property
    def options(self) -> Tuple[T, ...]:
        return (self.preferred, *self.alternatives)

    def allows(self, value: T) -> bool:
        return any(value == option for option in self.options)


def resolve_common_choice(values: Iterable[Union[T, Choice[T]]]) -> T:
    """Resolve declarations that must describe one shared object.

    Scalars are singleton hard constraints.  Choices contribute their complete
    domain.  The selected common value maximizes satisfied ``preferred`` values;
    stable option order breaks ties.  A disjoint set is a real declaration
    conflict and must be separated by coloring or reported to the caller.
    """

    declarations = tuple(values)
    if not declarations:
        raise ValueError("cannot resolve an empty choice set")

    seed = declarations[0]
    candidates = seed.options if isinstance(seed, Choice) else (seed,)
    legal = [candidate for candidate in candidates if all(declaration.allows(candidate) if isinstance(declaration, Choice) else declaration == candidate for declaration in declarations)]
    if not legal:
        raise ValueError("declarations have no common legal value")

    def preference_score(candidate: T) -> int:
        return sum(isinstance(declaration, Choice) and declaration.preferred == candidate for declaration in declarations)

    return max(enumerate(legal), key=lambda item: (preference_score(item[1]), -item[0]))[1]


# --- spaces ---------------------------------------------------------------


class Stage(Enum):
    """Which translation stage a space's page table participates in.

    Explicit on every :class:`Space` (default :attr:`SINGLE`). A single-stage or
    VS-stage space is rooted by an satp/vsatp; a G-stage
    space is rooted by an hgatp (16 KiB-aligned root, user leaves). A two-stage
    translation is a :attr:`VS` space mapping into a :attr:`G` space.
    """

    SINGLE = "single"  # satp: translates directly to physical
    VS = "vs"  # vsatp: first stage of a two-stage walk (VA -> GPA)
    G = "g"  # hgatp: g-stage (GPA -> HPA)


@dataclass(frozen=True, eq=False)
class Space:
    """An address domain: an address pool, a paging mode, and a translation stage.

    A space that is the source of at least one mapping has a page table and a root
    register; a space that is only ever a mapping *target* is a leaf (the physical
    domain) with no page table. Whether an allocation in this space is physical or linear
    is derived from the space -- the physical leaf (``PageTableBuilder.phys``) is physical,
    every other space is linear.

    ``secure_pt_probability`` is the chance that each *auto-allocated* page-table node frame
    in this space is drawn from secure memory. It lives here, rather than with the consumer,
    because only the allocator can choose a frame's memory pool -- the consumer never sees
    those frames. A consumer not in secure mode simply passes 0.

    ``priv_mode`` is the privilege this space is architecturally entered at, which decides the
    correct leaf U bit. It does not randomize leaf PTE bits: PTE-bit policy belongs to the
    consumer, which declares concrete bits via ``Mapping.pt_nodes``.

    ``root_frame`` optionally declares the page this space's root register (satp / vsatp /
    hgatp) and root table are bound to -- the declarative form of a root pin. A VS space's
    root frame is a GPA page in the target G-stage space and must also be the source of an
    explicit GPA-to-HPA mapping; no leaf/self-map is synthesized for it. ``None`` (the
    default) leaves root placement to the builder, which declares its own root frame per
    table-bearing space: a physical page for an satp/hgatp root
    (``PageTableBuilder._declare_root_frames``), or, for a VS space under a table-bearing
    G space, a GPA page plus its identity GPA-to-HPA leaf
    (``PageTableBuilder._synthesize_vs_root_frames``). Declare it only to place the root
    somewhere specific -- a recursive page table, or a root a test rewrites through a
    read-back window.
    """

    paging_mode: RV.RiscvPagingModes
    stage: Stage = Stage.SINGLE
    secure_pt_probability: int = 0
    priv_mode: RV.RiscvPrivileges = RV.RiscvPrivileges.SUPER
    root_frame: Optional["Page"] = None

    def __post_init__(self) -> None:
        probability = self.secure_pt_probability
        if isinstance(probability, bool) or not isinstance(probability, int) or not 0 <= probability <= 100:
            raise ValueError(f"Space.secure_pt_probability must be an integer probability in [0, 100], got {probability!r}")


# --- relational constraints (address = f(another page's resolved address)) ---


@dataclass(frozen=True, eq=False)
class SameAs:
    """This address equals ``target``'s resolved address (an alias).

    Two pages sharing one physical page put ``SameAs`` on their PA. The shared address
    is not reserved twice.
    """

    target: "Page"


@dataclass(frozen=True, eq=False)
class OffsetFrom:
    """This address equals ``target``'s resolved address plus a fixed ``delta``.

    Models a linked child page (address = parent + offset) or a buddy. The derived span
    is reserved so nothing else collides with it.
    """

    target: "Page"
    delta: int = 0


@dataclass(frozen=True, eq=False)
class DerivedFrom:
    """``((target & and_mask) ^ not_mask) | or_mask`` of ``target``'s resolved address.

    ``and_mask`` selects which source bits are copied; ``not_mask`` XORs the selected
    bits; ``or_mask`` sets bits. When ``random_mask`` is non-zero the derivation is
    *partial*: selected bits stay pinned to the source and the unselected
    (``random_mask``) bits are filled by a fresh, collision-free free draw.
    """

    target: "Page"
    and_mask: int = 0xFFFFFFFFFFFFFFFF
    or_mask: int = 0
    not_mask: int = 0
    random_mask: int = 0


Relation = Union[SameAs, OffsetFrom, DerivedFrom]


# --- page-table nodes -----------------------------------------------------

# Sentinel ``pt_nodes`` key for "this mapping's leaf level" -- resolved to
# ``RV.RiscvPageSizes.pt_leaf_level(src.pagesize)`` where consumed, so the common
# case need not know the pagesize's leaf level. All other keys are architectural
# level ints in the source's own stage. A distinct object (never an int).
LEAF = object()


# --- regions --------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class MemoryRegion:
    """A placed memory region: geometry plus placement qualifiers, nothing else.

    A *fixed* region pins ``base``; a *floating* region (``base is None``) gives only
    ``size``/``align`` and the solver places it at the declared alignment. A page targets a region
    with ``AddrSpec(region=...)``. RieMap never interprets what a region is *for* --
    a caller that needs to recover the request behind a region
    keeps its own ``MemoryRegion -> payload`` map alongside the declaration, keyed by
    the object identity, and reads the payload back next to ``result.region_base(region)``.
    """

    size: int
    align: int = 0x1000
    base: Optional[int] = None
    bits: Optional[int] = None
    qualifiers: Set[RV.AddressQualifiers] = field(default_factory=set)

    def __post_init__(self) -> None:
        object.__setattr__(self, "qualifiers", frozenset(self.qualifiers))
        _require_positive_int(self.size, "MemoryRegion.size")
        _require_positive_int(self.align, "MemoryRegion.align")
        if self.align & (self.align - 1):
            raise ValueError(f"MemoryRegion.align must be a power of two, got 0x{self.align:x}")
        if self.base is not None and self.base % self.align:
            raise ValueError(f"MemoryRegion.base 0x{self.base:x} is not aligned to align 0x{self.align:x}")


# --- address specification ------------------------------------------------


@dataclass(frozen=True, eq=False)
class AddrSpec:
    """How one address is chosen.

    Choosing an address proceeds in exactly one of four **modes**, tried in this order:

    1. ``exact`` -- pin the value.
    2. ``relation`` -- derive it from another page (``SameAs`` / ``OffsetFrom`` /
       ``DerivedFrom``).
    3. ``region`` -- place it inside a :class:`MemoryRegion`.
    4. otherwise -- a **free draw**.

    Only the free-draw mode consults its own ``qualifiers``, and only it goes through
    AddrGen. A relational page derives its address class from the ultimate free root;
    qualifiers on the follower are not independent metadata. Region mode selects among
    the free addresses inside the ``MemoryRegion`` window
    that satisfy ``and_mask`` / ``or_mask`` / ``bits`` and the pagesize alignment; it
    does not re-intersect builder exclusions or platform DRAM/MMIO segments, since the
    window is the consumer's placement domain. A default-constructed spec means "draw
    any suitable address".

    ``exclude`` overrides the builder-level exclusion set for AddrGen draws only.
    ``None`` (the default) keeps that set; an explicit tuple replaces it, so ``()``
    means nothing excludes that draw. Exact, relation, and region modes never consult
    exclusions. Page-table node frames are drawn through
    :class:`~riescue.riemap.addrgen.types.AddressConstraint` directly and never
    construct an ``AddrSpec``, so this override cannot reach them.
    """

    exact: Optional[int] = None
    and_mask: Optional[int] = None
    or_mask: Optional[int] = None
    bits: Optional[int] = None
    qualifiers: Set[RV.AddressQualifiers] = field(default_factory=set)
    region: Optional[MemoryRegion] = None
    relation: Optional[Relation] = None
    # Colored free draw: ``and_mask``/``or_mask`` pin specific index bits (attribute-based
    # coloring), so the draw picks from the reachable slot set instead of probe-and-mask.
    # Set by the builder's Phase A coloring; always False when coloring is off.
    pinned: bool = False
    # Per-draw override of the builder-level exclusion set. Typed as a plain tuple so
    # request.py does not import addrgen; callers pass ExcludedRegion values.
    exclude: Optional[Tuple[Any, ...]] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "qualifiers", frozenset(self.qualifiers))
        if self.exclude is not None:
            object.__setattr__(self, "exclude", tuple(self.exclude))
        declared = [name for name, value in (("exact", self.exact), ("relation", self.relation), ("region", self.region)) if value is not None]
        if len(declared) > 1:
            raise ValueError(f"AddrSpec declares more than one placement mode ({', '.join(declared)}); exact, relation and region are mutually exclusive")


# --- pages ----------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Page:
    """An allocation in one address space: the allocation unit.

    A location -- an address drawn from ``space``'s pool (per its :class:`AddrSpec`) at a
    given pagesize. A ``Page`` referenced by a :class:`Mapping` (as ``src`` or ``dst``)
    gets a leaf PTE and participates in translation; a ``Page`` that no mapping references
    is simply a reserved bare address. VA, GPA, PA, and bare
    scratch addresses are all ``Page`` s in their respective spaces -- whether a page is
    "mapped" is a property of the mapping graph, not of the type.

    ``reserve_size`` is the authoritative reservation size in bytes (default: the
    pagesize). It may exceed the pagesize (an init-data window larger than the PTE) or
    undercut it (a large page that covers only one small window); alignment
    stays the pagesize's.

    ``reserve_granule``, when set, expands this page's reserved address span to every
    aligned granule touched by ``[address, address + reserve_size)`` in ``space``:

        start = align_down(address, reserve_granule)
        end   = align_up(address + reserve_size, reserve_granule)

    The granule claim is domain-local: it never consumes physical backing and is not
    propagated through ``SameAs`` / identity into another address domain. Typical use is
    root-PTE exclusivity (1 GiB / 512 GiB / 256 TiB) without reserving that much DRAM.
    """

    space: Space
    pagesize: RV.RiscvPageSizes = RV.RiscvPageSizes.S4KB
    addr: AddrSpec = field(default_factory=AddrSpec)
    reserve_size: Optional[int] = None
    reserve_granule: Optional[int] = None

    def __post_init__(self) -> None:
        if self.reserve_size is not None:
            _require_positive_int(self.reserve_size, "Page.reserve_size")
        if self.reserve_granule is not None and (self.reserve_granule <= 0 or self.reserve_granule & (self.reserve_granule - 1)):
            raise ValueError("Page.reserve_granule must be a positive power of two, " f"got 0x{self.reserve_granule:x}")


@dataclass(frozen=True, eq=False)
class PTNode:
    """This mapping's node at one walk level: the frame holding its PTE + that PTE's bits.

    ``page`` = the frame backing this node (``None`` -> the builder auto-allocates & shares
    it). A concrete :class:`Page` pins the node's frame to that page's EXACT resolved
    address (its PTTable lives there; several :class:`PTNode` s naming the SAME frame pack
    their PTEs into it -- aliasing/recursion). For a single/G-stage tree the frame is a
    ``builder.phys`` Page; for a VS-stage tree it is a ``Stage.G``-space Page whose OWN
    explicit :class:`Mapping` supplies HPA + g-stage attrs. A
    :class:`PTGPage` instead declares the *synthesized* g-stage identity of this VS node's
    frame (RieMap allocates + shares the frame's GPA); it never pins that GPA.
    ``attrs`` = this level's PTE bits as plain base names ({v,a,d,g,w,r,x,u,n,pbmt}); the
    level is implied by the ``pt_nodes`` key. ``attrs`` (the VS PTE bits) and a
    ``page=PTGPage(...)`` (this node's frame's own g-stage tree) are DISTINCT and may
    coexist on one node.
    """

    page: Optional[Union[Page, "PTGPage"]] = None
    attrs: TypingMapping[str, Any] = field(default_factory=dict)
    choice: Optional[Choice[TypingMapping[str, Any]]] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _freeze_mapping(self.attrs))


@dataclass(frozen=True, eq=False)
class PTGPage:
    """A g-stage PT-node frame declared by PT NODES + PAGESIZE, not address. Usable only as
    :attr:`PTNode.page`.

    A VS-stage walk's non-leaf tables live at GPAs that need a g-stage identity translation
    (GPA -> HPA) so the guest can walk them. Those tables are auto-synthesized by the
    builder, so no declared :class:`Page` carries their g-stage PTE bits. A ``PTGPage`` names
    that translation's page-table nodes and leaves the frame's GPA + sharing to RieMap
    (contrast ``PTNode(page=Page)`` which pins an EXACT GPA frame the consumer controls).

    ``pt_nodes`` is the same vocabulary as :attr:`Mapping.pt_nodes`, one stage down: keyed by
    architectural level in the frame's OWN g-stage tree (or :data:`LEAF`, which resolves to
    that tree's leaf level -- see :meth:`level_attrs`), each :class:`PTNode` carrying that
    g-level's PTE bits as plain base names ({v,a,d,g,u,r,w,x,n,pbmt}). Per-g-level keying is
    load-bearing: g-nonleaf forcing (e.g. ``v_nonleaf_gnonleaf=0``, a bit at g-LEVEL 1)
    cannot be expressed by plain base names alone. A declared bit OVERRIDES the g-stage
    default; everything unset keeps the default. A node's ``page`` pins the host-physical
    frame holding that g-level's PTEs, exactly as in a :class:`Mapping` (a nested
    ``PTGPage`` there is meaningless -- the g-stage is the last stage -- and is rejected).

    ``pagesize`` is this node's g-stage frame pagesize and the only authority on its geometry:
    it says the frame's GPA is translated by a page of that size, so fewer g-stage levels
    translate it (a 2 MiB ``pagesize`` makes the g-stage leaf level 1). It
    also fixes the frame's GPA alignment and reservation. ``pt_nodes`` is purely an ATTRIBUTE
    declaration -- an omitted g-level simply keeps the g-stage defaults and carries no
    geometric meaning, so declaring a level's defaults explicitly never moves an address.

    ``identity=True`` means the frame's GPA maps to the same HPA (GPA==HPA).
    ``identity=False`` lets RieMap allocate the frame's GPA and physical backing
    independently and synthesize the resulting GPA-to-HPA translation.
    """

    pt_nodes: TypingMapping[Any, PTNode] = field(default_factory=dict)
    pagesize: Union[RV.RiscvPageSizes, Choice[RV.RiscvPageSizes]] = RV.RiscvPageSizes.S4KB
    identity: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "pt_nodes", _freeze_mapping(self.pt_nodes))

    def level_attrs(
        self,
        pagesize: Optional[RV.RiscvPageSizes] = None,
    ) -> Dict[int, Dict[str, Any]]:
        """``pt_nodes``' PTE bits as ``{g_level: {base: value}}``, :data:`LEAF` resolved.

        The frame's g-stage tree's leaf level is ``pt_leaf_level(pagesize)``, so that is
        what a :data:`LEAF` key means here. This is the shape the walker overlays onto
        its default g-stage identity matrix (and the shape
        :func:`resolve.gstage_frame_pt_node_levels` produces at the consumer boundary).
        """
        resolved_pagesize = pagesize if pagesize is not None else (self.pagesize.preferred if isinstance(self.pagesize, Choice) else self.pagesize)
        leaf_level = RV.RiscvPageSizes.pt_leaf_level(resolved_pagesize)
        out: Dict[int, Dict[str, Any]] = {}
        for key, node in self.pt_nodes.items():
            attrs = dict(node.choice.preferred) if node.choice is not None else {}
            attrs.update(node.attrs)
            out.setdefault(leaf_level if key is LEAF else key, {}).update(attrs)
        return out


@dataclass(frozen=True, eq=False)
class Mapping:
    """A translation from one page to another: exactly one leaf PTE.

    In ``src``'s space page table, a leaf at ``src``'s address translates to ``dst``'s
    address at ``src``'s pagesize. ``src``'s space is the indexed (from) domain; ``dst``'s
    space is the target (to) domain. A single-stage map is one Mapping (VA page -> PA
    page); a two-stage map is two (VA->GPA, GPA->PA).

    The leaf's PTE bits are declared, not passed as a freeform attr dict. ``pt_nodes``
    declares per-level page-table nodes (frame + that level's PTE bits), keyed by
    architectural level int in ``src``'s own stage or the :data:`LEAF` sentinel; the leaf
    node carries the leaf PTE bits ({v,r,w,x,u,a,d,g,n,pbmt}) and each non-leaf node its
    own bits. A missing level is auto (builder-allocated, attr-shared). A secure (STEE)
    leaf is signalled by ``AddressQualifiers.ADDRESS_SECURE`` on ``dst``'s effective
    allocation root (bit 55 on the destination), never a mapping flag. A VS non-leaf node's synthesized g-stage
    identity is declared by a :class:`PTGPage` on that node (``pt_nodes[level].page``), whose
    own ``pt_nodes`` describe that identity's g-stage tree; there is no freeform attr dict.

    A ``Mapping`` carries NO second-stage geometry: it is one stage. In a two-stage map the
    g-stage geometry lives entirely on the objects it belongs to --

    * the g-stage translation of the leaf's target is ``dst``'s own geometry, i.e.
      ``dst.pagesize`` (``dst`` is a :attr:`Stage.G` page whose own ``Mapping`` is the
      GPA -> HPA leaf); for a bare-g-stage source (the guest walks hgatp directly) the
      source itself is that leaf, so it is ``src.pagesize``;
    * the g-stage translation of each non-leaf node's frame is that node's
      :class:`PTGPage` (or pinned :class:`Page`) ``pagesize``.
    """

    src: Page
    dst: Page
    pt_nodes: TypingMapping[Any, PTNode] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pt_nodes", _freeze_mapping(self.pt_nodes))
