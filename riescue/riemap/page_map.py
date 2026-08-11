# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Mutable walker model used to construct one address space's page tables."""

from __future__ import annotations
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Callable, Optional

import riescue.lib.enums as RV
import riescue.riemap.pagetables as pagetables
from riescue.riemap.config import PagingParams
from riescue.riemap.attributes import page_default_attrs
from riescue.riemap.addrgen import AddrGen
from riescue.lib.rand import RandNum

if TYPE_CHECKING:
    # Declaration-layer types the builder threads onto a walker page. Type-only: the walker
    # core imports nothing from ``request`` at runtime.
    from riescue.riemap.request import PTGPage


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmittedGStageIdentity:
    """The canonical declaration actually accepted for one synthesized identity."""

    gpa: int
    hpa: int
    pagesize: RV.RiscvPageSizes
    attrs: tuple[tuple[str, Optional[int]], ...]
    pinned_frame_bases: tuple[tuple[int, int], ...]


class Page:
    """
    Page class models a Page that has a virtual and physical address
    Also, a page belongs to exactly one PageMap.
    """

    def __init__(
        self,
        page_map: "PageMap",
        featmgr: PagingParams,
        addrgen: AddrGen,
        pagesize: RV.RiscvPageSizes = RV.RiscvPageSizes.S4KB,
    ):
        self.featmgr: PagingParams = featmgr
        self.addrgen: AddrGen = addrgen
        self.map: PageMap = page_map
        # Determine u-bit value based on the privilege mode
        ubit = self.featmgr.priv_mode == RV.RiscvPrivileges.USER
        # If 2-stage paging is enabled and vs-stage is disabled, we are only dealing with g-stage
        # so we mark u=1 since all g-stage tablewalks are treated as user. The paging mode is a
        # property of the map this page lives in, not the config: a map is a bare g-stage walk
        # (vs-stage disabled) either when its own mode is DISABLE, or when it is a g-stage map
        # whose g-stage mode is its own table mode (the hgatp the guest walks directly).
        if self.map.paging_mode == RV.RiscvPagingModes.DISABLE or (self.map.g_map and self.map.paging_mode == self.map.paging_g_mode):
            ubit = 1

        self.pagesize: RV.RiscvPageSizes = pagesize

        self.lin_addr: Optional[int] = None
        self.phys_addr: Optional[int] = None

        # Frame base addresses this page's walk must use, keyed by the walker ``pt_level`` that
        # reaches the frame (``_create_pt_non_leaf``): the child table at that level is placed
        # there. The walker draws NOTHING -- every node's frame is placed by the builder before
        # the tree is built (a consumer's ``pt_nodes`` frame, or the builder's own placement in
        # ``PageTableBuilder._place_pt_node_frames``), because a frame is also a mapped address in
        # whatever domain it is identity-mapped into and only the solve can see that domain's
        # reservations. A missing entry for a level the walk reaches is a builder bug.
        self.pinned_frame_bases: dict[int, int] = dict()
        # Physical storage address for each frame. Absent means the frame is
        # explicitly identity-backed and therefore uses ``pinned_frame_bases``.
        self.pinned_frame_backings: dict[int, int] = dict()

        # Whether each of those frames was drawn as secure (STEE), same keying. It decides the
        # attrs of the g-stage identity the walker emits for the frame, so the walker must be told
        # rather than re-rolling it. Absent -> non-secure.
        self.pinned_frame_secure: dict[int, bool] = dict()

        # Declared g-stage identity forcing for this (VS-stage) page's SYNTHESIZED non-leaf
        # nodes: ``gstage_nodes[vs]`` is a ``request.PTGPage`` whose per-g-level PTE bits
        # (``level_attrs()``) OVERRIDE the walker's default g-stage identity matrix (the
        # consumer's explicit g-forcing) and whose ``pagesize`` drives that node's g-stage frame
        # pagesize. It never pins the frame's GPA. The DEFAULT matrix itself is read live from
        # ``page.attrs`` at emit time via the resolve converter (``_emit_gstage_identity``), so
        # no ``_glevel`` string grammar lives here. Typed under TYPE_CHECKING only: the walker
        # core imports nothing from the declaration layer at runtime.
        self.gstage_nodes: dict[int, "PTGPage"] = dict()
        # The PTGPage object the consumer declared for each level, kept apart from
        # the canonical ``gstage_nodes`` identity shared by every frame sharer.
        # Attribution is per declaration (``PTEntry.ptg_page`` returns the
        # consumer's own object, and two distinct declarations reaching one interned
        # pointer PTE must clear it); geometry is per frame.
        self.gstage_node_declarations: dict[int, "PTGPage"] = dict()
        self.resolved_gstage_pagesizes: dict[int, RV.RiscvPageSizes] = dict()
        # Correlated whole-node policy domains, resolved once all sharers of a
        # pointer PTE are known.
        self.node_choices: dict[int, object] = dict()

        # Pinned frames declared INSIDE those PTGPages: ``gstage_node_pins[vs]`` is the
        # ``pinned_frame_bases`` the synthesized g-stage identity of the VS-level-``vs`` frame
        # must be built with (``{g_pt_level: base}``, resolved by the builder). Threaded through
        # the identity emitter onto that raw PT page, so the g-stage tables of one VS node's
        # identity land at consumer-chosen host-physical frames.
        self.gstage_node_pins: dict[int, dict[int, int]] = dict()

        self.attrs: dict[str, Optional[int]] = page_default_attrs(ubit)

        # Pagesize of the g-stage translation of THIS page's leaf target -- the geometry of the
        # g-stage leaf fronting the address this page's leaf PTE points at. A superpage here
        # means fewer g-stage levels translate that address. Set by the builder from the
        # mapping's destination page (``_gstage_leaf_pagesize``); ``None`` -> 4 KiB. The
        # corresponding NON-LEAF geometry needs no field: it travels with each node's own
        # ``PTGPage`` / pinned frame in ``gstage_nodes`` / ``pinned_frame_bases``.
        self.gstage_leaf_pagesize: Optional[RV.RiscvPageSizes] = None

    def __str__(self) -> str:
        s = "Page:\n"
        if self.lin_addr is not None:
            s += f"    lin_addr: {self.lin_addr:016x}\n"
        if self.phys_addr is not None:
            s += f"    phys_addr: {self.phys_addr:016x}\n"
        s += f"    pagesize: {self.pagesize}\n"

        s += "    "
        for attr, val in self.attrs.items():
            s += f"{attr}: {val}, "

        return s

    def set_pagetable_levels(self, page_map: PageMap) -> None:
        """Set the map depth and this page's leaf level."""
        self.max_levels = page_map.max_levels

        # stop_level() provides how many levels to stop at
        self.pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(self.pagesize)

    def create_pagetables(self, rng: RandNum, page_map: PageMap) -> None:
        """
        Create pagetables by using lower level of Pagetable class
        """
        self.set_pagetable_levels(page_map=page_map)

        pt = pagetables.Pagetables(page=self, page_map=page_map, featmgr=self.featmgr, addrgen=self.addrgen)
        pt.create_pagetables(rng=rng)


class PageMap:
    def __init__(
        self,
        paging_mode: RV.RiscvPagingModes,
        featmgr: PagingParams,
        addrgen: AddrGen,
        g_map: bool = False,
        paging_g_mode: RV.RiscvPagingModes = RV.RiscvPagingModes.DISABLE,
    ):
        self.paging_mode: RV.RiscvPagingModes = paging_mode
        # The g-stage mode of this map's walk context (DISABLE unless this VS/single map
        # walks under a g-stage, or is itself a g-stage map). A property of the space, not
        # the config.
        self.paging_g_mode: RV.RiscvPagingModes = paging_g_mode
        self.g_map: bool = g_map

        self.featmgr: PagingParams = featmgr
        self.addrgen: AddrGen = addrgen

        # Hook invoked when the walker derives a G-stage identity mapping for one
        # of this (VS-stage) map's PT nodes. A builder wires this to route the
        # mapping into that builder's G-stage space, so the walker need not know about
        # any other map. Required on any two-stage source map (the walker raises if
        # it is unset). Signature: ``emitter(*, gpa, hpa, attrs, pagesize)``.
        self.gstage_emitter: Optional[Callable] = None

        # Whether the walker synthesizes identity G-stage mappings for this
        # (VS-stage) map's PT nodes. True reproduces today's behavior; a builder
        # sets it False for a VS map whose G-stage space is non-identity (populated
        # with explicit GPA->HPA requests instead of derived identity mappings).
        self.emit_gstage_identity: bool = True

        # Set the pagetable levels based on the paging_mode
        self.max_levels: int = RV.RiscvPagingModes.max_levels(self.paging_mode)

        # Pages joined to this map, keyed by their resolved address in this map's own
        # domain (this map's own VA/GPA) -- the natural dedup key now that pages carry
        # no name.
        self.pages: dict[int, Page] = dict()
        # Raw PT-node pages (g-stage structural identity leaves), keyed the same way.
        # Merged into self.pages once create_pagetables() runs.
        self.pt_pages: dict[int, Page] = dict()
        # Canonical, post-default metadata for every accepted structural identity. Unlike
        # declaration-layer PTGPages this is keyed by the emitted GPA, so shared/interned
        # identities have one authoritative read-back record rather than whichever declaration wrote first.
        self.emitted_gstage_identities: dict[int, EmittedGStageIdentity] = dict()
        # PTTables interned by their physical base address, so a frame reached from several
        # walk positions (auto siblings, or pinned/aliased frames) shares one table object
        # and packs its PTEs. Auto frames draw a fresh unique base each time, so this is a
        # no-op for them; pinned/aliased frames coalesce here (aliasing/recursion).
        self.tables_by_base: dict[int, pagetables.PTTable] = dict()
        self.basetable: Optional[pagetables.PTTable] = None  # Set to PTTable after initialize()

        # Root (sptbr) frame pinned to a solved page's resolved base: a mapping declared a
        # top-level ``PTNode(page=frame)`` so this map's root page table must physically live
        # at that frame (a recursive / self-referencing page table). ``None`` -> the root is
        # drawn normally. Set by the builder before ``initialize()``; consumed by
        # ``create_sptbr`` (skips the root physical draw and places the root there).
        self.pinned_sptbr: Optional[int] = None
        self.pinned_sptbr_backing: Optional[int] = None

    def initialize(self) -> None:
        # Create the sptrb page
        self.create_sptbr()

        # Also create the base table
        self.create_base_table()

    # Add raw page to this map (used for the g-stage structural identity emitter).
    # Deduped on ``linear_addr``. A repeat is idempotent only when its complete emitted
    # identity is equal; differing target, attributes, geometry, or frame placement is a
    # contradiction, never a first-write no-op.
    def add_raw_pt_page(
        self,
        linear_addr: int,
        physical_addr: int,
        attrs: Optional[dict[str, int]] = None,
        pagesize: RV.RiscvPageSizes = RV.RiscvPageSizes.S4KB,
        pinned_frame_bases: Optional[dict[int, int]] = None,
    ) -> None:
        if attrs is None:
            attrs = dict()

        log.debug(f"Adding raw page: linear_addr: {linear_addr:x}, physical_addr: {physical_addr:x}")
        page = Page(
            page_map=self,
            featmgr=self.featmgr,
            addrgen=self.addrgen,
            pagesize=pagesize,
        )
        page.lin_addr = linear_addr
        page.phys_addr = physical_addr
        if pinned_frame_bases:
            page.pinned_frame_bases.update(pinned_frame_bases)

        for attr, val in attrs.items():
            page.attrs[attr] = val

        # Make sure to mark u=1 in the user mode
        if self.featmgr.priv_mode == RV.RiscvPrivileges.USER:
            page.attrs["u"] = 1  # always user

        identity = EmittedGStageIdentity(
            gpa=linear_addr,
            hpa=physical_addr,
            pagesize=pagesize,
            attrs=tuple(sorted(page.attrs.items())),
            pinned_frame_bases=tuple(sorted(page.pinned_frame_bases.items())),
        )
        existing = self.emitted_gstage_identities.get(linear_addr)
        if existing is not None:
            if existing != identity:
                raise ValueError(f"conflicting g-stage structural identity at GPA 0x{linear_addr:x}: " f"existing {existing!r} vs new {identity!r}")
            return

        self.emitted_gstage_identities[linear_addr] = identity
        self.add_pt_page(page=page)

    def add_page(self, page: Page) -> None:
        assert page.lin_addr is not None
        self.pages[page.lin_addr] = page

    def add_pt_page(self, page: Page) -> None:
        assert page.lin_addr is not None
        self.pt_pages[page.lin_addr] = page

    def get_basetable(self) -> pagetables.PTTable:
        """
        Return the pointer to the base of the pagetable for this map
        """
        if self.basetable is None:
            raise ValueError("PageMap basetable is None - initialize() must be called before accessing basetable")
        return self.basetable

    def create_sptbr(self) -> None:
        """
        Create a new page for the SPTBR
        """
        # The root frame's address always comes from the solve now -- either a consumer's
        # top-level ``PTNode(page=frame)`` (a recursive / self-referencing page table) or the
        # builder's own root-frame page (``PageTableBuilder._declare_root_frames``). The walker
        # draws nothing: a draw made here after the solve cannot see the reservations the
        # solve already committed. That is how an SV57 root was placed inside a 1 GiB page's VA
        # span and collided with its leaf PTE in the level-2 slot.
        if self.pinned_sptbr is None:
            raise RuntimeError("pinned_sptbr not set: the builder must place this map's root frame before initialize()")
        self.sptbr = self.pinned_sptbr
        # A root is a register value and a table frame, not an implicit leaf in
        # any address space. Frontends that need to address or G-translate it
        # declare an ordinary Mapping for the root frame.

    def create_base_table(self) -> None:
        root_level = self.max_levels - 1
        root_backing = self.sptbr if self.pinned_sptbr_backing is None else self.pinned_sptbr_backing
        self.basetable = pagetables.PTTable(
            root_backing,
            input_addr=self.sptbr,
            capacity=self._entries_per_table(root_level),
        )
        self.tables_by_base[root_backing] = self.basetable

    def _entries_per_table(self, level: int) -> int:
        """Slot capacity of a table indexed at ``level`` (2 ** index-field width).

        Derived per mode rather than assumed 512: Sv32's index fields are 10 bits wide, so
        its tables hold 1024 four-byte PTEs. ``index_bits`` raises for a level the mode does
        not have (and for DISABLE, which has no levels at all) -- it never returns None -- so
        there is no fallback to write here. A caller reaching this with a level outside the
        mode is a bug and should say so, which is what the raise does."""
        index_hi, index_lo = RV.RiscvPagingModes.index_bits(mode=self.paging_mode, level=level)
        return 1 << (index_hi - index_lo + 1)

    def create_pagetables(self, rng: RandNum) -> None:
        """
        For every page in this pagemap, create pagetables
        """
        # Build pages that PIN a PT-node frame first, so their pinned (interned) tables are
        # registered before any prefix-sharing page that draws an auto table at the same slot:
        # the sharer then adopts the pinned frame instead of demanding a conflicting one. This
        # lets an owner pin its whole walk (e.g. so declared window mappings can target those
        # frames) even when a plain page shares its fixed-VA prefix. Stable within each group.
        pinned = [p for p in self.pages.values() if p.pinned_frame_bases]
        rest = [p for p in self.pages.values() if not p.pinned_frame_bases]
        for page in pinned + rest:
            page.create_pagetables(rng=rng, page_map=self)

        # The above method might have added some additional pages (a g-stage structural
        # identity leaf) to back the page tables themselves. So, we need to iterate through
        # self.pt_pages and create pagetables for the new pages.
        for lin_addr, page in list(self.pt_pages.items()):
            self.pages[lin_addr] = page
            page.create_pagetables(rng=rng, page_map=self)
