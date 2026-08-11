# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Translate RiescueD's parsed directives into riemap constraint inputs.

Turns parsed page mappings, random addresses and reserved-memory directives into the
neutral, reference-based constraint objects the
:class:`~riescue.riemap.builder.PageTableBuilder` consumes (:class:`~riescue.riemap.request.Space`,
:class:`~riescue.riemap.request.Page`, :class:`~riescue.riemap.request.Mapping`,
:class:`~riescue.riemap.request.MemoryRegion`, relations, reserved spans). Nothing here
allocates an address or builds a page table -- it only describes what must hold.

Allocation policy that stays in RiescueD (pagesize selection, private-map fan-out,
``modify_pt`` VA enlargement) is already resolved onto the parsed objects by the time
this runs; the translator reads those resolved fields.

RieMap's declarations are frozen, identity-based objects with no id/name fields -- a
consumer that needs a ``name -> object`` (or ``object -> name``) lookup keeps its own.
This module builds those constraints in two stages:

1. :class:`PageTableRequestBuilder` reads the parsed pool and builds *recipes* --
   RiescueD-owned intermediate dataclasses (:class:`_PageReq`, :class:`_AddrReq`) that
   describe a page/address's geometry using RiescueD's own string ids for cross
   references (``SameAs``/``OffsetFrom``/``DerivedFrom`` targets are ids at this stage,
   not yet objects), because a recipe may reference another recipe declared later in the
   test file.
2. :func:`build_page_tables` materializes those recipes into the real, frozen
   :class:`~riescue.riemap.request.Page` / :class:`~riescue.riemap.request.Mapping`
   objects the builder consumes (a topological pass resolves relation targets in
   whatever order they become ready, so recipe declaration order is not significant),
   drives the :class:`~riescue.riemap.builder.PageTableBuilder`, and packages the
   object-keyed :class:`Translation` the generator reads addresses back through.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union, TYPE_CHECKING

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.parser import ParsedPageMapping, ParsedRandomAddress
from riescue.riemap import resolve
from riescue.riemap.config import PagingParams
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import Choice, LEAF, AddrSpec, DerivedFrom, Mapping, MemoryRegion, OffsetFrom, Page, PTGPage, PTNode, Relation, SameAs, Space, Stage
from riescue.riemap.result import AllocationResult

if TYPE_CHECKING:
    from riescue.dtest_framework.generator.generator import PmaRegionBinding


@dataclass
class SectionAddr:
    """A section side's address, threaded through contiguity layout symbolically.

    A section chain (``runtime`` + its pages, ``code`` + its pages, ...) is laid
    out by adding sizes to a running address. But RieMap -- not RiescueD -- chooses
    the actual base, so the running value is symbolic: ``OffsetFrom`` an anchor
    section by a byte ``offset``. Adding an int advances the offset, so the existing
    ``addr + size`` threading in ``handle_sections`` needs no change -- only the
    address *type* differs (int stays a pinned address; ``SectionAddr`` becomes an
    ``OffsetFrom`` relation)."""

    anchor: str
    offset: int = 0

    def __add__(self, n: int) -> "SectionAddr":
        return SectionAddr(self.anchor, self.offset + n)

    __radd__ = __add__


@dataclass
class _Placement:
    """How one side (VA or PA) of a section page is chosen: pinned, relative, or free."""

    exact: Optional[int] = None
    anchor: Optional[str] = None
    offset: int = 0


@dataclass
class SectionSpec:
    """One 4 KiB section page RiescueD declares -- allocated + mapped by RieMap.

    RiescueD's section layout (``add_section_handler``) emits one of these per
    page. ``phys``/``lin`` say how
    each side is placed (pinned to a fixed address, ``OffsetFrom`` an anchor section
    for contiguity, or freely chosen). ``skip_page_map`` pages get no translation (a
    bare physical address + a linker section only); ``skip_linker`` pages get a
    translation but no linker section (page-table-only aliases). The remaining
    fields define attribute policy: ``iscode`` (executable leaf),
    ``always_super`` / ``always_user`` (the leaf U bit)."""

    name: str
    phys_name: str
    size: int
    phys: _Placement
    lin: _Placement
    iscode: bool = False
    always_super: bool = False
    always_user: bool = False
    skip_page_map: bool = False
    skip_linker: bool = False
    identity: bool = False


# The shared G-stage map every VS-stage map walks through.
GSTAGE_MAP_ID = "map_hyp"
DEFAULT_MAP_ID = "map_os"
# Sentinel space-name key (RiescueD-internal only) meaning "the engine's physical leaf
# domain" (``builder.phys``). Resolved to the real object once the builder exists.
_PHYS = "__phys"


# -- recipe-stage relations: RiescueD-side, string-id-keyed ----------------------
#
# A recipe relation mirrors one of riemap's :class:`SameAs` / :class:`OffsetFrom` /
# :class:`DerivedFrom`, but its ``target`` is a *recipe id string* (a forward
# reference to a page/address declared later in the test file), never a
# :class:`~riescue.riemap.request.Page`. RieMap only ever sees object references; the
# string id is RiescueD-internal bookkeeping and is converted to a real ``Page`` at
# the ``_materialize`` boundary. RieMap's ``Relation.target: Page`` is untouched.


@dataclass(frozen=True, eq=False)
class RecipeSameAs:
    """This recipe's address equals ``target``'s resolved address (``target`` a recipe id)."""

    target: str


@dataclass(frozen=True, eq=False)
class RecipeOffsetFrom:
    """This recipe's address equals ``target``'s resolved address plus ``delta``."""

    target: str
    delta: int = 0


@dataclass(frozen=True, eq=False)
class RecipeDerivedFrom:
    """``((target & and_mask) ^ not_mask) | or_mask`` of ``target``'s resolved address."""

    target: str
    and_mask: int = 0xFFFFFFFFFFFFFFFF
    or_mask: int = 0
    not_mask: int = 0
    random_mask: int = 0


RecipeRelation = Union[RecipeSameAs, RecipeOffsetFrom, RecipeDerivedFrom]


@dataclass(frozen=True, eq=False)
class RecipeAddrSpec:
    """A recipe-stage address spec: like :class:`AddrSpec`, but ``relation`` targets a recipe id.

    Converted to a real :class:`AddrSpec` (with a :class:`Page`-target relation) in
    :func:`_materialize`. Mirrors :class:`AddrSpec`'s fields so the conversion is a
    field-for-field copy plus the relation swap.
    """

    exact: Optional[int] = None
    and_mask: Optional[int] = None
    or_mask: Optional[int] = None
    bits: Optional[int] = None
    qualifiers: Set[RV.AddressQualifiers] = field(default_factory=set)
    region: Optional[MemoryRegion] = None
    relation: Optional[RecipeRelation] = None
    pinned: bool = False
    exclude: Optional[Tuple[Any, ...]] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "qualifiers", frozenset(self.qualifiers))
        if self.exclude is not None:
            object.__setattr__(self, "exclude", tuple(self.exclude))

    def to_addrspec(self, relation: Optional["Relation"]) -> AddrSpec:
        """Materialize into a real :class:`AddrSpec`, substituting the object-target relation."""
        return AddrSpec(
            exact=self.exact,
            and_mask=self.and_mask,
            or_mask=self.or_mask,
            bits=self.bits,
            qualifiers=set(self.qualifiers),
            region=self.region,
            relation=relation,
            pinned=self.pinned,
            exclude=None if self.exclude is None else tuple(self.exclude),
        )


# Base PTE attribute knobs carried straight through to the page request; the
# level/glevel expansions are recomputed by the builder's resolve layer.
_BASE_ATTRS = (*resolve.LEVEL_TYPES, "secure", "modify_pt")

# G-stage leaf/non-leaf forcing shorthand ({base}_{leaf|nonleaf}_g{leaf|nonleaf}).
# Carried through untouched; the builder's resolve layer materializes each into
# the concrete {base}_level{vs}_glevel{g} PTE key (skipping None-valued forms).
_GSTAGE_FORCING_ATTRS = tuple(resolve.GSTAGE_LEAF_NONLEAF_ATTRS)


# -- recipes: RiescueD-owned, string-id-keyed intermediate description --------------
#
# A recipe's ``AddrSpec.relation`` may carry another recipe's *id* (a plain string) as
# its ``target`` rather than an object -- a forward reference that survives until
# materialization, since a recipe may reference one declared later in the test file.
# Real ``Page``/``Mapping`` objects always need an already-built target, so recipes are
# resolved into objects by :func:`_materialize` (a topological pass, ordered by
# readiness rather than by declaration order).


@dataclass
class _PageReq:
    """One page to map, fully described except for its not-yet-allocated addresses.

    ``attrs`` holds concrete attribute values (``v``, ``r``, level-specific
    forcing knobs, ...) as a neutral dict. The builder runs it through
    :mod:`riescue.riemap.resolve` to expand level-specific keys.
    ``gstage_vs_leaf_size`` / ``gstage_vs_nonleaf_size`` apply only to a page
    in a VS-stage space that walks a G-stage space.
    """

    page_id: str
    pagesize: RV.RiscvPageSizes
    attrs: Dict[str, Any] = field(default_factory=dict)
    # The subset of ``attrs``' g-stage forcing that the TEST actually asked for, expanded to the
    # same concrete ``{base}_level{vs}_glevel{g}`` keys. ``attrs`` cannot answer that question:
    # ``ParsedPageMapping`` hard-defaults the v/g/n/pbmt g-stage knobs (unlike the a/d/r/w/x/u
    # ones beside them, which are ``Optional[...] = None``), so every page carries all four
    # whether or not the test named them. Both feed the g-stage identity, but differently: the
    # full ``attrs`` is the DEFAULT matrix the walker seeds every identity from, while only a
    # real force belongs on a ``PTGPage``, because a PTGPage is a coloring-visible demand for a
    # frame of one's own (see :func:`_vs_gstage_pt_nodes`).
    gstage_forced_attrs: Dict[str, Any] = field(default_factory=dict)
    va: RecipeAddrSpec = field(default_factory=RecipeAddrSpec)
    pa: RecipeAddrSpec = field(default_factory=RecipeAddrSpec)
    gstage_vs_leaf_size: Optional[RV.RiscvPageSizes] = None
    gstage_vs_nonleaf_size: Optional[RV.RiscvPageSizes] = None
    gstage_vs_nonleaf_size_options: Tuple[RV.RiscvPageSizes, ...] = ()
    va_reserve_size: Optional[int] = None
    va_reserve_granule: Optional[int] = None
    pa_reserve_size: Optional[int] = None
    # modify_nonleaf_pt / modify_leaf_pt are PT-node-shape flags (not PTE bits), so they ride
    # here rather than in ``attrs``; modify_pt already rides in ``attrs`` (_BASE_ATTRS) for the
    # walker. Each says which g-stage walk the runtime rewrites a POINTER PTE in; exclusivity is
    # one G-stage level above that walk's leaf/frame geometry:
    # ``modify_nonleaf_pt`` the walk translating the frame holding this page's leaf VS-stage PTE,
    # ``modify_leaf_pt`` the walk translating this page's own GPA (the final g-stage walk).
    modify_nonleaf_pt: bool = False
    modify_leaf_pt: bool = False


@dataclass
class _AddrReq:
    """A bare address to allocate, with no page table (a ``;#random_addr`` directive)."""

    request_id: str
    addr_type: RV.AddressType = RV.AddressType.PHYSICAL
    size: int = 0x1000
    addr: RecipeAddrSpec = field(default_factory=RecipeAddrSpec)


@dataclass
class _RawPageSpec:
    """A recipe for one materialized :class:`~riescue.riemap.request.Page`: which
    (RiescueD-named) space it lives in, its geometry, and its (possibly forward-
    referencing) address spec."""

    space_name: str
    pagesize: RV.RiscvPageSizes
    addr: RecipeAddrSpec
    reserve_size: Optional[int] = None
    reserve_granule: Optional[int] = None


@dataclass
class _RawMapping:
    """A recipe for one materialized :class:`~riescue.riemap.request.Mapping`, by
    (not-yet-necessarily-built) page id."""

    src_id: str
    dst_id: str
    # Per-level forced PTE bits declared as pt_nodes so attr-aware coloring separates
    # conflicting siblings. Keyed by architectural level int (or the LEAF sentinel) in
    # the source's own stage; a PTNode's ``page``, when present, is a recipe page id
    # (resolved in _materialize).
    pt_nodes: Dict[Any, PTNode] = field(default_factory=dict)
    # PTNode frame page ids to pin (modify_pt / modify_nonleaf_pt exclusive PT nodes),
    # keyed by source-stage level (or the LEAF sentinel) -> recipe page id. Resolved to
    # Page objects in _materialize and folded into ``pt_nodes`` (so the level owns its
    # whole subtree, and -- for modify_pt -- a declared window addresses its frame).
    pt_node_frames: Dict[Any, str] = field(default_factory=dict)


@dataclass
class Translation:
    """Everything riemap needs to allocate + build, derived from a parsed test --
    the object-keyed read-back surface :func:`build_page_tables` returns alongside the
    :class:`~riescue.riemap.result.AllocationResult`.

    RieMap declarations carry no names of their own, so every map here is keyed by the
    declaration OBJECT the generator got back from the builder; the generator looks up
    its own symbol name from these maps rather than from a string id.
    """

    # Space -> the RiescueD map name it was declared for (map_os, map_hyp, a user map).
    space_names: Dict[Space, str] = field(default_factory=dict)
    # Every in_pma MemoryRegion the translator declared, mapped to its PmaInfo + read-back
    # provenance (see generator.PmaRegionBinding). A custom_region MemoryRegion carries no
    # PmaInfo and is absent here -- its base is fixed and its PMA setup is the consumer's
    # own responsibility.
    region_pma: "Dict[MemoryRegion, PmaRegionBinding]" = field(default_factory=dict)
    # VA/source Page -> (lin_name, phys_name) it stands for.
    page_names: Dict[Page, Tuple[str, str]] = field(default_factory=dict)
    # VA/source Page -> (map name it was built for, the parsed mapping it came from).
    page_source: Dict[Page, Tuple[str, ParsedPageMapping]] = field(default_factory=dict)
    # Bare (unmapped) Page -> the random_addr name it stands for, and its declared type
    # (a LINEAR bare address is sign-extended on read-back; PHYSICAL/GPA is not).
    addr_names: Dict[Page, str] = field(default_factory=dict)
    addr_types: Dict[Page, RV.AddressType] = field(default_factory=dict)
    # Section pages, in layout order: (spec, Page, is_page). ``is_page`` True means a
    # mapped page (address via ``address_of``); False means a bare skip_page_map
    # address (via ``address``). Read-back walks these in order to populate
    # ``random_addrs`` + ``pool.sections`` from the result.
    sections: List[Tuple["SectionSpec", Page, bool]] = field(default_factory=list)
    # Every materialized Page (mapped or bare, source or synthetic destination) whose
    # AddrSpec constrains it to a MemoryRegion -- covers pages ``page_names``/``addr_names``
    # never name (a mapping's destination/HPA page). ``result.address(page)`` resolves any
    # of these regardless of role, since the builder's ``addresses`` dict covers every page.
    page_regions: Dict[Page, MemoryRegion] = field(default_factory=dict)
    # modify_pt PT-node read-back windows: the source (VA) Page a ``modify_pt`` mapping
    # was built from -> list of ``(level, window_va_page, frame_page)``. RiescueD walks
    # the source VA in its space, and for each level emits ``{lin}__pt_level{level}`` =
    # ``window_va + (pte_addr - frame_base)`` (a writable VA aliasing that level's PTE
    # slot) plus ``{lin}__pt_level{level}__phys`` = ``pte_addr``. The window is an ordinary
    # leaf mapping whose destination IS the pinned PT-node frame, so a runtime ld/sd
    # through it reads/writes the live page-table entry.
    pt_windows: Dict[Page, List[Tuple[int, Page, Page]]] = field(default_factory=dict)


class PageTableRequestBuilder:
    """Build riemap recipes from a resolved parsed :class:`Pool`.

    The pool must already have been through pagesize resolution (RiescueD's
    ``randomize_pagesize`` fills ``final_pagesize`` / ``address_mask`` /
    ``phys_address_mask`` on each mapping). The translator does not run policy
    itself; it reads the resolved fields and emits constraints.
    """

    def __init__(self, pool, featmgr, rng=None, pma_region_bindings: "Optional[Dict[int, PmaRegionBinding]]" = None):
        self.pool = pool
        self.featmgr = featmgr
        self.rng = rng
        self.pbmt_rng = RandNum(seed=rng.get_seed() ^ 0x50424D54) if rng is not None else None
        self.twostage = featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE
        # Explicit read-back provenance for pre-allocated in_pma PmaInfo objects, keyed by
        # id(PmaInfo) -- see generator.PmaRegionBinding. A caller (generator.py) that ran
        # the pre-allocation pass hands this in so _build_regions never has to re-derive
        # "is this new/reused/shared/decoy" heuristically. None (direct/standalone
        # construction, e.g. unit tests) means every region defaults to registering once.
        self.pma_region_bindings: "Dict[int, PmaRegionBinding]" = pma_region_bindings or {}

        # -- built up by build() --
        self.spaces_by_name: Dict[str, Space] = {}
        self.regions_by_id: Dict[str, MemoryRegion] = {}
        self.regions: List[MemoryRegion] = []
        # MemoryRegion -> its read-back binding (PmaInfo + register_on_readback), one per
        # in_pma region _build_regions declared; a custom_region carries no PmaInfo and is
        # absent here (see Translation.region_pma).
        self.region_pma: "Dict[MemoryRegion, PmaRegionBinding]" = {}
        # region_id -> True for a region whose PmaInfo is an adopted randomized decoy (see
        # _build_regions): the three in_pma AddrSpec(region=...) sites use this to disable
        # the exclusion set for that draw (a decoy IS one of the excluded windows, so a
        # member placed inside it must not exclude itself).
        self.decoy_region_ids: set = set()
        # random_addr name -> the memory-map range name its custom_region= spec resolved to. A spec may
        # be a tag matching several ranges, so the choice is per address and has to be remembered.
        self._custom_region_choices: Dict[str, str] = {}
        self.reserved_spans: List[Tuple[RV.AddressType, int, int]] = []
        self.page_reqs_by_map: Dict[str, List[_PageReq]] = {}
        self.address_reqs: List[_AddrReq] = []
        # Page-request ids that are identity-mapped (VA == PA): identity sections. The
        # recipe -> Page conversion pins each one's destination ``SameAs`` it, which is
        # how the builder recognizes identity -- there is no identity flag on the request.
        self.identity_page_ids: set = set()
        # RiescueD-side naming, filled in as recipes are built; translated into the
        # object-keyed Translation once recipes are materialized into real Pages.
        self.names_by_id: Dict[str, Tuple[str, str]] = {}
        self.page_source_by_id: Dict[str, Tuple[str, ParsedPageMapping]] = {}
        self.addr_names_by_id: Dict[str, str] = {}
        self.addr_types_by_id: Dict[str, RV.AddressType] = {}
        # Section pages, in layout order: (spec, id, is_page).
        self.sections_raw: List[Tuple["SectionSpec", str, bool]] = []
        # Lazily-built classification of page-owned addr names (see _page_addr_domains):
        # name -> (domain, owner_page_id) where domain is "lin" or "phys" and owner_page_id
        # is the name's owning map_os page recipe id.
        self._page_addr_domain_cache: "Dict[str, Tuple[str, str]] | None" = None
        # Lazily-built page-id -> owning-map paging-mode / _PageReq maps, used to resolve a
        # page's PA/destination slot id at recipe-build time (mirrors build_page_tables).
        self._reqs_by_id_cache: "Dict[str, _PageReq] | None" = None
        self._mode_of_page_cache: "Dict[str, RV.RiscvPagingModes] | None" = None

    # -- top-level -----------------------------------------------------------

    def build(self) -> "PageTableRequestBuilder":
        """Produce the full recipe set for the parsed test (returns self for chaining).

        Covers paging config, VS/single + shared identity G-stage spaces
        (incl. private maps), page requests (fixed/free VA+PA, pagesize, g-stage
        page sizes, base attrs), aliases (SameAs on PA), bare address requests
        (fixed/free with masks/bits/secure/mmio qualifiers), and reserved spans.

        Also covers linked child pages (OffsetFrom on VA+PA -- a child sits at
        ``parent + offset`` in both spaces), derived random addresses -- buddies
        (OffsetFrom(+size)), fully-deterministic derivations (DerivedFrom), and
        partial-select derivations (DerivedFrom with ``random_mask`` -- selected bits
        pinned to the source, unselected bits drawn free) -- in_pma addresses (a
        floating tagged MemoryRegion per PMA region, the address constrained
        ``in_region``), and custom_region addresses -- both bare and *page* VA/PA (a
        fixed MemoryRegion per resolved memory-map range, named or tagged, the physical
        address/PA constrained ``in_region``). A linear custom_region stays a no-op (it
        constrains only physical draws).

        The paging-disabled VA==PA identity is deliberately *not* a per-request
        relation: it follows from ``paging_mode == DISABLE`` (generator sets
        ``phys_addr = lin_addr``), so the builder collapses each page's PA onto its
        VA under a paging-disabled configuration when it runs the solver.
        """
        self._build_spaces()
        self._build_regions()
        self._build_custom_regions()
        self._build_page_requests()
        self._build_section_requests()
        self._build_address_requests()
        self._build_reserved_spans()
        self._resolve_gstage_forcing()
        return self

    # -- config --------------------------------------------------------------

    def _space_env(self) -> dict:
        """Per-space paging environment copied off FeatMgr for each space.

        ``secure_pt_probability`` is gated on secure mode HERE, not in riemap: riemap owns
        the *allocation* policy (only the allocator can pick which memory pool an
        auto-allocated page-table frame comes from) and honors whatever probability it is
        handed, while "am I even in secure mode" is the consumer's own state. A consumer not
        in secure mode simply passes 0.
        """
        fm = self.featmgr
        return dict(
            priv_mode=fm.priv_mode,
            secure_pt_probability=fm.secure_pt_probability if fm.secure_mode else 0,
        )

    # -- spaces --------------------------------------------------------------

    def _map_names(self) -> List[str]:
        """Every VS/single map the parse references: map_os, user ``;#page_map`` s,
        and any map a mapping joins via ``page_maps=[...]``."""
        names = {DEFAULT_MAP_ID}
        names.update(self.pool.get_parsed_page_maps().keys())
        for ppm in self.pool.get_parsed_page_mappings().values():
            names.update(ppm.page_maps)
        return [DEFAULT_MAP_ID, *sorted(n for n in names if n != DEFAULT_MAP_ID)]

    def _paging_mode_for_map(self, map_name: str) -> RV.RiscvPagingModes:
        """A user ``;#page_map(name=…, mode=sv48)`` picks its own mode; everything
        else inherits the test's paging mode (generator add_page_maps)."""
        parsed = self.pool.get_parsed_page_maps().get(map_name)
        if parsed is not None and parsed.mode != "testmode":
            return RV.RiscvPagingModes[parsed.mode.upper()]
        return self.featmgr.paging_mode

    def _build_spaces(self) -> None:
        """Declare the mapping-engine spaces RiescueD needs.

        Every RiescueD map (map_os + user ``;#page_map`` s) is one VA space, explicitly
        ``Stage.VS`` when the test is two-stage (it walks into the shared G-stage space)
        or ``Stage.SINGLE`` otherwise. When the test is two-stage there is one shared
        G-stage space ``map_hyp`` (``Stage.G``): identity-mapped when the VS stage is
        enabled (RiescueD's identity G-stage, GPA == HPA == PA), or a non-identity
        G-stage source when the VS stage is bare (the guest walks hgatp directly,
        GPA -> HPA). The engine's own physical leaf domain (``builder.phys``) is where
        single-stage / bare-VS PA/HPA pages live; RiescueD mints no leaf space of its own.
        """
        env = self._space_env()
        vs_stage = Stage.VS if self.twostage else Stage.SINGLE
        for map_name in self._map_names():
            self.spaces_by_name[map_name] = Space(paging_mode=self._paging_mode_for_map(map_name), stage=vs_stage, **env)
            self.page_reqs_by_map[map_name] = []
        if self.twostage:
            # One shared G-stage space walked by every VS map. When VS is enabled the VA -> GPA
            # mappings into it are identity mappings (RiescueD's identity G-stage, GPA == HPA);
            # when VS is bare it is itself a plain G-stage source (GPA -> HPA). Identity is
            # declared per mapping (see _emit_page_mapping), not on the space.
            self.spaces_by_name[GSTAGE_MAP_ID] = Space(paging_mode=self.featmgr.paging_g_mode, stage=Stage.G, **env)
            self.page_reqs_by_map[GSTAGE_MAP_ID] = []

    # -- PMA regions ---------------------------------------------------------

    def _build_regions(self) -> None:
        """Declare each PMA selected by an ``in_pma`` address.

        New PMAs are floating NAPOT regions that RieMap places. Existing PMAs are
        fixed regions at their programmed base. In both cases every member carries
        an explicit region constraint; matching attributes alone do not constrain
        an address draw to the PMA's range.
        """
        # Deferred import: generator.py imports this module at top level, so importing it
        # back here at module scope would cycle.
        from riescue.dtest_framework.generator.generator import PmaRegionBinding

        mapped_spans: Dict[str, int] = {}
        for ppm in self.pool.get_parsed_page_mappings().values():
            pagesize = ppm.final_pagesize or RV.RiscvPageSizes.S4KB
            phys_name = self._phys_name(ppm.lin_name, ppm)
            mapped_spans[phys_name] = max(
                mapped_spans.get(phys_name, 0),
                RV.RiscvPageSizes.memory(pagesize),
            )

        region_members: Dict[Any, List[Any]] = {}
        for parsed in self.pool.get_parsed_addrs().values():
            region_id = self._pma_region_of(parsed.name)
            if region_id is not None:
                region_members.setdefault(region_id, []).append(parsed)

        seen: set = set()
        for _name, parsed in self.pool.get_parsed_addrs().items():
            region_id = self._pma_region_of(parsed.name)
            if region_id is None or region_id in seen:
                continue
            seen.add(region_id)
            pma_info = parsed.pma_info
            member_span = sum(max(member.size or 0x1000, mapped_spans.get(member.name, 0)) for member in region_members[region_id])
            floating = bool(pma_info.pma_name and pma_info.pma_name.startswith("pma_") and pma_info.pma_address == 0)
            if floating:
                requested_size = max(pma_info.pma_size, member_span)
                size = 1 << max(12, (requested_size - 1).bit_length())
                align = size
                base = None
            else:
                size = pma_info.pma_size
                align = 0x1000
                base = pma_info.pma_address
            # ADDRESS_MMIO is an AddrGen claim about the platform memory map, not a
            # pmacfg attribute. It applies only when anchoring a new floating
            # region from the page's random_addr.io flag; fixed/adopted windows
            # (including io-typed decoys) place by geometry alone. Never derive it
            # from pma_memory_type -- that type may legally overlay any PA range.
            members = region_members[region_id]
            qualifiers = {RV.AddressQualifiers.ADDRESS_MMIO} if floating and any(member.io for member in members) else set()
            region = MemoryRegion(
                size=size,
                align=align,
                base=base,
                qualifiers=qualifiers,
            )
            self.regions_by_id[region_id] = region
            self.regions.append(region)
            binding = self.pma_region_bindings.get(id(pma_info))
            if binding is None:
                # Standalone construction (no generator provenance map): default to
                # registering once, the safe choice for a caller that has no other
                # bookkeeping tracking this PmaInfo.
                binding = PmaRegionBinding(info=pma_info, register_on_readback=True)
            self.region_pma[region] = binding
            if pma_info.pma_randomized:
                self.decoy_region_ids.add(region_id)

    def _build_custom_regions(self) -> None:
        """One fixed tagged MemoryRegion per memory-map range a ``custom_region`` resolves to.

        A physical ``;#random_addr(custom_region=spec)`` is drawn from a memory-map range at
        a pinned base; express that as a fixed :class:`MemoryRegion` the address then
        constrains itself ``in_region`` (the generator's ``custom_region`` constraint).

        ``spec`` is a range name or a tag (:meth:`Memory.resolve_custom_region`). A tag matching
        several ranges is resolved per address, so a handful of addresses naming the same tag
        spread across the matching windows instead of piling into one. The resolved *range* keys
        the region, so two addresses landing on the same range share one MemoryRegion -- declaring
        a second region over the same span would make the two reservations fight."""
        memory = self.featmgr.memory
        for name, parsed in self.pool.get_parsed_addrs().items():
            spec = parsed.custom_region
            if spec is None or parsed.fixed_addr is not None or parsed.derive_from is not None:
                continue
            if self._addr_type(parsed) != RV.AddressType.PHYSICAL:
                continue  # custom_region only constrains physical draws (generator handle_random_addr)
            matches = memory.resolve_custom_region(spec)
            if not matches:
                names, tags = memory.custom_region_choices()
                raise ValueError(f"random_addr {name!r}: custom_region {spec!r} is not a memory-map range name or tag. Defined names: {names}. Defined tags: {tags}")
            chosen = matches[0] if len(matches) == 1 or self.rng is None else self.rng.random_entry_in(list(matches))
            self._custom_region_choices[name] = chosen.name
            region_id = self._custom_region_id(chosen.name)
            if region_id in self.regions_by_id:
                continue  # another address already declared this range's region
            region = MemoryRegion(
                size=chosen.size,
                align=0x1000,
                base=chosen.start,
                qualifiers={memory.address_qualifier_of(chosen)},
            )
            self.regions_by_id[region_id] = region
            self.regions.append(region)

    def _custom_region_of(self, addr_name: str) -> str:
        """The region id the named address' ``custom_region`` resolved to in :meth:`_build_custom_regions`."""
        return self._custom_region_id(self._custom_region_choices[addr_name])

    @staticmethod
    def _custom_region_id(region_name: str) -> str:
        return f"custom::{region_name}"

    def _pma_region_of(self, name: str) -> "str | None":
        """The PMA region id an ``in_pma`` address must be placed in."""
        if not self.pool.parsed_random_addr_exists(addr_name=name):
            return None
        parsed = self.pool.get_parsed_addr(name)
        if not parsed.in_pma or parsed.pma_info is None:
            return None
        region = parsed.pma_info
        if region.pma_name:
            return region.pma_name
        return f"pma@{id(region)}"

    def _in_pma_addr_spec(self, region_id: str) -> RecipeAddrSpec:
        """``AddrSpec`` for an address placed inside PMA region ``region_id``.

        A region adopted from ``pool.pma_random_exclusions`` (a decoy) IS one of the
        builder's declared exclusion windows; a member drawn inside it would otherwise
        veto its own placement, so the draw must not carry the exclusion set at all
        (``exclude=()``). Every other region (new floating PMA, reused hint, or a
        custom_region -- which never reaches here) keeps the default ``exclude=None``,
        i.e. the builder's normal exclusion set still applies to that draw.
        """
        exclude = () if region_id in self.decoy_region_ids else None
        return RecipeAddrSpec(region=self.regions_by_id[region_id], exclude=exclude)

    # -- section pages -------------------------------------------------------

    def _build_section_requests(self) -> None:
        """Turn RiescueD's section layout (``pool.section_specs``) into recipes.

        Each section page is one page recipe (joining the same emission machinery as
        an ordinary page mapping). A mapped page joins every VS/single map (map_os
        primary + a SameAs copy per other map; g-stage is derived by the identity
        emitter, so sections are not added to the G map). A ``skip_page_map`` page
        (M-mode runtime/CSR pages) needs no translation -- it becomes a bare page
        recipe in the physical domain (a linker section only, no leaf PTE).
        Contiguity is expressed as ``OffsetFrom`` an anchor section; a fixed address
        (``reset_pc``, MMIO) is ``exact``; everything else is a free draw."""
        specs = list(self.pool.section_specs)
        if not specs:
            return
        by_name = {s.name: s for s in specs}
        maps = self._map_names()

        def anchor_id(section_name: str) -> str:
            anchor = by_name.get(section_name)
            if anchor is not None and anchor.skip_page_map:
                return f"secaddr::{section_name}"
            return f"sec::{section_name}::{DEFAULT_MAP_ID}"

        def side_spec(pl: "_Placement", dram: bool) -> RecipeAddrSpec:
            if pl.exact is not None:
                return RecipeAddrSpec(exact=pl.exact)
            if pl.anchor is not None:
                return RecipeAddrSpec(relation=RecipeOffsetFrom(anchor_id(pl.anchor), pl.offset))
            return RecipeAddrSpec(qualifiers={RV.AddressQualifiers.ADDRESS_DRAM} if dram else set())

        for spec in specs:
            attrs = self._section_attrs(spec)
            if spec.skip_page_map:
                req_id = f"secaddr::{spec.name}"
                self.address_reqs.append(_AddrReq(request_id=req_id, addr_type=RV.AddressType.PHYSICAL, size=spec.size, addr=side_spec(spec.phys, dram=True)))
                self.sections_raw.append((spec, req_id, False))
                continue
            primary_id = f"sec::{spec.name}::{DEFAULT_MAP_ID}"
            pa = side_spec(spec.phys, dram=True)
            # A section's declared size can exceed one page: the C-section anchor owns a
            # whole contiguous region (e.g. c_stack = 400 pages) that its OffsetFrom
            # followers land inside. Reserve that full span up-front so no unrelated free
            # draw can slip into the region before the followers force-place into it.
            reserve = spec.size if spec.size > RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S4KB) else None
            if spec.identity:
                # Identity map (VA == PA): give the page only its physical spec and record
                # it as identity. The recipe -> Page conversion pins its destination
                # ``SameAs`` it, so the builder draws the value once physically and ties
                # the VA to it. Do NOT also set va -- two specs for one identity would
                # over-constrain it (and a physical-qualified free spec cannot be satisfied
                # on the linear side).
                primary = _PageReq(page_id=primary_id, pagesize=RV.RiscvPageSizes.S4KB, attrs=attrs, pa=pa, pa_reserve_size=reserve)
                self.identity_page_ids.add(primary_id)
            else:
                va = side_spec(spec.lin, dram=False)
                primary = _PageReq(page_id=primary_id, pagesize=RV.RiscvPageSizes.S4KB, attrs=attrs, va=va, pa=pa, va_reserve_size=reserve, pa_reserve_size=reserve)
            self.page_reqs_by_map.setdefault(DEFAULT_MAP_ID, []).append(primary)
            self.names_by_id[primary_id] = (spec.name, spec.phys_name)
            self.sections_raw.append((spec, primary_id, True))
            for map_name in maps:
                if map_name == DEFAULT_MAP_ID:
                    continue
                copy_id = f"sec::{spec.name}::{map_name}"
                copy = _PageReq(
                    page_id=copy_id,
                    pagesize=RV.RiscvPageSizes.S4KB,
                    attrs=attrs,
                    va=RecipeAddrSpec(relation=RecipeSameAs(primary_id)),
                    pa=RecipeAddrSpec(relation=RecipeSameAs(primary_id)),
                )
                self.page_reqs_by_map.setdefault(map_name, []).append(copy)
                self.names_by_id[copy_id] = (spec.name, spec.phys_name)

    def _section_attrs(self, spec: "SectionSpec") -> Dict[str, object]:
        """The PTE attributes a section leaf needs.

        Sections are always-accessed OS/test infrastructure, so their leaves are
        pre-marked accessed+dirty (a=d=1) -- without hardware A/D update an a=0 leaf
        faults on first touch. ``iscode`` marks the leaf executable; the leaf U bit
        follows privilege / ``always_super`` / ``always_user``. r/w come from the
        builder's page defaults."""
        attrs: Dict[str, object] = {"a": 1, "d": 1}
        if spec.iscode:
            attrs["x_level0"] = 1
            if self.twostage:
                for lvl in range(5):
                    attrs[f"x_level{lvl}_glevel0"] = 1
        if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
            attrs["u_level0"] = 0
        if (not spec.always_super and self.featmgr.priv_mode == RV.RiscvPrivileges.USER) or spec.always_user:
            attrs["u_level0"] = 1
        return attrs

    # -- page requests -------------------------------------------------------

    def _build_page_requests(self) -> None:
        for (lin_name, _key_map), ppm in self.pool.get_parsed_page_mappings().items():
            # The generator maps one page (one VA/PA) into map_os plus each map named
            # in page_maps=[...]. The map_os copy is the primary that actually chooses
            # the address; every other map holds a copy pinned to it via SameAs, so a
            # shared page has one address across all its page tables.
            maps = self._maps_for_mapping(ppm)
            for index, map_name in enumerate(maps):
                primary = index == 0
                request = self._page_request(lin_name, map_name, ppm) if primary else self._shared_page_request(lin_name, map_name, maps[0], ppm)
                self.page_reqs_by_map.setdefault(map_name, []).append(request)
                self.names_by_id[request.page_id] = (lin_name, self._phys_name(lin_name, ppm))
                self.page_source_by_id[request.page_id] = (map_name, ppm)

    def _shared_page_request(self, lin_name: str, map_name: str, primary_map: str, ppm: ParsedPageMapping) -> _PageReq:
        """A copy of a page in a non-primary map: same VA and PA as the map_os copy."""
        primary_id = self._page_id(lin_name, primary_map)
        return self._make_page_request(
            page_id=self._page_id(lin_name, map_name),
            ppm=ppm,
            va=RecipeAddrSpec(relation=RecipeSameAs(primary_id)),
            pa=RecipeAddrSpec(relation=RecipeSameAs(primary_id)),
        )

    def _maps_for_mapping(self, ppm: ParsedPageMapping) -> List[str]:
        """The maps a single mapping's page joins: map_os plus its page_maps list.

        Under ``--private_maps`` a *shared* (non-private) mapping must additionally
        join every private map so all harts see it -- e.g. a cross-hart IPC buffer
        declared with no ``page_maps=``. A private mapping
        (``in_private_map``) stays in its own map only.
        """
        if ppm.in_private_map and ppm.page_maps:
            # A private declaration belongs only to its own private map(s): the pool
            # registers it under those maps alone (never map_os), so two private
            # mappings may reuse one VA with independent PAs.
            return list(dict.fromkeys(ppm.page_maps))
        maps = [DEFAULT_MAP_ID]
        for m in ppm.page_maps:
            if m not in maps:
                maps.append(m)
        if self.featmgr.private_maps and not ppm.in_private_map:
            for m in self._map_names():
                if m != DEFAULT_MAP_ID and m not in maps:
                    maps.append(m)
        return maps

    def _page_request(self, lin_name: str, map_name: str, ppm: ParsedPageMapping) -> _PageReq:
        va = self._va_spec(lin_name, ppm)
        pa = self._pa_spec(lin_name, map_name, ppm)
        return self._make_page_request(
            page_id=self._page_id(lin_name, map_name),
            ppm=ppm,
            va=va,
            pa=pa,
        )

    def _phys_name(self, lin_name: str, ppm: ParsedPageMapping) -> str:
        """The name the page's physical address is known by.

        A ``phys_name=&random`` mapping resolves to ``__auto_phys_<lin_name>`` (the
        generator's auto-naming); a declared or fixed phys keeps its name (a fixed
        phys already encodes its address in ``__auto_phys_0x...``)."""
        if ppm.phys_name == "&random":
            return "__auto_phys_" + lin_name
        return ppm.phys_name

    def _make_page_request(self, page_id: str, ppm: ParsedPageMapping, va: RecipeAddrSpec, pa: RecipeAddrSpec) -> _PageReq:
        pagesize = ppm.final_pagesize if ppm.final_pagesize else RV.RiscvPageSizes.S4KB
        gleaf = ppm.gstage_vs_leaf_final_pagesize
        gnonleaf = ppm.gstage_vs_nonleaf_final_pagesize

        # Bare ``addr::`` recipes own the ``;#random_addr`` window. A page that
        # ``SameAs`` that recipe must not also reserve the same span (exact overlap
        # of two BACKING claims). An ``OffsetFrom`` leaf reserves only its pagesize
        # footprint inside the window (contained share).
        def _bare_target(spec: RecipeAddrSpec):
            rel = spec.relation
            if isinstance(rel, (RecipeSameAs, RecipeOffsetFrom)) and rel.target.startswith("addr::"):
                return rel
            return None

        leaf_bytes = RV.RiscvPageSizes.memory(pagesize)
        nonleaf_span = self._modify_nonleaf_vs_span(ppm, pagesize) or 0
        va_rel = _bare_target(va)
        if isinstance(va_rel, RecipeSameAs):
            # Bare owns the window; keep only modify_nonleaf isolation on the page.
            va_reserve_size = nonleaf_span or None
        elif isinstance(va_rel, RecipeOffsetFrom):
            va_reserve_size = max(leaf_bytes, nonleaf_span)
        else:
            va_window = self._reserve_size(ppm.lin_name, ppm.address_size)
            va_reserve_size = max(leaf_bytes, va_window or 0, nonleaf_span)

        pa_rel = _bare_target(pa)
        if isinstance(pa_rel, RecipeSameAs):
            pa_reserve = None
        elif isinstance(pa_rel, RecipeOffsetFrom):
            pa_reserve = leaf_bytes
        else:
            pa_reserve = self._phys_reserve_size(ppm, pagesize, gleaf)
        return _PageReq(
            page_id=page_id,
            pagesize=pagesize,
            attrs=self._attrs(ppm),
            gstage_forced_attrs=self._gstage_forced_attrs(ppm),
            va=va,
            pa=pa,
            gstage_vs_leaf_size=gleaf if self.twostage else None,
            gstage_vs_nonleaf_size=gnonleaf if self.twostage else None,
            gstage_vs_nonleaf_size_options=self._gstage_nonleaf_size_options(ppm, gnonleaf) if self.twostage and gnonleaf is not None else (),
            # Reservation bytes are authoritative constraints riemap honors verbatim (the
            # engine derives bit widths / alignment). The VA reserves its whole pagesize,
            # widened by a declared window or the VS leaf-node isolation required by
            # modify_nonleaf_pt. modify_pt's stronger full-walk ownership is a separate
            # aligned containing-span granule, so it does not change the ordinary footprint.
            # SameAs(addr::) leaves reserve_size unset so the bare recipe alone claims
            # the window.
            va_reserve_size=va_reserve_size,
            va_reserve_granule=self._modify_pt_granule(ppm),
            pa_reserve_size=pa_reserve,
            modify_nonleaf_pt=bool(ppm.modify_nonleaf_pt),
            modify_leaf_pt=bool(ppm.modify_leaf_pt),
        )

    def _gstage_nonleaf_size_options(self, ppm: ParsedPageMapping, preferred: RV.RiscvPageSizes) -> Tuple[RV.RiscvPageSizes, ...]:
        """Allowed non-leaf frame geometries, with the seeded draw first."""
        if self.featmgr.all_4kb_pages:
            return (preferred,)
        if ppm.gstage_vs_nonleaf_pagesizes:
            allowed = [RV.RiscvPageSizes.str_to_enum(value) for value in ppm.gstage_vs_nonleaf_pagesizes]
        else:
            allowed = list(RV.RiscvPagingModes.supported_pagesizes(self.featmgr.paging_g_mode))
            root_level = RV.RiscvPagingModes.max_levels(self.featmgr.paging_g_mode) - 1
            allowed = [size for size in allowed if size == preferred or RV.RiscvPageSizes.pt_leaf_level(size) != root_level]
        ordered = [preferred]
        ordered.extend(size for size in allowed if size != preferred)
        return tuple(ordered)

    def _phys_reserve_size(self, ppm: ParsedPageMapping, pagesize: RV.RiscvPageSizes, gleaf: "RV.RiscvPageSizes | None") -> int:
        """Physical bytes a mapped page reserves -- RiescueD policy, not engine geometry.

        Under g-stage a ``&random`` page's PA is a GPA fronted by g-stage-leaf frames, so
        only one leaf window is reserved (a big VS page keeps its alignment but backs just
        that window, which is how a 4 GiB mmap fits many 1 GiB virtualized pages): 4 KiB by
        default, widened to the g-stage leaf size under ``--reserve_partial_phys_memory``.
        The flag *widens* there, opposite to everywhere else, where it *clamps* to one
        4 KiB page. A named/pinned phys page under g-stage keeps its full span, widened to
        the g-stage leaf window."""
        flag = self.featmgr.reserve_partial_phys_memory
        if self.twostage:
            gleaf_bytes = RV.RiscvPageSizes.memory(gleaf if gleaf else RV.RiscvPageSizes.S4KB)
            if ppm.phys_name == "&random":
                return max(0x1000, gleaf_bytes) if flag else 0x1000
            if flag:
                return 0x1000
            window = self._reserve_size(self._phys_name(ppm.lin_name, ppm), ppm.phys_address_size)
            return max(RV.RiscvPageSizes.memory(pagesize), window or 0, gleaf_bytes)
        if flag:
            return 0x1000
        window = self._reserve_size(self._phys_name(ppm.lin_name, ppm), ppm.phys_address_size)
        return max(RV.RiscvPageSizes.memory(pagesize), window or 0)

    def _reserve_size(self, name: str, mapping_size: int) -> "int | None":
        """Bytes this page side must reserve beyond its pagesize, or None.

        Two sources: a declared ``;#random_addr(size=…)`` the name refers to (e.g. a
        64 KiB window with 64 KiB of init data that resolves to a 4 KiB PTE), and the
        mapping's own resolved ``address_size``. Runtime PTE exclusivity is represented
        separately by ``reserve_granule``. Reserve the larger ordinary footprint here."""
        declared = self.pool.get_parsed_addr(name).size if self.pool.parsed_random_addr_exists(addr_name=name) else 0
        size = max(declared, mapping_size)
        return size or None

    def _modify_pt_granule(self, ppm: ParsedPageMapping) -> "int | None":
        """Root-entry coverage owned by a ``modify_pt`` family anchor."""
        # ``lin_name=base+offset`` siblings (``lin_addr_link``) sit inside the
        # SameAs / bare window's family; only the anchor owns the root granule.
        if not ppm.modify_pt or ppm.lin_addr_link is not None:
            return None
        return _root_entry_span(self.featmgr.paging_mode)

    def _modify_nonleaf_vs_span(
        self,
        ppm: ParsedPageMapping,
        pagesize: RV.RiscvPageSizes,
    ) -> "int | None":
        """VS bytes a ``modify_nonleaf_pt`` family reserves to isolate its leaf-PTE frame.

        The runtime rewrites the g-stage walk of the frame holding this page's VS leaf PTE.
        First ensure no unrelated VS leaf PTE occupies that frame by owning the complete VS
        leaf-table span. The frame's own GPA separately gets root-entry granule ownership.

        Returned for the family anchor only; ``lin_addr_link`` offset children stay inside
        its reservation."""
        if not ppm.modify_nonleaf_pt or ppm.lin_addr_link is not None:
            return None
        mode = self.featmgr.paging_mode
        max_levels = RV.RiscvPagingModes.max_levels(mode)
        leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
        # ``index_bits`` RAISES on an out-of-range level, so bound-check first: a leaf already at
        # (or above) the root level has no node below the root, and paging DISABLE has none at all
        # (``max_levels`` 0).
        if leaf_level + 1 >= max_levels:
            return None
        bits = RV.RiscvPagingModes.index_bits(mode=mode, level=leaf_level + 1)
        if bits is None:
            return None
        return 1 << bits[1]

    @staticmethod
    def _align_down_to_final_pagesize(addr: int, ppm: ParsedPageMapping) -> int:
        """``addr`` rounded down to the mapping's leaf pagesize alignment.

        A declared ``lin_addr``/``phys_addr`` is an exact address, but the leaf PTE it
        lands in still spans the whole page: the address the page-table math anchors on
        is the page-aligned base, not whatever offset-into-page the user wrote. RieMap's
        exact-address placement enforces this alignment (rejects an exact address that is
        not a multiple of its own size), so align here rather than surface that as a
        placement error. :meth:`generator.Generator.pick_pagesize` already prefers a
        pagesize the exact address satisfies when one is available, so this is a no-op in
        the common case.
        """
        size = RV.RiscvPageSizes.memory(ppm.final_pagesize if ppm.final_pagesize else RV.RiscvPageSizes.S4KB)
        return addr & ~(size - 1)

    def _va_spec(self, lin_name: str, ppm: ParsedPageMapping) -> RecipeAddrSpec:
        if ppm.lin_addr_link is not None:
            parent, offset = ppm.lin_addr_link
            return RecipeAddrSpec(relation=RecipeOffsetFrom(f"addr::{parent}", offset))
        if ppm.lin_addr_specified:
            return RecipeAddrSpec(exact=self._align_down_to_final_pagesize(int(ppm.lin_addr, 0), ppm))
        # Identity default (phys pinned + lin unspecified => VA == PA).
        # MMIO pages (IMSIC/ACLINT) are declared ``phys_addr=0x...`` with no ``lin_addr``
        # and are reached by the runtime at their raw physical address; without this the
        # VA free-draws elsewhere and the identity access faults. A declared
        # ``;#random_addr(type=linear)`` *is* a lin specification (free-draw / derive /
        # in-region), so the identity default must not fire over it.
        if ppm.phys_addr_specified and not self.pool.parsed_random_addr_exists(addr_name=lin_name):
            return RecipeAddrSpec(exact=self._align_down_to_final_pagesize(int(ppm.phys_addr, 0), ppm))
        # A VA that names a free-draw ``;#random_addr`` SameAs that bare ``addr::``
        # recipe (window / derive / free live on the bare draw). Region-constrained
        # names (in_pma) stay page-owned so the page PA/VA is the in_region member --
        # a SameAs follower of a bare in_region addr cannot nest its BACKING claim
        # inside the region's reservation.
        if self.pool.parsed_random_addr_exists(addr_name=lin_name):
            parsed = self.pool.get_parsed_addr(lin_name)
            if not self._addr_is_region_constrained(parsed):
                return RecipeAddrSpec(relation=RecipeSameAs(f"addr::{lin_name}"))
            if parsed.derive_from is not None:
                return self._derived_addr_spec(parsed)
        region_id = self._pma_region_of(lin_name)
        if region_id is not None:
            return self._in_pma_addr_spec(region_id)
        and_mask = self._folded_mask(ppm.address_mask, lin_name)
        # ``modify_nonleaf_pt`` must keep the complete VS leaf-table family together so
        # no unrelated leaf PTE occupies the frame whose g-stage walk is rewritten.
        # Only a free draw can be aligned; exact/region addresses remain where declared.
        span = self._modify_nonleaf_vs_span(
            ppm,
            ppm.final_pagesize if ppm.final_pagesize else RV.RiscvPageSizes.S4KB,
        )
        if span is not None:
            and_mask &= ~(span - 1) & 0xFFFFFFFFFFFFFFFF
        return RecipeAddrSpec(and_mask=and_mask)

    def _pa_secure_qualifiers(self, ppm: ParsedPageMapping) -> set:
        """ADDRESS_SECURE for a page-owned free PA draw.

        Relational PAs never call this helper: their allocation class belongs to the
        bare physical root and is decided once by :meth:`_addr_spec`.
        """
        if self._draw_secure(ppm.secure, is_physical=True):
            return {RV.AddressQualifiers.ADDRESS_SECURE}
        return set()

    def _phys_needs_secure(self, name: str) -> bool:
        """True when a free-draw phys ``addr::{name}`` must allocate secure.

        Explicit ``ppm.secure`` on any SameAs / OffsetFrom follower is consumer policy
        that must be lowered onto the family's allocation root. The relational follower
        carries no duplicate qualifier: RieMap derives its address class and secure PTE
        tag from this root.
        """
        for (_lin, _map), ppm in self.pool.get_parsed_page_mappings().items():
            if not ppm.secure:
                continue
            if ppm.phys_addr_link is not None and ppm.phys_addr_link[0] == name:
                return True
            if self._phys_name(ppm.lin_name, ppm) == name:
                return True
        return False

    def _pa_spec(self, lin_name: str, map_name: str, ppm: ParsedPageMapping) -> RecipeAddrSpec:
        # Alias: this mapping shares another mapping's physical page.
        if ppm.alias:
            canonical = self.pool.resolve_canonical_lin_name(lin_name, map_name)
            if canonical != lin_name:
                return RecipeAddrSpec(relation=RecipeSameAs(self._page_id(canonical, map_name)))
        if ppm.phys_addr_link is not None:
            parent, offset = ppm.phys_addr_link
            return RecipeAddrSpec(relation=RecipeOffsetFrom(f"addr::{parent}", offset))
        phys_name = self._phys_name(lin_name, ppm)
        if ppm.phys_addr_specified:
            return RecipeAddrSpec(exact=self._align_down_to_final_pagesize(int(ppm.phys_addr, 0), ppm))
        # Free-draw ``;#random_addr`` phys: SameAs the bare recipe. Region-constrained
        # phys (in_pma / physical custom_region) stay on the page as in_region members.
        if self.pool.parsed_random_addr_exists(addr_name=phys_name):
            parsed = self.pool.get_parsed_addr(phys_name)
            if not self._addr_is_region_constrained(parsed):
                return RecipeAddrSpec(relation=RecipeSameAs(f"addr::{phys_name}"))
        # in_pma phys: the page owns its PA, constrained to its PMA region.
        region_id = self._pma_region_of(phys_name)
        if region_id is not None:
            return self._in_pma_addr_spec(region_id)
        # custom_region phys: the page's PA is placed inside the named user range, exactly
        # as the bare custom_region draw is (same qualifiers/masks); no secure promotion.
        custom = self._custom_region_pa_spec(phys_name)
        if custom is not None:
            return custom
        qualifiers = self._pa_secure_qualifiers(ppm)
        return RecipeAddrSpec(and_mask=self._folded_mask(ppm.phys_address_mask, phys_name), qualifiers=qualifiers)

    def _custom_region_pa_spec(self, phys_name: str) -> "RecipeAddrSpec | None":
        """The in_region PA spec for a page whose phys_name is a physical custom_region
        random_addr, or None. Mirrors the bare :meth:`_addr_spec` custom_region branch:
        the addr keeps its own mask (the generator never overwrites a custom phys mask
        with the page's), placed in_region with no secure/mmio qualifier."""
        if not self.pool.parsed_random_addr_exists(addr_name=phys_name):
            return None
        parsed = self.pool.get_parsed_addr(phys_name)
        if parsed.custom_region is None or parsed.fixed_addr is not None or parsed.derive_from is not None:
            return None
        if self._addr_type(parsed) != RV.AddressType.PHYSICAL:
            return None
        return RecipeAddrSpec(region=self.regions_by_id[self._custom_region_of(phys_name)], and_mask=parsed.and_mask, or_mask=parsed.or_mask, bits=parsed.addr_bits)

    def _folded_mask(self, base_mask: int, name: str) -> int:
        """AND a page side's mask with the referenced random_addr's mask.

        A page mapping's lin_name/phys_name may name a declared ``;#random_addr``;
        the generator AND-combines that directive's stricter mask onto the page's
        alignment. Custom-region addresses are excluded: a *physical* custom_region PA
        never reaches here (it is placed in_region by :meth:`_pa_spec`); a *linear*
        custom_region is a no-op draw that keeps the page's own alignment."""
        if self.pool.parsed_random_addr_exists(addr_name=name):
            parsed = self.pool.get_parsed_addr(name)
            if parsed.custom_region is None:
                return base_mask & parsed.and_mask
        return base_mask

    # -- address requests ----------------------------------------------------

    def _build_address_requests(self) -> None:
        owned = self._page_owned_addr_names()
        for name, parsed in self.pool.get_parsed_addrs().items():
            if name in owned:
                # This address is a page's VA or PA; the page request owns it and the
                # equate reads back from the page. Emitting a bare request too would
                # allocate it twice.
                continue
            request = self._address_request(name, parsed)
            self.address_reqs.append(request)
            self.addr_names_by_id[request.request_id] = name
            self.addr_types_by_id[request.request_id] = request.addr_type

    def _page_addr_domains(self) -> "Dict[str, Tuple[str, str]]":
        """Classify every random_addr name a page mapping allocates, walking the parsed
        page mappings exactly once.

        Returns ``name -> (domain, owner_page_id)`` where ``domain`` is ``"lin"`` (the
        page's VA) or ``"phys"`` (the page's PA), and ``owner_page_id`` is the primary
        page recipe that owns the address. :meth:`_page_owned_addr_names` reads this
        classifier; :meth:`_derive_source_id` uses ``owner_page_id`` to resolve a
        page-owned physical derive source to the owning page's PA/destination slot."""
        if self._page_addr_domain_cache is None:
            domains: "Dict[str, Tuple[str, str]]" = {}

            def classify(lin_name: str, ppm: ParsedPageMapping) -> None:
                owner_map = self._maps_for_mapping(ppm)[0]
                owner = self._page_id(lin_name, owner_map)
                domains[lin_name] = ("lin", owner)
                domains[self._phys_name(lin_name, ppm)] = ("phys", owner)

            for (lin_name, _map), ppm in self.pool.get_parsed_page_mappings().items():
                classify(lin_name, ppm)
            self._page_addr_domain_cache = domains
        return self._page_addr_domain_cache

    def _addr_is_region_constrained(self, parsed: ParsedRandomAddress) -> bool:
        """True when the address must be an in_region page member (not a bare recipe).

        ``in_pma`` and physical ``custom_region`` draws are placed as region members;
        riemap nests those BACKING claims under the region's reservation. A bare
        ``addr::`` + page ``SameAs`` follower cannot re-claim that span through the
        forced-relation path, so those names stay page-owned.
        """
        if parsed.in_pma:
            return True
        return parsed.custom_region is not None and self._addr_type(parsed) == RV.AddressType.PHYSICAL

    def _page_owned_addr_names(self) -> set:
        """random_addr names a page request already allocates (as its VA or PA).

        Free-draw ``;#random_addr`` names referenced by ``lin_name`` / ``phys_name``
        (or ``name+offset``) are *not* owned: the bare ``addr::`` recipe owns the
        window and the page SameAs / OffsetFrom it.

        Region-constrained names (``in_pma``, physical ``custom_region``) *are* owned:
        the page's PA/VA is the in_region member (see :meth:`_pa_spec`), so no
        separate bare request is emitted. A *linear* custom_region is a no-op
        (custom_region constrains only physical draws), so such a name keeps its
        own bare free draw.
        """
        owned: set = set()
        for name in self._page_addr_domains():
            if self.pool.parsed_random_addr_exists(addr_name=name):
                parsed = self.pool.get_parsed_addr(name)
                if self._addr_is_region_constrained(parsed):
                    owned.add(name)
                # else: free-draw / linear custom_region -- bare recipe owns.
                continue
            # Auto (__auto_phys_*), fixed underscored names, etc.
            owned.add(name)
        return owned

    def _address_request(self, name: str, parsed: ParsedRandomAddress) -> _AddrReq:
        return _AddrReq(
            request_id=f"addr::{name}",
            addr_type=self._addr_type(parsed),
            size=self._bare_window_size(name, parsed),
            addr=self._addr_spec(parsed),
        )

    def _bare_window_size(self, name: str, parsed: ParsedRandomAddress) -> int:
        """Bytes the bare ``addr::`` recipe must own for its SameAs / OffsetFrom followers.

        A page that ``SameAs`` this recipe defaults its BACKING claim to pagesize when
        ``reserve_size`` is unset; riemap only folds that claim into the bare window when
        the footprint fits. Offset leaves need ``offset + leaf`` covered. Widen the
        declared ``;#random_addr(size=…)`` to that union so containment succeeds.

        Under two-stage without ``--reserve_partial_phys_memory``, named phys followers
        also widen by the g-stage leaf window (same policy as :meth:`_phys_reserve_size`).
        """
        need = parsed.size if parsed.size else 0x1000
        widen_gleaf = self.twostage and not self.featmgr.reserve_partial_phys_memory
        for (_lin, _map), ppm in self.pool.get_parsed_page_mappings().items():
            pagesize = ppm.final_pagesize if ppm.final_pagesize else RV.RiscvPageSizes.S4KB
            leaf = RV.RiscvPageSizes.memory(pagesize)
            if ppm.lin_addr_link is not None and ppm.lin_addr_link[0] == name:
                need = max(need, ppm.lin_addr_link[1] + leaf)
            elif ppm.lin_name == name:
                need = max(need, leaf, ppm.address_size or 0)
            gleaf_bytes = 0
            if widen_gleaf:
                gleaf = ppm.gstage_vs_leaf_final_pagesize or RV.RiscvPageSizes.S4KB
                gleaf_bytes = RV.RiscvPageSizes.memory(gleaf)
            if ppm.phys_addr_link is not None and ppm.phys_addr_link[0] == name:
                need = max(need, ppm.phys_addr_link[1] + leaf, gleaf_bytes)
            elif self._phys_name(ppm.lin_name, ppm) == name:
                need = max(need, leaf, ppm.phys_address_size or 0, gleaf_bytes)
        return need

    def _bare_modify_nonleaf_span(self, name: str) -> "int | None":
        """Largest ``modify_nonleaf_pt`` VS leaf-table span among anchors named ``name``.

        Free-draw pages ``SameAs(addr::{name})`` do not carry the span in their own
        ``and_mask``; the span folds onto the bare recipe so the window stays leaf-table aligned.
        """
        span = None
        for (_lin, _map), ppm in self.pool.get_parsed_page_mappings().items():
            if ppm.lin_name != name:
                continue
            pagesize = ppm.final_pagesize if ppm.final_pagesize else RV.RiscvPageSizes.S4KB
            s = self._modify_nonleaf_vs_span(ppm, pagesize)
            if s is not None:
                span = max(span or 0, s)
        return span

    @staticmethod
    def _addr_type(parsed: ParsedRandomAddress) -> RV.AddressType:
        """Map a parsed ``type=`` (an enum default or the parser's raw string) to an
        :class:`AddressType`; linear if declared linear, else physical (generator's
        handle_random_addr rule)."""
        is_linear = parsed.type == RV.AddressType.LINEAR or str(parsed.type).startswith("linear")
        return RV.AddressType.LINEAR if is_linear else RV.AddressType.PHYSICAL

    def _addr_spec(self, parsed: ParsedRandomAddress) -> RecipeAddrSpec:
        if parsed.fixed_addr is not None:
            return RecipeAddrSpec(exact=parsed.fixed_addr)
        if parsed.derive_from is not None:
            return self._derived_addr_spec(parsed)
        is_physical = self._addr_type(parsed) == RV.AddressType.PHYSICAL
        if parsed.custom_region is not None:
            if parsed.in_pma:
                raise ValueError(f"random_addr {parsed.name}: in_pma=1 cannot be combined with custom_region (region placement ignores the bound)")
            # custom_region constrains only the physical draw (generator handle_random_addr);
            # a linear custom_region is a no-op there, so fall through to a free draw.
            if is_physical:
                return RecipeAddrSpec(region=self.regions_by_id[self._custom_region_of(parsed.name)], and_mask=parsed.and_mask, or_mask=parsed.or_mask, bits=parsed.addr_bits)
        region_id = self._pma_region_of(parsed.name)
        if region_id is not None:
            return self._in_pma_addr_spec(region_id)
        qualifiers = set()
        # io/secure are physical address-map claims, stamped only on a PHYSICAL draw;
        # a LINEAR draw never gets either.
        if parsed.io and is_physical:
            qualifiers.add(RV.AddressQualifiers.ADDRESS_MMIO)
        elif is_physical and (self._draw_secure(parsed.secure, is_physical) or self._phys_needs_secure(parsed.name)):
            qualifiers.add(RV.AddressQualifiers.ADDRESS_SECURE)
        and_mask = parsed.and_mask
        # Free-draw SameAs pages leave the modify_nonleaf span out of their own VA
        # mask; the bare window carries it so the window stays leaf-table aligned for
        # those followers.
        if not is_physical:
            span = self._bare_modify_nonleaf_span(parsed.name)
            if span is not None:
                and_mask &= ~(span - 1) & 0xFFFFFFFFFFFFFFFF
        return RecipeAddrSpec(
            and_mask=and_mask,
            or_mask=parsed.or_mask,
            bits=parsed.addr_bits if parsed.addr_bits is not None else self._default_addr_bits(is_physical),
            qualifiers=qualifiers,
        )

    def _default_addr_bits(self, is_physical: bool) -> int:
        """The draw width a bare address inherits when it declares none: the physical
        address width, or the test's linear-address width (generator handle_random_addr
        defaults addr_bits to physical_addr_bits / linear_addr_bits)."""
        if is_physical:
            return self.featmgr.physical_addr_bits
        return RV.RiscvPagingModes.linear_addr_bits(self.featmgr.paging_mode)

    def _draw_secure(self, explicit_secure: bool, is_physical: bool) -> bool:
        """Whether a physical PA/address draw takes the secure address class.

        Explicit ``secure`` always qualifies; otherwise, in secure mode, a randomly
        placed physical address is promoted with ``secure_access_probability`` (the
        generator rolled this at every random PA/addr site). The run's seeded RNG drives
        the roll -- never the global ``random`` -- so it stays reproducible."""
        if explicit_secure:
            return True
        if is_physical and self.featmgr.secure_mode and self.rng is not None:
            return self.rng.with_probability_of(self.featmgr.secure_access_probability)
        return False

    def _derived_addr_spec(self, parsed: ParsedRandomAddress) -> RecipeAddrSpec:
        """Address derived from another random_addr's resolved value.

        A buddy (a single-bit ``not_mask`` equal to the size, over a fully-selecting
        all-ones and-mask, on a source that clears that bit) sits exactly one size
        above its source, so it becomes an ``OffsetFrom(+size)``. Any other fully
        deterministic derivation -- all-ones select mask, so every result bit is a
        function of the source -- becomes ``DerivedFrom((src ^ not_mask) | or_mask)``.
        A partial select mask pins the selected bits to the source and randomizes the
        unselected ones; that becomes a ``DerivedFrom`` carrying ``random_mask`` (the
        unselected bits), which the allocator resolves as a masked free draw. The draw's
        alignment mask comes from ``and_mask`` (the referenced random_addr's own mask,
        the generator's ``address_mask``).
        """
        if parsed.derive_from is None:
            raise ValueError("_derived_addr_spec requires parsed.derive_from to be set")
        src_id = self._derive_source_id(parsed.derive_from)
        if self._is_buddy(parsed):
            return RecipeAddrSpec(relation=RecipeOffsetFrom(src_id, delta=parsed.size))
        if parsed.derive_and_mask == 0xFFFFFFFFFFFFFFFF:
            return RecipeAddrSpec(relation=RecipeDerivedFrom(src_id, and_mask=parsed.derive_and_mask, or_mask=parsed.derive_or_mask, not_mask=parsed.derive_not_mask))
        bits = parsed.addr_bits if parsed.addr_bits is not None else self._default_addr_bits(self._addr_type(parsed) == RV.AddressType.PHYSICAL)
        random_mask = (~parsed.derive_and_mask) & ((1 << bits) - 1)
        return RecipeAddrSpec(
            relation=RecipeDerivedFrom(src_id, and_mask=parsed.derive_and_mask, or_mask=parsed.derive_or_mask, not_mask=parsed.derive_not_mask, random_mask=random_mask),
            and_mask=parsed.and_mask,
            bits=bits,
        )

    def _derive_source_id(self, name: str) -> str:
        """The recipe id a ``derive_from`` source name resolves to.

        A free-draw ``;#random_addr`` is a bare ``addr::<name>`` recipe (the page
        SameAs / OffsetFrom it), so derivation targets that bare id.

        A region-constrained or page-owned name with no bare request (``in_pma`` /
        physical ``custom_region``, ``__auto_phys_*``, or a plain phys_name) has no
        bare request, so the relation must target the page slot:

        - a page mapping's VA (``"lin"``) -> the page's primary owning-map VA slot;
        - a page mapping's PA (``"phys"``) -> the owning page's PA/destination slot,
          resolved the same, mode-dependent way Phase-2 emission mints it (see
          :meth:`_resolve_phys_slot`), so the relation target and the emitted page id
          are guaranteed identical.
        """
        if self.pool.parsed_random_addr_exists(addr_name=name):
            parsed = self.pool.get_parsed_addr(name)
            if not self._addr_is_region_constrained(parsed):
                return f"addr::{name}"
        entry = self._page_addr_domains().get(name)
        if entry is not None:
            domain, owner = entry
            if domain == "lin":
                return owner
            return self._resolve_phys_slot(owner)
        return f"addr::{name}"

    def _reqs_by_id(self) -> "Dict[str, _PageReq]":
        """page_id -> _PageReq over every map's page requests (mirrors build_page_tables)."""
        if self._reqs_by_id_cache is None:
            self._reqs_by_id_cache = {req.page_id: req for reqs in self.page_reqs_by_map.values() for req in reqs}
        return self._reqs_by_id_cache

    def _mode_of_page(self) -> "Dict[str, RV.RiscvPagingModes]":
        """page_id -> owning-map paging mode (mirrors build_page_tables' mode_of_req)."""
        if self._mode_of_page_cache is None:
            self._mode_of_page_cache = {req.page_id: self._paging_mode_for_map(map_name) for map_name, reqs in self.page_reqs_by_map.items() for req in reqs}
        return self._mode_of_page_cache

    def _resolve_phys_slot(self, owner_page_id: str) -> str:
        """The page id holding a page's physical (PA/HPA) address, resolved the same way
        Phase-2 emission mints it: follow PA-side ``SameAs`` aliases to the canonical
        owner, then pick its PA slot id from that owner's map paging mode + two-stage."""
        owner = _pa_owner(owner_page_id, self._reqs_by_id())
        mode = self._mode_of_page().get(owner)
        return _pa_slot_id(owner, mode, self.twostage)

    def _is_buddy(self, parsed: ParsedRandomAddress) -> bool:
        """Mirror of Generator._is_pinned_buddy_shape on the parsed objects."""
        if parsed.derive_from is None:
            return False
        not_mask = parsed.derive_not_mask
        if parsed.derive_and_mask != 0xFFFFFFFFFFFFFFFF or parsed.derive_or_mask != 0:
            return False
        if not_mask == 0 or (not_mask & (not_mask - 1)) != 0 or not_mask != parsed.size:
            return False
        if not self.pool.parsed_random_addr_exists(addr_name=parsed.derive_from):
            return False
        source = self.pool.get_parsed_addr(parsed.derive_from)
        return source.size == parsed.size and (source.and_mask & not_mask) == 0

    # -- reserved spans ------------------------------------------------------

    def _build_reserved_spans(self) -> None:
        for res in self.pool.get_parsed_res_mems().values():
            start = res.start_addr
            if isinstance(start, str):
                start = int(start, 16)
            if start is None:
                continue
            addr_type = RV.AddressType.PHYSICAL if res.addr_type == "physical" else RV.AddressType.LINEAR
            self.reserved_spans.append((addr_type, start, res.size))

    # -- helpers -------------------------------------------------------------

    def _page_id(self, lin_name: str, map_name: str) -> str:
        """Globally unique id for a page recipe (riescue-internal only)."""
        return f"page::{map_name}::{lin_name}"

    def _pbmt_ncio_value(self) -> Choice[int]:
        """The per-page NC/IO domain for ``--pbmt_ncio``.

        RiescueD owns the policy and makes a seeded preferred draw for every eligible
        leaf.  RieMap receives both legal values so pages sharing structural state can
        agree without discarding an explicit test force.  Where no sharing pressure
        exists, the preferred values provide seeded per-page variety.
        """
        assert self.rng is not None
        if self.pbmt_rng is None:
            raise RuntimeError("PBMT policy RNG is unavailable")
        preferred = 1 if self.pbmt_rng.with_probability_of(50) else 2
        return Choice(preferred=preferred, alternatives=(2 if preferred == 1 else 1,))

    def _attrs(self, ppm: ParsedPageMapping) -> Dict[str, object]:
        attrs: Dict[str, object] = {}
        for key in _BASE_ATTRS:
            val = getattr(ppm, key, None)
            if val is not None:
                attrs[key] = val
        # G-stage leaf/non-leaf forcing (v_leaf_gnonleaf=0, g_nonleaf_gnonleaf=1, ...).
        # The builder's resolve layer needs these to materialize the forced PTE; a
        # value equal to the bit's default is a no-op there, so forwarding every set
        # field is safe. None-valued forms stay absent so the resolver skips them.
        for key in _GSTAGE_FORCING_ATTRS:
            val = getattr(ppm, key, None)
            if val is not None:
                attrs[key] = val
        # Single-/VS-stage per-level PTE forcing (``{base}_level{n}``, e.g. v_level1=0 for
        # an invalid VS non-leaf PTE). generator.randomize_pt_attrs expands the scenario's
        # {base}/{base}_nonleaf shorthand into these concrete keys on the ppm; forward them
        # so the builder can materialize the forced PTE. Without this forwarding the
        # VS-stage non-leaf forcing never reaches a PTE and the walk wrongly succeeds
        # (hypervisor_paging_faults_vs SID_HPBVMS_018_nonleaf). None-valued forms stay
        # absent so the resolver skips them.
        for base in resolve.LEVEL_TYPES:
            for lvl in resolve.LEVELS:
                key = f"{base}_level{lvl}"
                val = getattr(ppm, key, None)
                if val is not None:
                    attrs[key] = val
        # PBMT NC/IO randomization is RiescueD policy: under --pbmt_ncio,
        # every mapped leaf gets a random NC(1)/IO(2) memory type.
        # Sections opt out simply by not going through here.
        #
        # The concrete leaf key has to be written too, not just the bare base:
        # ParsedPageMapping declares ``pbmt_level0..4`` with a DEFAULT of 0, the loop above
        # forwards every non-None field, and ``pt_node_levels_with_leaf`` lets an explicit
        # ``pbmt_level{leaf}`` beat a bare base -- so on a 4 KiB page the forwarded default 0
        # silently won and the bare roll never reached a PTE. Overwriting the level key
        # makes the roll beat every pbmt_level* for every page in every map.
        if self.featmgr.pbmt_ncio and self.rng is not None:
            pbmt = self._pbmt_ncio_value()
            attrs["pbmt"] = pbmt
            attrs[f"pbmt_level{RV.RiscvPageSizes.pt_leaf_level(ppm.final_pagesize or RV.RiscvPageSizes.S4KB)}"] = pbmt
        # Svadu A/D randomization, likewise RiescueD policy. With Svadu the hardware may set
        # A/D itself, so a leaf starting at 0 is legal and worth exercising; without it a 0
        # would fault on first access, so the bits stay absent and the builder's a_level0
        # default of 1 wins. A ppm that forced either bit keeps it -- ``_BASE_ATTRS`` above
        # already copied it, and this must not overwrite an explicit force.
        #
        # ROLL ONCE PER PAGE, which is what this placement gives: a NAPOT 64 KiB page builds
        # 16 PTAttrs objects and ``_pack_leaf`` compares their packed values, so a per-PTE roll
        # would emit a malformed NAPOT block and spurious "leaf PTE slot conflict" errors.
        if self.featmgr.svadu and self.rng is not None:
            for base in ("a", "d"):
                if attrs.get(base) is None:
                    attrs[base] = 1 if self.rng.with_probability_of(50) else 0
        return attrs

    # ``ParsedPageMapping``'s declared default for each g-stage forcing knob. A knob still
    # holding its default was never named by the test, so it is a seeded default rather than a
    # force -- see :meth:`_gstage_forced_attrs`.
    _GSTAGE_KNOB_DEFAULTS = {f.name: f.default for f in dataclasses.fields(ParsedPageMapping) if f.name in _GSTAGE_FORCING_ATTRS}

    def _gstage_forced_attrs(self, ppm: ParsedPageMapping) -> Dict[str, object]:
        """The g-stage forcing knobs this page's test actually named.

        The g-stage slice of :meth:`_attrs` minus every knob still sitting at its
        ``ParsedPageMapping`` default. Feeds the ``PTGPage`` declarations only; ``attrs`` keeps
        the full set, so the default identity matrix the walker seeds every g-stage identity
        from (``Pagetables._emit_gstage_identity``) is untouched and no emitted PTE moves.

        A test that names a knob and gives it the bit's default value is indistinguishable from
        one that stays silent, and reads as silent here. That costs nothing: the value it asks
        for is the value it gets either way -- what a PTGPage buys over the default is a frame
        of this page's own, and asking for the default needs no frame of its own.
        """
        out: Dict[str, object] = {}
        for key in _GSTAGE_FORCING_ATTRS:
            val = getattr(ppm, key, None)
            if val is not None and val != self._GSTAGE_KNOB_DEFAULTS.get(key):
                out[key] = val
        return out

    # -- g-stage attribute forcing -------------------------------------------

    def _resolve_gstage_forcing(self) -> None:
        """Expand each two-stage page's g-stage leaf/non-leaf forcing shorthand and record
        the g-stage PT-node reservations the engine consumes.

        This is RiescueD attribute translation (the same ``resolve`` layer json_frontend
        uses), not geometry: it materializes the concrete ``{base}_level{vs}_glevel{g}`` PTE
        keys in ``attrs`` and nothing else. The g-stage leaf/non-leaf pagesizes go in as
        inputs (they select which level each shorthand force lands on) and travel to the
        engine on the objects that own them -- the GPA/HPA ``Page`` for the leaf, each
        non-leaf node's ``PTGPage`` (or pinned frame ``Page``) for the non-leaf.
        The builder's attribute-aware coloring provides VS-stage sibling
        isolation. Pure address geometry -- bit widths, pagesize alignment, cross-space
        ``SameAs`` caps, secure promotion -- stays the engine's authority."""
        if not self.twostage:
            return
        config = PagingParams(
            physical_addr_bits=self.featmgr.physical_addr_bits,
            priv_mode=self.featmgr.priv_mode,
        )
        g_mode = self.featmgr.paging_g_mode
        for map_name, reqs in self.page_reqs_by_map.items():
            paging_mode = self._paging_mode_for_map(map_name)
            for req in reqs:
                # Both dicts get the SAME expansion, so a force lands on the same concrete key
                # in each: ``attrs`` is the full matrix the walker seeds identities from, and
                # ``gstage_forced_attrs`` the test-named subset the PTGPages declare.
                for attrs in (req.attrs, req.gstage_forced_attrs):
                    resolve.apply_gstage_leaf_nonleaf_attrs(
                        attrs=attrs,
                        config=config,
                        paging_mode=paging_mode,
                        paging_g_mode=g_mode,
                        final_pagesize_vs=req.pagesize,
                        gstage_vs_leaf_pagesize=req.gstage_vs_leaf_size or RV.RiscvPageSizes.S4KB,
                        gstage_vs_nonleaf_pagesize=req.gstage_vs_nonleaf_size or RV.RiscvPageSizes.S4KB,
                    )
                # After the expansion: ParsedPageMapping declares every ``pbmt_*_g*`` knob with
                # a DEFAULT of 0, and the expansion materializes those defaults into concrete
                # keys -- rolling first would just be overwritten by them. ``gstage_forced_attrs``
                # now holds exactly the TEST-NAMED concrete forces (``_gstage_forced_attrs``
                # drops any knob still at its default), so it doubles as the skip list.
                self._roll_gstage_pbmt(req, paging_mode, g_mode)

    def _roll_gstage_pbmt(self, req: "_PageReq", paging_mode: RV.RiscvPagingModes, g_mode: RV.RiscvPagingModes) -> None:
        """Declare the NC/IO memory type on this page's GPA -> HPA g-stage leaf.

        Under ``--pbmt_ncio``, RiescueD declares PBMT policy for every eligible
        leaf, including identity leaves fronting g-stage page-table pages. The channel is the
        ``{base}_level{vs}_glevel{g}`` key ``resolve.gstage_leaf_attrs_for`` copies onto the
        declared GPA -> HPA leaf, at the g-stage leaf level ``gstage_vs_leaf_size`` selects (the
        same rule ``setup_uwrx_bit`` uses to seed U/R/W/X/A/D). PBMT is leaf-only, so no other
        g-level gets a value.

        The identities fronting this page's VS-stage PT-node frames use the same
        declaration channel as explicit g-stage forcing: a ``PTGPage`` on each VS node.
        Policy values are ``Choice`` domains, so compatible NC/IO requests may share;
        a scalar test-named force remains exact and coloring-visible.

        Sections reach this method (``_build_section_requests`` emits ``_PageReq`` s into the
        same map lists) but never :meth:`_attrs`:
        a section's own VS-stage leaf stays ordinary memory -- an instruction fetch out of an
        IO-typed page does not survive the ISS -- while the g-stage leaf fronting it gets the
        selected memory type.

        Skipped for a bare-VS page (VS paging disabled): it owns no VS table, and the page is
        its own g-stage leaf, whose PBMT comes from the bare roll in :meth:`_attrs`.
        """
        if not self.featmgr.pbmt_ncio or self.rng is None:
            return
        if paging_mode == RV.RiscvPagingModes.DISABLE or g_mode == RV.RiscvPagingModes.DISABLE:
            return
        vs_leaf = RV.RiscvPageSizes.pt_leaf_level(req.pagesize)
        g_leaf = RV.RiscvPageSizes.pt_leaf_level(req.gstage_vs_leaf_size or RV.RiscvPageSizes.S4KB)
        key = f"pbmt_level{vs_leaf}_glevel{g_leaf}"
        # Whatever the test itself named wins; the expansion above has already landed those,
        # and ``gstage_forced_attrs`` holds exactly the test-named concrete forces.
        if key not in req.gstage_forced_attrs:
            # ``attrs`` only: the GPA -> HPA leaf is declared explicitly
            # (``resolve.gstage_leaf_pt_node_levels`` reads ``attrs``), so it needs no PTGPage --
            # and a PTGPage at the VS leaf level would describe the identity of a "frame" that
            # is really the data page, colliding with that declaration.
            req.attrs[key] = self._pbmt_ncio_value()

        # Each non-leaf VS node owns a frame whose GPA is itself translated by the
        # g-stage non-leaf pagesize. Put the choice on the PTGPage channel as well as the full attrs
        # matrix; unlike a scalar force, compatible Choice domains do not require
        # separate frames.
        frame_g_leaf = RV.RiscvPageSizes.pt_leaf_level(req.gstage_vs_nonleaf_size or RV.RiscvPageSizes.S4KB)
        for vs_level in range(vs_leaf + 1, RV.RiscvPagingModes.max_levels(paging_mode)):
            frame_key = f"pbmt_level{vs_level}_glevel{frame_g_leaf}"
            if frame_key in req.gstage_forced_attrs:
                continue
            choice = self._pbmt_ncio_value()
            req.attrs[frame_key] = choice
            req.gstage_forced_attrs[frame_key] = choice


# -- recipe -> Page/Mapping emission ------------------------------------------
#
# The translator's recipes carry unpinned address specs: they declare only what a page
# *is* (pagesize, relations, qualifiers, reservation bytes) and let the builder's
# geometry pre-pass derive every paging-mode value -- VA/PA bit widths, pagesize
# alignment, and cross-space ``SameAs`` width caps -- so that resolution lives in
# exactly one place.


def _vs_gstage_pt_nodes(req: "_PageReq", pinned_frames: Optional[Dict[Any, str]] = None, max_levels: int = 0, source_levels: int = 0) -> Dict[Any, PTNode]:
    """Build VS ``pt_nodes`` and their synthesized g-stage frame declarations.

    ``source_levels`` removes attributes for levels absent from the source mode. A pinned
    frame replaces the corresponding :class:`PTGPage`; its explicit GPA-to-HPA mapping owns
    that frame's g-stage attributes and geometry.
    """
    leaf_level = RV.RiscvPageSizes.pt_leaf_level(req.pagesize)
    levels = resolve.pt_node_levels_with_leaf(req.attrs, leaf_level)
    if source_levels:
        # Parsed attributes cover every architectural level; emit only levels this source
        # mode can walk.
        levels = {level: bits for level, bits in levels.items() if level < source_levels}
    pt_nodes = resolve.pt_nodes_from_levels(levels, leaf_level)
    # ``gstage_forced_attrs``, not ``attrs``: only a TEST-NAMED force belongs on a PTGPage. A
    # PTGPage is coloring-visible -- ``builder._color_sig`` signs it, splitting the page into a
    # node of its own so no sibling's default identity can win their shared frame. Built from
    # ``attrs`` it would sign the seeded defaults too, and those carry the page's random
    # g-stage non-leaf pagesize (``randomize_gstage_pt_attrs`` keys the g-level off
    # ``pt_leaf_level(pagesize)``), so two pages with identical g-stage intent would demand
    # separate nodes -- unsatisfiable, and reported as a collision, when both VAs are fixed
    # inside one node the way a virtualized test's ACLINT/IMSIC pages are.
    frame_size: Any = req.gstage_vs_nonleaf_size
    if len(req.gstage_vs_nonleaf_size_options) > 1:
        frame_size = Choice(
            preferred=req.gstage_vs_nonleaf_size_options[0],
            alternatives=req.gstage_vs_nonleaf_size_options[1:],
        )
    resolve.attach_gstage_ptgpages(pt_nodes, req.gstage_forced_attrs, leaf_level, frame_size, max_levels)
    for key, node in list(pt_nodes.items()):
        if not isinstance(node.page, PTGPage):
            continue
        frame_level = leaf_level if key is LEAF else key
        if _frame_key(frame_level, leaf_level) in (pinned_frames or {}):
            pt_nodes[key] = PTNode(attrs=node.attrs)
    return pt_nodes


def _frame_key(level: int, leaf_level: int) -> Any:
    """The ``pt_nodes`` key a PT-node frame at ``level`` occupies (leaf-folded)."""
    return LEAF if level == leaf_level else level


def _root_entry_span(mode: "RV.RiscvPagingModes") -> "int | None":
    """Bytes translated by one root PTE in ``mode``.

    ``modify_pt`` owns this complete VS root slot so runtime rewrites cannot share any
    ancestor with another walk: 1 GiB for Sv39, 512 GiB for Sv48, and 256 TiB for Sv57.
    RieMap intentionally uses its narrowed g-stage geometry here; x4 widening is out of scope.
    """
    levels = RV.RiscvPagingModes.max_levels(mode)
    if mode == RV.RiscvPagingModes.DISABLE or levels == 0:
        return None
    bits = RV.RiscvPagingModes.index_bits(mode, levels - 1)
    if bits is None:
        return None
    return 1 << bits[1]


def _next_level_span(mode: "RV.RiscvPagingModes", pagesize: "RV.RiscvPageSizes") -> "int | None":
    """Bytes of one G-stage level above ``pagesize`` -- the ``modify_leaf_pt`` /
    ``modify_nonleaf_pt`` exclusivity span.

    A 4 KiB leaf owns a 2 MiB GPA span, a 2 MiB leaf owns 1 GiB, and so on. Page-size
    picking already excludes geometries whose bump would be the root-level pagesize
    (``exclude_after_bump``), so this never demands a full Sv57 256 TiB root entry just
    because a test rewrites a G-stage PTE.
    """
    if mode == RV.RiscvPagingModes.DISABLE:
        return None
    bumped = RV.RiscvPagingModes.next_pt_level_pagesize(mode, pagesize)
    return RV.RiscvPageSizes.memory(bumped)


def _dst_page_id(page_id: str) -> str:
    return page_id + "__dst"


def _pa_slot_id(owner: str, mode: "RV.RiscvPagingModes | None", twostage: bool) -> str:
    """The page id that holds the physical/GPA address a mapping to ``owner`` targets.

    A pure function of the owner id, the owner map's paging mode, and whether the test is
    two-stage, so the recipe-build relation target (:meth:`_derive_source_id`) and the
    Phase-2 emission page id resolve identically. A disabled single-stage owner *is* its
    physical page (it lives in the leaf domain with id == its request id, no ``__dst``);
    every other owner has a separate ``__dst`` destination page."""
    if not twostage and mode == RV.RiscvPagingModes.DISABLE:
        return owner
    return _dst_page_id(owner)


def _rewrite_pa_relation(spec: RecipeAddrSpec, page_ids: set, mode_of_req: Dict[str, RV.RiscvPagingModes], twostage: bool) -> RecipeAddrSpec:
    """A PA-side ``OffsetFrom`` names a *logical page*; retarget it at that page's
    destination page (its PA/GPA lives there). A relation on a bare address id
    (``addr::`` / ``secaddr::``) is already the physical page and is left untouched.
    """
    rel = spec.relation
    if isinstance(rel, RecipeOffsetFrom) and rel.target in page_ids and rel.target in mode_of_req:
        return dataclasses.replace(spec, relation=RecipeOffsetFrom(_phys_page_id(rel.target, mode_of_req, twostage), rel.delta))
    return spec


def _pa_owner(page_id: str, reqs_by_id: Dict[str, _PageReq]) -> str:
    """Follow PA-side ``SameAs`` aliases to the page that actually owns the physical
    draw. An alias (and a chain of aliases) resolves to the canonical page whose
    ``__dst`` page holds the real PA/GPA; the alias reuses that destination."""
    seen: set = set()
    cur = page_id
    while cur in reqs_by_id and cur not in seen:
        rel = reqs_by_id[cur].pa.relation
        if isinstance(rel, RecipeSameAs) and rel.target in reqs_by_id:
            seen.add(cur)
            cur = rel.target
        else:
            break
    return cur


def _va_family_root(page_id: str, reqs_by_id: Dict[str, _PageReq]) -> str:
    """Follow VA-side relations to the anchor of this page's offset family.

    A ``;#page_mapping(lin_name=anchor+0xN000, ...)`` child has its VA forced to
    ``OffsetFrom(addr::anchor, delta)`` (or, in synthetic tests, ``OffsetFrom`` a
    sibling page). It shares the anchor's page-table nodes at every level whose index
    the delta does not move. The family -- SameAs base plus every such child -- is
    therefore ONE owner of those nodes, and :func:`_modify_pt_nodes` keys its pinned
    frames by this root so the members share them instead of each demanding its own
    (which no pointer PTE can satisfy). The PA-side analogue is :func:`_pa_owner`."""
    seen: set = set()
    cur = page_id
    while cur in reqs_by_id and cur not in seen:
        rel = reqs_by_id[cur].va.relation
        if isinstance(rel, (RecipeSameAs, RecipeOffsetFrom)) and rel.target.startswith("addr::"):
            window = rel.target
            for pid, req in reqs_by_id.items():
                r = req.va.relation
                if isinstance(r, RecipeSameAs) and r.target == window:
                    return pid
            return window
        if isinstance(rel, RecipeOffsetFrom) and rel.target in reqs_by_id:
            seen.add(cur)
            cur = rel.target
            continue
        break
    return cur


def _phys_page_id(owner: str, mode_of_req: Dict[str, RV.RiscvPagingModes], twostage: bool) -> str:
    """The page id holding the physical/GPA address a mapping to ``owner`` targets
    (Phase-2 emission wrapper over :func:`_pa_slot_id`, keyed by the ``mode_of_req`` map)."""
    return _pa_slot_id(owner, mode_of_req.get(owner), twostage)


def _emit_vs_two_stage(req, map_name, mode, g_mode, page_ids, reqs_by_id, mode_of_req, twostage, va_geom, pa_geom, pages, mappings, identity, window_sink):
    """The VS-enabled two-stage shape: VA -> GPA (VS-stage leaf) + GPA -> HPA (g-stage leaf).

    Identity is not a flag: the GPA is constrained ``SameAs`` a physical HPA page (GPA ==
    HPA), and the builder's structural emitter makes the VS table's intermediate g-stage PT
    nodes identity. The consumer-declared GPA -> HPA leaf then wins over synthesized identity
    for the final page (``_explicit_target_gpas``). The canonical owner (following PA-side
    ``SameAs`` aliases/copies) owns one shared HPA + GPA page and the single GPA -> HPA leaf;
    aliases/copies reuse them and add only their VA -> GPA leaf. An ``identity`` VA source
    is itself drawn physically (its own destination pins ``SameAs`` it), so VA == GPA == HPA."""
    # VA source page stays in its VS map.
    if identity:
        src = _RawPageSpec(
            space_name=map_name,
            pagesize=req.pagesize,
            addr=pa_geom,
            reserve_size=req.pa_reserve_size,
            reserve_granule=req.va_reserve_granule,
        )
    else:
        src = _RawPageSpec(
            space_name=map_name,
            pagesize=req.pagesize,
            addr=va_geom,
            reserve_size=req.va_reserve_size,
            reserve_granule=req.va_reserve_granule,
        )
    pages[req.page_id] = src

    owner = _pa_owner(req.page_id, reqs_by_id)
    # The HPA page uses the ``__dst`` id so a linked child's PA-side OffsetFrom (rewritten by
    # _rewrite_pa_relation to _phys_page_id) still lands on its parent's physical page.
    hpa_id = _phys_page_id(owner, mode_of_req, twostage)
    if mode_of_req.get(owner) == RV.RiscvPagingModes.DISABLE:
        # Bare-VS owner (its map walks hgatp directly): the owner's source page already
        # lives in the GPA domain and owns the GPA -> HPA leaf, so the alias's VS leaf
        # targets it directly -- there is no separate __gpa page. (A bare owner is never
        # this req: owner != req.page_id, so the creation branches below stay idle.)
        gpa_id = owner
    else:
        gpa_id = owner + "__gpa"
    # The g-stage leaf level derives from this page's pagesize (see pagetables.py
    # generate_pt_constraints), so the GPA/HPA pages take the g-stage leaf pagesize.
    gstage_leaf_ps = req.gstage_vs_leaf_size or RV.RiscvPageSizes.S4KB

    # The g-stage frame leaf's forced per-level PTE bits as pt_nodes so coloring separates
    # conflicting g-stage siblings: the
    # leaf's U/R/W/X/A/D default to 1 (a g-stage leaf is always user-reachable), with this
    # page's VS glevel forcing overlaid, remapped onto the g-stage leaf's own levels. Secure
    # rides the HPA frame's ADDRESS_SECURE qualifier (set on ``pa_geom`` by ``_pa_spec``).
    secure_qual = {RV.AddressQualifiers.ADDRESS_SECURE} if bool(req.attrs.get("secure")) else set()
    gstage_pt_nodes = resolve.pt_nodes_from_levels(
        resolve.gstage_leaf_pt_node_levels(
            req.attrs,
            vs_paging_mode=mode,
            gstage_mode=g_mode,
            vs_pagesize=req.pagesize,
            gstage_vs_leaf_size=req.gstage_vs_leaf_size,
            gstage_vs_nonleaf_size=req.gstage_vs_nonleaf_size,
            secure=bool(req.attrs.get("secure")),
        ),
        RV.RiscvPageSizes.pt_leaf_level(gstage_leaf_ps),
    )

    # ``modify_leaf_pt`` may rewrite PTEs in the final GPA -> HPA walk. Own one G-stage
    # level above the leaf. HPA stays ordinary backing.
    anchor_pa = not isinstance(req.pa.relation, RecipeOffsetFrom)
    leaf_granule = _next_level_span(g_mode, gstage_leaf_ps) if req.modify_leaf_pt and anchor_pa else None

    if identity:
        # Identity section (VA == PA): the VA source is identity-drawn (VA == its own PA);
        # the GPA and HPA are pinned SameAs it so VA == GPA == HPA. (owner == req.page_id
        # for an identity page: its PA carries a constraint, never a SameAs alias.) The HPA
        # frame carries the secure qualifier so the g-stage leaf lands bit 55.
        if gpa_id not in pages:
            pages[gpa_id] = _RawPageSpec(
                space_name=GSTAGE_MAP_ID,
                pagesize=gstage_leaf_ps,
                addr=RecipeAddrSpec(relation=RecipeSameAs(req.page_id)),
                reserve_size=req.pa_reserve_size,
                reserve_granule=leaf_granule,
            )
            pages[hpa_id] = _RawPageSpec(
                space_name=_PHYS,
                pagesize=gstage_leaf_ps,
                addr=RecipeAddrSpec(
                    relation=RecipeSameAs(gpa_id),
                    qualifiers=secure_qual,
                ),
                reserve_size=req.pa_reserve_size,
            )
            mappings.append(_RawMapping(src_id=gpa_id, dst_id=hpa_id, pt_nodes=gstage_pt_nodes))
    elif owner == req.page_id and hpa_id not in pages:
        # Shared physical HPA page (the real PA) and a shared GPA page pinned SameAs it.
        hpa_geom = _rewrite_pa_relation(pa_geom, page_ids, mode_of_req, twostage)
        pages[hpa_id] = _RawPageSpec(
            space_name=_PHYS,
            pagesize=gstage_leaf_ps,
            addr=hpa_geom,
            reserve_size=req.pa_reserve_size,
        )
        pages[gpa_id] = _RawPageSpec(
            space_name=GSTAGE_MAP_ID,
            pagesize=gstage_leaf_ps,
            addr=RecipeAddrSpec(relation=RecipeSameAs(hpa_id)),
            reserve_size=req.pa_reserve_size,
            reserve_granule=leaf_granule,
        )
        # The consumer-declared GPA -> HPA g-stage leaf (HPA == GPA -> identity).
        mappings.append(_RawMapping(src_id=gpa_id, dst_id=hpa_id, pt_nodes=gstage_pt_nodes))
    elif leaf_granule is not None and gpa_id in pages:
        pages[gpa_id] = dataclasses.replace(
            pages[gpa_id],
            reserve_granule=leaf_granule,
        )

    # VA -> GPA (VS-stage leaf). The g-stage geometry that sizes the VS table's structural
    # identity PT nodes rides those nodes themselves (a PTGPage per non-leaf level, from
    # ``_vs_gstage_pt_nodes``); the leaf's rides ``gpa_id``/``hpa_id``'s own pagesize above.
    # Source-stage forced per-level PTE bits ride as pt_nodes so coloring isolates conflicting
    # VS siblings, along with modify_pt and the g-stage glevel forces the walker synthesizes.
    frames = _modify_pt_nodes(req, map_name, mode, twostage, (pa_geom if identity else va_geom).exact is not None, pages, mappings, window_sink, reqs_by_id, g_mode)
    mappings.append(
        _RawMapping(
            src_id=req.page_id,
            dst_id=gpa_id,
            pt_nodes=_vs_gstage_pt_nodes(req, frames, RV.RiscvPagingModes.max_levels(mode), source_levels=RV.RiscvPagingModes.max_levels(mode)),
            pt_node_frames=frames,
        )
    )


def _emit_page_mapping(req, map_name, mode, g_mode, twostage, page_ids, reqs_by_id, mode_of_req, pages, mappings, identity, window_sink):
    """Turn one logical page request into recipe ``_RawPageSpec`` s + ``_RawMapping`` s.

    Four shapes: single-stage (VA -> PA leaf), single-stage disabled (one physical page,
    VA == PA, no table), VS-enabled two-stage (VA -> GPA + GPA -> HPA with GPA == HPA, see
    :func:`_emit_vs_two_stage`), and bare VS-stage (the guest walks hgatp directly: a
    GPA -> HPA leaf in map_hyp). ``identity`` marks a VA == PA page (an identity section):
    the source is given its physical spec and its destination is pinned ``SameAs`` it."""
    va_geom = req.va
    pa_geom = req.pa

    if not twostage and mode == RV.RiscvPagingModes.DISABLE:
        # Single-stage disabled: one physical page, VA == PA, no page table. It has no
        # own page table but may still be the destination of an enabled map's copy.
        pages[req.page_id] = _RawPageSpec(space_name=_PHYS, pagesize=req.pagesize, addr=pa_geom, reserve_size=req.pa_reserve_size)
        return

    if twostage and mode != RV.RiscvPagingModes.DISABLE:
        # VS-enabled two-stage: two mappings (VA -> GPA -> HPA), GPA constrained SameAs HPA.
        _emit_vs_two_stage(req, map_name, mode, g_mode, page_ids, reqs_by_id, mode_of_req, twostage, va_geom, pa_geom, pages, mappings, identity, window_sink)
        return

    # Bare VS-stage (twostage, DISABLE mode: the guest walks hgatp directly) or single-stage:
    # one source -> leaf mapping.
    src_space = GSTAGE_MAP_ID if twostage else map_name
    src_granule = req.va_reserve_granule
    if twostage and req.modify_leaf_pt and not isinstance(req.pa.relation, RecipeOffsetFrom):
        # Bare VS: the source page *is* the GPA; exclusivity is one level above its leaf.
        gleaf = req.gstage_vs_leaf_size or req.pagesize
        src_granule = _next_level_span(g_mode, gleaf)

    # Source (VA / guest) page.
    if identity:
        src = _RawPageSpec(
            space_name=src_space,
            pagesize=req.pagesize,
            addr=pa_geom,
            reserve_size=req.pa_reserve_size,
            reserve_granule=src_granule,
        )
    else:
        src = _RawPageSpec(
            space_name=src_space,
            pagesize=req.pagesize,
            addr=va_geom,
            reserve_size=req.va_reserve_size,
            reserve_granule=src_granule,
        )
    pages[req.page_id] = src

    # Destination (PA / HPA) leaf page + the id the mapping targets. An identity page ties
    # its own destination to itself (VA == PA); an alias / copy reuses the canonical owner's
    # destination; otherwise this page owns a fresh destination.
    owner = _pa_owner(req.page_id, reqs_by_id)
    dst_id = _phys_page_id(owner, mode_of_req, twostage)
    if identity:
        pages[dst_id] = _RawPageSpec(space_name=_PHYS, pagesize=req.pagesize, addr=RecipeAddrSpec(relation=RecipeSameAs(req.page_id)), reserve_size=req.pa_reserve_size)
    elif owner == req.page_id and dst_id not in pages:
        pages[dst_id] = _RawPageSpec(space_name=_PHYS, pagesize=req.pagesize, addr=_rewrite_pa_relation(pa_geom, page_ids, mode_of_req, twostage), reserve_size=req.pa_reserve_size)

    # Single-stage / bare-VS leaf. Source-stage forced per-level bits ride as pt_nodes so
    # coloring isolates conflicting siblings; a single-stage modify_pt page pins its PT-node
    # frames (a bare-VS g-stage source builds no VS table, so it declares none).
    frames = _modify_pt_nodes(req, map_name, mode, twostage, (pa_geom if identity else va_geom).exact is not None, pages, mappings, window_sink, reqs_by_id, g_mode)
    mappings.append(
        _RawMapping(
            src_id=req.page_id,
            dst_id=dst_id,
            # A bare-VS source's level keys are the NOMINAL indices of its absent VS stage,
            # re-interpreted downstream rather than addressed in its own g-stage tree, so only
            # the single-stage form has a level range to trim to.
            pt_nodes=_vs_gstage_pt_nodes(req, frames, source_levels=(0 if twostage else RV.RiscvPagingModes.max_levels(mode))),
            pt_node_frames=frames,
        )
    )


def _modify_pt_nodes(
    req: _PageReq,
    map_name: str,
    mode: "RV.RiscvPagingModes",
    twostage: bool,
    src_exact: bool,
    pages: Dict[str, _RawPageSpec],
    mappings: List["_RawMapping"],
    window_sink: List[Tuple[str, int, str, str]],
    reqs_by_id: Dict[str, _PageReq],
    g_mode: "RV.RiscvPagingModes" = RV.RiscvPagingModes.DISABLE,
) -> Dict[Any, str]:
    """Declare the PT-node frames + read-back windows for a ``modify_pt`` /
    ``modify_nonleaf_pt`` map, returning the ``{level_or_LEAF: frame_id}`` frames to pin.

    A ``modify_pt`` test writes its own live page-table entries at runtime through the
    symbols ``{lin}__pt_level{N}`` / ``{lin}__pt_level{N}__phys``. RieMap has no knowledge
    of that read-back: RiescueD declares it entirely with ordinary primitives.

    For a ``modify_pt`` map, at every walk level ``L`` (leaf..root) this pins the node's
    frame to a declared :class:`Page` ``frame_L`` (``pt_nodes[L] = PTNode(page=frame_L)``,
    the LEAF sentinel at the leaf level so it merges with the leaf PTE bits) and declares
    an ordinary leaf window mapping ``window_va_L -> frame_L`` in the same space, so a
    runtime ld/sd through ``window_va_L`` reads/writes that level's table frame. The root
    frame is ONE shared page per address space (the root table is shared by construction --
    a single satp/vsatp); the frames below the root belong to one OFFSET FAMILY (a page plus
    every page whose VA is ``OffsetFrom`` it -- see :func:`_va_family_root`), whose members
    share them because their forced VAs share the nodes. ``modify_nonleaf_pt`` pins the same
    frames (it never addresses a slot, so it declares no windows): its ``{base}_nonleaf_g*``
    forcing invalidates the g-stage translation of the frame holding the LEAF VS-stage PTE,
    which faults every page whose leaf PTE shares that frame -- so that frame must be owned
    too, not just leaf+1..root.

    A frame lives in the physical leaf domain for a single-stage map and in the GPA domain
    (``GSTAGE_MAP_ID``) for a VS-stage map -- the builder auto-identity-maps a ``Stage.G``
    frame to its HPA, so a VS-stage window is a guest VA -> GPA (frame) -> HPA walk. A
    bare-VS g-stage source builds no VS table (``mode`` DISABLE), so it
    declares no windows. A frame carrying forced
    g-stage PTE bits declares its own GPA -> HPA mapping rather than taking that plain
    identity (:func:`_declare_frame_gstage_leaf`).

    An *exact* source VA already fixes every level index (coloring never moves it), so a
    pure ``modify_nonleaf_pt`` (isolation-only) needs no frame pin there.
    A ``modify_pt`` map always needs its frames regardless of
    exactness (they are the window targets + read-back handles); the root is shared, and a
    fixed-VA sibling that shares a deeper prefix would surface a clear RieMap frame
    conflict."""
    if mode == RV.RiscvPagingModes.DISABLE:
        return {}
    modify_pt = bool(req.attrs.get("modify_pt"))
    modify_nonleaf = bool(req.modify_nonleaf_pt)
    if not (modify_pt or modify_nonleaf):
        return {}
    if src_exact and not modify_pt:
        return {}
    max_levels = RV.RiscvPagingModes.max_levels(mode)
    root_level = max_levels - 1
    leaf_level = RV.RiscvPageSizes.pt_leaf_level(req.pagesize)
    if leaf_level > root_level:
        return {}
    # A frame lives in the GPA domain for a VS-stage map, the physical leaf domain otherwise.
    frame_space = GSTAGE_MAP_ID if twostage else _PHYS
    # Both flavours own every level's frame (leaf..root); only modify_pt windows them.
    frame_levels = list(range(leaf_level, root_level + 1))
    window_levels = list(frame_levels) if modify_pt else []
    # A linked child (VA ``OffsetFrom`` its anchor, e.g. page 2 of a page-crossing num_pages
    # region) declares NO read-back windows: its runtime read-back symbol would be
    # ``{lin}__pt_level{N}`` with ``lin`` = ``anchor+0xNNN`` -- a ``+`` is illegal in an
    # assembler label. Such a child is never the target of a ``read_pte``/``write_pte`` anyway
    # (scenarios reference the Memory object, i.e. the anchor page; the runtime reaches a
    # child's PTE from the anchor's VA plus the ``napot_offset`` byte offset), so the windows
    # are dead and skipping them avoids a malformed equate.
    if isinstance(req.va.relation, RecipeOffsetFrom):
        window_levels = []

    # The frames belong to the whole OFFSET FAMILY, not to one page. A child's VA is forced to
    # ``anchor + delta``, so it shares the anchor's node at every level the delta does not move
    # -- one pointer PTE cannot name two frames, so per-page frames are unsatisfiable (16 pages
    # 0x1000 apart demanded 16 level-1 tables behind one root slot). Keying by the family root
    # makes the members pin the SAME frames, which is idempotent in the walker. Every value
    # taken off the request is the ANCHOR's: emission order must not decide the shared frame's
    # geometry or its g-stage attributes.
    owner = _va_family_root(req.page_id, reqs_by_id)
    owner_req = reqs_by_id.get(owner, req)

    frames: Dict[Any, str] = {}
    for level in frame_levels:
        if level == root_level:
            # Shared root frame (one satp/vsatp per space, so it can carry no per-page g-stage
            # geometry) -- and no g-stage force ever names it, see _declare_frame_gstage_leaf.
            frame_id = f"__ptroot::{map_name}"
            frame_ps: RV.RiscvPageSizes = RV.RiscvPageSizes.S4KB
        else:
            frame_id = f"{owner}__ptframe{level}"
            # Under g-stage a VS PT-node frame's *g-stage* geometry is the page's g-stage
            # non-leaf pagesize -- exactly what the engine's auto (unpinned) frame draw uses
            # (``Pagetables._gstage_nonleaf_geometry``, reading the PTGPage this pin replaces).
            # A superpage there is load-bearing: a misaligned-superpage g-stage test misaligns
            # that very PTE's PPN.
            frame_ps = (owner_req.gstage_vs_nonleaf_size or RV.RiscvPageSizes.S4KB) if twostage else RV.RiscvPageSizes.S4KB
        if frame_id not in pages:
            # ``modify_nonleaf_pt`` may rewrite PTEs in the g-stage walk translating the
            # frame holding the VS leaf PTE. Own one G-stage level above that frame's
            # geometry on the frame GPA only; its HPA remains ordinary backing.
            granule = None
            if twostage and modify_nonleaf and _frame_key(level, leaf_level) is LEAF:
                granule = _next_level_span(g_mode, frame_ps)
            pages[frame_id] = _RawPageSpec(
                space_name=frame_space,
                pagesize=frame_ps,
                addr=RecipeAddrSpec(),
                reserve_granule=granule,
            )
            if twostage:
                _declare_frame_gstage_leaf(
                    owner_req,
                    level,
                    frame_id,
                    pages,
                    mappings,
                    identity_only=level == root_level,
                )
        frames[_frame_key(level, leaf_level)] = frame_id

    for level in window_levels:
        frame_id = frames[_frame_key(level, leaf_level)]
        window_id = f"{req.page_id}__ptwin{level}"
        if window_id in pages:
            continue
        pages[window_id] = _RawPageSpec(space_name=map_name, pagesize=RV.RiscvPageSizes.S4KB, addr=RecipeAddrSpec(and_mask=0xFFFFFFFFFFFFF000))
        mappings.append(
            _RawMapping(
                src_id=window_id,
                dst_id=frame_id,
                pt_nodes={LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1})},
            )
        )
        window_sink.append((req.page_id, level, window_id, frame_id))
    return frames


def _declare_frame_gstage_leaf(
    req: _PageReq,
    level: int,
    frame_id: str,
    pages: Dict[str, _RawPageSpec],
    mappings: List["_RawMapping"],
    *,
    identity_only: bool = False,
) -> None:
    """Declare a pinned PT-node frame's own GPA -> HPA g-stage mapping, carrying the g-stage
    PTE bits this page forces on that frame.

    The frame at ``level`` holds the level-``level`` VS-stage PTEs, and the resolved
    ``{base}_level{vs}_glevel{g}`` grammar names it by the level of the pointer PTE that
    *targets* it (``vs == level + 1``) -- so ``v_nonleaf_gleaf=0`` on a 4 KiB page (vs == 1)
    invalidates the g-stage leaf of the frame holding the LEAF VS-stage PTE, which is what an
    implicit-PTW guest-page-fault test needs. The caller never routes the shared root frame
    here: it is one table per space, so no single page may force its g-stage translation.

    An *unpinned* frame takes those bits from a :class:`~riescue.riemap.request.PTGPage` on the
    VS node, because RieMap allocates and identity-maps that frame itself. A pinned frame is a
    real declared :class:`~riescue.riemap.request.Page`, and RieMap synthesizes no identity
    leaf for a GPA a consumer mapping already translates
    (:meth:`PageTableBuilder._explicit_target_gpas`) -- so the forced bits must ride the
    frame's OWN mapping, which also replaces the plain valid identity
    :meth:`PageTableBuilder._prepare_pinned_frames` would otherwise auto-declare. Without a
    force there is nothing to say and that auto identity stands.

    The g-stage leaf defaults to a valid identity (U comes from the engine's always-user
    g-stage default) with the forced bits overlaid; a force on a g-stage NON-leaf level lands
    on that level's pointer PTE."""
    matrix = {} if identity_only else resolve.gstage_frame_pt_node_levels(req.attrs, level + 1)
    hpa_id = frame_id + "__hpa"
    if hpa_id in pages:
        return
    frame_ps = pages[frame_id].pagesize
    pages[hpa_id] = _RawPageSpec(space_name=_PHYS, pagesize=frame_ps, addr=RecipeAddrSpec(relation=RecipeSameAs(frame_id)), reserve_size=pages[frame_id].reserve_size)
    g_leaf_level = RV.RiscvPageSizes.pt_leaf_level(frame_ps)
    pt_nodes: Dict[Any, PTNode] = {}
    for g_level, bits in matrix.items():
        pt_nodes[_frame_key(g_level, g_leaf_level)] = PTNode(attrs=dict(bits))
    leaf_attrs: Dict[str, int] = {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}
    forced_leaf = pt_nodes.get(LEAF)
    if forced_leaf is not None:
        leaf_attrs.update(forced_leaf.attrs)
    pt_nodes[LEAF] = PTNode(attrs=leaf_attrs)
    mappings.append(_RawMapping(src_id=frame_id, dst_id=hpa_id, pt_nodes=pt_nodes))


def _materialize(page_specs: Dict[str, _RawPageSpec], mapping_specs: List[_RawMapping], spaces_by_name: Dict[str, Space], phys: Space) -> Tuple[Dict[str, Page], List[Mapping]]:
    """Resolve every recipe into a real, frozen :class:`Page` / :class:`Mapping`.

    A topological pass: a recipe whose relation target (if any) is already built gets
    materialized, which readies the recipes waiting on it in turn. Recipe declaration
    order is not significant -- a recipe may reference one declared later in the test
    file; this resolves purely by readiness, mirroring the allocator's own topological
    resolution at solve time. Unresolvable input is diagnosed by kind: a relation
    target no recipe declares is a *missing target*, and recipes still waiting once
    nothing remains to ready them form a *dependency cycle*.
    """

    def _resolve_relation(rel: RecipeRelation) -> Relation:
        """Convert a recipe-stage relation (string recipe id) into a riemap relation
        (object :class:`Page` reference). The string id never crosses into RieMap."""
        target = built[rel.target]
        if isinstance(rel, RecipeSameAs):
            return SameAs(target)
        if isinstance(rel, RecipeOffsetFrom):
            return OffsetFrom(target, delta=rel.delta)
        return DerivedFrom(target, and_mask=rel.and_mask, or_mask=rel.or_mask, not_mask=rel.not_mask, random_mask=rel.random_mask)

    # A relation target no recipe declares can never resolve -- name it up front
    # rather than stalling on it below.
    missing = []
    for id_, spec in page_specs.items():
        rel = spec.addr.relation
        if rel is not None and rel.target not in page_specs:
            missing.append(f"'{id_}' -> '{rel.target}'")
    if missing:
        raise ValueError(f"pt_request_builder: page/address relation targets are not declared: {', '.join(missing)}")

    # Reverse edges: a recipe waits on its (single) relation target, so building the
    # target readies every recipe recorded under it.
    dependents: Dict[str, List[str]] = {}
    for id_, spec in page_specs.items():
        rel = spec.addr.relation
        if rel is not None:
            dependents.setdefault(rel.target, []).append(id_)

    built: Dict[str, Page] = {}
    ready = deque(id_ for id_, spec in page_specs.items() if spec.addr.relation is None)
    while ready:
        id_ = ready.popleft()
        spec = page_specs[id_]
        rel = spec.addr.relation
        addr = spec.addr.to_addrspec(None if rel is None else _resolve_relation(rel))
        space = phys if spec.space_name == _PHYS else spaces_by_name[spec.space_name]
        built[id_] = Page(
            space=space,
            pagesize=spec.pagesize,
            addr=addr,
            reserve_size=spec.reserve_size,
            reserve_granule=spec.reserve_granule,
        )
        ready.extend(dependents.get(id_, ()))

    if len(built) < len(page_specs):
        # Every unbuilt recipe waits on another unbuilt one (a built target would have
        # readied it), so walking relation targets from any of them must revisit a
        # node -- that loop is a cycle.
        path: List[str] = []
        seen_at: Dict[str, int] = {}
        cursor = next(id_ for id_ in page_specs if id_ not in built)
        while cursor not in seen_at:
            seen_at[cursor] = len(path)
            path.append(cursor)
            rel = page_specs[cursor].addr.relation
            assert rel is not None  # a relation-free recipe is never left unbuilt
            cursor = rel.target
        cycle = " -> ".join(path[seen_at[cursor] :] + [cursor])
        raise ValueError(f"pt_request_builder: dependency cycle among page/address relations: {cycle}")

    mappings: List[Mapping] = []
    for m in mapping_specs:
        # Fold the resolved modify_pt / modify_nonleaf_pt frames into pt_nodes: a level with
        # a pinned frame Page gets that frame (merged onto any forced-attr PTNode there).
        pt_nodes = dict(m.pt_nodes)
        if built[m.src_id].space.stage is Stage.VS:
            leaf_level = RV.RiscvPageSizes.pt_leaf_level(built[m.src_id].pagesize)
            for level, node in list(pt_nodes.items()):
                if node.page is not None:
                    continue
                # LEAF and its numeric level name the same node. Keep the generated frame
                # declaration on the numeric key, where its resolved geometry already lives.
                if level is LEAF and leaf_level in pt_nodes:
                    continue
                pt_nodes[level] = dataclasses.replace(
                    node,
                    page=PTGPage(identity=True),
                )
            max_level = RV.RiscvPagingModes.max_levels(built[m.src_id].space.paging_mode) - 1
            for level in range(leaf_level + 1, max_level):
                pt_nodes.setdefault(
                    level,
                    PTNode(page=PTGPage(identity=True)),
                )
        for level, frame_id in m.pt_node_frames.items():
            existing = pt_nodes.get(level)
            attrs = existing.attrs if existing is not None else {}
            pt_nodes[level] = PTNode(page=built[frame_id], attrs=attrs)
        mappings.append(Mapping(src=built[m.src_id], dst=built[m.dst_id], pt_nodes=pt_nodes))
    return built, mappings


def _declare_vs_root_frames(trb: "PageTableRequestBuilder", phys: Space, page_specs: Dict[str, _RawPageSpec], mapping_specs: List[_RawMapping]) -> List[Tuple[Page, Page, Mapping]]:
    """Declare every unpinned VS map's root frame on its :class:`Space` (``root_frame``).

    A ``modify_pt`` / ``modify_nonleaf_pt`` map already pins its space's root frame --
    ``__ptroot::<map>`` rides in the mapping's ``pt_node_frames`` at the root level -- so
    such a space declares nothing here (the builder's root-pin detection sees the pin once
    the recipes are materialized). ``Space`` is frozen, so each root-bearing space is
    replaced in ``trb.spaces_by_name`` before materialization can reference it.

    The root pages and their GPA -> HPA identity mappings are constructed but NOT added to
    the builder; they are returned ((root_hpa, root_gpa, mapping), in space order) so the
    caller hands them over alongside the materialized declarations as ordinary mappings.
    """
    declarations: List[Tuple[Page, Page, Mapping]] = []
    for map_name, space in list(trb.spaces_by_name.items()):
        if space.stage is not Stage.VS:
            continue
        root_level = RV.RiscvPagingModes.max_levels(space.paging_mode) - 1
        if any(page_specs[m.src_id].space_name == map_name and root_level in m.pt_node_frames for m in mapping_specs):
            continue
        gspace = next(
            (
                trb.spaces_by_name[page_specs[m.dst_id].space_name]
                for m in mapping_specs
                if page_specs[m.src_id].space_name == map_name and page_specs[m.dst_id].space_name in trb.spaces_by_name and trb.spaces_by_name[page_specs[m.dst_id].space_name].stage is Stage.G
            ),
            None,
        )
        if gspace is None:
            continue
        root_hpa = Page(
            space=phys,
            pagesize=RV.RiscvPageSizes.S4KB,
            addr=AddrSpec(
                qualifiers={
                    RV.AddressQualifiers.ADDRESS_DRAM,
                }
            ),
        )
        root_gpa = Page(
            space=gspace,
            pagesize=RV.RiscvPageSizes.S4KB,
            addr=AddrSpec(relation=SameAs(root_hpa)),
        )
        declarations.append(
            (
                root_hpa,
                root_gpa,
                Mapping(
                    src=root_gpa,
                    dst=root_hpa,
                    pt_nodes={
                        LEAF: PTNode(
                            attrs={
                                "v": 1,
                                "r": 1,
                                "w": 1,
                                "x": 1,
                                "u": 1,
                                "a": 1,
                                "d": 1,
                            }
                        )
                    },
                ),
            )
        )
        trb.spaces_by_name[map_name] = dataclasses.replace(space, root_frame=root_gpa)
    return declarations


def _emit_address_request(req: _AddrReq, pages: Dict[str, _RawPageSpec]) -> None:
    """A bare address (no page table) becomes a bare ``Page``: physical addresses live
    in the engine's physical leaf domain; linear ones live in RiescueD's default map
    (map_os). A genuinely map-less "global" linear pool -- distinct from every VA
    space -- has no equivalent in the reference-based engine; map_os is the practical
    stand-in (the common case has exactly one VA space anyway). Under
    ``--private_maps`` the guarantee narrows: a bare linear scratch
    address is guaranteed unmapped in map_os but not independently reserved against
    every *other* private map's own VA pool."""
    space_name = _PHYS if req.addr_type == RV.AddressType.PHYSICAL else DEFAULT_MAP_ID
    pages[req.request_id] = _RawPageSpec(space_name=space_name, pagesize=RV.RiscvPageSizes.S4KB, addr=req.addr, reserve_size=req.size)


def _finalize_translation(trb: PageTableRequestBuilder, built: Dict[str, Page]) -> Translation:
    """Package the object-keyed :class:`Translation` from the translator's recipes
    plus the id -> Page map materialization produced."""
    translation = Translation()
    for map_name, space in trb.spaces_by_name.items():
        translation.space_names[space] = map_name
    translation.region_pma = dict(trb.region_pma)
    for page_id, names in trb.names_by_id.items():
        if page_id in built:
            translation.page_names[built[page_id]] = names
    for page_id, source in trb.page_source_by_id.items():
        if page_id in built:
            translation.page_source[built[page_id]] = source
    for page_id, name in trb.addr_names_by_id.items():
        if page_id in built:
            page = built[page_id]
            translation.addr_names[page] = name
            translation.addr_types[page] = trb.addr_types_by_id[page_id]
    for spec, page_id, is_page in trb.sections_raw:
        translation.sections.append((spec, built[page_id], is_page))
    for page in built.values():
        if page.addr.region is not None:
            translation.page_regions[page] = page.addr.region
    return translation


def build_page_tables(
    pool,
    featmgr,
    rng: RandNum,
    extra_reserved_spans: Optional[List[Tuple[RV.AddressType, int, int]]] = None,
    pma_region_bindings: "Optional[Dict[int, PmaRegionBinding]]" = None,
) -> Tuple[AllocationResult, Translation]:
    """Translate ``pool``'s parsed directives and drive the riemap mapping engine.

    RiescueD translates its parsed directives into recipes (neutral riemap
    constraints described with RiescueD-internal string ids), materializes them into
    the mapping engine's frozen :class:`~riescue.riemap.request.Page` s +
    :class:`~riescue.riemap.request.Mapping` s, and drives a
    :class:`~riescue.riemap.builder.PageTableBuilder` -- which owns its own
    bookkeeping and allocates in any order -- getting back an
    :class:`~riescue.riemap.result.AllocationResult`.

    RiescueD's identity G-stage is expressed as an identity-mapped ``map_hyp`` GPA space;
    every VS map's page maps VA -> GPA (== PA) into it. A single-stage map's PA lives in
    the engine's physical leaf domain (``builder.phys``); a bare VS-stage walks
    ``map_hyp`` (hgatp) directly.

    ``extra_reserved_spans`` are ``(type, start, size)`` intervals RiescueD has already
    committed to in its own space (section LMAs/VMAs, ``;#reserve_memory``, the
    code/runtime region) and declares to the builder as constraints to avoid.

    ``pma_region_bindings`` is the caller's id(PmaInfo) -> PmaRegionBinding provenance map
    (see generator.PmaRegionBinding), recorded when the caller pre-allocated in_pma
    addresses; it tells ``Translation.region_pma`` which regions are new (must be
    registered into ``pool.pma_regions`` on read-back) versus reused/adopted/shared
    (already tracked elsewhere). ``None`` defaults every region to registering once.

    Returns the :class:`AllocationResult` plus the object-keyed :class:`Translation` it
    was built from, so the caller can re-attach RiescueD symbol names to the allocated
    addresses.
    """
    trb = PageTableRequestBuilder(pool, featmgr, rng, pma_region_bindings=pma_region_bindings).build()

    twostage = trb.twostage
    g_mode = featmgr.paging_g_mode
    mode_by_map = {name: space.paging_mode for name, space in trb.spaces_by_name.items()}

    # Phase 1: build the mapping engine (spaces, regions). The page requests already
    # carry g-stage attribute forcing + reservation policy (resolved in the
    # translator); all paging geometry is derived by the builder's own pre-pass.
    builder = PageTableBuilder(
        rng=rng,
        memory=featmgr.memory,
        physical_addr_bits=featmgr.physical_addr_bits,
        reserved_spans=trb.reserved_spans + list(extra_reserved_spans or []),
        # The randomized decoy windows (as generic ExcludedRegion values). They carry
        # deliberately hostile attributes (no read permission, no AMO support), and a
        # masked one matches scattered windows all over the address space, so a page --
        # or a page-table frame, whose PTEs the walker reads implicitly -- landing in one
        # takes an access fault the test never asked for. The builder owns its own AddrGen,
        # so the exclusion has to be declared to it here. The list is held by reference so
        # loader truncation of non-emitted decoys remains visible.
        excluded_regions=pool.pma_random_exclusions,
        # Attr-aware coloring (always on) separates conflicting siblings (forced per-level
        # PTE bits + pinned modify_pt/modify_nonleaf frames ride in Mapping.pt_nodes).
    )

    # Phase 2: convert each logical page request recipe into page/mapping recipes.
    reqs_by_id = {req.page_id: req for reqs in trb.page_reqs_by_map.values() for req in reqs}
    page_ids = set(reqs_by_id)
    mode_of_req = {req.page_id: mode_by_map[map_name] for map_name, reqs in trb.page_reqs_by_map.items() for req in reqs}
    page_specs: Dict[str, _RawPageSpec] = {}
    mapping_specs: List[_RawMapping] = []
    # (src_page_id, level, window_id, frame_id) for every modify_pt read-back window.
    window_sink: List[Tuple[str, int, str, str]] = []
    for map_name, requests in trb.page_reqs_by_map.items():
        mode = mode_by_map[map_name]
        for req in requests:
            _emit_page_mapping(req, map_name, mode, g_mode, twostage, page_ids, reqs_by_id, mode_of_req, page_specs, mapping_specs, req.page_id in trb.identity_page_ids, window_sink)
    for addr_req in trb.address_reqs:
        _emit_address_request(addr_req, page_specs)

    # Phase 2.5: declare each unpinned VS map's root frame on its Space. The recipes
    # must exist first (a modify_pt family's root pin is read off them), and the
    # root-bearing spaces must be in place before any Page/Mapping references them.
    root_declarations = _declare_vs_root_frames(trb, builder.phys, page_specs, mapping_specs)

    # Phase 3: register spaces + regions, materialize every recipe into real
    # Page/Mapping objects (order-independent -- resolved by readiness, not by recipe
    # declaration order), and hand them to the builder.
    for space in trb.spaces_by_name.values():
        builder.add_space(space)
    for region in trb.regions:
        builder.add_region(region)
    built, mappings = _materialize(page_specs, mapping_specs, trb.spaces_by_name, builder.phys)
    for page in built.values():
        builder.add_page(page)
    for mapping in mappings:
        builder.add_mapping(mapping)
    for root_hpa, root_gpa, structural in root_declarations:
        builder.add_page(root_hpa)
        builder.add_page(root_gpa)
        builder.add_mapping(structural)

    translation = _finalize_translation(trb, built)
    for src_id, level, window_id, frame_id in window_sink:
        if src_id in built and window_id in built and frame_id in built:
            translation.pt_windows.setdefault(built[src_id], []).append((level, built[window_id], built[frame_id]))
    return builder.build(), translation
