# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Constraint-based page-table builder: spaces, pages, and mappings.

Spaces are address pools. The consumer allocates bare
:class:`~riescue.riemap.request.Page` s in those spaces and declares
:class:`~riescue.riemap.request.Mapping` s (``src_page -> dst_page``) for
VA->GPA->PA translations. :class:`PageTableBuilder` builds each space's page
table from the mappings that originate in it.

- A **space** (:class:`~riescue.riemap.request.Space`) is an address domain: a paging
  mode, its own address pool, and -- if it is the source of any mapping -- a page table
  and root. Its :class:`~riescue.riemap.request.Stage` (``SINGLE``/``VS``/``G``)
  selects the translation stage.
- ``builder.phys`` is the physical/leaf domain the builder creates: the address pool
  for real PA/HPA pages. Consumers reference this object for leaf pages instead of
  declaring their own physical space.
- Identity translation (output == input) is expressed by pinning the destination page
  ``SameAs`` the source (dst ``SameAs`` src). The builder recognizes that shape: the
  source is drawn once in the physical pool (physical qualifiers applied there, not on
  a linear draw) and the value is reserved in both the physical pool and the source
  space's pool -- one draw, two reserves, so VA == PA. Structural PT-node mappings that
  a source space allocates in its target domain are also identity by construction;
  only leaf mappings come from the consumer. RiescueD's identity G-stage uses the same
  pattern: each VA -> GPA mapping into the shared G space is paired with a GPA -> HPA
  leaf whose HPA is ``SameAs`` the GPA.

Declarations are frozen and carry no build state. ``build()`` builds a temporary
declaration-to-mutable-state table (keyed by object identity), then walks the
declaration graph by attribute access (``page.space``, ``mapping.src``,
``relation.target``).

Low-level machinery comes from :class:`~riescue.riemap.page_map.PageMap`, the walker,
and :class:`~riescue.riemap.addrgen.AddrGen`. Which space targets which is driven by
mappings.

Supports single-stage (VA->PA), two-stage (VA->GPA->PA) with identity or remapped
G-stage, and switch-hgatp (one VS-stage table under several G-stages, shared GPA to a
different PA in each). Structural identity for PT nodes is emitted by the builder for
every PT node a source space allocates in its target domain; a GPA that already has a
consumer leaf mapping uses that mapping instead.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import itertools
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum

from riescue.riemap.addrgen import AddrGen
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.allocator import (
    AllocationStrategy,
    AllocRequest,
    BatchAllocationStrategy,
    ClaimKind,
    SpanClaim,
)
from riescue.riemap.config import PagingParams
from riescue.riemap.memory import Memory
from riescue.riemap.layout import (
    IntentProvenance,
    NodeKey,
    TopologyConflict,
    TopologyPlan,
    plan_topology,
)
from riescue.riemap.page_map import Page as WalkerPage, PageMap
from riescue.riemap.pagetables import PTAttrs
from riescue.riemap.planner import (
    ChoicePolicy,
    JointPlanner,
    PlanBranch,
)
from riescue.riemap.request import Choice, AddrSpec, LEAF, Mapping, MemoryRegion, OffsetFrom, Page, PTGPage, PTNode, SameAs, Space, Stage, resolve_common_choice
from riescue.riemap.result import AllocationResult, PageMeta, SpaceResult
from riescue.riemap import resolve

log = logging.getLogger(__name__)


def canonical_va(raw: int, space_mode: RV.RiscvPagingModes, twostage: bool, g_mode: RV.RiscvPagingModes, gstage_source: bool = False) -> int:
    """Canonical form of a source page's drawn address (mirrors builder._canonicalize).

    A paging-enabled space sign-extends its VA; a bare (DISABLE) VS space that still
    walks a G-stage means the "VA" is really a GPA and is zero-extended to the G-stage
    input width; a bare single-stage space leaves the value untouched (VA == PA).

    A g-stage source space (``gstage_source``: a Stage.G domain, whether the guest
    walks it directly or it is the two-stage target of a VS walk; ``space_mode`` is
    the hgatp mode) draws a GPA, not a VA -- it is zero-extended to that g-stage
    input width, never sign-extended.
    """
    if gstage_source:
        return resolve.make_canonical_gpa(raw, space_mode)
    if space_mode != RV.RiscvPagingModes.DISABLE:
        return resolve.make_canonical_va(raw, space_mode)
    if twostage:
        return resolve.make_canonical_gpa(raw, g_mode)
    return raw


def canonical_pa(raw: int, twostage: bool, g_mode: RV.RiscvPagingModes) -> int:
    """Canonical form of a destination page's drawn address: a GPA is zero-extended to
    the G-stage width in a two-stage walk, a physical address is left as-is."""
    if twostage:
        return resolve.make_canonical_gpa(raw, g_mode)
    return raw


_PTGPAGE_BASES = frozenset(resolve.LEVEL_TYPES)

# Mapping nodes also support software-defined fields and the internal marker used to
# distinguish an explicitly forced bare-g-stage leaf U bit.
_MAPPING_ATTR_BASES = frozenset(resolve.LEVEL_TYPES) | {"_gstage_leaf_u"}

# Bare-g-stage declarations use nominal VS indices, up to Sv57.
_NOMINAL_VS_LEVELS = 5

# The g-stage leaf of a synthesized VS root frame (_synthesize_vs_root_frames). Every
# g-stage access is a user access, hence u=1; the rest matches the leaf level of the
# identity matrix the walker emits for an ordinary VS page-table node frame
# (``Pagetables._emit_gstage_identity``), so a synthesized root is indistinguishable
# from one a consumer declares by hand.
_VS_ROOT_IDENTITY_LEAF_ATTRS = {"v": 1, "r": 1, "w": 1, "x": 1, "u": 1, "a": 1, "d": 1}


def _declared_bases(node: PTNode) -> set:
    """Every PTE base name one node forces, across its attrs and its policy domain."""
    bases = set(node.attrs)
    if node.choice is not None:
        bases.update(base for option in node.choice.options for base in option)
    return bases


def _validate_mapping_pt_nodes(mapping: Mapping) -> None:
    """Validate node levels, attribute names, and duplicate leaf declarations."""
    mode = mapping.src.space.paging_mode
    max_levels = RV.RiscvPagingModes.max_levels(mode)
    # Bare-g-stage keys are nominal VS indices interpreted downstream, not levels in the
    # source's own tree.
    if mapping.src.space.stage is Stage.G:
        max_levels = max(max_levels, _NOMINAL_VS_LEVELS)
    leaf_level = RV.RiscvPageSizes.pt_leaf_level(mapping.src.pagesize)
    for key, node in mapping.pt_nodes.items():
        if not isinstance(node, PTNode):
            raise ValueError(f"Mapping.pt_nodes[{key!r}] must be a PTNode, got {node!r}")
        if key is not LEAF:
            if isinstance(key, bool) or not isinstance(key, int):
                raise ValueError(f"Mapping.pt_nodes key must be an int level or LEAF, got {key!r}")
            # A paging-DISABLE source has no page-table levels to validate, so it has no level range
            # to validate against.
            if mode != RV.RiscvPagingModes.DISABLE and not 0 <= key < max_levels:
                raise ValueError(f"Mapping.pt_nodes level {key} does not exist in the source space's " f"{resolve.PAGING_MODE_STR_MAP[mode].upper()} mode, whose levels are " f"0..{max_levels - 1}")
            # LEAF and its numeric level may describe disjoint aspects of one node, but
            # declarations targeting the same channel are ambiguous.
            if key == leaf_level and LEAF in mapping.pt_nodes:
                sentinel = mapping.pt_nodes[LEAF]
                doubled = _declared_bases(node) & _declared_bases(sentinel)
                if doubled:
                    raise ValueError(
                        f"Mapping.pt_nodes declares the leaf level twice: LEAF and level {key} " f"name the same node for a {mapping.src.pagesize.name} page and both " f"force {sorted(doubled)}"
                    )
                if node.page is not None and sentinel.page is not None and isinstance(node.page, PTGPage) == isinstance(sentinel.page, PTGPage):
                    raise ValueError(
                        f"Mapping.pt_nodes declares the leaf level twice: LEAF and level {key} "
                        f"name the same node and both declare its "
                        f"{'g-stage identity' if isinstance(node.page, PTGPage) else 'pinned frame'}"
                    )
        declared_attrs = [node.attrs]
        if node.choice is not None:
            declared_attrs.extend(node.choice.options)
        for attrs in declared_attrs:
            for base in attrs:
                if base not in _MAPPING_ATTR_BASES:
                    raise ValueError(f"Mapping.pt_nodes[{key!r}] attribute name must be a plain PTE base " f"{sorted(_MAPPING_ATTR_BASES)}, got {base!r}")


def _validate_ptgpage(ptg: PTGPage) -> None:
    """A :class:`PTGPage`'s ``pt_nodes`` is keyed by int g-level (or :data:`LEAF`) and holds
    :class:`PTNode` s whose ``attrs`` are plain base names. Reject the ``_level``/``_glevel``
    string grammar (that lives only at the consumer boundary, never inside a PTGPage), and a
    nested PTGPage (the g-stage is the last stage -- there is nothing below it to declare).

    A node's ``page`` pins the frame holding that g-level's PTEs. A g-stage table is walked by
    hardware in the HOST physical domain, so that frame must be a leaf-domain (physical) Page:
    a ``Stage.G`` page would name a GPA, which is not where a g-stage table lives."""
    for g_level, node in ptg.pt_nodes.items():
        if g_level is not LEAF and not isinstance(g_level, int):
            raise ValueError(f"PTGPage.pt_nodes key must be an int g-level or LEAF, got {g_level!r}")
        if not isinstance(node, PTNode):
            raise ValueError(f"PTGPage.pt_nodes[{g_level!r}] must be a PTNode, got {node!r}")
        declared_attrs = [node.attrs]
        if node.choice is not None:
            declared_attrs.extend(node.choice.options)
        for attrs in declared_attrs:
            for base in attrs:
                if base not in _PTGPAGE_BASES:
                    raise ValueError(f"PTGPage.pt_nodes[{g_level!r}] attrs/choice key must be a plain base name " f"{sorted(_PTGPAGE_BASES)}, got {base!r} (no _level/_glevel grammar)")
        if isinstance(node.page, PTGPage):
            raise ValueError(f"PTGPage.pt_nodes[{g_level!r}].page must not be a PTGPage: the g-stage is the last stage, it has no g-stage of its own")
        if node.page is not None and node.page.space.stage is not Stage.SINGLE:
            raise ValueError(f"PTGPage.pt_nodes[{g_level!r}].page must be a physical (leaf-domain) Page -- a g-stage table lives at an HPA -- got a {node.page.space.stage} page")


def _sig_atom(value: Any):
    # Any mapping (a plain dict, or the MappingProxyType a frozen declaration exposes)
    # signs as its sorted key/value atoms, so the atom stays comparable -- an
    # unwrapped mappingproxy would fall through to the scalar case and make
    # sorting signatures compare two dicts.
    if isinstance(value, collections.abc.Mapping):
        return ("dict", tuple((key, _sig_atom(item)) for key, item in sorted(value.items())))
    if isinstance(value, (tuple, list)):
        return ("seq", tuple(_sig_atom(item) for item in value))
    if hasattr(value, "value"):
        return ("enum", type(value).__name__, _sig_atom(value.value))
    return (type(value).__name__, value)


def _choice_sig(value: Any) -> tuple:
    """Stable signature for a hard scalar or a caller-declared choice domain."""
    if isinstance(value, Choice):
        return ("choice", tuple(sorted(_sig_atom(option) for option in value.options)))
    return ("exact", _sig_atom(value))


def _color_sigs_compatible(signatures: List[tuple]) -> bool:
    """Whether coloring signatures have a common value for every choice field.

    Hard parts of a signature must be identical. A ``Choice`` part is compatible with
    another choice or an exact declaration when their domains have a non-empty common
    intersection. Compatibility is checked across the whole bucket rather than pairwise:
    ``{a, b}``, exact ``a``, and exact ``b`` cannot all share one node.
    """
    parsed: List[Dict[tuple, set]] = []
    for signature in signatures:
        fields: Dict[tuple, set] = {}
        for part in signature:
            declaration = part[-1] if part else None
            if isinstance(declaration, tuple) and declaration and declaration[0] in {"choice", "exact"}:
                key = part[:-1]
                fields[key] = set(declaration[1]) if declaration[0] == "choice" else {declaration[1]}
            else:
                fields[part[:-1]] = {declaration}
        parsed.append(fields)

    keys = set().union(*(fields.keys() for fields in parsed))
    for key in keys:
        common = None
        for fields in parsed:
            domain = fields.get(key)
            if domain is None:
                base = key[0] if len(key) == 1 else None
                if base in resolve.LEVEL_TYPES:
                    domain = {("int", 1 if base == "v" else 0)}
                elif len(key) == 3 and key[0] == "g" and key[2] in resolve.LEVEL_TYPES:
                    g_level, base = key[1], key[2]
                    pagesizes = fields.get(
                        ("pagesize",),
                        {_sig_atom(RV.RiscvPageSizes.S4KB)},
                    )
                    defaults = set()
                    for atom in pagesizes:
                        if atom[0] == "enum" and atom[1] == "RiscvPageSizes":
                            pagesize = RV.RiscvPageSizes(atom[2][1])
                            leaf = RV.RiscvPageSizes.pt_leaf_level(pagesize)
                            defaults.add(
                                (
                                    "int",
                                    int(
                                        base == "v"
                                        or (
                                            g_level == leaf
                                            and base
                                            in {
                                                "u",
                                                "r",
                                                "w",
                                                "x",
                                                "a",
                                                "d",
                                            }
                                        )
                                    ),
                                )
                            )
                    domain = defaults or {("int", 0)}
                else:
                    # Structural declarations such as a pinned child frame
                    # constrain only the mappings that carry them.
                    continue
            if common is None:
                common = set(domain)
                continue
            intersection = common & domain
            if not intersection:
                return False
            common = intersection
    return True


def _common_declaration(values: List[Any]) -> Any:
    """Resolve a shared preference while preserving its remaining legal domain."""
    selected = resolve_common_choice(values)
    domains = [value.options if isinstance(value, Choice) else (value,) for value in values]
    common = [candidate for candidate in domains[0] if all(any(candidate == option for option in domain) for domain in domains[1:])]
    alternatives = tuple(candidate for candidate in common if candidate != selected)
    return Choice(preferred=selected, alternatives=alternatives) if alternatives else selected


def _ptgpage_sig(page: Optional[Union[Page, PTGPage]], page_seq=None) -> List[tuple]:
    """Coloring-signature parts from a VS node's declared g-stage identity.

    A :class:`PTGPage` on VS level ``L`` describes the g-stage identity of the frame the
    level-``L`` pointer PTE targets. That frame is shared by every mapping that shares the
    level-``L`` slot, and only the first walk to reach it emits its identity
    (``Pagetables._emit_gstage_identity`` runs once per frame; ``add_raw_pt_page``
    dedups by GPA). A mapping whose identity differs from a sibling's must not share the
    slot; otherwise the sibling's default identity is used and this mapping's
    ``{base}_(non)leaf_g(non)leaf`` force never reaches a PTE.

    An empty ``PTGPage`` -- the attribute-free one :func:`resolve.attach_gstage_ptgpages`
    puts on every non-leaf level to carry geometry -- contributes nothing, so it signs
    the same as an absent node and plain two-stage pages can share. Only a node that
    declares g-level PTE bits splits the signature; two mappings with the same bits still
    share.

    Exact PTE bits, exact geometry, and pinned g-stage table frames are hard
    requirements. Choice declarations sign by allowed domain (not preference), so
    compatible randomized policies still share and resolve jointly.
    """
    if not isinstance(page, PTGPage):
        return []
    parts: List[tuple] = [("pagesize", _choice_sig(page.pagesize))]
    if not page.identity:
        # Identity is the historical/default structural contract for generated
        # declarations, so keep an empty identity=True PTGPage sharing-compatible
        # with an absent declaration. A non-identity frame is materially different
        # and must never coalesce with either form.
        parts.append(("identity", False))
    for g_level, bits in sorted(page.level_attrs().items()):
        node = page.pt_nodes.get(g_level)
        if node is None and g_level == RV.RiscvPageSizes.pt_leaf_level(page.pagesize.preferred if isinstance(page.pagesize, Choice) else page.pagesize):
            node = page.pt_nodes.get(LEAF)
        choice_bases = {base for option in node.choice.options for base in option} if node is not None and node.choice is not None else set()
        for base in sorted(bits):
            val = bits[base]
            if val is None:
                continue
            if base in choice_bases and base not in node.attrs:
                continue
            pagesizes = page.pagesize.options if isinstance(page.pagesize, Choice) else (page.pagesize,)
            defaults = {int(base == "v" or (g_level == RV.RiscvPageSizes.pt_leaf_level(pagesize) and base in {"u", "r", "w", "x", "a", "d"})) for pagesize in pagesizes}
            if not isinstance(val, Choice) and defaults == {int(val)}:
                continue
            parts.append(("g", g_level, base, _choice_sig(val)))
        if node is not None and node.choice is not None:
            parts.append(("g_node_choice", g_level, _choice_sig(node.choice)))
        if node is not None and isinstance(node.page, Page):
            parts.append(("g_frame", g_level, page_seq(node.page) if page_seq is not None else id(node.page)))
    return parts


def _ptgpage_requires_isolation(
    page: PTGPage,
    pagesize: RV.RiscvPageSizes,
) -> bool:
    """Whether this declaration can emit non-default bits or pinned structure."""
    leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)

    def differs(attrs: Dict[str, Any], level: int) -> bool:
        for base, declaration in attrs.items():
            default = int(base == "v" or (level == leaf_level and base in {"u", "r", "w", "x", "a", "d"}))
            values = declaration.options if isinstance(declaration, Choice) else (declaration,)
            if any(value != default for value in values):
                return True
        return False

    for key, node in page.pt_nodes.items():
        level = leaf_level if key is LEAF else key
        # Leaf attributes live in distinct leaf PTEs and therefore do not require
        # separate intermediate tables. Only a non-leaf pointer declaration can
        # conflict merely because two identities share structural prefixes.
        if isinstance(node.page, Page) or (level > leaf_level and differs(node.attrs, level)):
            return True
        if level > leaf_level and node.choice is not None and any(differs(attrs, level) for attrs in node.choice.options):
            return True
    return False


class _Spill(Exception):
    """Internal coloring signal: a trie node at ``level`` needs more index buckets than
    its field can hold. Absorbed by the nearest single-bucket ancestor (which splits to
    make room); if none exists it reaches the root and becomes a hard exhaustion error."""

    def __init__(self, level: int):
        super().__init__(f"coloring spill at level {level}")
        self.level = level


@dataclass
class _PageState:
    """Mutable per-``Page`` build state, side-keyed by object identity since the
    declaration itself is frozen. Filled in over the build's phases; never read back
    by a consumer directly (``AllocationResult`` is the read-back surface)."""

    seq: int
    allocated: Optional[int] = None
    alloc_size: Optional[int] = None
    alloc_align: Optional[int] = None
    alloc_bits: Optional[int] = None
    # Overrides ``page.addr.qualifiers`` when a mapping forces the secure qualifier
    # onto a free-draw destination. AddrSpec remains immutable.
    qualifiers: Optional[set] = None
    # Attribute-based coloring (Phase A) folds chosen VA index bits into a free draw: clear the
    # colored index fields (``color_clear``), OR in the chosen values (``color_or``), and
    # flag the draw pinned (scarce reachable slots). All zero/False when coloring is off,
    # so ``_geom_addr`` stays byte-identical.
    color_or: int = 0
    color_clear: int = 0
    color_pinned: bool = False

    def copy(self) -> "_PageState":
        return dataclasses.replace(
            self,
            qualifiers=(None if self.qualifiers is None else set(self.qualifiers)),
        )


@dataclass
class _BuildRollback:
    """The declaration-time state ``build()`` derives from, captured before it runs.

    None of what ``build()`` does to the builder is idempotent: it synthesizes VS root
    frames and their g-stage leaves, promotes root and pinned :class:`PTNode` frames into
    ``pages``, colors per-page index bits, replays the reserved spans and per-space pools
    into the address generator, and half-populates the page maps. A build that raises
    therefore has to be undone completely, or a retry after the caller relaxes a
    constraint would hit ``page already added``, declare a second set of synthesized root
    frames, or draw from pools that already reserved the previous attempt's addresses.
    """

    pages: Tuple[Page, ...]
    mappings: Tuple[Mapping, ...]
    page_state: Dict[Page, _PageState]
    declared_roots: Dict[Space, Page]
    addrgen: AddrGen
    rng: RandNum
    next_seq: int


class PageTableBuilder:
    """Collect spaces, bare pages, and mappings; then allocate and build all tables."""

    def __init__(
        self,
        rng: RandNum,
        memory: Memory,
        physical_addr_bits: int = 56,
        allocator: Optional[AllocationStrategy] = None,
        reserved_spans: Optional[List[Tuple[RV.AddressType, int, int]]] = None,
        excluded_regions: Optional[List[Any]] = None,
    ):
        """``excluded_regions`` are physical windows no drawn address may land in
        (:class:`~riescue.riemap.addrgen.types.ExcludedRegion` values). A masked window is
        scattered across the whole address space, so it cannot be expressed as a reserved
        span; the draw filters against it instead. The list is held by reference -- a
        consumer that appends to or truncates it later still has the exclusion honored."""
        self.rng = rng
        self.memory = memory
        self.physical_addr_bits = physical_addr_bits
        self.allocator = allocator or BatchAllocationStrategy()
        self.addrgen = AddrGen(rng=rng, mem=memory, excluded_regions=excluded_regions)
        self.spaces: Dict[Space, None] = {}  # insertion-ordered set
        self.pages: Dict[Page, None] = {}
        self.mappings: List[Mapping] = []
        self._regions: List[MemoryRegion] = []
        self._reserved_spans: List[Tuple[RV.AddressType, int, int]] = list(reserved_spans or [])
        self._page_maps: Dict[Space, PageMap] = {}
        # Canonical (va, pa) of every installed mapping's source page, so a consumer can
        # read back the exact addresses the tree was built with (the raw allocated value
        # is pre-canonicalization). Keyed by the mapping's src page.
        self.mapping_addrs: Dict[Page, Tuple[int, int]] = {}
        # Source pages resolved to VA == PA identity pages; filled in build().
        self._identity_pages: set = set()
        # space -> the engine-declared root page-table frame page for it (_declare_root_frames).
        self._root_frames: Dict[Space, Page] = {}
        # space -> the consumer-declared root frame, adopted from ``Space.root_frame`` in
        # build() and read by the root-pin detection (_root_pin_frames / _root_pin_binding).
        self._declared_roots: Dict[Space, Page] = {}
        # Node input address, physical backing address, secure placement.
        self._planned_node_frames: Dict[
            NodeKey,
            Tuple[int, int, bool],
        ] = {}
        self._planned_node_pagesizes: Dict[NodeKey, RV.RiscvPageSizes] = {}
        self._topology_plan = TopologyPlan()
        self._planned_structural_identities: set[Tuple[Space, int, RV.RiscvPageSizes]] = set()
        self._planned_structural_targets: Dict[
            Tuple[Space, int, RV.RiscvPageSizes],
            Optional[int],
        ] = {}
        self._planned_explicit_spans: Dict[
            Space,
            List[Tuple[int, int]],
        ] = {}
        self._color_reachability_cache: Dict[tuple, bool] = {}
        self._color_ranges = {
            RV.AddressQualifiers.ADDRESS_SECURE: tuple((entry.start, entry.end) for entry in memory.secure_ranges),
            RV.AddressQualifiers.ADDRESS_MMIO: tuple((entry.start, entry.end) for entry in memory.io_ranges),
            RV.AddressQualifiers.ADDRESS_DRAM: tuple((entry.start, entry.end) for entry in memory.dram_ranges),
        }
        # Spaces that originate / receive a mapping. Cached once at build() start
        # (mappings are frozen thereafter by ``_built``), so the role predicates are set
        # lookups instead of O(mappings) scans. ``None`` before build() -> scan fallback.
        self._source_spaces: Optional[set] = None
        self._dst_spaces: Optional[set] = None
        self._built = False
        self._next_seq = 0
        self._page_state: Dict[Page, _PageState] = {}
        # The engine provides the physical leaf domain; consumers reference ``self.phys``
        # for their leaf pages rather than adding their own paging-DISABLE space.
        self.phys: Space = Space(paging_mode=RV.RiscvPagingModes.DISABLE, stage=Stage.SINGLE)
        self.add_space(self.phys)

    # -- collection --------------------------------------------------------

    def _reject_after_build(self, what: str) -> None:
        """A solved builder is a read-back surface, not a declaration surface.

        Every derived structure -- addresses, reservations, the topology plan, the emitted
        trees -- was solved against the declarations as they stood, so a later declaration
        would be silently absent from the result the caller already holds. ``build()`` on a
        failed attempt is retriable (see :class:`_BuildRollback`); on a successful one the
        declarations are final.
        """
        if self._built:
            raise RuntimeError(f"cannot {what} after build() has already built the page tables; " "declare a new PageTableBuilder instead")

    def add_space(self, space: Space) -> Space:
        self._reject_after_build("add a space")
        if space in self.spaces:
            raise ValueError(f"space already added: {space!r}")
        self.spaces[space] = None
        return space

    def add_page(self, page: Page) -> Page:
        self._reject_after_build("add a page")
        if isinstance(page, PTGPage):
            raise ValueError("PTGPage is usable only as a PTNode.page, never as an allocation Page (add_page)")
        if page in self.pages:
            raise ValueError(f"page already added: {page!r}")
        if page.space not in self.spaces:
            raise ValueError(f"page names unknown space: {page.space!r}")
        self.pages[page] = None
        self._page_state[page] = _PageState(seq=self._next_seq)
        self._next_seq += 1
        return page

    def add_mapping(self, mapping: Mapping) -> Mapping:
        self._reject_after_build("add a mapping")
        if isinstance(mapping.src, PTGPage) or isinstance(mapping.dst, PTGPage):
            raise ValueError("PTGPage is usable only as a PTNode.page, never as a Mapping src/dst")
        # ``src``/``dst`` are allocations, so they must already be in the solve: a page the
        # builder never saw draws no address, and the mapping would resolve against
        # ``None``. A pinned ``PTNode.page`` frame is different -- ``_prepare_pinned_frames``
        # promotes those during build() -- so only the two endpoints are required here.
        for role, page in (("src", mapping.src), ("dst", mapping.dst)):
            if page not in self.pages:
                raise ValueError(f"Mapping.{role} names an undeclared page that was not added to the " f"builder: {page!r}; call add_page() on it first")
        _validate_mapping_pt_nodes(mapping)
        for node in mapping.pt_nodes.values():
            if isinstance(node.page, PTGPage):
                _validate_ptgpage(node.page)
        self.mappings.append(mapping)
        return mapping

    def add_two_stage_mapping(
        self,
        *,
        va_page: Page,
        hpa_page: Page,
        gpa_space: Space,
        attrs: Dict[str, Any],
        vs_pagesize: RV.RiscvPageSizes,
        gstage_mode: RV.RiscvPagingModes,
        secure: bool = False,
        gstage_nonleaf_pagesize: Optional[RV.RiscvPageSizes] = None,
        pt_nodes: Optional[Dict[Any, PTNode]] = None,
    ) -> Page:
        """Declare a two-stage VA -> GPA -> HPA identity mapping in one call.

        Declares the usual virtualized-page shape: a physical HPA leaf, a GPA page
        pinned ``SameAs`` it (GPA == HPA), a VA -> GPA VS-stage mapping, and a
        GPA -> HPA g-stage leaf whose PTE attrs are the VS leaf's g-level forcing
        remapped onto the g-stage leaf's levels. Given the already-added VA source
        (``va_page``) and physical ``hpa_page``, this adds ``hpa_page``, creates the
        GPA page ``SameAs`` it in ``gpa_space``, adds both mappings, and derives
        g-stage leaf attrs via :func:`resolve.gstage_leaf_attrs_for`. Returns the new
        GPA page.

        G-stage leaf geometry is ``hpa_page.pagesize`` (inherited by the GPA page), not
        a separate parameter. ``gstage_nonleaf_pagesize`` is the geometry of the VS
        walk's synthesized non-leaf identity nodes -- created here, not represented by
        a caller-visible page -- and is written onto their :class:`PTGPage` s.

        ``attrs`` are the VS mapping's resolved base and g-stage forcing attributes.
        G-stage leaf attrs come from the same per-level expansion the VS mapping
        install uses; consumers do not re-run that expansion. Policy about which pages
        alias a shared HPA/GPA (identity section, bare-VS owner, etc.) stays with the
        consumer, which chooses when to call this.
        """
        self.add_page(hpa_page)
        gpa_page = self.add_page(
            Page(
                space=gpa_space,
                pagesize=hpa_page.pagesize,
                addr=AddrSpec(relation=SameAs(hpa_page)),
                reserve_size=hpa_page.reserve_size,
            )
        )
        # Build VS ``pt_nodes`` from ``attrs``: leaf and per-level PTE bits, secure from
        # ``hpa_page``'s ADDRESS_SECURE qualifier, and g-stage forces for synthesized
        # non-leaf identity nodes as :class:`PTGPage` on the matching VS ``PTNode``.
        # An explicit ``pt_nodes`` argument overlays those defaults (caller frames and
        # forces take precedence).
        vs_leaf_level = RV.RiscvPageSizes.pt_leaf_level(vs_pagesize)
        vs_pt_nodes = resolve.pt_nodes_from_levels(resolve.pt_node_levels_with_leaf(attrs, vs_leaf_level), vs_leaf_level)
        resolve.attach_gstage_ptgpages(vs_pt_nodes, attrs, vs_leaf_level, gstage_nonleaf_pagesize, RV.RiscvPagingModes.max_levels(va_page.space.paging_mode))
        for key, node in (pt_nodes or {}).items():
            generated = vs_pt_nodes.get(key)
            if node.page is None and generated is not None and isinstance(generated.page, PTGPage):
                node = dataclasses.replace(
                    node,
                    page=generated.page,
                )
            vs_pt_nodes[key] = node
        vs_mapping = Mapping(src=va_page, dst=gpa_page, pt_nodes=vs_pt_nodes)
        self.add_mapping(vs_mapping)
        # GPA -> HPA forced per-level PTE bits become pt_nodes so attribute-based
        # coloring can isolate conflicting g-stage siblings; secure comes from
        # ``hpa_page``'s ADDRESS_SECURE qualifier. Level keys stay as ints (leaf need
        # not fold onto LEAF: _color_node_at reads it directly).
        g_levels = resolve.gstage_leaf_pt_node_levels(
            attrs,
            vs_paging_mode=va_page.space.paging_mode,
            gstage_mode=gstage_mode,
            vs_pagesize=vs_pagesize,
            gstage_vs_leaf_size=hpa_page.pagesize,
            gstage_vs_nonleaf_size=gstage_nonleaf_pagesize,
            secure=secure,
        )
        gstage_pt_nodes = {level: PTNode(attrs=dict(a)) for level, a in g_levels.items()}
        self.add_mapping(Mapping(src=gpa_page, dst=hpa_page, pt_nodes=gstage_pt_nodes))
        return gpa_page

    def _install_pt_node_attrs(self, page: WalkerPage, mapping: Mapping, twostage: bool, g_mode: RV.RiscvPagingModes, leaf_level: int) -> None:
        """Fold a mapping's declared ``pt_nodes`` (plus g-stage/secure/NAPOT signals)
        onto the walker page's attr set.

        Each node's plain base bits become this level's ``{base}_level{L}`` attrs. A
        pinned frame's resolved base is passed to the walker (keyed by the pt_level that
        allocates it -- the child table at level L+1) so the level-L node is placed in
        that frame. Bases the leaf node omits fall back to the page's default base bit.
        A ``PTGPage`` node goes to ``page.gstage_nodes`` (g-stage identity forces) with
        pinned g-stage table frames on ``page.gstage_node_pins``; the VS frame's GPA is
        never pinned. NAPOT N and secure are applied last."""
        leaf_provided: set = set()
        for key, node in mapping.pt_nodes.items():
            level = leaf_level if key is LEAF else key
            if node.choice is not None:
                if level == leaf_level:
                    for base, value in node.choice.preferred.items():
                        page.attrs[f"{base}_level{level}"] = value
                else:
                    page.node_choices[level] = node.choice
            for base, value in node.attrs.items():
                if isinstance(value, Choice) and level == leaf_level:
                    value = value.preferred
                page.attrs[f"{base}_level{level}"] = value
                if level == leaf_level:
                    leaf_provided.add(base)
            if isinstance(node.page, PTGPage):
                # A PTGPage and a concrete Page have the same node-level
                # meaning: both describe the frame holding this level's PTEs.
                # The walker reaches that frame through the pointer one level
                # above, so key the runtime policy by that parent level.
                parent_level = level + 1
                page.gstage_nodes[parent_level] = node.page
                page.gstage_node_declarations[parent_level] = node.page
                pins = self._gstage_node_pins(node.page, twostage, g_mode)
                if pins:
                    page.gstage_node_pins[parent_level] = pins
            elif node.page is not None:
                input_addr, backing_addr = self._resolved_frame_binding(
                    node.page,
                    twostage,
                    g_mode,
                )
                page.pinned_frame_bases[level + 1] = input_addr
                page.pinned_frame_backings[level + 1] = backing_addr

        # A base the leaf node did not force takes the page's default base bit
        # (or its level-0 default). A forced leaf level takes precedence.
        for base in resolve.LEVEL_TYPES:
            if base in leaf_provided:
                continue
            fallback = page.attrs.get(base)
            if fallback is None:
                fallback = page.attrs.get(f"{base}_level0")
            if fallback is not None:
                page.attrs[f"{base}_level{leaf_level}"] = fallback

        # NAPOT 64KB: N is on unless the leaf node forces n=0. Write the *resolved* value
        # either way -- ``_create_pt_leaf`` reads the base ``n`` key and treats a missing
        # one as "auto-on", so leaving it unset on a forced n=0 silently turned the N bit
        # back on (the per-level ``n_level{leaf}`` key alone never reached that check).
        if page.pagesize == RV.RiscvPageSizes.S64KB:
            leaf = mapping.pt_nodes.get(LEAF) or mapping.pt_nodes.get(leaf_level)
            value = 1 if leaf is None else leaf.attrs.get("n", 1)
            page.attrs["n"] = value.preferred if isinstance(value, Choice) else int(value)

    def _resolved_frame_binding(
        self,
        frame: Page,
        twostage: bool,
        g_mode: RV.RiscvPagingModes,
    ) -> Tuple[int, int]:
        """Return the pointer/root input address and physical table backing."""

        allocated = self._page_state[frame].allocated
        if allocated is None:
            raise RuntimeError("page-table frame was not allocated")
        input_addr = canonical_pa(allocated, twostage, g_mode)
        if twostage and frame.space.stage is not Stage.G:
            raise ValueError("a concrete VS page-table frame must be a Stage.G Page " "with an explicit GPA-to-HPA mapping; use " "PTGPage(identity=True) for a RieMap-generated identity frame")
        if frame.space.stage is not Stage.G:
            return input_addr, allocated

        destinations = {self._page_state[mapping.dst].allocated for mapping in self.mappings if mapping.src is frame}
        destinations.discard(None)
        if not destinations:
            raise ValueError("VS page-table frame has no explicit GPA-to-HPA mapping")
        if len(destinations) != 1:
            raise ValueError("VS page-table frame has multiple physical backings")
        return input_addr, destinations.pop()

    def _gstage_node_pins(self, ptg: PTGPage, twostage: bool, g_mode: RV.RiscvPagingModes) -> Dict[int, int]:
        """A :class:`PTGPage`'s own pinned g-stage table frames as ``{g_pt_level: base}``.

        Keyed the way the walker reads them (``pinned_frame_bases``): a node at g-level ``L``
        names the frame holding the level-``L`` PTEs, which the walker draws at pt_level
        ``L + 1``. The frames are physical pages, already solved by Phase 1.

        The g-stage ROOT table is one table per g-stage space, shared by every GPA in it, so
        no single VS node may place it -- pin it through the G-space mapping's own ``pt_nodes``
        (:meth:`_root_pin_binding`) instead. A node naming the root level is a declaration error,
        not a silently-dropped pin."""
        pins: Dict[int, int] = {}
        if isinstance(ptg.pagesize, Choice) and any(key is LEAF and node.page is not None for key, node in ptg.pt_nodes.items()):
            raise ValueError("a Choice PTGPage pagesize cannot carry a LEAF-keyed pinned g-stage frame; use whole-node variants")
        pagesize = ptg.pagesize.preferred if isinstance(ptg.pagesize, Choice) else ptg.pagesize
        g_leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
        max_levels = RV.RiscvPagingModes.max_levels(g_mode) if twostage else None
        for key, node in ptg.pt_nodes.items():
            if node.page is None:
                continue
            g_level = g_leaf_level if key is LEAF else key
            if max_levels is not None and g_level + 1 >= max_levels:
                raise ValueError(
                    f"PTGPage.pt_nodes[{key!r}].page pins the g-stage ROOT table (g-level {g_level} of {max_levels}), "
                    "which is shared by the whole g-stage space: pin it via that space's own mapping pt_nodes instead"
                )
            pins[g_level + 1] = self._page_state[node.page].allocated  # type: ignore[assignment]
        return pins

    def _prepare_pinned_frames(self) -> None:
        """Promote every pinned :class:`PTNode` frame to a solved page so it draws an
        address in the solve (auto frames stay lazy).

        This method intentionally creates no translation. A Stage.G frame must
        be the source of an explicit GPA-to-HPA Mapping."""
        frames: Dict[Page, None] = {}
        for m in self.mappings:
            for node in m.pt_nodes.values():
                # A PTGPage never pins its own frame (RieMap allocates that synthesized
                # identity frame's GPA), but its pt_nodes may pin the PHYSICAL frames of that
                # identity's g-stage tree -- those are promoted here like any other frame.
                if isinstance(node.page, PTGPage):
                    for g_node in node.page.pt_nodes.values():
                        if isinstance(g_node.page, Page):
                            frames.setdefault(g_node.page, None)
                elif isinstance(node.page, Page):
                    frames.setdefault(node.page, None)
        for frame in frames:
            if frame not in self.pages:
                self.add_page(frame)

    def add_region(self, region: MemoryRegion) -> MemoryRegion:
        self._reject_after_build("add a memory region")
        self._regions.append(region)
        return region

    # -- build -------------------------------------------------------------

    def _snapshot_declarations(self) -> _BuildRollback:
        return _BuildRollback(
            pages=tuple(self.pages),
            mappings=tuple(self.mappings),
            page_state={page: state.copy() for page, state in self._page_state.items()},
            declared_roots=dict(self._declared_roots),
            addrgen=self.addrgen.clone(),
            rng=self.rng,
            next_seq=self._next_seq,
        )

    def _rollback_declarations(self, snapshot: _BuildRollback) -> None:
        """Restore the pre-build declaration state after a failed build()."""

        self.pages = {page: None for page in snapshot.pages}
        self.mappings = list(snapshot.mappings)
        self._page_state = snapshot.page_state
        self._declared_roots = snapshot.declared_roots
        self._next_seq = snapshot.next_seq
        # The pristine clone carries its own forked RNG; rebind it to the builder's
        # committed stream so a retry continues that stream rather than replaying a fork.
        self.rng = snapshot.rng
        self.addrgen = snapshot.addrgen
        self.addrgen.bind_rng(snapshot.rng)
        self._page_maps = {}
        self.mapping_addrs = {}
        self._identity_pages = set()
        self._root_frames = {}
        self._planned_node_frames = {}
        self._planned_node_pagesizes = {}
        self._topology_plan = TopologyPlan()
        self._planned_structural_identities = set()
        self._planned_structural_targets = {}
        self._planned_explicit_spans = {}
        self._color_reachability_cache = {}
        self._source_spaces = None
        self._dst_spaces = None

    def build(self) -> AllocationResult:
        if self._built:
            raise RuntimeError("build() already called")
        snapshot = self._snapshot_declarations()
        try:
            result = self._build()
        except BaseException:
            self._rollback_declarations(snapshot)
            raise
        self._built = True
        return result

    def _build(self) -> AllocationResult:
        # Adopt every space's declarative root frame (``Space.root_frame``): the space's
        # root register/table is bound to that page, so it joins the solve like any other
        # allocation. The root-pin detection (_root_pin_frames / _root_pin_binding) reads
        # it here, so this runs before anything that inspects the mapping graph.
        for space in self.spaces:
            root = space.root_frame
            if root is None:
                continue
            self._declared_roots[space] = root
            if root not in self.pages:
                self.add_page(root)

        # Give any VS space still without one a synthesized root frame, so declaring
        # ``Space.root_frame`` stays optional in the two-stage case.
        self._synthesize_vs_root_frames()

        # Promote pinned pt_nodes frames to solved pages before
        # the role/target caches read the mapping graph.
        self._prepare_pinned_frames()

        # Cache the source/dst space sets before anything reads the role predicates.
        self._source_spaces = {m.src.space for m in self.mappings}
        self._dst_spaces = {m.dst.space for m in self.mappings}

        targets = self._resolve_targets()  # space -> list of target spaces
        self._validate_stages(targets)
        # Source pages whose destination is pinned SameAs them (dst SameAs src): VA == PA
        # identity pages, drawn once in the physical pool and reserved in both pools.
        self._identity_pages = self._compute_identity_pages()

        # Phase A: attribute-based coloring. Pin per-space VA index bits per attr-signature so
        # conflicting forced pointer attrs are placed in different nodes. Pure spec mutation
        # (folds into ``AddrSpec`` masks in ``_geom_addr``); runs before geometry. With no
        # conflicting forced attrs it pins nothing (single bucket everywhere), so addresses
        # match the pre-coloring engine.
        self._apply_coloring()

        for addr_type, start, size in self._reserved_spans:
            self.addrgen.reserve_memory(address_type=addr_type, start_address=start, size=size)

        # A space that any mapping originates from gets its own VA/GPA pool for its source
        # pages. A non-physical space that has bare (unmapped) pages also needs a pool --
        # allocation routes every non-phys page through ``space_key=page.space``, and a
        # bare Page is a valid reservation with no Mapping (see ``Page``). A leaf
        # (physical) space needs none. VA pools mirror reservations with the global
        # linear pool so a bare (space-less) draw stays unmapped in every space; a G-stage
        # (GPA) pool is a separate universe and stays isolated (same predicate as g_map).
        owned_spaces = {page.space for page in self.pages if page.space is not self.phys}
        for space in self.spaces:
            if self._is_source(space) or space in owned_spaces:
                self.addrgen.make_space_pool(space, mirror_global=not self._is_gstage_domain(space))

        # Phase 0.4: declare each table-bearing space's ROOT page-table frame as a page in the
        # solve, so it is placed like any other allocation instead of being drawn later during
        # tree construction (see :meth:`_declare_root_frames`).
        self._root_frames = self._declare_root_frames(targets)

        # Phase 0.5: resolve each page's address geometry (paging-mode bit width, pagesize-
        # and g-stage-enlarged reservation size + alignment) onto the pages. A consumer that
        # pins ``addr.bits`` keeps its width; otherwise the engine derives it from the space.
        self._validate_page_sizes()
        self._resolve_geometry(targets)

        # Phase 1: addresses, policy choices, structural closure, and symbolic
        # topology form one transaction. A rejected branch publishes neither
        # reservations nor page-state addresses.
        committed_addrgen, committed_rng = self.addrgen, self.rng
        allocated_snapshot = {page: state.allocated for page, state in self._page_state.items()}

        def attempt(
            branch_addrgen: AddrGen,
            policy: ChoicePolicy,
        ) -> PlanBranch:
            self.addrgen = branch_addrgen
            self.rng = branch_addrgen._rng
            for page, allocated in allocated_snapshot.items():
                self._page_state[page].allocated = allocated
            self._planned_node_frames.clear()
            self._planned_node_pagesizes.clear()
            self._topology_plan = TopologyPlan()
            solved_branch = self._allocate_pages(targets)
            self._preplan_pt_node_frames(
                targets,
                prefer_small_choices=(policy is ChoicePolicy.MINIMUM_GEOMETRY),
            )
            return PlanBranch(
                addrgen=branch_addrgen,
                allocation=solved_branch,
                topology=self._topology_plan,
            )

        try:
            accepted = JointPlanner(committed_addrgen).solve(attempt)
        except Exception:
            self.addrgen, self.rng = committed_addrgen, committed_rng
            for page, allocated in allocated_snapshot.items():
                self._page_state[page].allocated = allocated
            raise
        solved = accepted.allocation
        self.addrgen = accepted.addrgen
        committed_rng.rand.setstate(self.addrgen._rng.rand.getstate())
        self.rng = committed_rng
        self.addrgen.bind_rng(committed_rng)

        # Phase 2: create a PageMap for every table-bearing space, initialize its root, and
        # wire each source space's g-stage emitter to fan structural PT-node mappings into
        # every table-bearing target it walks (one target normally; several for switch-hgatp).
        order = self._topo_order(targets)
        for space in order:
            self._create_page_map(space, targets)
        active_topology_mappings = self._topology_plan.reachable_mappings() - self._topology_plan.suppressed_mappings
        self._planned_structural_identities = {
            (
                intent.span.space,
                intent.span.start,
                intent.span.pagesize,
            )
            for mapping, intent in self._topology_plan.intents.items()
            if (intent.provenance.conditional and mapping in active_topology_mappings)
        }
        self._planned_structural_targets = {
            (
                intent.span.space,
                intent.span.start,
                intent.span.pagesize,
            ): intent.contract.target
            for mapping, intent in self._topology_plan.intents.items()
            if (intent.provenance.conditional and mapping in active_topology_mappings)
        }
        self._planned_explicit_spans = {}
        for mapping, intent in self._topology_plan.intents.items():
            if intent.provenance is IntentProvenance.EXPLICIT and mapping in active_topology_mappings:
                self._planned_explicit_spans.setdefault(
                    intent.span.space,
                    [],
                ).append(
                    (
                        intent.span.start,
                        intent.span.end,
                    )
                )
        for space in order:
            tb_targets = [t for t in targets[space] if self._table_bearing(t)]
            if tb_targets:
                fan = [
                    (
                        target,
                        self._page_maps[target],
                    )
                    for target in tb_targets
                ]
                self._page_maps[space].gstage_emitter = self._make_structural_emitter(fan)

        # Phase 3: install one walker Page per source. Same Page repeats must agree on the
        # declared leaf; distinct pages that alias one VA may only share that leaf when the
        # packed PTE (after defaults) matches.
        installed: set = set()
        active_mappings = self._topology_plan.reachable_mappings()
        va_owner: Dict[Tuple[Space, int], Tuple[Page, int]] = {}
        leaf_contracts: Dict[Tuple[Space, int], tuple] = {}
        source_destinations: Dict[Page, int] = {}
        source_leaf_nodes: Dict[Page, tuple] = {}
        for mapping in self.mappings:
            if not self._table_bearing(mapping.src.space):
                continue
            primary_target = self._primary_target(targets, mapping.src.space)
            twostage = primary_target is not None and self._table_bearing(primary_target)
            g_mode = primary_target.paging_mode if twostage else RV.RiscvPagingModes.DISABLE
            destination = canonical_pa(self._page_state[mapping.dst].allocated, twostage, g_mode)
            prior_destination = source_destinations.setdefault(mapping.src, destination)
            if prior_destination != destination:
                raise ValueError(f"source page maps to a different destination: " f"0x{prior_destination:x} versus 0x{destination:x}")
            leaf_nodes = self._declared_leaf_signature(mapping)
            prior_leaf_nodes = source_leaf_nodes.setdefault(mapping.src, leaf_nodes)
            if prior_leaf_nodes != leaf_nodes:
                raise ValueError(f"source page is declared more than once with conflicting leaf attributes, " f"geometry, or pinned frames: {mapping.src!r}")
        for mapping in self.mappings:
            if mapping in self._topology_plan.suppressed_mappings or mapping not in active_mappings:
                continue
            if not self._table_bearing(mapping.src.space):
                continue  # paging disabled in that space: no tree to install a leaf into
            if mapping.src in installed:
                continue
            installed.add(mapping.src)
            page = self._install_mapping(mapping, targets)
            va, pa = self.mapping_addrs[mapping.src]
            prior_src, prior_pa = va_owner.setdefault((mapping.src.space, va), (mapping.src, pa))
            if prior_src is not mapping.src and prior_pa != pa:
                raise ValueError(
                    f"two distinct pages resolve to VA 0x{va:x} in one space but map to different "
                    f"destinations (0x{prior_pa:x} and 0x{pa:x}); only one leaf PTE can map a VA, so "
                    f"one of them would report an address no page table translates: {prior_src!r} and {mapping.src!r}"
                )
            leaf_level = RV.RiscvPageSizes.pt_leaf_level(mapping.src.pagesize)
            leaf_contract = (
                mapping.src.pagesize,
                PTAttrs(rng=self.rng, featmgr=page.featmgr, level=leaf_level, page=page, leaf=True).get_value(),
                tuple(sorted(page.pinned_frame_bases.items())),
                page.attrs.get("secure"),
            )
            prior_contract = leaf_contracts.setdefault((mapping.src.space, va), leaf_contract)
            if prior_contract != leaf_contract:
                raise ValueError(f"duplicate VA 0x{va:x} and destination 0x{pa:x} have " f"conflicting effective leaf attributes, geometry, or pinned frames")

        # Phase 4: place each space's page-table node frames, then build its tree. Source spaces
        # come first so a source's structural emitter populates the target map before the target
        # is enumerated -- a VS frame's GPA becomes a page in the G space, and the G space's own
        # node set is only complete once every source that feeds it has been built.
        for space in order:
            # One line per tree built, naming the stage a reader would recognize it by:
            # a g-stage domain's root is an hgatp, everything else's an satp/vsatp.
            stage = "G" if self._is_gstage_domain(space) else "VS"
            log.info("Generating %s-stage page tables (mode=%s)", stage, resolve.PAGING_MODE_STR_MAP[space.paging_mode].upper())
            self._place_pt_node_frames(space)
            self._page_maps[space].create_pagetables(rng=self.rng)

        return self._result(solved)

    def _preplan_pt_node_frames(
        self,
        targets: Dict[Space, List[Space]],
        prefer_small_choices: bool = False,
    ) -> None:
        """Materialize and batch-allocate structural frames before PTE emission.

        A first topology pass covers declared mappings.  Planned VS frame addresses
        then become synthetic leaves in their target G spaces; a second pass closes
        the finite two-stage structure and allocates any G-stage table frames they
        introduce.
        """

        planning_mappings = list(self.mappings)
        provenance = {mapping: IntentProvenance.EXPLICIT for mapping in planning_mappings}
        required_translations: Dict[
            Space,
            Dict[int, Optional[int]],
        ] = {}
        addresses: Dict[Page, int] = {}
        target_addresses: Dict[Page, int] = {}
        for page in self.pages:
            allocated = self._page_state[page].allocated
            if allocated is not None:
                target_addresses[page] = allocated
        for mapping in planning_mappings:
            source = mapping.src
            target = self._primary_target(targets, source.space)
            twostage = target is not None and self._table_bearing(target)
            g_mode = target.paging_mode if twostage else RV.RiscvPagingModes.DISABLE
            allocated = self._page_state[source].allocated
            if allocated is None:
                continue
            addresses[source] = canonical_va(
                allocated,
                source.space.paging_mode,
                twostage,
                g_mode,
                gstage_source=self._is_gstage_domain(source.space),
            )
        synthetic_keys: set[NodeKey] = set()
        aliased_leaf_slots: Dict[Tuple[Space, int, int], set[int]] = {}
        for _ in range(3):  # declared leaves -> VS frames -> G frames (finite at two stages)
            partial_topology = plan_topology(
                planning_mappings,
                addresses,
                target_addresses,
                provenance=provenance,
            )
            demands = [demand for demand in partial_topology.frame_demands() if demand.key not in self._planned_node_frames]
            added_synthetic = False
            gstage_sizes: Dict[Space, set[RV.RiscvPageSizes]] = {}
            for demand in demands:
                target = self._primary_target(targets, demand.key.space)
                if target is None or not self._table_bearing(target):
                    continue
                demand_ptgs = []
                for mapping in demand.mappings:
                    node = self._color_node_at(mapping, demand.key.level)
                    if node is not None and isinstance(node.page, PTGPage):
                        demand_ptgs.append(node.page)
                declarations = [ptg.pagesize for ptg in demand_ptgs]
                if declarations:
                    if prefer_small_choices:
                        first = declarations[0]
                        candidates = first.options if isinstance(first, Choice) else (first,)
                        legal = [
                            candidate for candidate in candidates if all(declaration.allows(candidate) if isinstance(declaration, Choice) else declaration == candidate for declaration in declarations)
                        ]
                        selected_size = min(
                            legal,
                            key=RV.RiscvPageSizes.memory,
                        )
                    else:
                        selected_size = resolve_common_choice(declarations)
                else:
                    selected_size = RV.RiscvPageSizes.S4KB
                gstage_sizes.setdefault(target, set()).add(selected_size)
            requests: List[AllocRequest] = []
            request_info: Dict[
                Page,
                Tuple[NodeKey, bool, Optional[Space], RV.RiscvPageSizes, Optional[PTGPage]],
            ] = {}
            request_metadata: Dict[
                Page,
                Tuple[NodeKey, bool, Optional[Space], RV.RiscvPageSizes, Optional[PTGPage]],
            ] = {}
            request_backings: Dict[Page, Page] = {}
            for demand in demands:
                # A consumer-pinned child frame already participates in the main page solve.
                concrete_frames = {
                    frame
                    for mapping in demand.mappings
                    for frame in (
                        self._concrete_frame_for_demand(
                            mapping,
                            demand.key.level,
                        ),
                    )
                    if frame is not None
                }
                ptgs = [
                    ptg
                    for mapping in demand.mappings
                    for ptg in (
                        self._generated_frame_policy_for_demand(
                            mapping,
                            demand.key.level,
                        ),
                    )
                    if ptg is not None
                ]
                if concrete_frames:
                    if len(concrete_frames) > 1:
                        raise ValueError(f"conflicting pinned frames for planned node {demand.key}")
                    frame = next(iter(concrete_frames))
                    target = self._primary_target(
                        targets,
                        demand.key.space,
                    )
                    twostage = target is not None and self._table_bearing(target)
                    g_mode = target.paging_mode if twostage else RV.RiscvPagingModes.DISABLE
                    input_addr, backing_addr = self._resolved_frame_binding(
                        frame,
                        twostage,
                        g_mode,
                    )
                    declarations = [ptg.pagesize for ptg in ptgs]
                    if declarations:
                        pagesize = resolve_common_choice(declarations)
                        if prefer_small_choices:
                            candidates = declarations[0].options if isinstance(declarations[0], Choice) else (declarations[0],)
                            pagesize = min(
                                (
                                    candidate
                                    for candidate in candidates
                                    if all(declaration.allows(candidate) if isinstance(declaration, Choice) else declaration == candidate for declaration in declarations)
                                ),
                                key=RV.RiscvPageSizes.memory,
                            )
                    else:
                        pagesize = RV.RiscvPageSizes.S4KB
                    self._planned_node_frames[demand.key] = (
                        input_addr,
                        backing_addr,
                        bool(backing_addr & (1 << 55)),
                    )
                    self._planned_node_pagesizes[demand.key] = pagesize
                    continue

                target = self._primary_target(targets, demand.key.space)
                gspace = target if target is not None and self._table_bearing(target) else None
                g_mode = gspace.paging_mode if gspace is not None else RV.RiscvPagingModes.DISABLE
                if gspace is not None and not ptgs:
                    raise TopologyConflict(
                        f"VS page-table frame {demand.key} has no explicit " "G-stage translation declaration",
                        mappings=demand.mappings,
                        node_keys=(demand.key,),
                        retriable=False,
                    )
                identities = {ptg.identity for ptg in ptgs}
                if len(identities) > 1:
                    raise ValueError(f"sharers of planned frame {demand.key} disagree on whether it is identity-mapped")
                identity = next(iter(identities), True)
                if ptgs:
                    pagesize_declarations = [ptg.pagesize for ptg in ptgs]
                    if prefer_small_choices:
                        first = pagesize_declarations[0]
                        candidates = first.options if isinstance(first, Choice) else (first,)
                        legal = [
                            candidate
                            for candidate in candidates
                            if all(declaration.allows(candidate) if isinstance(declaration, Choice) else declaration == candidate for declaration in pagesize_declarations)
                        ]
                        if not legal:
                            raise ValueError("planned PTGPage declarations have no common pagesize")
                        pagesize = min(legal, key=RV.RiscvPageSizes.memory)
                    else:
                        pagesize = resolve_common_choice(pagesize_declarations)
                    # A PTGPage's per-g-level declarations describe the PTE bits of this
                    # synthesized identity. They do not turn the frame into a runtime
                    # pointer-PTE rewrite and therefore must not reserve the pointer's
                    # full GPA span. RiescueD's modify_*pt path makes that stronger
                    # request explicitly with Page.reserve_size.
                    size = RV.RiscvPageSizes.memory(pagesize)
                    mask = RV.RiscvPageSizes.address_mask(pagesize)
                else:
                    pagesize = RV.RiscvPageSizes.S4KB
                    size = RV.RiscvPageSizes.memory(pagesize)
                    mask = RV.RiscvPageSizes.address_mask(pagesize)
                or_mask = 0
                # The GPA window whose g-stage leaf table this frame was slotted into, as
                # (window size, this frame's byte offset inside it). Every GPA in the window
                # walks through the same pinned table, so the frame has to own the window --
                # see the claim built from this below.
                slotted_window: Optional[Tuple[int, int]] = None
                if gspace is not None and ptgs:
                    g_leaf = RV.RiscvPageSizes.pt_leaf_level(pagesize)
                    pinned_leaf_pages = {node.page for ptg in ptgs for node in (ptg.pt_nodes.get(g_leaf) or ptg.pt_nodes.get(LEAF),) if node is not None and isinstance(node.page, Page)}
                    if len(pinned_leaf_pages) > 1:
                        raise ValueError("one planned VS frame identity declares " "conflicting g-stage leaf frames")
                    if pinned_leaf_pages:
                        pinned_page = next(iter(pinned_leaf_pages))
                        pinned_base = self._page_state[pinned_page].allocated
                        if pinned_base is None:
                            raise RuntimeError("pinned g-stage leaf frame was not allocated")
                        hi, lo = RV.RiscvPagingModes.index_bits(
                            g_mode,
                            g_leaf,
                        )
                        field_mask = ((1 << (hi - lo + 1)) - 1) << lo
                        if mask & field_mask == field_mask:
                            occupied = aliased_leaf_slots.setdefault(
                                (gspace, pinned_base, g_leaf),
                                set(),
                            )
                            capacity = 1 << (hi - lo + 1)
                            slot = next(
                                (candidate for candidate in range(capacity) if candidate not in occupied),
                                None,
                            )
                            if slot is None:
                                raise ValueError("aliased g-stage leaf frame is over-packed")
                            occupied.add(slot)
                            mask &= ~field_mask
                            or_mask |= slot << lo
                            slotted_window = (field_mask + (1 << lo), slot << lo)

                secure = self.rng.with_probability_of(self._config_for(demand.key.space).secure_pt_probability)
                qualifiers = {RV.AddressQualifiers.ADDRESS_SECURE if secure else RV.AddressQualifiers.ADDRESS_DRAM}
                input_bits = self.physical_addr_bits
                if gspace is not None:
                    # A VS frame's GPA must fit the G-stage input width whether or not
                    # it is tied to the physical backing address.
                    input_bits = min(input_bits, RV.RiscvPagingModes.linear_addr_bits(g_mode, gstage=True))
                backing_frame = Page(space=self.phys, pagesize=RV.RiscvPageSizes.S4KB)
                coverage_targets = tuple(target for target in targets[demand.key.space] if self._table_bearing(target))
                backing_claims = [
                    SpanClaim(
                        addr_type=RV.AddressType.PHYSICAL,
                        space=None,
                        size=0x1000,
                        kind=ClaimKind.BACKING,
                        share_key=demand.key,
                    )
                ]
                input_claims = [
                    SpanClaim(
                        addr_type=RV.AddressType.LINEAR,
                        space=target,
                        size=size,
                        kind=ClaimKind.STRUCTURAL,
                        share_key=(
                            ClaimKind.COVERAGE,
                            target,
                            "translation",
                        ),
                    )
                    for target in coverage_targets
                ]
                if slotted_window is not None:
                    # A slot in the pinned table is only unique among the frames this loop
                    # slotted. Any OTHER GPA in the same window reaches the same table through
                    # the same pointer PTE and takes whatever slot its own address falls on --
                    # which collides with a slotted frame from a different window sooner or
                    # later. Claiming the whole window (it starts ``offset`` bytes below this
                    # frame) keeps the window's occupants exactly the frames with reserved slots.
                    window_size, offset = slotted_window
                    input_claims.append(
                        SpanClaim(
                            addr_type=RV.AddressType.LINEAR,
                            space=gspace,
                            size=window_size,
                            kind=ClaimKind.STRUCTURAL,
                            share_key=(
                                ClaimKind.COVERAGE,
                                gspace,
                                "translation",
                            ),
                            offset=-offset,
                        )
                    )
                ptg_template = next(
                    (ptg for ptg in ptgs if any(isinstance(node.page, Page) for node in ptg.pt_nodes.values())),
                    ptgs[0] if ptgs else None,
                )
                info = (demand.key, secure, gspace, pagesize, ptg_template)
                if identity or gspace is None:
                    input_frame = backing_frame
                    request = AllocRequest(
                        page=input_frame,
                        addr_type=RV.AddressType.PHYSICAL,
                        size=0x1000,
                        addr=AddrSpec(and_mask=mask, or_mask=or_mask, bits=input_bits, qualifiers=qualifiers),
                        space_key=None,
                        seq=len(requests),
                        claims=tuple(backing_claims + input_claims),
                    )
                    requests.append(request)
                    request_metadata[input_frame] = info
                else:
                    input_frame = Page(space=gspace, pagesize=pagesize)
                    backing_request = AllocRequest(
                        page=backing_frame,
                        addr_type=RV.AddressType.PHYSICAL,
                        size=0x1000,
                        addr=AddrSpec(
                            and_mask=RV.RiscvPageSizes.address_mask(pagesize),
                            bits=self.physical_addr_bits,
                            qualifiers=qualifiers,
                        ),
                        space_key=None,
                        seq=len(requests),
                        claims=tuple(backing_claims),
                    )
                    requests.append(backing_request)
                    request_metadata[backing_frame] = info
                    input_request = AllocRequest(
                        page=input_frame,
                        addr_type=RV.AddressType.LINEAR,
                        size=size,
                        addr=AddrSpec(and_mask=mask, or_mask=or_mask, bits=input_bits),
                        space_key=gspace,
                        seq=len(requests),
                        claims=tuple(input_claims),
                    )
                    requests.append(input_request)
                    request_metadata[input_frame] = info
                request_info[input_frame] = info
                request_backings[input_frame] = backing_frame

            if not requests:
                if added_synthetic:
                    continue
                break
            try:
                solved = self.allocator.solve_in_place(
                    requests,
                    [],
                    self.addrgen,
                    self.rng,
                )
            except AddrGenError as exc:
                geometries = sorted(
                    {
                        (
                            info[3].name,
                            request.addr.and_mask,
                        )
                        for request in requests
                        for info in (request_metadata[request.page],)
                    }
                )
                raise AddrGenError(f"structural frame placement failed for " f"geometries {geometries!r}") from exc
            for frame, (key, secure, gspace, pagesize, ptg_template) in request_info.items():
                input_base = solved.address(frame)
                backing_base = solved.address(request_backings[frame])
                if secure and gspace is None:
                    input_base |= 0x0080000000000000
                    backing_base |= 0x0080000000000000
                self._planned_node_frames[key] = (
                    input_base,
                    backing_base,
                    secure,
                )
                self._planned_node_pagesizes[key] = pagesize
                gspaces = [target for target in targets[key.space] if self._table_bearing(target)]
                if not gspaces or key in synthetic_keys:
                    continue
                gpa_base = input_base & RV.RiscvPageSizes.address_mask(pagesize)
                hpa_base = backing_base & RV.RiscvPageSizes.address_mask(pagesize)
                for target in gspaces:
                    src = Page(
                        space=target,
                        pagesize=pagesize,
                        addr=AddrSpec(exact=gpa_base),
                    )
                    dst = Page(
                        space=self.phys,
                        pagesize=pagesize,
                        addr=AddrSpec(exact=hpa_base),
                    )
                    synthetic_mapping = Mapping(
                        src=src,
                        dst=dst,
                        pt_nodes=(dict(ptg_template.pt_nodes) if ptg_template is not None else {}),
                    )
                    planning_mappings.append(synthetic_mapping)
                    provenance[synthetic_mapping] = IntentProvenance.SYNTHETIC_FRAME
                    addresses[src] = gpa_base
                    effective_target = hpa_base | ((1 << 55) if secure else 0)
                    target_addresses[dst] = effective_target
                    required_translations.setdefault(target, {})[gpa_base] = effective_target
                synthetic_keys.add(key)
                added_synthetic = True
            if not added_synthetic:
                break
        self._topology_plan = plan_topology(
            planning_mappings,
            addresses,
            target_addresses,
            provenance=provenance,
            required_translations=required_translations,
        )
        missing = [demand.key for demand in self._topology_plan.frame_demands() if demand.key not in self._planned_node_frames]
        if missing:
            raise RuntimeError(f"structural planning closure is incomplete: {missing}")

    def _resolve_shared_node_choices(
        self,
        pages: List[WalkerPage],
        level: int,
        planned_pagesize: Optional[RV.RiscvPageSizes] = None,
    ) -> None:
        """Resolve policy domains once for the shared node/frame they describe."""
        whole_choices = [page.node_choices.get(level) for page in pages]
        if any(choice is not None for choice in whole_choices):
            bases = {base for choice in whole_choices if choice is not None for option in choice.options for base in option}
            declarations = []
            for page, choice in zip(pages, whole_choices):
                if choice is not None:
                    declarations.append(choice)
                else:
                    declarations.append({base: page.attrs.get(f"{base}_level{level}", 1 if base == "v" else 0) for base in bases})
            try:
                selected = resolve_common_choice(declarations)
            except ValueError as exc:
                raise ValueError(f"no common whole-node variant for shared level-{level} node") from exc
            for page in pages:
                for base, value in selected.items():
                    page.attrs[f"{base}_level{level}"] = value

        for base in resolve.LEVEL_TYPES:
            key = f"{base}_level{level}"
            declarations = [page.attrs.get(key, 1 if base == "v" else 0) for page in pages]
            if not any(isinstance(value, Choice) for value in declarations):
                continue
            try:
                value = resolve_common_choice(declarations)
            except ValueError as exc:
                raise ValueError(f"no common {base} value for shared level-{level} node") from exc
            for page in pages:
                page.attrs[key] = value

        ptgs = [page.gstage_nodes.get(level) for page in pages]
        declared_ptgs = [ptg for ptg in ptgs if ptg is not None]
        if not declared_ptgs:
            return
        has_ptg_choice = any(
            isinstance(ptg.pagesize, Choice) or any(node.choice is not None or any(isinstance(value, Choice) for value in node.attrs.values()) for node in ptg.pt_nodes.values())
            for ptg in declared_ptgs
        )
        try:
            pagesize = planned_pagesize if planned_pagesize is not None else resolve_common_choice([ptg.pagesize for ptg in declared_ptgs])
            if not all(ptg.pagesize.allows(pagesize) if isinstance(ptg.pagesize, Choice) else ptg.pagesize == pagesize for ptg in declared_ptgs):
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"no common g-stage pagesize for shared level-{level} frame") from exc

        leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
        whole_g_choices: Dict[int, List[Choice[Dict[str, Any]]]] = {}
        for ptg in declared_ptgs:
            for key, node in ptg.pt_nodes.items():
                if node.choice is not None:
                    whole_g_choices.setdefault(leaf_level if key is LEAF else key, []).append(node.choice)
        resolved_g_variants = {g_level: resolve_common_choice(declarations) for g_level, declarations in whole_g_choices.items()}

        fields: Dict[Tuple[int, str], List[Any]] = {}
        for ptg in declared_ptgs:
            for g_level, attrs in ptg.level_attrs(pagesize).items():
                for base, value in attrs.items():
                    fields.setdefault((g_level, base), []).append(value)
        resolved_fields: Dict[Tuple[int, str], Any] = {}
        for field, declarations in fields.items():
            if any(isinstance(value, Choice) for value in declarations):
                try:
                    resolved_fields[field] = _common_declaration(declarations)
                except ValueError as exc:
                    raise ValueError(f"no common {field[1]} value for shared level-{level} g-stage identity") from exc

        # Publish one resolved identity to every sharer because the identity belongs
        # to the shared frame, not to an individual declaration. Merging all sharers
        # makes geometry independent of declaration order and exposes contradictory
        # requirements before frame planning.
        if len({ptg.identity for ptg in declared_ptgs}) > 1:
            raise ValueError(f"sharers of one level-{level} g-stage frame disagree on whether it is identity-mapped")
        nodes: Dict[Any, PTNode] = {}
        for ptg in declared_ptgs:
            for key, node in ptg.pt_nodes.items():
                g_level = leaf_level if key is LEAF else key
                merged = nodes.get(g_level, PTNode())
                if merged.page is not None and node.page is not None and merged.page is not node.page:
                    raise ValueError(f"sharers of one level-{level} g-stage frame pin different frames for its g-level-{g_level} table")
                attrs = dict(merged.attrs)
                for base, value in node.attrs.items():
                    # A field a Choice reaches is settled by ``resolved_fields`` below, so only
                    # scalar-vs-scalar disagreement is a conflict here.
                    if base in attrs and attrs[base] != value and (g_level, base) not in resolved_fields:
                        raise ValueError(f"sharers of one level-{level} g-stage frame force different {base} at g-level {g_level}")
                    attrs[base] = value
                if g_level in resolved_g_variants:
                    attrs.update(resolved_g_variants[g_level])
                nodes[g_level] = PTNode(page=node.page if node.page is not None else merged.page, attrs=attrs)
        for (g_level, base), value in resolved_fields.items():
            node = nodes.get(g_level, PTNode())
            attrs = dict(node.attrs)
            attrs[base] = value
            nodes[g_level] = PTNode(page=node.page, attrs=attrs)
        # The host-physical frames a PTGPage's own pt_nodes pin were resolved onto each
        # declaring page when its mapping was installed; they describe this one frame's g-stage
        # tables, so they travel with it to every sharer too.
        pins: Dict[int, int] = {}
        for page in pages:
            for g_pt_level, base in page.gstage_node_pins.get(level, {}).items():
                if pins.setdefault(g_pt_level, base) != base:
                    raise ValueError(f"sharers of one level-{level} g-stage frame pin different g-stage tables")
        resolved_ptg = PTGPage(pt_nodes=nodes, pagesize=pagesize, identity=declared_ptgs[0].identity)
        for page in pages:
            page.gstage_nodes[level] = resolved_ptg
            if pins:
                page.gstage_node_pins[level] = dict(pins)

        if not has_ptg_choice:
            return
        # Resolving a Choice changed the geometry these bits are derived from, so re-resolve
        # them on the pages that declared it.
        for page, ptg in zip(pages, ptgs):
            if ptg is None:
                continue
            for base in ("u", "r", "w", "x", "a", "d"):
                resolve.setup_uwrx_bit(
                    base,
                    attrs=page.attrs,
                    paging_mode=page.map.paging_mode,
                    paging_g_mode=page.map.paging_g_mode,
                    final_pagesize=page.pagesize,
                    gstage_vs_leaf_final_pagesize=page.gstage_leaf_pagesize or RV.RiscvPageSizes.S4KB,
                    gstage_vs_nonleaf_final_pagesize=pagesize,
                )

    def _place_pt_node_frames(self, space: Space) -> None:
        """Place every intermediate page-table frame ``space``'s tree needs, before it is built.

        A node is identified by the VA prefix above the level whose pointer PTE reaches it --
        exactly the walker's own interning, so two pages sharing that prefix share one frame. For
        each node this writes the chosen base onto every sharer's ``pinned_frame_bases``, and the
        walker then places the table there and draws nothing (see
        :meth:`Pagetables._create_pt_non_leaf`).

        Why the frames cannot be drawn during the walk: under a g-stage every frame's GPA is
        identity-mapped into the target G space, so the frame is a mapped address there as well as
        a physical one. A draw made during the walk sees neither that space's reservations nor the
        spans the solve has already handed out, so a frame could land inside a superpage identity
        leaf's span and the two would contend for one slot -- the same class of bug the root frame
        hit in ``rvv_fp_test``. Here the draw checks both pools.

        A node already carrying a CONSUMER pin (a mapping's ``pt_nodes`` frame) keeps it, and every
        sharer of that node adopts it: a pinned and an unpinned page that share a prefix
        necessarily share the frame, so giving the unpinned one a fresh frame would be an
        unsatisfiable demand on one pointer PTE. Two consumer pins that disagree on one node are a
        declaration conflict, reported here rather than surfacing as a slot conflict later.

        Only nodes present in the accepted topology receive frames; emission
        cannot reconstruct extra paths below a surviving superpage leaf."""
        page_map = self._page_maps[space]
        mode = page_map.paging_mode
        max_levels = page_map.max_levels
        if max_levels < 2:
            return
        g_mode = page_map.paging_g_mode
        # (level, VA prefix above that level) -> the pages reaching that node. pt_pages are the
        # g-stage identity leaves earlier spaces emitted into this map; they are merged into
        # ``pages`` by create_pagetables, so both sets must be enumerated.
        #
        # The prefix is the walk's own index path -- bits ``[top_hi:lo(level)]``, cut off at the
        # mode's TOP index field, exactly the bits ``Pagetables.calc_index_from_va`` reads. Address
        # bits above that field are invisible to the walk, so two pages differing only there land
        # in the same slot of the same frame and must share one child frame. A self-mapped root
        # (``create_sptbr``: lin_addr == the frame's physical base, which under SV39 is wider than
        # the 39-bit VA) is exactly such a page: keyed on the raw shifted address it formed its own
        # bucket, drew its own child frame, and then collided in the root slot with the real VA it
        # aliases.
        # ``index_bits`` RAISES for a disabled mode or an out-of-range level; it never returns
        # None, so neither this nor the per-level lookup below needs a None fallback. A
        # paging-disabled space would already have raised on ``level = max_levels - 1 = -1``
        # here, which is why such a space must be excluded before Phase 4 rather than guarded.
        top_hi = RV.RiscvPagingModes.index_bits(mode, max_levels - 1)[0]
        sharers: Dict[Tuple[int, int], List[WalkerPage]] = {}
        for page in [*page_map.pages.values(), *page_map.pt_pages.values()]:
            if page.lin_addr is None:
                continue
            lin_addr = canonical_va(
                page.lin_addr,
                mode,
                False,
                RV.RiscvPagingModes.DISABLE,
                gstage_source=self._is_gstage_domain(space),
            )
            leaf_level = RV.RiscvPageSizes.pt_leaf_level(page.pagesize)
            for level in range(max_levels - 1, leaf_level, -1):
                lo = RV.RiscvPagingModes.index_bits(mode, level)[1]
                prefix = common.bits(value=lin_addr, bit_hi=top_hi, bit_lo=lo)
                sharers.setdefault((level, prefix), []).append(page)

        bits = self.physical_addr_bits
        if g_mode != RV.RiscvPagingModes.DISABLE:
            # The frame's GPA is identity-mapped, so it must fit the g-stage input width too.
            bits = min(bits, RV.RiscvPagingModes.linear_addr_bits(g_mode, gstage=True))

        # Sorted so placement order -- and therefore the addresses drawn -- is a function of the
        # declarations alone, not of dict iteration order.
        for (level, prefix), pages in sorted(sharers.items()):
            node_key = NodeKey(space=space, level=level, prefix=prefix)
            planned_pagesize = self._planned_node_pagesizes.get(node_key)
            self._resolve_shared_node_choices(
                pages,
                level,
                planned_pagesize,
            )
            if planned_pagesize is not None:
                for page in pages:
                    page.resolved_gstage_pagesizes[level] = planned_pagesize
            declared = {p.pinned_frame_bases[level] for p in pages if level in p.pinned_frame_bases}
            if len(declared) > 1:
                raise ValueError(f"conflicting pinned frames for one level-{level} node in space {space!r}: {sorted(hex(b) for b in declared)}")
            if declared:
                input_addr = declared.pop()
                backing = {p.pinned_frame_backings.get(level, input_addr) for p in pages if level in p.pinned_frame_bases}
                if len(backing) != 1:
                    raise ValueError(f"conflicting backing frames for one level-{level} " f"node in space {space!r}")
                backing_addr, secure = backing.pop(), False
            elif node_key in self._planned_node_frames:
                (
                    input_addr,
                    backing_addr,
                    secure,
                ) = self._planned_node_frames[node_key]
            else:
                source_addresses = ", ".join(f"0x{page.lin_addr:x}" for page in pages)
                planned_prefixes = ", ".join(f"0x{key.prefix:x}" for key in self._planned_node_frames if key.space is space and key.level == level)
                raise RuntimeError(f"no preplanned frame for level-{level} node prefix 0x{prefix:x} " f"in {space!r}; source addresses: {source_addresses}; " f"planned prefixes: {planned_prefixes}")
            for p in pages:
                p.pinned_frame_bases.setdefault(level, input_addr)
                p.pinned_frame_backings.setdefault(level, backing_addr)
                p.pinned_frame_secure.setdefault(level, secure)

        # Leaf PTEs are not shared by the pointer-prefix groups above. Resolve
        # any propagated policy domain to its seeded preference only after all
        # ancestor nodes have consumed the full domain.
        for page in [*page_map.pages.values(), *page_map.pt_pages.values()]:
            leaf_level = RV.RiscvPageSizes.pt_leaf_level(page.pagesize)
            for base in resolve.LEVEL_TYPES:
                for key in (base, f"{base}_level{leaf_level}"):
                    value = page.attrs.get(key)
                    if isinstance(value, Choice):
                        page.attrs[key] = value.preferred

    def _make_structural_emitter(
        self,
        fan: List[Tuple[Space, PageMap]],
    ):
        """Emit only structural identities accepted by the topology plan."""

        def emit(*, gpa, hpa, attrs, pagesize, pinned_frame_bases=None):
            page_mask = RV.RiscvPageSizes.address_mask(pagesize)
            gpa &= page_mask
            hpa &= page_mask
            end = gpa + RV.RiscvPageSizes.memory(pagesize)
            for target_space, target_map in fan:
                identity_key = (
                    target_space,
                    gpa,
                    pagesize,
                )
                if identity_key not in self._planned_structural_identities:
                    continue
                expected_target = self._planned_structural_targets.get(identity_key)
                emitted_target = hpa | ((1 << 55) if attrs.get("secure") else 0)
                if expected_target is not None and expected_target != emitted_target:
                    raise RuntimeError("structural identity emission disagrees with the " f"accepted topology at GPA 0x{gpa:x}: planned " f"0x{expected_target:x}, emitted " f"0x{emitted_target:x}")
                explicit_overlap = any(
                    start < end and gpa < explicit_end
                    for start, explicit_end in self._planned_explicit_spans.get(
                        target_space,
                        (),
                    )
                )
                if explicit_overlap:
                    continue
                target_map.add_raw_pt_page(linear_addr=gpa, physical_addr=hpa, attrs=attrs, pagesize=pagesize, pinned_frame_bases=pinned_frame_bases)

        return emit

    # -- target / role resolution -----------------------------------------

    def _is_source(self, space: Space) -> bool:
        if self._source_spaces is not None:
            return space in self._source_spaces
        return any(m.src.space == space for m in self.mappings)

    def _is_dst(self, space: Space) -> bool:
        if self._dst_spaces is not None:
            return space in self._dst_spaces
        return any(m.dst.space == space for m in self.mappings)

    def _table_bearing(self, space: Space) -> bool:
        """A space has a page table iff a mapping originates from it AND its paging mode is
        enabled. RiescueD's shared G space has a table because it originates GPA -> HPA leaf
        mappings.

        A DISABLE-mode space has no tree to build: ``max_levels`` is 0, so its root level is
        -1 and ``index_bits`` raises "paging mode disabled doesn't have index bits" the moment
        anything tries to size its root table. Its mappings are still real declarations -- the
        addresses are drawn and reported -- there is simply no translation between them, so
        every table-building phase skips it through this one predicate."""
        return self._is_source(space) and space.paging_mode != RV.RiscvPagingModes.DISABLE

    def _is_leaf(self, space: Space) -> bool:
        """A space with no page table: never a mapping source, or paging-disabled."""
        return not self._table_bearing(space)

    def _is_bare_gstage_source(self, space: Space) -> bool:
        """True for a *bare* g-stage source: stage ``G`` but never a mapping target --
        it is walked VS-disabled and draws GPAs directly (RiescueD's bare-VS ``map_hyp``
        / a JSON gonly space). Distinct from an ordinary two-stage target, which is also
        stage ``G`` but *is* a mapping target."""
        return space.stage is Stage.G and not self._is_dst(space)

    def _is_gstage_domain(self, space: Space) -> bool:
        """True when a space's root is an hgatp (16 KiB-aligned, user leaves): the single
        predicate the pool-isolation pass and the ``PageMap`` ``g_map`` flag both read.
        ``Stage`` is always explicit now, so this is exactly ``stage is Stage.G``."""
        return space.stage is Stage.G

    def _validate_stages(self, targets: Dict[Space, List[Space]]) -> None:
        """Raise when a space's declared ``Stage`` contradicts the mapping structure.

        A g-stage domain cannot itself be a two-stage source, and a space that is the
        table-bearing target of another space's walk must itself be stage ``G``."""
        for space in self.spaces:
            walks_into_table = [t for t in targets[space] if self._table_bearing(t)]
            if space.stage is Stage.G and walks_into_table:
                raise ValueError(f"space (stage=G) walks into table-bearing target(s); a g-stage domain cannot be a two-stage source: {space!r}")
            if space.stage is not Stage.G and self._is_dst(space) and self._table_bearing(space):
                raise ValueError(f"space (stage={space.stage}) is a table-bearing target of another space's walk (requires stage=G): {space!r}")

    def _validate_page_sizes(self) -> None:
        """Reject source-page geometries that the source paging mode cannot encode."""
        for mapping in self.mappings:
            mode = mapping.src.space.paging_mode
            if mode is RV.RiscvPagingModes.DISABLE:
                continue
            supported = RV.RiscvPagingModes.supported_pagesizes(mode, napot_supported=True)
            if mapping.src.pagesize not in supported:
                raise ValueError(f"pagesize {mapping.src.pagesize.name} is not supported by {mode.name}; " f"supported sizes are {[size.name for size in supported]}")

    def _compute_identity_pages(self) -> set:
        """Source pages whose destination page is pinned ``SameAs`` them (dst SameAs
        src). Such a source is an identity page (VA == PA): drawn once in the physical
        pool and reserved in both pools. A source that is itself a ``SameAs`` follower is
        excluded -- it draws nothing, so it is not the identity anchor (e.g. an identity
        G-stage's GPA page, which follows the source and whose own HPA leaf pins itself
        SameAs the GPA)."""
        ids: set = set()
        for m in self.mappings:
            dst_rel = m.dst.addr.relation
            if isinstance(dst_rel, SameAs) and dst_rel.target is m.src:
                if not isinstance(m.src.addr.relation, SameAs):
                    ids.add(m.src)
        return ids

    def _root_pin_frames(self, space: Space) -> List[Page]:
        """The consumer pages a mapping pins ``space``'s ROOT page-table frame to.

        The static (pre-allocation) form of :meth:`_root_pin_binding`, which needs solved
        addresses. A mapping pins the root by declaring a ``PTNode(page=frame)`` at the top
        arch level; normally there is at most one such page (two disagreeing pins are reported
        as a conflict once their addresses are known)."""
        root_level = RV.RiscvPagingModes.max_levels(space.paging_mode) - 1
        frames: List[Page] = []
        declared = self._declared_roots.get(space)
        if declared is not None:
            frames.append(declared)
        for m in self.mappings:
            if m.src.space is not space:
                continue
            node = m.pt_nodes.get(root_level)
            if node is not None and isinstance(node.page, Page) and node.page not in frames:
                frames.append(node.page)
        return frames

    def _root_pin_declared(self, space: Space) -> bool:
        """True when a mapping already pins ``space``'s root frame to a consumer page: such a
        space must NOT get an engine-declared root frame, since the consumer chose where its
        root lives (a recursive / self-referencing page table)."""
        return bool(self._root_pin_frames(space))

    def _pinned_root_frame_spaces(self) -> Dict[Page, List[Space]]:
        """Consumer-pinned root frame page -> the table-bearing space(s) it is the root of.

        A root is a register value and a table frame, not an implicit VA==PA leaf
        (:meth:`PageMap.create_sptbr`). A consumer-pinned frame is declared in the
        physical / GPA domain -- and is often also the destination of the read-back
        window mappings that let a ``modify_pt`` test write its own live PTEs -- so
        the rooted space cannot be read off ``page.space`` and is carried to the
        solve separately (``AllocRequest.extra_linear_spaces``) so the frame's span
        stays free of conflicting VA coverage in that space.

        Without it the frame draws blind to that pool: a 4 KiB root frame was placed at
        0x1343fd000, inside the 1 GiB VA span [0x100000000, 0x140000000) the solve had already
        given a 1 GiB page, and the root slot then collided with that page's leaf
        PTE in root slot 4 (``hypervisor_paging_faults_g_reserved``)."""
        rooted: Dict[Page, List[Space]] = {}
        for space in self.spaces:
            if not self._table_bearing(space):
                continue
            for frame in self._root_pin_frames(space):
                spaces = rooted.setdefault(frame, [])
                if space not in spaces:
                    spaces.append(space)
        return rooted

    def _synthesize_vs_root_frames(self) -> None:
        """Declare a root frame for every table-bearing VS space that has none.

        A VS space's root register holds a GPA, so unlike an satp/hgatp root
        (:meth:`_declare_root_frames`) its frame cannot be a plain physical page: it is a
        page in the target G space, and the guest's first fetch only reaches it if that GPA
        is itself translated. The default is the identity (GPA == HPA) every *other* VS
        page-table node already gets structurally -- the same
        ``v``/``r``/``w``/``x``/``u``/``a``/``d`` leaf the walker's identity matrix emits
        for a VS node frame (``Pagetables._emit_gstage_identity``) -- so a two-stage
        consumer that has no opinion about where its root lives declares nothing.

        A consumer with an opinion keeps it: this runs only for a space whose root is not
        already bound, either by ``Space.root_frame`` or by a root-level ``PTNode`` frame
        pin (:meth:`_root_pin_declared`). A VS space walked under several G-stages
        (switch-hgatp) gets one root GPA per G space, all pinned ``SameAs`` the one root
        HPA, so the single vsatp value resolves under every hgatp.
        """
        declared = list(self.mappings)
        for space in self.spaces:
            if space.stage is not Stage.VS or not self._table_bearing(space):
                continue
            if self._root_pin_declared(space):
                continue
            # Mapping order, so the G space picked here is the one _primary_target will
            # later call this space's primary target.
            g_spaces: List[Space] = []
            for m in declared:
                if m.src.space is space and self._table_bearing(m.dst.space) and m.dst.space not in g_spaces:
                    g_spaces.append(m.dst.space)
            if not g_spaces:
                continue
            root_hpa = self.add_page(
                Page(
                    space=self.phys,
                    pagesize=RV.RiscvPageSizes.S4KB,
                    addr=AddrSpec(
                        qualifiers={
                            RV.AddressQualifiers.ADDRESS_DRAM,
                        }
                    ),
                )
            )
            for g_space in g_spaces:
                root_gpa = self.add_page(
                    Page(
                        space=g_space,
                        pagesize=RV.RiscvPageSizes.S4KB,
                        addr=AddrSpec(relation=SameAs(root_hpa)),
                    )
                )
                self.add_mapping(
                    Mapping(
                        src=root_gpa,
                        dst=root_hpa,
                        pt_nodes={LEAF: PTNode(attrs=dict(_VS_ROOT_IDENTITY_LEAF_ATTRS))},
                    )
                )
                self._declared_roots.setdefault(space, root_gpa)

    def _declare_root_frames(self, targets: Dict[Space, List[Space]]) -> Dict[Space, Page]:
        """One solver-placed :class:`Page` per table-bearing space: its ROOT page-table frame.

        The root is an ordinary physical page whose address becomes ``satp``/``hgatp``
        (:meth:`PageMap.create_sptbr`); it is not an implicit VA==PA leaf. Geometry is
        set explicitly so :meth:`_resolve_geometry`'s mapping-less fallback leaves it
        alone: 4 KiB, or 2 MiB for an hgatp root, and the width clamped to the g-stage
        input width under two-stage so a G-stage root is a valid GPA.

        A space whose root a mapping already pins declares nothing -- the consumer
        controls that placement (:meth:`_root_pin_declared`)."""
        frames: Dict[Space, Page] = {}
        for space in self.spaces:
            if not self._table_bearing(space) or self._root_pin_declared(space):
                continue
            target = self._primary_target(targets, space)
            if space.stage is Stage.VS and target is not None and self._table_bearing(target):
                # A VS root under a g-stage is a GPA, so it cannot be the physical page
                # declared below. Every such space is bound before this runs -- by the
                # consumer, or by _synthesize_vs_root_frames -- so reaching here is a
                # wiring bug in that ordering, not a declaration the caller can fix.
                raise AssertionError("two-stage VS space reached root-frame declaration with no bound root frame")
            g_map = self._is_gstage_domain(space)
            # A hypervisor (hgatp) root is at least 16 KiB aligned; the walker has always given
            # it 2 MiB, so keep that rather than change what the tables look like here.
            align = 0xFFFFFFFFFFE00000 if g_map else 0xFFFFFFFFFFFFF000
            size = 0x200000 if g_map else 0x1000
            page = self.add_page(
                Page(
                    space=self.phys,
                    addr=AddrSpec(
                        and_mask=align,
                        qualifiers={
                            RV.AddressQualifiers.ADDRESS_DRAM,
                        },
                    ),
                    reserve_size=size,
                )
            )
            bits = self.physical_addr_bits
            _vs_mode, g_mode = self._walk_modes(space, target)
            if g_map:
                bits = min(bits, RV.RiscvPagingModes.linear_addr_bits(space.paging_mode, gstage=True))
            self._set_geom(page, size, align, bits)
            frames[space] = page
        return frames

    def _resolve_targets(self) -> Dict[Space, List[Space]]:
        """space -> the distinct spaces its mappings point at (its page table's target
        domain(s)). A space usually has one target; a VS space walked under several
        G-stages (switch-hgatp: the same VA->GPA declared once per G space) has several,
        which must share a paging mode since the source's PT nodes live in that domain."""
        targets: Dict[Space, List[Space]] = {space: [] for space in self.spaces}
        for m in self.mappings:
            src_space = m.src.space
            dst_space = m.dst.space
            if dst_space not in targets[src_space]:
                targets[src_space].append(dst_space)
        for src_space, tgts in targets.items():
            modes = {t.paging_mode for t in tgts if self._table_bearing(t)}
            if len(modes) > 1:
                raise ValueError(f"space is walked under G-stages with differing paging modes {modes}; they must match: {src_space!r}")
        return targets

    def _primary_target(self, targets: Dict[Space, List[Space]], space: Space) -> Optional[Space]:
        """The representative target for this space's paging config: the first
        table-bearing target (a G-stage domain) if any, else the first target (a leaf
        physical domain), else None (a space that is only ever a target)."""
        tb = [t for t in targets[space] if self._table_bearing(t)]
        if tb:
            return tb[0]
        return targets[space][0] if targets[space] else None

    def _topo_order(self, targets: Dict[Space, List[Space]]) -> List[Space]:
        """Table-bearing spaces ordered so a space precedes its targets (a source space's
        walk must run before the targets it emits structural mappings into)."""
        post: List[Space] = []
        seen: set = set()

        def visit(space: Space) -> None:
            if space in seen or self._is_leaf(space):
                return
            seen.add(space)
            for tgt in targets.get(space, []):
                visit(tgt)
            post.append(space)  # post-order: targets appended before their source

        for space in self.spaces:
            visit(space)
        return list(reversed(post))  # reverse -> source before target

    # -- coloring (Phase A) ------------------------------------------------

    def _leaf_level(self, mapping: Mapping) -> int:
        """The mapping's leaf level in its source's own stage."""
        return RV.RiscvPageSizes.pt_leaf_level(mapping.src.pagesize)

    def _color_node_at(self, mapping: Mapping, level: int) -> Optional[PTNode]:
        """The declared :class:`PTNode` for ``mapping`` at ``level`` (the ``LEAF`` sentinel
        resolved to the mapping's own leaf level), or ``None`` if undeclared."""
        node = mapping.pt_nodes.get(level)
        if node is None and level == self._leaf_level(mapping):
            node = mapping.pt_nodes.get(LEAF)
        return node

    def _concrete_frame_for_demand(
        self,
        mapping: Mapping,
        parent_level: int,
    ) -> Optional[Page]:
        """Return a caller-owned frame bound to a pointer demand.

        A concrete ``Page`` declaration is keyed by the child table's level,
        while a :class:`FrameDemand` is keyed by the parent PTE that reaches
        that table.  Concrete and generated frames use this same
        conversion so ``PTNode.page`` has one meaning regardless of its type.
        """

        node = self._color_node_at(mapping, parent_level - 1)
        return node.page if node is not None and isinstance(node.page, Page) else None

    def _generated_frame_policy_for_demand(
        self,
        mapping: Mapping,
        parent_level: int,
    ) -> Optional[PTGPage]:
        """Return the RieMap-owned frame policy for a pointer demand."""

        node = self._color_node_at(mapping, parent_level - 1)
        return node.page if node is not None and isinstance(node.page, PTGPage) else None

    def _color_sig(self, mapping: Mapping, level: int):
        """The level-``L`` sharing signature: a **sorted tuple** (never a set -- so it is
        ``PYTHONHASHSEED``-independent) of the forced pointer-PTE bits, the level's declared
        g-stage identity (:func:`_ptgpage_sig`), a pinned-child-frame marker keyed by the
        frame's stable seq, and the leaf/pointer flag. Two mappings sharing a level-``L``
        pointer PTE with equal signatures may coalesce; unequal signatures must be placed in
        different nodes."""
        is_leaf = self._leaf_level(mapping) == level
        parts: List[tuple] = [("is_leaf", is_leaf)]
        node = self._color_node_at(mapping, level)
        if node is not None:
            if node.choice is not None:
                parts.append(("node_choice", _choice_sig(node.choice)))
            for base in sorted(node.attrs):
                raw = node.attrs[base]
                val = raw.preferred if isinstance(raw, Choice) else int(raw)
                # A pointer (non-leaf) PTE's shareability depends only on bits that differ
                # from the natural pointer default (v=1, rest 0): an explicitly declared
                # default must sign identically to an undeclared node, else two data pages
                # -- one carrying an all-default level-N node, one carrying none -- would
                # falsely conflict and (when both pin exact VAs) collide.
                if not is_leaf and not isinstance(raw, Choice) and val == (1 if base == "v" else 0):
                    continue
                parts.append((base, _choice_sig(raw)))
        child = self._color_node_at(mapping, level - 1) if level > self._leaf_level(mapping) else None
        if child is not None:
            # A PTGPage has the same node-level meaning as a concrete Page:
            # it describes the child table reached by this pointer. Its
            # identity contract therefore belongs in the parent pointer's
            # sharing signature.
            parts.extend(
                _ptgpage_sig(
                    child.page,
                    lambda page: self._page_state[page].seq,
                )
            )
        # NB: the node's OWN pinned frame is intentionally not signed here. Two mappings that
        # would share a level-``L`` slot are necessarily in the same parent table, hence the
        # same level-``L`` frame -- so the frame at this level never legitimately separates
        # them, and signing it would spuriously split (a) any frame-pinning mapping from its
        # plain siblings at the shared root table, and (b) a fixed-VA frame-pinning owner from
        # a fixed-VA sibling it shares a prefix (and thus an adopted, interned frame) with.
        # Isolation of a PT-node owner comes from the CHILD-frame marker below instead.
        #
        # A pinned CHILD frame (the table this level's PTE points to) forces a distinct slot
        # here: two mappings whose level-L PTE targets different child tables cannot share the
        # level-L slot. An exact mapping with no child frame still adopts a pinned child via
        # the inner ``child is not None`` check below; when an exact mapping DOES pin its own
        # child frame, that frame must be signed so conflicting pins separate.
        if child is not None and isinstance(child.page, Page):
            parts.append(("child_frame", self._page_state[child.page].seq))
        root_level = RV.RiscvPagingModes.max_levels(mapping.src.space.paging_mode) - 1
        granule = mapping.src.reserve_granule
        if level == root_level and granule is not None:
            # Only a complete root-entry granule steers free full-walk owners away from
            # fixed OS/code root slots. Smaller granules remain allocator-owned; coloring
            # must not force-align those draws.
            root_span = self._root_entry_coverage(mapping.src.space.paging_mode)
            if root_span is not None and granule == root_span:
                parts.append(
                    (
                        "reservation_granule",
                        granule,
                        self._page_state[mapping.src].seq,
                    )
                )
        return tuple(parts)

    @staticmethod
    def _root_entry_coverage(mode: RV.RiscvPagingModes) -> Optional[int]:
        """Bytes covered by one root PTE in ``mode``, or ``None`` when paging is off."""
        levels = RV.RiscvPagingModes.max_levels(mode)
        if mode == RV.RiscvPagingModes.DISABLE or levels == 0:
            return None
        bits = RV.RiscvPagingModes.index_bits(mode, levels - 1)
        if bits is None:
            return None
        return 1 << bits[1]

    def _color_rng(self, space_seq: int, level: int, prefix: Tuple[int, ...]) -> RandNum:
        """A derived RNG for index selection, seeded by the run seed + a stable
        ``(space seq, level, path-prefix)`` key. Independent of ``self.rng``'s draw stream
        (so coloring never shifts other draws) and of ``PYTHONHASHSEED`` (all-int key)."""
        s = self.rng.get_seed() & 0xFFFFFFFFFFFFFFFF
        s = (s * 1000003 + space_seq) & 0xFFFFFFFFFFFFFFFF
        s = (s * 1000003 + level) & 0xFFFFFFFFFFFFFFFF
        for p in prefix:
            s = (s * 1000003 + (p & 0xFFFFFFFF)) & 0xFFFFFFFFFFFFFFFF
        return RandNum(seed=s)

    def _apply_coloring(self) -> None:
        """Per source space, pin VA index bits so mappings with conflicting forced pointer
        attrs (or different pinned frames) are placed in different nodes using selected
        index bits.

        Coloring is speculative: no ``_PageState`` is changed until every source space has
        a complete coloring.  This matters because local fallback tries several
        partitions and indices, and because a later hard conflict must not leave masks from
        an abandoned attempt behind."""
        plan: Dict[Page, Tuple[int, int, bool]] = {}
        for space in self.spaces:
            if self._is_source(space):
                self._color_space(space, plan)
        for page, (clear, value, pinned) in plan.items():
            st = self._page_state[page]
            st.color_clear = clear
            st.color_or = value
            st.color_pinned = pinned

    @staticmethod
    def _commit_color_attempt(plan: Dict[Page, Tuple[int, int, bool]], attempt: Dict[Page, Tuple[int, int, bool]]) -> None:
        plan.clear()
        plan.update(attempt)

    def _plan_color(self, plan: Dict[Page, Tuple[int, int, bool]], page: Page, clear: int, value: int) -> None:
        """Fold one speculative field assignment into ``plan`` without touching page state."""
        old_clear, old_value, _ = plan.get(page, (0, 0, False))
        overlap = old_clear & clear
        if overlap & (old_value ^ value):
            raise ValueError(f"conflicting color assignments for page seq={self._page_state[page].seq}: " f"mask=0x{overlap:x}")
        plan[page] = (old_clear | clear, (old_value & ~clear) | value, True)

    def _resolve_affine_root(self, page: Page) -> Optional[Tuple[Page, int]]:
        """Follow a ``SameAs``/``OffsetFrom`` chain to the freely-placeable allocation
        root whose raw address ultimately determines ``page``'s address, and the
        cumulative offset ``page`` sits above that root.

        A zero-offset chain (``SameAs`` links only) colors the root: pinning the root's
        raw address pins every follower's address identically, whatever space the root
        lives in (a linear bare page, or a physical HPA behind a GPA).  A nonzero
        cumulative offset can also follow the root's draw when the offset is small enough
        that the root-placement logic can force the follower across a page-table boundary.
        ``DerivedFrom`` (bit-mask preimages -- the final index depends on
        drawn bits the mask does not pin) and cyclic relation families have no affine
        root, so they return ``None``. A region may be an affine root but is not freely
        colorable; coloring rejects it at the call site.
        """
        root = page
        offset = 0
        seen: set = set()
        while True:
            if root in seen:
                return None  # relation cycle
            seen.add(root)
            spec = root.addr
            rel = spec.relation
            if rel is None:
                break
            if isinstance(rel, SameAs):
                root = rel.target
            elif isinstance(rel, OffsetFrom):
                offset += rel.delta
                root = rel.target
            else:  # DerivedFrom: drawn-bit preimage, no affine root
                return None
        return root, offset

    def _effective_qualifiers(self, page: Page) -> set:
        """Return the qualifiers owned by ``page``'s affine allocation root.

        Qualifiers classify a free draw; they are not independent metadata on a
        relational follower. ``SameAs`` and ``OffsetFrom`` pages therefore derive their
        address class from the root that was actually drawn. RiescueD lowers any secure
        intent on a linked page onto that root before constructing requests. This keeps
        allocation and emitted PTE tags on one source of truth without mutating frozen
        :class:`AddrSpec` declarations.

        A region root uses the region's placement qualifiers. ``DerivedFrom`` has no
        affine root, so it does not inherit a target's class; declaring qualifiers on
        such a follower is unsupported rather than silently producing an allocation/PTE
        mismatch.
        """
        resolved = self._resolve_affine_root(page)
        if resolved is None:
            if page.addr.qualifiers:
                raise ValueError("address qualifiers on a cyclic or non-affine relation are unsupported")
            return set()
        root, _offset = resolved
        st = self._page_state[root]
        if st.qualifiers is not None:
            return set(st.qualifiers)
        if root.addr.region is not None:
            return set(root.addr.region.qualifiers)
        return set(root.addr.qualifiers)

    def _color_page(self, mapping: Mapping) -> Optional[Page]:
        """The freely-placeable page whose raw address determines ``mapping.src``.

        A normal source colors itself.  A relation follower colors its resolved
        allocation root (see :func:`_resolve_affine_root`) when the chain is a pure
        zero-offset ``SameAs`` family: pinning the root's raw address pins the follower's
        identically.  ``OffsetFrom`` followers, regions, ``DerivedFrom`` and cyclic
        families derive their address after allocation and cannot safely accept an
        independent index mask.
        """
        resolved = self._resolve_affine_root(mapping.src)
        if resolved is None:
            return None
        root, offset = resolved
        if offset != 0 or root.addr.region is not None:
            return None
        return root

    def _color_exact(self, mapping: Mapping) -> Optional[int]:
        """The fixed raw address governing a colored mapping, if any."""
        page = self._color_page(mapping)
        return page.addr.exact if page is not None else None

    def _color_is_free(self, mapping: Mapping) -> bool:
        page = self._color_page(mapping)
        return page is not None and page.addr.exact is None and page.addr.region is None

    @staticmethod
    def _leaf_slot_weight(mapping: Mapping) -> int:
        """Number of leaf PTE slots one mapping occupies in its leaf table."""
        return 16 if mapping.src.pagesize == RV.RiscvPageSizes.S64KB else 1

    def _color_space(self, space: Space, plan: Dict[Page, Tuple[int, int, bool]]) -> None:
        mode = space.paging_mode
        max_levels = RV.RiscvPagingModes.max_levels(mode)
        if mode == RV.RiscvPagingModes.DISABLE or max_levels <= 1:
            return  # no paging levels -> nothing to color
        seen: set = set()
        maps: List[Mapping] = []
        for m in self.mappings:
            if m.src.space is space and m.src not in seen:
                seen.add(m.src)
                maps.append(m)
        if not maps:
            return
        va_bits = RV.RiscvPagingModes.linear_addr_bits(mode)
        space_seq = list(self.spaces).index(space)
        top = max_levels - 1
        # Deduce root-edge placement for OffsetFrom-linked PT-node-frame-pinning pairs, and
        # collect the root slots reserved to them so the trie coloring below avoids those.
        preplaced, reserved_top = self._place_linked_frame_pinners(space_seq, mode, maps, top, va_bits, plan)
        maps = [m for m in maps if m.src not in preplaced]
        # Relation/region addresses are placed as bundles by the transactional allocator.
        # Their final index can depend on an anchor carry or region base, so assigning an
        # independent color here would be discarded by _geom_addr.  Leave them to their
        # family placement; fixed-slot conflicts are still diagnosed during construction.
        maps = [m for m in maps if self._color_is_free(m) or self._color_exact(m) is not None]
        if not maps:
            return
        try:
            self._color_bucket(space_seq, mode, maps, top, va_bits, (), reserved_top, plan)
        except _Spill as sp:
            # Escaped the recursion unabsorbed -> no single-bucket layer had room, so the
            # top level itself is full. Hard-fail with a precise error (never silent).
            raise ValueError(f"coloring exhausted for space {space!r}: level-{sp.level} split needs more index buckets than the {va_bits}-bit VA field can hold") from None

    @staticmethod
    def _pins_frames(mapping: Mapping) -> bool:
        """True when the mapping pins any concrete :class:`Page` PT-node frame (``modify_pt`` /
        ``modify_nonleaf_pt``). A ``PTGPage`` node (g-stage identity declaration) pins no frame."""
        return any(isinstance(node.page, Page) for node in mapping.pt_nodes.values())

    def _place_linked_frame_pinners(
        self,
        space_seq: int,
        mode: RV.RiscvPagingModes,
        maps: List[Mapping],
        top: int,
        va_bits: int,
        plan: Dict[Page, Tuple[int, int, bool]],
    ) -> "Tuple[set, set]":
        """Pin the free allocation root of every affine frame-pinning family to a top-level
        (root) page-table region edge, so the follower at ``root + delta`` falls into the
        NEXT root slot -- a wholly separate subtree.

        A frame-pinning family is a group of frame-pinning mappings whose sources resolve
        (through ``SameAs``/``OffsetFrom`` chains) to one free allocation root with distinct
        cumulative offsets -- e.g. a base ``SameAs`` a bare page plus a child
        ``OffsetFrom`` that bare page. Two mappings that each pin their own PT-node frames
        but share a page-table slot demand contradictory child tables there (a frame
        conflict). The trie coloring below already separates *free* frame-pinners into
        distinct slots via the child-frame signature, but a relational follower is not
        independently placeable -- it follows the root's draw. The only placement that gives
        the family disjoint subtrees is one where the members straddle ROOT-entry
        boundaries: each member's cumulative offset carries it into its own root slot. We
        pin the root so the lowest-offset member sits just below a boundary (root index
        ``k``, lower VPN fields forced so the offsets carry into slots ``k+1``...) and
        reserve every occupied slot against the other frame-pinners the trie coloring
        places.

        Returns ``(preplaced_src_pages, reserved_root_slots)``: the family members removed
        from trie coloring, and the root indices reserved for them.

        Only families whose cumulative offsets each stay within one root granule and land
        in distinct root slots are separated this way; a fixed or unresolvable root, a
        family member that itself pins no frames, or offsets that collide in one root
        slot (which cannot simultaneously occupy two slots) are left to the ordinary path
        (and surface as a clear frame conflict if truly unsatisfiable). Masks are only
        ever applied to the family's root -- never to a relational follower."""
        hi, lo = RV.RiscvPagingModes.index_bits(mode, top)
        eff_hi = min(hi, va_bits - 2)  # never pin the sign bit
        root_slots = 1 << (eff_hi - lo + 1)
        root_granule = 1 << lo
        low_mask = (1 << (eff_hi + 1)) - 1  # the [eff_hi:0] field this placement fully determines

        # resolved root -> frame-pinning members of one affine family, keyed by their
        # cumulative offset from that root. A follower's final VA follows the root's draw,
        # so the family can only be separated by straddling root-region boundaries.
        families: Dict[Page, Dict[int, List[Mapping]]] = {}
        for m in maps:
            if not self._pins_frames(m):
                continue
            resolved = self._resolve_affine_root(m.src)
            if resolved is None:
                continue
            root, offset = resolved
            if root.addr.exact is not None or root.addr.region is not None:
                continue
            families.setdefault(root, {}).setdefault(offset, []).append(m)

        preplaced: set = set()
        reserved: set = set()
        for root, by_offset in families.items():
            if len(by_offset) < 2:
                continue  # a single offset never straddles a boundary
            offsets = sorted(by_offset)
            if offsets[0] < 0 or any(off <= 0 or off >= root_granule for off in offsets[1:]):
                continue
            if len(by_offset[0]) > 1 or any(len(ms) != 1 for ms in by_offset.values()):
                continue  # same-offset members can only share, never straddle
            members = [by_offset[off][0] for off in offsets]
            if len(members) > root_slots:
                continue
            rng = self._color_rng(space_seq, top, (self._page_state[root].seq,))
            # Sit the lowest-offset member at the END of root slot k (its low field =
            # root_granule - first_gap) so the first gap carries into slot k+1. Remaining
            # members follow by their cumulative offset; each must land in its own slot.
            first_gap = offsets[1] - offsets[0]
            if first_gap >= root_granule:
                continue
            # Member i's absolute VA is root + offsets[i]; its root slot is
            # (granule - first_gap + offsets[i]) // granule relative to k.
            member_slots = [(root_granule - first_gap + off) // root_granule for off in offsets]
            if len(set(member_slots)) != len(member_slots):
                continue
            span = 1 + member_slots[-1]
            if span > root_slots:
                continue
            candidates = [k for k in range(root_slots - span + 1) if all((k + i) not in reserved for i in range(span))]
            if not candidates:
                continue
            k = candidates[rng.random_in_range(0, len(candidates))]
            self._plan_color(plan, root, low_mask, (k << lo) | (root_granule - first_gap))
            for slot in member_slots:
                reserved.add(k + slot)
            preplaced.update(m.src for m in members)
        return preplaced, reserved

    def _color_bucket(
        self,
        space_seq: int,
        mode: RV.RiscvPagingModes,
        members: List[Mapping],
        level: int,
        va_bits: int,
        prefix: Tuple[int, ...],
        reserved: "frozenset|set" = frozenset(),
        plan: Optional[Dict[Page, Tuple[int, int, bool]]] = None,
        fanout: int = 1,
    ) -> None:
        """Color one trie node: the ``members`` share the node reached by ``prefix`` (the
        indices chosen at higher levels). Split them by signature at ``level`` into distinct
        index slots when they conflict, recursing per slot to the next level down. A single
        signature pins nothing here (maximal sharing); a descendant overflow spills up into
        this node when it is a single bucket.

        ``reserved`` are index slots at THIS node already claimed out-of-band (root slots reserved
        for edge-placed linked frame-pinning pairs); free members never draw them. It applies only
        at this node -- recursion carries none."""
        if level < 1:
            return
        if plan is None:
            plan = {}
        pointer_members = [m for m in members if self._leaf_level(m) < level]
        leaf_members = [m for m in members if self._leaf_level(m) == level]
        if not pointer_members and not leaf_members:
            return
        hi, lo = RV.RiscvPagingModes.index_bits(mode, level)
        eff_hi = min(hi, va_bits - 2)  # never pin the sign bit (va_bits-1)
        capacity = (1 << (eff_hi - lo + 1)) if eff_hi >= lo else 1

        groups: Dict[tuple, List[Mapping]] = {}
        for m in pointer_members:
            groups.setdefault(self._color_sig(m, level), []).append(m)

        # Exact signature equality is unnecessarily strict for policy Choices. Merge
        # signatures whose declarations have a common value; the shared-node resolver
        # selects that value after placement. Fold immovable (exact-VA) signatures
        # first, then free ones, each class in sorted-signature order: a free child-
        # frame pinner must not claim the plain-exact bucket before an exact sibling
        # that pins a different child has joined it (else the two exacts at one index
        # end up in conflicting buckets). Insertion order of mappings never matters.
        ordered_groups: List[List[Mapping]] = []
        ordered_signatures: List[List[tuple]] = []

        def _has_exact(ms: List[Mapping]) -> bool:
            return any(self._color_exact(m) is not None for m in ms)

        for signature in sorted(sig for sig, ms in groups.items() if _has_exact(ms)) + sorted(sig for sig, ms in groups.items() if not _has_exact(ms)):
            for i, signatures in enumerate(ordered_signatures):
                if _color_sigs_compatible(signatures + [signature]):
                    signatures.append(signature)
                    ordered_groups[i].extend(groups[signature])
                    break
            else:
                ordered_signatures.append([signature])
                ordered_groups.append(list(groups[signature]))
        # Leaves own their final PTE slots; they do not describe the shared child
        # pointer node being colored here. Address allocation and the authoritative
        # topology plan prevent a leaf from occupying the same complete index as a
        # pointer. Treating all leaves as one color bucket is incorrect: fixed leaves
        # spread across many indices and would appear to consume every partial-lane
        # code even though none conflicts with the pointer's complete slot.
        if len(ordered_groups) <= 1:
            # No conflict at this level: recurse unpinned. A descendant overflow spills up
            # to here -- we then split the single signature's members across slots to give
            # the descendant more room.
            attempt = dict(plan)
            try:
                self._color_bucket(
                    space_seq,
                    mode,
                    pointer_members,
                    level - 1,
                    va_bits,
                    prefix,
                    plan=attempt,
                    fanout=fanout * capacity,
                )
                self._commit_color_attempt(plan, attempt)
                return
            except _Spill as sp:
                if sp.level >= level or capacity <= 1:
                    raise
        if len(ordered_groups) > capacity:
            raise _Spill(level)

        # Fast path: assign one bucket per signature. Only a descendant spill causes
        # the local, deterministic fallback below.
        attempt = dict(plan)
        first_spill = None
        try:
            self._color_buckets(
                space_seq,
                mode,
                ordered_groups,
                level,
                va_bits,
                prefix,
                reserved,
                attempt,
                capacity,
                eff_hi,
                lo,
                fanout,
            )
            self._commit_color_attempt(plan, attempt)
            return
        except _Spill as spill:
            first_spill = spill
            if spill.level >= level:
                raise

        # Give overflowing signatures extra slots at this parent.  Search by total bucket
        # count, so the first success uses the minimum necessary partitioning.
        limits = [max(1, len(self._spill_partition(group, level, capacity))) for group in ordered_groups]
        for total in range(len(ordered_groups) + 1, capacity + 1):
            for counts in self._partition_counts(limits, total):
                buckets: List[List[Mapping]] = []
                try:
                    for group, count in zip(ordered_groups, counts):
                        buckets.extend(self._spill_partition(group, level, count))
                except _Spill:
                    # This candidate lends an overflowing signature too few parent
                    # slots.  It is not a global exhaustion: try the next
                    # partition/count before declaring the whole trie impossible.
                    continue
                if len(buckets) != total:
                    continue
                attempt = dict(plan)
                try:
                    self._color_buckets(
                        space_seq,
                        mode,
                        buckets,
                        level,
                        va_bits,
                        prefix,
                        reserved,
                        attempt,
                        capacity,
                        eff_hi,
                        lo,
                        fanout,
                    )
                    self._commit_color_attempt(plan, attempt)
                    return
                except _Spill:
                    # The lane itself was reachable; its descendants need more
                    # parent fanout.  Let the caller partition this signature
                    # instead of exhaustively retrying equivalent bit subsets.
                    raise
        assert first_spill is not None
        raise first_spill

    @staticmethod
    def _partition_counts(limits: List[int], total: int):
        """Yield deterministic per-signature bucket counts summing to ``total``."""

        def visit(pos: int, remaining: int, prefix: Tuple[int, ...]):
            if pos == len(limits):
                if remaining == 0:
                    yield prefix
                return
            minimum_rest = len(limits) - pos - 1
            for count in range(1, min(limits[pos], remaining - minimum_rest) + 1):
                yield from visit(pos + 1, remaining - count, prefix + (count,))

        yield from visit(0, total, ())

    @staticmethod
    def _lane_value(code: int, positions: Tuple[int, ...]) -> int:
        value = 0
        for code_bit, address_bit in enumerate(reversed(positions)):
            if code & (1 << code_bit):
                value |= 1 << address_bit
        return value

    @staticmethod
    def _lane_subsets(eff_hi: int, lo: int, width: int):
        """Yield deterministic bit subsets, preferring high contiguous lanes."""
        bits = tuple(range(eff_hi, lo - 1, -1))
        preferred = bits[:width]
        yield preferred
        low_contiguous = bits[-width:]
        if low_contiguous != preferred:
            yield low_contiguous
        for subset in itertools.combinations(bits, width):
            if subset not in {preferred, low_contiguous}:
                yield subset

    @staticmethod
    def _masked_value_in_range(
        lower: int,
        upper: int,
        bits: int,
        fixed_mask: int,
        fixed_value: int,
        size: int,
    ) -> bool:
        """Whether one aligned fixed/variable-bit value fits an inclusive range."""
        upper -= size - 1
        if lower > upper:
            return False
        bits = max(
            bits,
            upper.bit_length(),
            fixed_mask.bit_length(),
            fixed_value.bit_length(),
        )
        width_mask = (1 << bits) - 1
        fixed_mask &= width_mask
        fixed_value &= fixed_mask
        mismatch = (lower ^ fixed_value) & fixed_mask
        if not mismatch:
            return lower <= upper

        # The least matching value above ``lower`` differs at one pivot:
        # preserve its higher prefix, change a permitted 0 to 1, then use the
        # minimum fixed suffix.  The pivot must be at or above the highest
        # fixed-bit mismatch so every mismatch is replaced.
        lowest_pivot = mismatch.bit_length() - 1
        can_set = (~fixed_mask | fixed_value) & ~lower & width_mask
        can_set &= ~((1 << lowest_pivot) - 1)
        if not can_set:
            return False
        pivot_mask = can_set & -can_set
        pivot = pivot_mask.bit_length() - 1
        high = lower & ~((1 << (pivot + 1)) - 1)
        low = fixed_value & (pivot_mask - 1)
        return high | pivot_mask | low <= upper

    def _color_code_reachable(
        self,
        mapping: Mapping,
        positions: Tuple[int, ...],
        code: int,
        plan: Dict[Page, Tuple[int, int, bool]],
        va_bits: int,
    ) -> bool:
        """Check one partial lane code against the page's effective address domain."""
        page = self._color_page(mapping)
        if page is None:
            return False
        exact = page.addr.exact
        lane_mask = sum(1 << bit for bit in positions)
        lane_value = self._lane_value(code, positions)
        if exact is not None:
            return (exact & lane_mask) == lane_value

        spec = page.addr
        old_mask, old_value, _ = plan.get(page, (0, 0, False))
        if old_mask & lane_mask & (old_value ^ lane_value):
            return False
        physical = page.space is self.phys or page in self._identity_pages
        bits = spec.bits if spec.bits is not None else (self.physical_addr_bits if physical else va_bits)
        width_mask = (1 << bits) - 1
        if lane_value & ~width_mask:
            return False
        user_mask = spec.and_mask if spec.and_mask is not None else 0xFFFFFFFFFFFFFFFF
        variable = user_mask & self._pagesize_align_mask(page) & width_mask
        forced_value = spec.or_mask or 0
        forced_mask = width_mask & ~variable
        fixed_mask = forced_mask | old_mask | lane_mask
        fixed_value = forced_value | old_value | lane_value
        if fixed_mask & forced_mask & (fixed_value ^ forced_value):
            return False
        size = page.reserve_size if page.reserve_size is not None else self._page_size_bytes(page)
        if not physical:
            # Linear allocation spans the complete address-width domain.  The
            # smallest value satisfying a fixed-bit pattern is the pattern
            # itself, so no interval search is needed.
            minimum = fixed_value & fixed_mask
            return minimum + size - 1 <= width_mask

        qualifiers = set(spec.qualifiers)
        if RV.AddressQualifiers.ADDRESS_SECURE in qualifiers:
            ranges = self._color_ranges[RV.AddressQualifiers.ADDRESS_SECURE]
        elif RV.AddressQualifiers.ADDRESS_MMIO in qualifiers:
            ranges = self._color_ranges[RV.AddressQualifiers.ADDRESS_MMIO]
        else:
            ranges = self._color_ranges[RV.AddressQualifiers.ADDRESS_DRAM]
            secure_bit = 1 << 55
            if fixed_mask & secure_bit and fixed_value & secure_bit:
                return False
            fixed_mask |= secure_bit
            fixed_value &= ~secure_bit
        key = (bits, size, fixed_mask, fixed_value, ranges)
        cached = self._color_reachability_cache.get(key)
        if cached is not None:
            return cached
        reachable = any(
            self._masked_value_in_range(
                lower,
                upper,
                bits,
                fixed_mask,
                fixed_value,
                size,
            )
            for lower, upper in ranges
        )
        self._color_reachability_cache[key] = reachable
        return reachable

    @staticmethod
    def _reserved_lane_codes(
        positions: Tuple[int, ...],
        lo: int,
        reserved: "frozenset|set",
    ) -> set:
        """Project reserved index slots onto partial-lane codes at level ``lo``."""
        return {sum(((index >> (bit - lo)) & 1) << code_bit for code_bit, bit in enumerate(reversed(positions))) for index in reserved}

    def _assign_lane_codes(
        self,
        space_seq: int,
        level: int,
        prefix: Tuple[int, ...],
        buckets: List[List[Mapping]],
        positions: Tuple[int, ...],
        plan: Dict[Page, Tuple[int, int, bool]],
        va_bits: int,
        reserved: "frozenset|set",
        lo: int,
        field_width: int,
        leaf_code_capacity: int,
    ) -> Optional[Tuple[Dict[int, int], List[set]]]:
        """Match reachable-domain groups to codes owned by one signature bucket.

        A signature may span several codes (for example normal and secure physical
        ranges), but incompatible signatures may never own the same code.
        """
        code_count = 1 << len(positions)
        exact_codes: List[set] = []
        exact_owner: Dict[int, int] = {}
        code_loads: Dict[Tuple[int, int], int] = {}
        for bi, bucket in enumerate(buckets):
            codes = set()
            for mapping in bucket:
                exact = self._color_exact(mapping)
                if exact is None:
                    continue
                code = sum(((exact >> bit) & 1) << code_bit for code_bit, bit in enumerate(reversed(positions)))
                codes.add(code)
                if self._leaf_level(mapping) == level:
                    load_key = (bi, code)
                    code_loads[load_key] = code_loads.get(load_key, 0) + 1
                    if code_loads[load_key] > leaf_code_capacity:
                        return None
            exact_codes.append(codes)
            for code in codes:
                if exact_owner.setdefault(code, bi) != bi:
                    return None

        domain_groups: List[Tuple[int, List[Mapping], List[int], int]] = []
        for bi, bucket in enumerate(buckets):
            free = [mapping for mapping in bucket if self._color_is_free(mapping)]
            if not free:
                continue
            by_domain: Dict[Tuple[int, ...], List[Mapping]] = {}
            equivalent: Dict[tuple, List[Mapping]] = {}
            for mapping in free:
                page = self._color_page(mapping)
                assert page is not None
                old_mask, old_value, _ = plan.get(
                    page,
                    (0, 0, False),
                )
                spec = page.addr
                domain_key = (
                    spec.bits,
                    spec.and_mask,
                    spec.or_mask,
                    tuple(sorted(qualifier.value for qualifier in spec.qualifiers)),
                    page.pagesize,
                    page.reserve_size,
                    page.space is self.phys or page in self._identity_pages,
                    old_mask,
                    old_value,
                )
                equivalent.setdefault(domain_key, []).append(mapping)
            for mappings in equivalent.values():
                mapping = mappings[0]
                codes = {
                    code
                    for code in range(code_count)
                    if self._color_code_reachable(
                        mapping,
                        positions,
                        code,
                        plan,
                        va_bits,
                    )
                }
                codes = {code for code in codes if code not in exact_owner or exact_owner[code] == bi}
                codes -= self._reserved_lane_codes(positions, lo, reserved)
                if not codes:
                    return None
                by_domain.setdefault(tuple(sorted(codes)), []).extend(mappings)
            for domain, mappings in sorted(by_domain.items()):
                leafs = [mapping for mapping in mappings if self._leaf_level(mapping) == level]
                descendants = [mapping for mapping in mappings if self._leaf_level(mapping) < level]
                chunks = [
                    leafs[index : index + leaf_code_capacity]
                    for index in range(
                        0,
                        len(leafs),
                        leaf_code_capacity,
                    )
                ]
                if descendants:
                    if chunks:
                        chunks[0] = [*chunks[0], *descendants]
                    else:
                        chunks = [descendants]
                for chunk in chunks:
                    rng = self._color_rng(
                        space_seq,
                        level,
                        prefix + tuple(positions) + (bi, len(domain_groups)),
                    )
                    ordered = list(domain)
                    rng.shuffle(ordered)
                    ordered.sort(key=lambda code: code not in exact_codes[bi])
                    weight = sum(self._leaf_level(mapping) == level for mapping in chunk)
                    domain_groups.append((bi, chunk, ordered, weight))

        ordered_groups = sorted(
            enumerate(domain_groups),
            key=lambda item: (len(item[1][2]), item[1][0], item[0]),
        )
        owner = dict(exact_owner)
        assignment: Dict[int, int] = {}

        def place(group_pos: int) -> bool:
            if group_pos == len(ordered_groups):
                return True
            _, (bi, mappings, codes, weight) = ordered_groups[group_pos]
            for code in codes:
                previous = owner.get(code)
                if previous is not None and previous != bi:
                    continue
                load_key = (bi, code)
                previous_load = code_loads.get(load_key, 0)
                if previous_load + weight > leaf_code_capacity:
                    continue
                owner[code] = bi
                code_loads[load_key] = previous_load + weight
                for mapping in mappings:
                    assignment[id(mapping)] = code
                if place(group_pos + 1):
                    return True
                for mapping in mappings:
                    assignment.pop(id(mapping), None)
                if previous_load:
                    code_loads[load_key] = previous_load
                else:
                    code_loads.pop(load_key, None)
                if previous is None:
                    owner.pop(code)
            return False

        if not place(0):
            return None
        return assignment, exact_codes

    def _color_buckets(
        self,
        space_seq: int,
        mode: RV.RiscvPagingModes,
        buckets: List[List[Mapping]],
        level: int,
        va_bits: int,
        prefix: Tuple[int, ...],
        reserved: "frozenset|set",
        plan: Dict[Page, Tuple[int, int, bool]],
        capacity: int,
        eff_hi: int,
        lo: int,
        fanout: int,
    ) -> None:
        """Assign minimal partial-index lanes and recurse with remaining fanout."""
        for bucket in buckets:
            for mapping in bucket:
                exact = self._color_exact(mapping)
                if exact is None and not self._color_is_free(mapping):
                    spec = mapping.src.addr
                    mode_name = "relation" if spec.relation is not None else "region"
                    raise ValueError(f"cannot color {mode_name}-constrained source page " f"at level-{level}: its address mode ignores coloring masks")

        field_width = eff_hi - lo + 1
        minimum_width = max(1, (len(buckets) - 1).bit_length())
        has_exact = any(self._color_exact(mapping) is not None for bucket in buckets for mapping in bucket)
        for width in range(minimum_width, field_width + 1):
            remaining_indices = 1 << (field_width - width)
            subsets = self._lane_subsets(eff_hi, lo, width)
            if not has_exact:
                # With no fixed source codes, use the two contiguous
                # orientations and let a failed descendant spill request more
                # parent fanout. Exhaustively retrying equivalent free-domain
                # subsets grows exponentially and fragments the lane.
                subsets = itertools.islice(subsets, 2)
            for positions in subsets:
                matched = self._assign_lane_codes(
                    space_seq,
                    level,
                    prefix,
                    buckets,
                    positions,
                    plan,
                    va_bits,
                    reserved,
                    lo,
                    field_width,
                    fanout * remaining_indices,
                )
                if matched is None:
                    continue
                assignment, exact_codes = matched
                if level == 1:
                    slots_per_code = fanout * remaining_indices * 512
                    for bucket in buckets:
                        code_weights: Dict[int, int] = {}
                        for mapping in bucket:
                            if self._leaf_level(mapping) < level and self._color_is_free(mapping):
                                code = assignment[id(mapping)]
                                code_weights[code] = code_weights.get(code, 0) + self._leaf_slot_weight(mapping)
                        if any(weight > slots_per_code for weight in code_weights.values()):
                            raise _Spill(level)

                attempt = dict(plan)
                lane_mask = sum(1 << bit for bit in positions)
                recurse_groups: List[Tuple[int, List[Mapping]]] = []
                for bi, bucket in enumerate(buckets):
                    by_code: Dict[int, List[Mapping]] = {}
                    for mapping in bucket:
                        if self._color_is_free(mapping):
                            free_code = assignment[id(mapping)]
                            page = self._color_page(mapping)
                            assert page is not None
                            lane_value = self._lane_value(
                                free_code,
                                positions,
                            )
                            self._plan_color(
                                attempt,
                                page,
                                lane_mask,
                                lane_value,
                            )
                            code = free_code
                        else:
                            exact = self._color_exact(mapping)
                            assert exact is not None
                            code = next(code for code in exact_codes[bi] if (exact & lane_mask) == self._lane_value(code, positions))
                        by_code.setdefault(code, []).append(mapping)
                    recurse_groups.extend(sorted(by_code.items()))

                try:
                    for code, group in recurse_groups:
                        lane_value = self._lane_value(code, positions)
                        self._color_bucket(
                            space_seq,
                            mode,
                            group,
                            level - 1,
                            va_bits,
                            prefix + (lane_mask, lane_value),
                            plan=attempt,
                            fanout=fanout * remaining_indices,
                        )
                    self._commit_color_attempt(plan, attempt)
                    return
                except _Spill:
                    continue
        exact_owner: Dict[int, int] = {}
        for bi, bucket in enumerate(buckets):
            for mapping in bucket:
                exact = self._color_exact(mapping)
                if exact is None:
                    continue
                index = common.bits(exact, eff_hi, lo)
                if exact_owner.setdefault(index, bi) != bi:
                    raise ValueError(f"pinned VAs collide at level-{level} index " f"0x{index:x} across conflicting signatures")
        if exact_owner:
            raise ValueError(f"color conflict among immovable mappings at level-{level}")
        raise _Spill(level - 1)

    def _spill_partition(self, members: List[Mapping], level: int, capacity: int) -> List[List[Mapping]]:
        """Split same-signature ``members`` into up to ``capacity`` buckets so a descendant
        node that overflowed gets more parent slots. Members with the same remaining
        sub-path stay together; distinct sub-paths are spread round-robin."""
        # A spill from level 1 means the compatible mappings overfill one leaf table.
        # They have no differing descendant signature to partition on, so make the
        # minimum number of PTE-capacity bins here (at its level-2 parent).  This is
        # randomized per seed, but uses best-fit placement so the random
        # order cannot fragment an otherwise feasible set. Exact/relational mappings
        # cannot be moved; the normal spill path reports their true fixed-node error.
        if level == 2 and members and all(self._color_is_free(m) for m in members):
            table_capacity = 512
            raw_child_groups: Dict[tuple, List[Mapping]] = {}
            for mapping in members:
                raw_child_groups.setdefault(self._color_sig(mapping, level - 1), []).append(mapping)
            # Match _color_bucket's Choice-domain compatibility merge exactly.
            # Splitting raw signatures independently is unsound: several raw
            # Choice signatures may have a common value and therefore coalesce
            # into one physical leaf table.
            child_groups: List[List[Mapping]] = []
            child_signatures: List[List[tuple]] = []
            for signature in sorted(raw_child_groups):
                for i, signatures in enumerate(child_signatures):
                    if _color_sigs_compatible(signatures + [signature]):
                        signatures.append(signature)
                        child_groups[i].extend(raw_child_groups[signature])
                        break
                else:
                    child_signatures.append([signature])
                    child_groups.append(list(raw_child_groups[signature]))
            # Different level-1 signatures already consume distinct leaf tables
            # inside one level-2 subtree.  Only the most crowded compatible
            # child group determines how many parent slots it needs.
            bucket_count = max((sum(self._leaf_slot_weight(m) for m in group) + table_capacity - 1) // table_capacity for group in child_groups)
            if bucket_count > 1:
                if bucket_count > capacity:
                    raise _Spill(level)
                seed = self.rng.get_seed() & 0xFFFFFFFFFFFFFFFF
                for mapping in sorted(members, key=lambda m: self._page_state[m.src].seq):
                    seed = (seed * 1000003 + self._page_state[mapping.src].seq) & 0xFFFFFFFFFFFFFFFF
                rng = RandNum(seed=seed)
                buckets: List[List[Mapping]] = [[] for _ in range(bucket_count)]
                for group in child_groups:
                    by_weight: Dict[int, List[Mapping]] = {}
                    for mapping in group:
                        by_weight.setdefault(self._leaf_slot_weight(mapping), []).append(mapping)
                    ordered: List[Mapping] = []
                    for weight in sorted(by_weight, reverse=True):
                        shuffled = list(by_weight[weight])
                        rng.shuffle(shuffled)
                        ordered.extend(shuffled)
                    loads = [0] * bucket_count
                    for mapping in ordered:
                        weight = self._leaf_slot_weight(mapping)
                        candidates = [i for i, load in enumerate(loads) if load + weight <= table_capacity]
                        if not candidates:
                            raise _Spill(level)
                        fullest = max(loads[i] for i in candidates)
                        choices = [i for i in candidates if loads[i] == fullest]
                        chosen = choices[rng.random_in_range(0, len(choices))]
                        buckets[chosen].append(mapping)
                        loads[chosen] += weight
                return [bucket for bucket in buckets if bucket]
        subpaths: Dict[tuple, List[Mapping]] = {}
        for m in members:
            key = tuple(self._color_sig(m, ell) for ell in range(level - 1, 0, -1))
            subpaths.setdefault(key, []).append(m)
        buckets: List[List[Mapping]] = [[] for _ in range(min(capacity, len(subpaths)))]
        for i, key in enumerate(sorted(subpaths)):
            buckets[i % len(buckets)].extend(subpaths[key])
        return [b for b in buckets if b]

    # -- geometry ----------------------------------------------------------

    @staticmethod
    def _page_size_bytes(page: Page) -> int:
        return RV.RiscvPageSizes.memory(page.pagesize)

    @staticmethod
    def _pagesize_align_mask(page: Page) -> int:
        return RV.RiscvPageSizes.address_mask(page.pagesize)

    @staticmethod
    def _own_va_bits(paging_mode: RV.RiscvPagingModes, twostage: bool, g_mode: RV.RiscvPagingModes) -> int:
        """The VA draw width a source page gets from its own space's paging mode."""
        va_bits = RV.RiscvPagingModes.linear_addr_bits(paging_mode)
        if twostage and paging_mode == RV.RiscvPagingModes.DISABLE:
            # The "VA" is really a GPA; clamp to the G-stage input width.
            va_bits = min(va_bits, RV.RiscvPagingModes.linear_addr_bits(g_mode, gstage=True))
        return va_bits

    def _shared_va_bits_caps(self, src_bits: Dict[Page, int]) -> Dict[Page, int]:
        """Cap the free VA draw of every source page in a ``SameAs`` group that spans
        differing paging-mode widths, so the one drawn value has the same canonical form
        in every member space (the private-maps sv39/48/57 canaries depend on this).

        A heterogeneous group is capped to (narrowest width - 1) bits: that keeps the
        value below the narrowest mode's sign bit, so it zero-extends identically under
        every member mode (a set sign bit would sign-extend narrow but zero-extend wide,
        splitting the "same" page into two canonical VAs). This holds regardless of which
        member is the root -- including the root-is-narrowest case. Homogeneous groups
        need no cap."""
        group_bits: Dict[Page, List[int]] = {}
        for page, bits in src_bits.items():
            group_bits.setdefault(self._va_root(page, src_bits), []).append(bits)
        return {root: min(bits_list) - 1 for root, bits_list in group_bits.items() if min(bits_list) != max(bits_list)}

    def _resolve_geometry(self, targets: Dict[Space, List[Space]]) -> None:
        """Resolve each page's address geometry (bit width, reservation size + alignment)
        into its ``_PageState`` before allocation.

        A source page's width comes from its space's paging mode (with the bare-VS GPA
        clamp and the cross-space ``SameAs`` cap); a destination/physical page's from the
        g-stage input width (two-stage) or 56 (single-stage). The reservation size is the
        consumer's ``reserve_size`` when set (the authoritative bytes to occupy -- it may be
        smaller than the pagesize, e.g. a large page that only backs one 4 KiB window), else
        the pagesize. Alignment is the pagesize mask, AND-combined with the caller's own mask
        at draw time -- so a big-pagesize alignment survives even when the reservation is
        clamped small. A consumer-pinned ``addr.bits`` is preserved over the derived width.
        Attributes are never touched here -- geometry is pure address math."""
        # First pass: own VA bit width per source page (for the shared-VA cap).
        src_mapping: Dict[Page, Mapping] = {}
        for m in self.mappings:
            src_mapping.setdefault(m.src, m)
        src_bits: Dict[Page, int] = {}
        for src_page in src_mapping:
            space = src_page.space
            pt = self._primary_target(targets, space)
            twostage = pt is not None and self._table_bearing(pt)
            g_mode = pt.paging_mode if twostage and pt is not None else RV.RiscvPagingModes.DISABLE
            src_bits[src_page] = self._own_va_bits(space.paging_mode, twostage, g_mode)
        caps = self._shared_va_bits_caps(src_bits)

        for m in self.mappings:
            src = m.src
            dst = m.dst
            space = src.space
            pt = self._primary_target(targets, space)
            twostage = pt is not None and self._table_bearing(pt)
            g_mode = pt.paging_mode if twostage and pt is not None else RV.RiscvPagingModes.DISABLE

            va_size, va_mask = self._page_size_bytes(src), self._pagesize_align_mask(src)
            pa_size, pa_mask = self._page_size_bytes(dst), self._pagesize_align_mask(dst)

            va_bits = src_bits.get(src, self._own_va_bits(space.paging_mode, twostage, g_mode))
            cap = caps.get(self._va_root(src, src_bits))
            if cap is not None:
                va_bits = min(va_bits, cap)
            pa_bits = RV.RiscvPagingModes.linear_addr_bits(g_mode, gstage=True) if twostage else self.physical_addr_bits

            if src.reserve_size is not None:
                va_size = src.reserve_size
            if dst.reserve_size is not None:
                pa_size = dst.reserve_size

            # NOTE: declaring a non-default g-stage POINTER PTE bit does NOT imply the mapping
            # reserves that pointer's whole span. It is a plain attribute declaration: siblings may
            # share the pointer, and attribute-based coloring (:meth:`_apply_coloring`) is what
            # keeps a sibling that demands the DEFAULT out of a forced node -- it signs the
            # forced bits and splits conflicting signatures into different slots. Deriving an
            # exclusive span here instead made every declared force cost the whole pointer span
            # (1 GiB for a level-2 force under sv39), which a config that randomizes non-leaf
            # bits per page over-subscribes almost immediately.
            #
            # A consumer whose RUNTIME rewrites the pointer PTE does need the span -- every GPA
            # beneath it faults when the PTE is invalidated, so no sibling may live there -- but
            # that is a stronger claim than any declaration carries, and it is stated explicitly
            # as ``reserve_size`` + an alignment ``and_mask`` (RiescueD's ``modify_leaf_pt`` /
            # ``modify_nonleaf_pt``, see ``pt_request_builder._gstage_pointer_span_above``). The
            # ``src.reserve_size`` / ``dst.reserve_size`` handling above is that route.

            # A secure (STEE) leaf is signalled by ADDRESS_SECURE on the destination's own
            # AddrSpec, which the physical draw (see _effective_spec) already applies -- so the
            # HPA is placed with bit 55 set. No separate secure-flag promotion is needed.

            # An identity source draws its PA (VA tied to it), so it takes the PA geometry.
            if src in self._identity_pages:
                id_bits = pa_bits
                if self._is_bare_gstage_source(space):
                    # The identity value doubles as a GPA in this bare g-stage source
                    # space, so it must fit the g-stage input width for GPA == HPA to
                    # hold. Without this the free physical draw can exceed the GPA width
                    # and canonical_va truncates the GPA so it no longer equals the HPA --
                    # breaking identity for OS structures the runtime shares by raw
                    # pointer between M-mode (HPA) and VS-mode (GPA), e.g. hart_context.
                    id_bits = min(id_bits, RV.RiscvPagingModes.linear_addr_bits(space.paging_mode, gstage=True))
                elif space.paging_mode != RV.RiscvPagingModes.DISABLE:
                    # A single-stage / VS identity source's value is sign-extended into a
                    # VA, so it must stay below the space's VA sign bit for VA == PA to
                    # hold. A set sign bit sign-extends the VA into the upper canonical
                    # half while the physical dst keeps the raw value, so VA != PA -- e.g.
                    # the io_htif tohost page, whose faulting VA breaks HTIF (which reads
                    # the tohost symbol as a physical address).
                    id_bits = min(id_bits, va_bits - 1)
                self._set_geom(src, pa_size, pa_mask, id_bits)
            else:
                self._set_geom(src, va_size, va_mask, va_bits)
            # The leaf PTE that reaches ``dst`` carries the SOURCE page's granularity, so
            # its PPN has to be aligned to the source pagesize however small the
            # destination's own pagesize is -- a 4 KiB-aligned target under a 2 MiB leaf
            # is a misaligned PTE the hardware faults on.
            self._set_geom(dst, pa_size, pa_mask & va_mask, pa_bits)

        # SameAs aliases have one raw allocation value. The free root therefore has to
        # satisfy the narrowest width and strictest alignment of every follower; validating
        # only after the draw is too late because canonicalization may already have changed
        # the follower's address.
        changed = True
        while changed:
            changed = False
            for page in self.pages:
                relation = page.addr.relation
                if not isinstance(relation, SameAs):
                    continue
                follower = self._page_state[page]
                target = self._page_state[relation.target]
                bits = min(target.alloc_bits or 64, follower.alloc_bits or 64)
                align = (target.alloc_align or 0xFFFFFFFFFFFFFFFF) & (follower.alloc_align or 0xFFFFFFFFFFFFFFFF)
                size = max(target.alloc_size or 0, follower.alloc_size or 0)
                if (target.alloc_bits, target.alloc_align, target.alloc_size) != (
                    bits,
                    align,
                    size,
                ):
                    target.alloc_bits = bits
                    target.alloc_align = align
                    target.alloc_size = size
                    changed = True

        # Pages with no mapping (a single-stage-disabled physical leaf) take default
        # physical geometry.
        for page in self.pages:
            st = self._page_state[page]
            if st.alloc_size is None:
                size = page.reserve_size if page.reserve_size is not None else self._page_size_bytes(page)
                self._set_geom(page, size, self._pagesize_align_mask(page), self.physical_addr_bits)

    def _va_root(self, page: Page, src_bits: Dict[Page, int]) -> Page:
        seen: set = set()
        while isinstance(page.addr.relation, SameAs) and page.addr.relation.target in src_bits and page not in seen:
            seen.add(page)
            page = page.addr.relation.target
        return page

    def _set_geom(self, page: Page, size: int, align: int, bits: int) -> None:
        st = self._page_state[page]
        st.alloc_size = size
        st.alloc_align = align
        # A consumer-pinned width takes precedence over the derived one.
        st.alloc_bits = page.addr.bits if page.addr.bits is not None else bits

    @staticmethod
    def _alloc_alignment(st: _PageState) -> Optional[int]:
        """The byte alignment ``st.alloc_align`` (a mask) demands, or None for no extra one."""
        mask = st.alloc_align
        if not mask:
            return None
        return mask & -mask

    def _geom_addr(self, page: Page) -> AddrSpec:
        """A copy of ``page.addr`` with resolved alignment + bit width folded into a free
        draw. Pinned (exact) / relational specs are placed as-is.

        A ``region`` spec is folded too (bit width is unused there): the allocator
        derives its placement step from the mask's lowest set bit, so without the
        folded alignment a superpage member could land page-aligned but not
        superpage-aligned -- a faulting misaligned leaf PTE. AND-combining alignment
        masks is idempotent, so a caller that already folded is unaffected."""
        spec = page.addr
        st = self._page_state[page]
        if spec.exact is not None or spec.relation is not None:
            qualifiers = st.qualifiers if st.qualifiers is not None else spec.qualifiers
            return dataclasses.replace(spec, bits=st.alloc_bits, qualifiers=qualifiers)
        user = spec.and_mask if spec.and_mask is not None else 0xFFFFFFFFFFFFFFFF
        align = st.alloc_align if st.alloc_align is not None else self._pagesize_align_mask(page)
        qualifiers = st.qualifiers if st.qualifiers is not None else spec.qualifiers
        # Fold coloring: clear the colored index fields and OR in the chosen values. When
        # coloring is off (color_clear/color_or 0, color_pinned False) this is exactly the
        # pre-coloring result -- ``~0`` leaves the and_mask untouched and ``or_mask`` keeps
        # its original None-vs-0 identity (only rewritten when a value is actually pinned).
        or_mask = spec.or_mask
        if st.color_or:
            or_mask = (spec.or_mask or 0) | st.color_or
        return dataclasses.replace(spec, and_mask=align & user & ~st.color_clear, or_mask=or_mask, bits=st.alloc_bits, qualifiers=qualifiers, pinned=st.color_pinned)

    # -- allocation --------------------------------------------------------

    def _allocate_pages(self, targets: Dict[Space, List[Space]]):
        """Draw every page's address (per-space pool) plus regions."""
        requests: List[AllocRequest] = []
        # Consumer-pinned roots lead any relation family, but a root is not an
        # implicit address/coverage claim in the space it roots.
        rooted = self._pinned_root_frame_spaces()
        source_pages = {mapping.src for mapping in self.mappings}
        mapped_pages = {page for mapping in self.mappings for page in (mapping.src, mapping.dst)}
        relation_targets = {page.addr.relation.target for page in self.pages if isinstance(page.addr.relation, (SameAs, OffsetFrom))}

        def contained_claim_owner(page: Page, claim_size: int) -> Page:
            """Collapse only aliases/offsets proven inside one declared span."""
            current = page
            offset = 0
            owner = page
            seen: set[Page] = set()
            while current not in seen:
                seen.add(current)
                relation = current.addr.relation
                if isinstance(relation, SameAs):
                    target = relation.target
                elif isinstance(relation, OffsetFrom) and relation.delta >= 0:
                    offset += relation.delta
                    target = relation.target
                else:
                    break
                target_span = max(
                    (target.reserve_size if target.reserve_size is not None else self._page_size_bytes(target)),
                    self._page_state[target].alloc_size or 0,
                )
                if offset + claim_size <= target_span:
                    owner = target
                current = target
            return owner

        for page in self.pages:
            st = self._page_state[page]
            size = page.reserve_size if page.reserve_size is not None else self._page_size_bytes(page)
            if page.addr.region is not None and page.reserve_size is not None:
                size = page.reserve_size
            # What the page's translation OCCUPIES, as opposed to what it is backed by: its own
            # leaf span. Backing (real memory) stays ``size``.
            covered = RV.RiscvPageSizes.memory(page.pagesize)
            if page in self._identity_pages:
                # VA == PA: draw the value from the global physical pool using the
                # page's qualifiers, but assign its own (linear) space so the solver also
                # reserves it there -- a free VA draw in that space then cannot collide.
                # A PHYSICAL request carrying a space_key is the identity constraint; the
                # solver dual-reserves the drawn value (see allocator._dual_reserve).
                addr_type, space_key = RV.AddressType.PHYSICAL, page.space
            elif page.space is self.phys:
                # A leaf (physical) page is real physical memory drawn from the global
                # physical pool.
                addr_type, space_key = RV.AddressType.PHYSICAL, None
            else:
                # Every other page draws from its own space's VA/GPA pool. A GPA page
                # constrained ``SameAs`` a physical page still lives in its own space, but
                # its relation ties it to the physical draw, so no independent draw happens.
                addr_type, space_key = RV.AddressType.LINEAR, page.space
            extra: Tuple[Space, ...] = ()
            claims: List[SpanClaim] = []
            if addr_type == RV.AddressType.PHYSICAL:
                backing_owner = contained_claim_owner(page, size)
                # Only a direct fixed leaf declaration may opt into contained
                # physical aliasing.  A RiescueD SameAs/OffsetFrom child can
                # legitimately sit inside an exact runtime backing span, but
                # it belongs to that relation family and must retain the
                # owner's claim key rather than being reclassified as a JSON
                # alias.  Identity mappings have a linear-space Page, so
                # ``backing_owner is page``—rather than ``page.space``—is
                # what distinguishes a direct physical declaration.
                # Identity sources draw once in the physical pool and share that
                # backing with a SameAs destination through the relation-family
                # owner, not the JSON fixed-leaf-alias key. Followers of an
                # identity source must use the same family key.
                direct_fixed_leaf = (
                    page not in self._identity_pages
                    and backing_owner not in self._identity_pages
                    and backing_owner in mapped_pages
                    and backing_owner.addr.exact is not None
                    and (backing_owner is page or (isinstance(page.addr.relation, SameAs) and backing_owner.reserve_size is None))
                )
                fixed_leaf_alias = direct_fixed_leaf
                contained_relation_alias = page in relation_targets or (backing_owner is not page and isinstance(page.addr.relation, (SameAs, OffsetFrom)))
                backing_share = (ClaimKind.BACKING, "fixed-leaf-alias") if fixed_leaf_alias else backing_owner
                claims.append(
                    SpanClaim(
                        addr_type=RV.AddressType.PHYSICAL,
                        space=None,
                        size=size,
                        kind=ClaimKind.BACKING,
                        share_key=backing_share,
                        allow_contained_share=(fixed_leaf_alias or contained_relation_alias),
                    )
                )
                if page in mapped_pages:
                    claims.append(
                        SpanClaim(
                            addr_type=RV.AddressType.PHYSICAL,
                            space=None,
                            size=covered,
                            kind=ClaimKind.COVERAGE,
                            share_key=backing_share,
                            allow_contained_share=(fixed_leaf_alias or contained_relation_alias),
                        )
                    )
                coverage_spaces = ((space_key,) if space_key is not None else ()) + extra
            else:
                address_size = page.reserve_size if page.reserve_size is not None else (size if page in source_pages else 0x1000)
                relation_anchor = page.addr.relation.target if isinstance(page.addr.relation, SameAs) else None
                fixed_leaf_alias = (
                    page in source_pages
                    and page.reserve_granule is None
                    and (page.addr.exact is not None or (relation_anchor is not None and relation_anchor.addr.exact is not None))
                    and not (relation_anchor is not None and relation_anchor.reserve_granule is not None)
                )
                address_share = (
                    (
                        ClaimKind.ADDRESS,
                        space_key,
                        "fixed-leaf-alias",
                    )
                    if fixed_leaf_alias
                    else contained_claim_owner(page, address_size)
                )
                if space_key is not None:
                    claims.append(
                        SpanClaim(
                            addr_type=RV.AddressType.LINEAR,
                            space=space_key,
                            size=address_size,
                            kind=ClaimKind.ADDRESS,
                            share_key=address_share,
                            allow_contained_share=fixed_leaf_alias,
                        )
                    )
                    if page in source_pages:
                        coverage_owner = contained_claim_owner(page, covered)
                        granule_family = coverage_owner if coverage_owner.reserve_granule is not None else None
                        claims.append(
                            SpanClaim(
                                addr_type=RV.AddressType.LINEAR,
                                space=space_key,
                                size=covered,
                                kind=ClaimKind.COVERAGE,
                                share_key=coverage_owner,
                                allow_contained_share=(granule_family is not None or coverage_owner is not page),
                            )
                        )
                coverage_spaces = extra
            is_root_frame = page in self._root_frames.values()
            for space in coverage_spaces:
                if space is None:
                    continue
                # Two frames sharing the ``translation`` key occupy the same GPA pool with the
                # same kind of leaf. They must never nest: a small frame inside a larger one's
                # leaf span falls in a PTE slot that span already covers, which ``plan_topology``
                # can only report as a non-retriable conflict.
                claims.append(
                    SpanClaim(
                        addr_type=RV.AddressType.LINEAR,
                        space=space,
                        size=covered,
                        kind=(ClaimKind.RESERVATION if is_root_frame else ClaimKind.STRUCTURAL),
                        share_key=(
                            (
                                ClaimKind.RESERVATION,
                                space,
                                page,
                            )
                            if is_root_frame
                            else (
                                ClaimKind.COVERAGE,
                                space,
                                "translation",
                            )
                        ),
                    )
                )
            # Optional containing-span ownership in this page's own address domain.
            # Never inflate physical BACKING: a large root-PTE granule belongs in the
            # VA/GPA pool only (identity sources dual-reserve their linear space_key).
            if page.reserve_granule is not None:
                if addr_type == RV.AddressType.LINEAR and space_key is not None:
                    granule_space = space_key
                    granule_footprint = address_size
                    granule_share = contained_claim_owner(page, granule_footprint)
                elif page in self._identity_pages and space_key is not None:
                    granule_space = space_key
                    granule_footprint = page.reserve_size if page.reserve_size is not None else covered
                    granule_share = contained_claim_owner(page, granule_footprint)
                else:
                    granule_space = None
                    granule_footprint = 0
                    granule_share = page
                if granule_space is not None:
                    claims.append(
                        SpanClaim(
                            addr_type=RV.AddressType.LINEAR,
                            space=granule_space,
                            size=granule_footprint,
                            kind=ClaimKind.RESERVATION,
                            share_key=granule_share,
                            allow_contained_share=True,
                            granule=page.reserve_granule,
                        )
                    )
            requests.append(
                AllocRequest(
                    page=page,
                    addr_type=addr_type,
                    size=size,
                    addr=self._geom_addr(page),
                    validation_size=st.alloc_size,
                    # A free draw already has the resolved alignment folded into its mask;
                    # a pinned (exact / relation-derived) address is placed as declared, so
                    # the solver is the only place the geometry can still be enforced.
                    align=self._alloc_alignment(st),
                    space_key=space_key,
                    extra_linear_spaces=extra,
                    seq=st.seq,
                    # Consumer-pinned frames must lead their relation family.
                    # Floating roots are independent identity pages: placing
                    # them after constrained leaves lets their dual-pool draw
                    # avoid accepted leaf geometry instead of backtracking
                    # every leaf around a random root.
                    place_first=page in rooted,
                    claims=tuple(claims),
                )
            )
        solved = self.allocator.solve_in_place(
            requests,
            self._regions,
            self.addrgen,
            self.rng,
            reserved_spans=self._reserved_spans,
        )
        for req in requests:
            self._page_state[req.page].allocated = req.allocated
        return solved

    # -- page map wiring ---------------------------------------------------

    def _config_for(self, space: Space) -> PagingParams:
        """The per-space paging *policy* (no modes -- those live on the space / PageMap)."""
        return PagingParams(
            physical_addr_bits=self.physical_addr_bits,
            priv_mode=space.priv_mode,
            secure_pt_probability=space.secure_pt_probability,
        )

    def _walk_modes(self, space: Space, target: Optional[Space]) -> Tuple[RV.RiscvPagingModes, RV.RiscvPagingModes]:
        """The (vs_mode, g_mode) walk context for a space -- the modes the walker reads.

        A G-stage space (its own root is an hgatp) walks with VS paging DISABLE (so its
        leaves are treated as user, u=1) and the g-stage mode carried by the space itself.
        A VS/single space walks with its own mode; if it targets a table-bearing (G-stage)
        domain, that domain's mode is its g-stage mode (a two-stage walk), else DISABLE.
        """
        if self._is_bare_gstage_source(space):
            return RV.RiscvPagingModes.DISABLE, space.paging_mode
        if target is not None and self._table_bearing(target):
            return space.paging_mode, target.paging_mode
        return space.paging_mode, RV.RiscvPagingModes.DISABLE

    def _create_page_map(self, space: Space, targets: Dict[Space, List[Space]]) -> None:
        config = self._config_for(space)
        _vs_mode, g_mode = self._walk_modes(space, self._primary_target(targets, space))
        # A g-stage domain's PageMap is a g_map: its root is 16 KiB aligned and its leaves
        # are treated as user. Always ``stage is Stage.G`` now -- the one predicate that
        # also drives pool isolation.
        g_map = self._is_gstage_domain(space)
        page_map = PageMap(
            paging_mode=space.paging_mode,
            paging_g_mode=g_mode,
            addrgen=self.addrgen,
            featmgr=config,
            g_map=g_map,
        )
        # A top-level PTNode(page=frame) makes the root page table itself structural: place
        # it at the frame's resolved base (recursive / self-referencing page table). Otherwise
        # the root is at the address the solve drew for this space's engine-declared root
        # frame (_declare_root_frames) -- either way the walker never draws it.
        root_binding = self._root_pin_binding(space, targets)
        if root_binding is None:
            root_frame = self._root_frames.get(space)
            pinned = None if root_frame is None else self._page_state[root_frame].allocated
            root_binding = (
                None
                if pinned is None
                else (
                    pinned,
                    pinned,
                )
            )
        pinned = None if root_binding is None else root_binding[0]
        page_map.pinned_sptbr = pinned
        page_map.pinned_sptbr_backing = None if root_binding is None else root_binding[1]
        page_map.initialize()
        self._page_maps[space] = page_map

    def _root_pin_binding(
        self,
        space: Space,
        targets: Dict[Space, List[Space]],
    ) -> Optional[Tuple[int, int]]:
        """Resolved root input and physical backing addresses."""

        root_level = RV.RiscvPagingModes.max_levels(space.paging_mode) - 1
        primary_target = self._primary_target(targets, space)
        twostage = primary_target is not None and self._table_bearing(primary_target)
        g_mode = primary_target.paging_mode if twostage and primary_target is not None else RV.RiscvPagingModes.DISABLE
        binding: Optional[Tuple[int, int]] = None
        declared = self._declared_roots.get(space)
        if declared is not None:
            binding = self._resolved_frame_binding(
                declared,
                twostage,
                g_mode,
            )
        for m in self.mappings:
            if m.src.space is not space:
                continue
            node = m.pt_nodes.get(root_level)
            if node is None or not isinstance(node.page, Page):
                continue  # a PTGPage declares g-stage attrs, never a pinned root frame
            resolved = self._resolved_frame_binding(
                node.page,
                twostage,
                g_mode,
            )
            if binding is not None and binding != resolved:
                raise ValueError(f"conflicting root-level PTNode frames in space " f"{space!r}: root pinned to both {binding!r} and " f"{resolved!r}")
            binding = resolved
        return binding

    def _declared_leaf_signature(self, mapping: Mapping) -> tuple:
        """Declaration-layer leaf signature (attrs/choice/frame), before addresses.

        Compared only between repeats of the *same* source Page, where one declaration would
        silently lose to the other at install. Distinct pages that alias one VA are checked
        instead on packed PTE bits after defaults, since two spellings of the same leaf are
        legal there; that comparison needs a built page and so lives at the install layer.
        """
        leaf_level = RV.RiscvPageSizes.pt_leaf_level(mapping.src.pagesize)
        node = mapping.pt_nodes.get(LEAF)
        if node is None:
            node = mapping.pt_nodes.get(leaf_level)
        if node is None:
            return (_sig_atom(mapping.src.pagesize), None, None, None)
        # Pinned frames sign by object identity; equivalent PTGPages sign by value.
        frame: Any = None
        if isinstance(node.page, PTGPage):
            frame = ("generated", tuple(_ptgpage_sig(node.page)))
        elif node.page is not None:
            frame = ("pinned", node.page)
        return (
            _sig_atom(mapping.src.pagesize),
            _sig_atom(dict(node.attrs)),
            None if node.choice is None else _choice_sig(node.choice),
            frame,
        )

    def _install_mapping(self, mapping: Mapping, targets: Dict[Space, List[Space]]) -> WalkerPage:
        src = mapping.src
        dst = mapping.dst
        space = src.space
        primary_target = self._primary_target(targets, space)
        config = self._config_for(space)
        # A two-stage walk means this source space walks a table-bearing (G-stage)
        # target: the leaf's target address is a GPA and the tree emits structural
        # g-stage identity mappings. A source whose target is a leaf (physical) domain
        # -- a single-stage map, or a G-stage map that is itself the source (bare VS) --
        # is not.
        twostage = primary_target is not None and self._table_bearing(primary_target)
        g_mode = primary_target.paging_mode if twostage and primary_target is not None else RV.RiscvPagingModes.DISABLE
        src_state = self._page_state[src]
        dst_state = self._page_state[dst]
        va = canonical_va(src_state.allocated, space.paging_mode, twostage, g_mode, gstage_source=self._is_gstage_domain(space))
        phys = canonical_pa(dst_state.allocated, twostage, g_mode)
        # The walk-context modes (what the config carried): a bare-VS g-stage source has
        # vs_mode DISABLE and a real g-stage mode; a two-stage VS source has its own vs_mode
        # and the target's g-stage mode.
        vs_walk_mode, g_walk_mode = self._walk_modes(space, primary_target)
        # A g-stage context (this space is virtualized with a g-stage mode) needs the
        # u/r/w/x/a/d level/glevel defaults seeded even when it is itself the g-stage
        # source (bare VS-stage walking hgatp), where the target is a physical leaf.
        gstage_ctx = self._is_bare_gstage_source(space) or twostage

        page_map = self._page_maps[space]
        page = WalkerPage(
            page_map=page_map,
            featmgr=config,
            addrgen=self.addrgen,
            pagesize=src.pagesize,
        )
        page.lin_addr = va
        page.phys_addr = phys

        # G-stage geometry + the u/r/w/x/a/d level/glevel defaults (mirror builder
        # _create_page -> _apply_gstage_page_sizes). Run before folding the declared
        # pt_nodes / glevel forces so a forced level/glevel value overrides the default.
        if gstage_ctx:
            self._apply_gstage_page_sizes(page, mapping, vs_walk_mode, g_walk_mode)

        # Fold declared node attributes, pinned frames, and NAPOT state onto the walker page.
        leaf_level = RV.RiscvPageSizes.pt_leaf_level(src.pagesize)
        self._install_pt_node_attrs(page, mapping, twostage, g_mode, leaf_level)

        # A secure (STEE) leaf is signalled by ADDRESS_SECURE on the destination frame's
        # AddrSpec (where bit 55 is placed), not a mapping flag: the leaf PTE carries the
        # secure bit iff its target is a secure address. Resolve it effectively so a
        # follower whose physical anchor alone declared ``secure=1`` still tags the leaf.
        if RV.AddressQualifiers.ADDRESS_SECURE in self._effective_qualifiers(dst):
            page.attrs["secure"] = 1

        # A mapping OUT OF a g-stage domain emits a GPA -> HPA g-stage leaf: a bare-VS source
        # (the guest walks hgatp directly), a two-stage GPA -> HPA leaf, an auto-identity leaf
        # for a pinned g-stage PT-node frame, or a g-stage root self-map. Every such leaf shares
        # one invariant handled here.
        #
        # R/W/X/A/D come straight from the page's already-resolved leaf value
        # (``{base}_level{leaf}`` in ``page.attrs``, folded from pt_nodes just above): these are
        # exactly the permissions a g-stage permission / A-D fault test declared, honored
        # verbatim (default 1). The valid bit needs no touch -- the fold already set
        # ``v_level{leaf}`` (an explicit invalid-PTE force verbatim, else the base fallback), and
        # a base ``v=0`` never reaches the non-leaf table pointers.
        #
        # U is the exception. A g-stage leaf is always reached as a *user* access (the guest
        # runs in VS/VU, which g-stage sees as user), so U defaults to 1. Crucially, RiescueD's
        # per-level expansion derives a single-index ``u_level{leaf}`` from the (often
        # undeclared -> 0) VS-stage u bit; that VS-stage intent must NOT reach the g-stage leaf,
        # or an ordinary supervisor/undeclared-u guest page becomes a U=0 g-stage leaf and faults
        # every guest access (regression: a bare-VS data page, a two-stage supervisor guest's
        # modify_pt PT-node frame, and the g-stage root self-map all took guest-page faults this
        # way). Only an EXPLICIT g-stage-leaf u may drive U to 0: either the mapping's own leaf
        # PTNode (a two-stage GPA -> HPA leaf's resolved g-stage u, or a forced g-stage user
        # fault) or a :class:`PTGPage` glevel force threaded to ``page.gstage_nodes``. The VS
        # single-index ``u_level{leaf}`` -- which ``_apply_gstage_page_sizes`` may also have
        # seeded into ``page.attrs`` -- is never that authority.
        if self._is_gstage_domain(space):
            for base in ("r", "w", "x", "a", "d"):
                val = int(page.attrs.get(f"{base}_level{leaf_level}", 1))
                page.attrs[base] = val
                page.attrs[f"{base}_level{leaf_level}"] = val
            # U precedence: (1) a two-stage GPA -> HPA leaf's own resolved g-stage u, carried on
            # the mapping's leaf PTNode -- but ONLY for a proper g-stage-target source (a bare-VS
            # source's leaf PTNode instead holds the VS single-index u, which must NOT leak); then
            # (2) a PTGPage glevel u force on ``page.gstage_nodes``; else (3) the always-user
            # default 1. The VS single-index ``u_level{leaf}`` in ``page.attrs`` is never the
            # authority -- that is the leak this guards against.
            explicit_u = None
            leaf_node = mapping.pt_nodes.get(LEAF) or mapping.pt_nodes.get(leaf_level)
            if self._is_bare_gstage_source(space):
                if leaf_node is not None:
                    explicit_u = leaf_node.attrs.get("_gstage_leaf_u")
            else:
                if leaf_node is not None:
                    explicit_u = leaf_node.attrs.get("u")
            # Inside a g-stage domain the source page IS the g-stage leaf, so its own pagesize
            # gives the g-leaf level a PTGPage force would be keyed at.
            g_leaf = RV.RiscvPageSizes.pt_leaf_level(src.pagesize)
            u_forced = None
            for node in page.gstage_nodes.values():
                bits = node.level_attrs(src.pagesize).get(g_leaf)
                if bits is not None and bits.get("u") is not None:
                    u_forced = bits["u"]
                    break
            if explicit_u is not None:
                u_val = explicit_u.preferred if isinstance(explicit_u, Choice) else int(explicit_u)
            elif u_forced is not None:
                u_val = u_forced.preferred if isinstance(u_forced, Choice) else int(u_forced)
            else:
                u_val = 1
            page.attrs["u"] = u_val
            page.attrs[f"u_level{leaf_level}"] = u_val

        page_map.add_page(page=page)
        self.mapping_addrs[src] = (va, phys)
        return page

    def _gstage_leaf_pagesize(self, mapping: Mapping) -> Optional[RV.RiscvPageSizes]:
        """The pagesize of the g-stage translation of ``mapping``'s leaf target, or ``None``.

        A ``Mapping`` is one stage, so this is never a field on it -- it is the geometry of the
        page that carries that g-stage leaf. For a two-stage VS mapping the leaf targets a
        :attr:`Stage.G` page whose own ``Mapping`` is the GPA -> HPA leaf, so it is
        ``dst.pagesize``. For a bare-g-stage source (the guest walks hgatp directly) the source
        page IS that leaf, so it is ``src.pagesize``. A single-stage mapping has no g-stage."""
        if mapping.dst.space.stage is Stage.G:
            return mapping.dst.pagesize
        if self._is_bare_gstage_source(mapping.src.space):
            return mapping.src.pagesize
        return None

    def _gstage_nonleaf_pagesize(self, mapping: Mapping) -> Optional[RV.RiscvPageSizes]:
        """The pagesize of the g-stage translation of ``mapping``'s non-leaf node frames.

        Declared per node -- a :class:`PTGPage` (a synthesized identity RieMap allocates) or a
        pinned :class:`Page` frame. Both consumers set one non-leaf pagesize across a mapping's
        non-leaf nodes, so the shallowest one is that mapping's non-leaf geometry; ``None`` means the
        mapping declares none (single-stage, or a bare-g-stage source that builds no VS table).
        Levels are visited in ascending order so the answer never depends on dict order."""
        leaf_level = self._leaf_level(mapping)
        levels = sorted(key for key in mapping.pt_nodes if isinstance(key, int) and key != leaf_level)
        for level in levels:
            page = mapping.pt_nodes[level].page
            if page is not None:
                pagesize = page.pagesize
                return pagesize.preferred if isinstance(pagesize, Choice) else pagesize
        return None

    def _apply_gstage_page_sizes(self, page: WalkerPage, mapping: Mapping, paging_mode: RV.RiscvPagingModes, paging_g_mode: RV.RiscvPagingModes) -> None:
        """Resolve the g-stage leaf page size onto the walker page and seed the u/r/w/x/a/d
        level/glevel defaults.

        The leaf pagesize is the only value the walker needs from outside its own ``pt_nodes``:
        each non-leaf node's geometry travels with the node itself (its ``PTGPage`` /
        pinned frame, read by ``Pagetables._gstage_nonleaf_geometry``)."""
        gstage_leaf_ps = self._gstage_leaf_pagesize(mapping) or RV.RiscvPageSizes.S4KB
        gstage_nonleaf_ps = self._gstage_nonleaf_pagesize(mapping) or RV.RiscvPageSizes.S4KB
        page.gstage_leaf_pagesize = gstage_leaf_ps

        for attr in ["u", "r", "w", "x", "a", "d"]:
            resolve.setup_uwrx_bit(
                attr,
                attrs=page.attrs,
                paging_mode=paging_mode,
                paging_g_mode=paging_g_mode,
                final_pagesize=page.pagesize,
                gstage_vs_leaf_final_pagesize=gstage_leaf_ps,
                gstage_vs_nonleaf_final_pagesize=gstage_nonleaf_ps,
            )

    # -- result ------------------------------------------------------------

    def _result(self, solved) -> AllocationResult:
        spaces = {space: SpaceResult(space, pm) for space, pm in self._page_maps.items()}
        # A secure page reached by no mapping has no leaf PTE to carry the STEE secure
        # bit (paging disabled: VA==PA, accessed directly), so the tag must live in its
        # own address. Mapped pages keep it in the leaf PTE (see pagetables.py).
        mapped = {m.src for m in self.mappings} | {m.dst for m in self.mappings}

        def _tag(page: Page, addr: Optional[int]) -> Optional[int]:
            if addr is None or page in mapped:
                return addr
            if RV.AddressQualifiers.ADDRESS_SECURE in self._effective_qualifiers(page):
                return addr | 0x0080000000000000
            return addr

        addresses = {page: _tag(page, self._page_state[page].allocated) for page in self.pages}
        page_addrs: Dict[Page, Tuple[int, int]] = dict(self.mapping_addrs)
        for page in self.pages:
            if page not in page_addrs:
                allocated = _tag(page, self._page_state[page].allocated)
                page_addrs[page] = (allocated, allocated)
        page_metas: Dict[Page, PageMeta] = {}
        for m in self.mappings:
            # A bare-VS g-stage source is itself the GPA -> HPA g-stage leaf, so its
            # g-stage leaf pagesize is just the page's own pagesize (there is no separate
            # VS leaf to carry g-stage geometry). Reporting it lets a consumer resolve a
            # ``g_level=leaf`` PTE reference for such a page (e.g. RiescueD's read_pte/
            # write_pte); leaving it None would make that reference unresolvable.
            gstage_leaf = self._gstage_leaf_pagesize(m)
            gstage_nonleaf = self._gstage_nonleaf_pagesize(m)
            node_pagesizes: Dict[int, RV.RiscvPageSizes] = {}
            source_result = spaces.get(m.src.space)
            target_result = spaces.get(m.dst.space)
            if source_result is not None and target_result is not None and m.src in self.mapping_addrs:
                steps, _ = source_result.walk(self.mapping_addrs[m.src][0])
                for step in steps:
                    if step.ptg_gpa is None:
                        continue
                    try:
                        node_pagesizes[step.level] = target_result.gstage_identity(step.ptg_gpa).pagesize
                    except KeyError:
                        continue
            immediate_nonleaf = RV.RiscvPageSizes.pt_leaf_level(m.src.pagesize) + 1
            if immediate_nonleaf in node_pagesizes:
                gstage_nonleaf = node_pagesizes[immediate_nonleaf]
            page_metas[m.src] = PageMeta(
                pagesize=m.src.pagesize,
                gstage_vs_leaf_pagesize=gstage_leaf,
                gstage_vs_nonleaf_pagesize=gstage_nonleaf,
                gstage_node_pagesizes=node_pagesizes,
            )
        for page in self.pages:
            if page not in page_metas:
                page_metas[page] = PageMeta(pagesize=page.pagesize)
        return AllocationResult(
            spaces=spaces,
            region_bases=solved.region_bases,
            addresses=addresses,
            physical_intervals=self.addrgen.allocated_physical_intervals(),
            linear_intervals=self.addrgen.allocated_linear_intervals(),
            page_addrs=page_addrs,
            page_metas=page_metas,
        )
