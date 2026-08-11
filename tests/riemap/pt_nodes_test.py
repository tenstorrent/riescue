# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Tests for declared page-table nodes (``Mapping.pt_nodes`` / ``PTNode``): pinning a
frame at a level, aliasing one frame across mappings, recursive self-maps, and the
walker's merge-or-error packing (conflict + slot exhaustion)."""

import unittest

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.pagetables import PTTable, PTEntry, PTAttrs
from riescue.riemap.config import PagingParams
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTGPage, PTNode, SameAs, Space, Stage
from riescue.riemap.resolve import pt_nodes_from_levels
from tests.riemap.root_policy import declare_vs_root_identity


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs():
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}


def _leaf_nodes():
    return {LEAF: PTNode(attrs=_leaf_attrs())}


def _vs_nodes(overrides=None):
    nodes = {
        **_leaf_nodes(),
        0: PTNode(page=PTGPage(identity=True)),
        1: PTNode(page=PTGPage(identity=True)),
    }
    nodes.update(overrides or {})
    return nodes


def _path(sr, addr):
    """Walk ``addr`` from the map root, returning the (level, table, index) steps and the
    reached leaf entry."""
    mode = sr._page_map.paging_mode
    table = sr._page_map.basetable
    steps = []
    for level in range(RV.RiscvPagingModes.max_levels(mode) - 1, -1, -1):
        idx = common.bits(addr, *RV.RiscvPagingModes.index_bits(mode, level))
        steps.append((level, table, idx))
        entry = table.table[idx]
        if entry.leaf:
            return steps, entry
        table = entry.basetable
    return steps, None


def _all_entries(root):
    stack, seen = [root], set()
    while stack:
        t = stack.pop()
        if id(t) in seen:
            continue
        seen.add(id(t))
        for idx, e in t.table.items():
            yield t, idx, e
            if not e.leaf and e.basetable is not None:
                stack.append(e.basetable)


class TestPinnedFrame(unittest.TestCase):
    """A pinned ``PTNode`` frame backs the node at its level: the level-1 table lands at
    the frame's resolved address and the level's PTE bits come from the node's attrs."""

    def test_frame_lands_at_pinned_level(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        node1 = b.add_page(Page(space=b.phys))
        p = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=node1)}))
        result = b.build()
        node1_pa = result.address_of(node1)[1]
        steps, _leaf = _path(result.space(va), 0x40000000)
        # The level-2 PTE points to the level-1 table, which must be the pinned frame.
        _lvl, root, idx2 = steps[0]
        self.assertEqual(root.table[idx2].basetable.base_addr, node1_pa)
        # Translation still works.
        self.assertEqual(result.space(va).walk(0x40000000)[1], 0x80010000)

    def test_node_attrs_win_over_mapping_attrs(self):
        # Force the A bit (bit 6) on the level-1 pointer PTE via pt_nodes; it defaults 0.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        node1 = b.add_page(Page(space=b.phys))
        p = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=node1, attrs={"a": 1})}))
        result = b.build()
        steps, _leaf = _path(result.space(va), 0x40000000)
        # steps[1] is the level-1 PTE (inside node1); its A bit must be forced set.
        _lvl, tbl, idx = steps[1]
        self.assertEqual((tbl.table[idx].pt_attr.get_value() >> 6) & 1, 1, "forced a=1 not applied to level-1 PTE")

    def test_leaf_sentinel_resolves_to_leaf_level(self):
        # LEAF-keyed node attrs land on the leaf PTE (level 0 for a 4KB page).
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        p = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes={LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 0, "x": 0, "a": 1, "d": 1})}))
        result = b.build()
        _steps, leaf = _path(result.space(va), 0x40000000)
        self.assertEqual(leaf.pt_attr.r, 1)
        self.assertEqual(leaf.pt_attr.w, 0)
        self.assertEqual(result.space(va).walk(0x40000000)[1], 0x80010000)


class TestAliasedFrame(unittest.TestCase):
    """One frame pinned by two mappings coalesces into a single shared PTTable object,
    packing both mappings' PTEs; each mapping's parent PTE points at that one table."""

    def test_two_mappings_share_one_table(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        shared = b.add_page(Page(space=b.phys))
        # Differ at BOTH level-2 and level-1 indices so they pack distinct slots in shared.
        va_a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))  # l2=1, l1=0
        va_b = b.add_page(Page(space=va, addr=AddrSpec(exact=0x80200000)))  # l2=2, l1=1
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_b = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=va_a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        b.add_mapping(Mapping(src=va_b, dst=pa_b, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        result = b.build()
        shared_pa = result.address_of(shared)[1]
        root = result.space(va)._page_map.basetable
        pointers = [e.basetable for _t, _i, e in _all_entries(root) if not e.leaf and e.basetable.base_addr == shared_pa]
        self.assertEqual(len(pointers), 2, "expected two parent PTEs pointing to the shared frame")
        self.assertEqual(len({id(t) for t in pointers}), 1, "aliased frame must be one shared PTTable object")
        self.assertEqual(result.space(va).walk(0x40000000)[1], 0x80010000)
        self.assertEqual(result.space(va).walk(0x80200000)[1], 0x80020000)

    def test_prefix_sharing_mappings_pin_one_frame_set(self):
        """Two VAs differing only in the LEAF index may pin the same frame at every level.

        This is the offset-family shape: a ``modify_pt`` page plus a child whose VA is forced to
        ``anchor + 0x1000`` share every non-leaf index, so they share every non-leaf node. One
        pointer PTE cannot name two frames, so the family must pin ONE frame set -- the walker
        has to accept the repeated (identical) pin as idempotent and pack the two leaf PTEs into
        distinct slots of the shared leaf frame. RiescueD relies on this to key a family's
        ``__ptframe{N}`` recipes by the family root (pt_request_builder._va_family_root)."""
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        mid = b.add_page(Page(space=b.phys))  # holds the level-1 PTEs
        leaf_frame = b.add_page(Page(space=b.phys))  # holds the level-0 (leaf) PTEs
        # Same level-2 and level-1 index, differing level-0 index: one shared walk, two slots.
        va_a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        va_b = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40001000)))
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_b = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        nodes = {LEAF: PTNode(attrs=_leaf_attrs(), page=leaf_frame), 1: PTNode(page=mid)}
        b.add_mapping(Mapping(src=va_a, dst=pa_a, pt_nodes=dict(nodes)))
        b.add_mapping(Mapping(src=va_b, dst=pa_b, pt_nodes=dict(nodes)))
        result = b.build()
        self.assertEqual(result.space(va).walk(0x40000000)[1], 0x80010000)
        self.assertEqual(result.space(va).walk(0x40001000)[1], 0x80020000)
        # Both leaf PTEs live in the one pinned leaf frame, at different slots.
        leaf_pa = result.address_of(leaf_frame)[1]
        slots = {_path(result.space(va), addr)[0][-1][2] for addr in (0x40000000, 0x40001000)}
        self.assertEqual(len(slots), 2, "the two leaf PTEs must occupy distinct slots")
        for addr in (0x40000000, 0x40001000):
            _lvl, table, _idx = _path(result.space(va), addr)[0][-1]
            self.assertEqual(table.base_addr, leaf_pa, "both leaf PTEs must sit in the pinned family frame")


class TestEveryFrameComesFromTheSolve(unittest.TestCase):
    """The walker is purely constructive: every page-table node frame is placed by the builder
    before the tree is built, and the walk draws no address of its own.

    A frame is not only a physical address -- under a g-stage it is identity-mapped into the
    target G space, so it occupies an address there too. A draw made during the walk, after the
    solve, sees neither that space's reservations nor the spans already handed out, which is how a
    root frame landed inside a 1 GiB page's VA span in ``rvv_fp_test``. Placing the frames in the
    builder lets the draw check both pools."""

    def _single_stage(self, seed=1):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV48))
        for i in range(6):
            src = b.add_page(Page(space=va))
            dst = b.add_page(Page(space=b.phys))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
        return b, va

    def test_walker_never_draws_a_frame(self):
        # If the builder missed a node, _create_pt_non_leaf raises rather than silently drawing.
        b, va = self._single_stage()
        result = b.build()
        root = result.space(va)._page_map.basetable
        self.assertTrue(any(not e.leaf for _t, _i, e in _all_entries(root)), "expected intermediate nodes to exist at all")

    def test_every_reached_node_was_pinned(self):
        b, va = self._single_stage()
        b.build()
        page_map = b._page_maps[va]
        for page in page_map.pages.values():
            leaf_level = RV.RiscvPageSizes.pt_leaf_level(page.pagesize)
            for level in range(page_map.max_levels - 1, leaf_level, -1):
                self.assertIn(level, page.pinned_frame_bases, f"level {level} of 0x{page.lin_addr:x} was not placed by the builder")

    def test_prefix_sharing_pages_share_one_placed_frame(self):
        # The builder's node key is the VA prefix above the level -- the walker's own interning --
        # so two pages under one node must be handed the same base.
        b = PageTableBuilder(rng=RandNum(seed=2), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        pages = []
        for addr in (0x40000000, 0x40001000, 0x40002000):
            src = b.add_page(Page(space=va, addr=AddrSpec(exact=addr)))
            dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + (addr & 0xF000))))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
            pages.append(src)
        b.build()
        walker = b._page_maps[va].pages
        bases = [walker[p.addr.exact].pinned_frame_bases for p in pages]
        for level in (1, 2):
            self.assertEqual(len({fb[level] for fb in bases}), 1, f"pages sharing the level-{level} node must share its frame")

    def test_an_unplaced_node_is_reported_not_drawn(self):
        # Every node must be placed by the builder. Drop one placement to
        # verify that the walker reports the broken invariant.
        b, va = self._single_stage()
        original = b._place_pt_node_frames

        def drop_one(space):
            original(space)
            for page in b._page_maps[space].pages.values():
                if page.pinned_frame_bases:
                    page.pinned_frame_bases.pop(max(page.pinned_frame_bases))
                    return

        b._place_pt_node_frames = drop_one  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "must place every page-table node frame"):
            b.build()

    def test_shared_node_rejects_conflicting_explicit_geometry(self):
        # Exact, incompatible PTGPage geometries are hard declarations. If pinned VAs
        # force them through one node, silently choosing the larger geometry would
        # discard one caller's requested g-stage leaf size.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, g)
        # Same level-2 and level-1 index -> one shared level-1 node; one page fronts its frame with
        # a 2 MiB g-stage leaf, the other with 4 KiB.
        addrs = (0x40000000, 0x40001000)
        for addr, ps in zip(addrs, (RV.RiscvPageSizes.S2MB, RV.RiscvPageSizes.S4KB)):
            va = b.add_page(Page(space=vs, addr=AddrSpec(exact=addr)))
            hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(bits=32)))
            gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
            ptg = PTGPage(pagesize=ps, identity=True)
            b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes=_vs_nodes({1: PTNode(page=ptg)})))
            b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes=_leaf_nodes()))
        with self.assertRaisesRegex(
            ValueError,
            "pinned VAs collide.*conflicting signatures",
        ):
            b.build()

    def test_gstage_frames_are_reserved_in_the_gpa_pool(self):
        # A VS frame's GPA == its HPA and is identity-mapped into the G space, so its span must be
        # taken in that space's pool too -- otherwise a synthesized superpage identity leaf could
        # be placed over it.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, g)
        for i in range(4):
            va = b.add_page(Page(space=vs))
            # bits=32 keeps the HPA a valid SV39 GPA: the identity gpa page reuses this exact
            # value, and a 56-bit draw would not survive the g-stage's 41-bit input width.
            hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(bits=32)))
            gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
            b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes=_vs_nodes()))
            b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes=_leaf_nodes()))
        b.build()
        frames = {base for page in b._page_maps[vs].pages.values() for base in page.pinned_frame_bases.values()}
        self.assertTrue(frames)
        for base in frames:
            self.assertTrue(b.addrgen.linear_overlap(base, 0x1000, g), f"VS frame 0x{base:x} must be reserved in the G space's GPA pool")


class TestHighDramStructuralFrames(unittest.TestCase):
    """Single-stage / VS-only PT frames are ordinary physical pages -- not VA==PA
    identities -- so they may land anywhere in DRAM, including above the Sv39 sign bit.

    Regression: when non-secure DRAM starts at 1 TiB (cluster 40+) and secure memory
    occupies everything below, clamping structural frames to ``va_bits - 1`` (= 38 for
    Sv39) made the DRAM/width intersection empty and failed before any real OOM."""

    _DRAM_LO = 0x10000000000  # 1 TiB
    _DRAM_HI = 0x8000000000000

    def _high_dram_memory(self):
        return Memory.from_dict(
            {
                "dram": {
                    "dram0": {
                        "address": hex(self._DRAM_LO),
                        "size": hex(self._DRAM_HI - self._DRAM_LO),
                        "cacheable": True,
                        "configurable": True,
                    },
                    "secure0": {
                        "address": "0x0",
                        "size": hex(self._DRAM_LO),
                        "cacheable": True,
                        "configurable": True,
                        "secure": True,
                    },
                }
            }
        )

    def _assert_frames_in_high_dram(self, result, space):
        root = result.space(space).root_addr
        self.assertIsNotNone(root)
        self.assertTrue(
            self._DRAM_LO <= root < self._DRAM_HI,
            f"root 0x{root:x} not in high DRAM",
        )
        tables = list(result.space(space).tables())
        self.assertTrue(tables, "expected at least one page-table frame")
        for view in tables:
            self.assertTrue(
                self._DRAM_LO <= view.addr < self._DRAM_HI,
                f"PT frame 0x{view.addr:x} not in high DRAM",
            )

    def test_sv39_single_stage_frames_may_land_above_sign_bit(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=self._high_dram_memory(), physical_addr_bits=52)
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        for _ in range(4):
            src = b.add_page(Page(space=va))
            dst = b.add_page(Page(space=b.phys))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        result = b.build()
        self._assert_frames_in_high_dram(result, va)

    def test_sv39_vs_only_frames_may_land_above_sign_bit(self):
        # twostage with g-stage disabled is still a single table-bearing space; the
        # same width clamp used to make it unsatisfiable under this mmap.
        b = PageTableBuilder(rng=RandNum(seed=2), memory=self._high_dram_memory(), physical_addr_bits=52)
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.SINGLE))
        for _ in range(4):
            src = b.add_page(Page(space=va))
            dst = b.add_page(Page(space=b.phys))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        result = b.build()
        self._assert_frames_in_high_dram(result, va)


class TestSecurePtNodeFramesUnderTwoStage(unittest.TestCase):
    """A secure (STEE) PT-node frame's secure-ness must reach the g-stage identity emitted for it.

    The frame's base is a GPA under two-stage, so bit 55 cannot ride the address itself -- it has
    to land on the HPA in the g-stage leaf PTE, which the walker does by passing ``secure`` into
    ``_emit_gstage_identity``. Now that the builder places the frame, the roll happens there and
    the answer travels with the pin (``pinned_frame_secure``); the walker must not re-roll it. The
    previous pinned-frame path hardcoded non-secure, so a consumer-pinned frame could not be
    secure at all -- and the only existing secure-PT coverage (json_frontend_test) is
    single-stage, where bit 55 goes straight onto the base instead."""

    _SECURE_BIT = 0x0080000000000000

    def _memory_with_secure(self):
        return Memory.from_dict(
            {
                "dram": {
                    "dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True},
                    "sec": {"address": "0x10000000", "size": "0x10000000", "secure": True, "cacheable": True},
                }
            }
        )

    def _build(self, probability, seed=1):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=self._memory_with_secure())
        g = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, g, secure_pt_probability=probability)
        for _ in range(3):
            va = b.add_page(Page(space=vs))
            hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(bits=32)))
            gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
            b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes=_vs_nodes()))
            b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes=_leaf_nodes()))
        return b, vs, g, b.build()

    def test_secure_frames_are_flagged_and_stay_valid_gpas(self):
        b, vs, _g, _result = self._build(probability=100)
        flags = [secure for page in b._page_maps[vs].pages.values() for secure in page.pinned_frame_secure.values()]
        self.assertTrue(flags, "expected VS PT-node frames to exist")
        self.assertTrue(all(flags), "secure_pt_probability=100 must mark every VS frame secure")
        # Under two-stage the base stays a GPA: bit 55 must NOT be OR-ed into it, or the g-stage
        # walk of the frame would leave the addressable range.
        for page in b._page_maps[vs].pages.values():
            for base in page.pinned_frame_bases.values():
                self.assertEqual(base & self._SECURE_BIT, 0, f"frame GPA 0x{base:x} must not carry bit 55 under two-stage")

    def test_secure_reaches_the_gstage_identity_leaf(self):
        b, vs, g, result = self._build(probability=100)
        g_space = result.space(g)
        pte_by_addr = dict(g_space.pte_entries())
        frames = {base for page in b._page_maps[vs].pages.values() for base in page.pinned_frame_bases.values()}
        checked = 0
        for base in frames:
            steps, _translated = g_space.walk(base)
            leaf = next((s for s in steps if s.leaf), None)
            if leaf is None or leaf.pte_addr not in pte_by_addr:
                continue
            ppn = (pte_by_addr[leaf.pte_addr] >> 10) << 12
            self.assertTrue(ppn & self._SECURE_BIT, f"g-stage identity of secure frame 0x{base:x} must map to a bit-55 HPA (got 0x{ppn:x})")
            checked += 1
        self.assertTrue(checked, "expected at least one VS frame's g-stage identity to be reachable")

    def test_zero_probability_leaves_every_frame_clear(self):
        b, vs, _g, _result = self._build(probability=0)
        flags = [secure for page in b._page_maps[vs].pages.values() for secure in page.pinned_frame_secure.values()]
        self.assertTrue(flags)
        self.assertFalse(any(flags), "secure_pt_probability=0 must leave every frame non-secure")


class TestRecursiveSelfMap(unittest.TestCase):
    """A frame used as an intermediate page table can also be a leaf target: walking the
    VA reaches the frame itself as data (a self-referential / recursive map)."""

    def test_frame_is_both_table_and_leaf_target(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        frame = b.add_page(Page(space=b.phys))
        p = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40001000)))
        b.add_mapping(Mapping(src=p, dst=frame, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=frame)}))
        result = b.build()
        frame_pa = result.address_of(frame)[1]
        steps, _leaf = _path(result.space(va), 0x40001000)
        _lvl, root, idx2 = steps[0]
        self.assertEqual(root.table[idx2].basetable.base_addr, frame_pa, "level-1 table must be the pinned frame")
        self.assertEqual(result.space(va).walk(0x40001000)[1], frame_pa, "self-map must translate to the frame itself")


class TestTwoStagePinnedFrame(unittest.TestCase):
    """A VS-stage PT frame has independent GPA and HPA coordinates."""

    def test_gstage_frame_uses_explicit_nonidentity_backing(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, g)
        gnode = b.add_page(Page(space=g, addr=AddrSpec(exact=0x400000)))
        node_hpa = b.add_page(
            Page(
                space=b.phys,
                addr=AddrSpec(exact=0x81000000),
            )
        )
        leaf_node = b.add_page(Page(space=g, addr=AddrSpec(exact=0x401000)))
        leaf_node_hpa = b.add_page(
            Page(
                space=b.phys,
                addr=AddrSpec(exact=0x81001000),
            )
        )
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x2000)))
        hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80030000)))
        gpa_leaf = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
        b.add_mapping(
            Mapping(
                src=va,
                dst=gpa_leaf,
                pt_nodes={
                    **_leaf_nodes(),
                    0: PTNode(page=leaf_node),
                    1: PTNode(page=gnode),
                },
            )
        )
        b.add_mapping(Mapping(src=gpa_leaf, dst=hpa, pt_nodes=_leaf_nodes()))
        b.add_mapping(
            Mapping(
                src=gnode,
                dst=node_hpa,
                pt_nodes=_leaf_nodes(),
            )
        )
        b.add_mapping(
            Mapping(
                src=leaf_node,
                dst=leaf_node_hpa,
                pt_nodes=_leaf_nodes(),
            )
        )
        result = b.build()
        gnode_gpa, gnode_hpa = result.address_of(gnode)
        self.assertEqual(gnode_gpa, 0x400000)
        self.assertEqual(gnode_hpa, 0x81000000)
        steps, _ = _path(result.space(vs), 0x2000)
        level2_entry = steps[0][1].table[steps[0][2]]
        self.assertEqual(level2_entry.get_base_addr(), gnode_gpa)
        self.assertEqual(level2_entry.basetable.base_addr, gnode_hpa)
        self.assertEqual(result.space(vs).walk(0x2000)[1], 0x80030000)
        self.assertEqual(result.space(g).walk(gnode_gpa)[1], gnode_hpa)


class TestPackingErrors(unittest.TestCase):
    """The walker packs PTEs into a shared frame but raises on a genuine (frame, slot)
    content conflict, and on over-packing a frame past its slot capacity."""

    def test_conflicting_shared_slot_raises(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        shared = b.add_page(Page(space=b.phys))
        # Same level-1 (and level-0) index -> both need the same slot in the shared frame
        # to point at different children: a genuine conflict.
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))  # l2=1, l1=0, l0=0
        c = b.add_page(Page(space=va, addr=AddrSpec(exact=0x80000000)))  # l2=2, l1=0, l0=0
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("conflict", str(cm.exception))

    def test_two_different_pins_for_one_node_raise(self):
        # Two VAs differing only in the leaf index share the level-1 node, so its single pointer
        # PTE has one child. Declaring two different frames there is unsatisfiable; coloring
        # rejects it as a pinned-VA signature collision before frame placement. (Pinning the
        # SAME frame is the offset-family case and is fine -- see
        # TestAliasedFrame.test_prefix_sharing_mappings_pin_one_frame_set.)
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        frame_a = b.add_page(Page(space=b.phys))
        frame_b = b.add_page(Page(space=b.phys))
        for addr, frame in ((0x40000000, frame_a), (0x40001000, frame_b)):
            src = b.add_page(Page(space=va, addr=AddrSpec(exact=addr)))
            dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + (addr & 0xF000))))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=frame)}))
        with self.assertRaisesRegex(ValueError, r"pinned VAs collide.*conflicting signatures"):
            b.build()

    def test_over_packing_a_frame_raises(self):
        # The walker guards every table with its slot capacity; packing past it raises.
        featmgr = PagingParams(physical_addr_bits=56)
        table = PTTable(base_addr=0x1000, capacity=2)
        for i in range(2):
            attr = PTAttrs(rng=RandNum(seed=1), featmgr=featmgr, level=0, leaf=True)
            table.insert_entry(PTEntry(basetable=PTTable(0x2000 + i * 0x1000, leaf=True), pt_attr=attr, level=0), index=i)
        attr = PTAttrs(rng=RandNum(seed=1), featmgr=featmgr, level=0, leaf=True)
        with self.assertRaises(ValueError) as cm:
            table.insert_entry(PTEntry(basetable=PTTable(0x9000, leaf=True), pt_attr=attr, level=0), index=2)
        self.assertIn("over-packed", str(cm.exception))


class TestPtNodesFromLevels(unittest.TestCase):
    """``pt_nodes_from_levels`` is the one regrouping every consumer feeds ``pt_nodes``
    from -- the builder's own two-stage idiom and the JSON frontend both call it, so its
    contract is pinned here rather than once per caller."""

    def test_leaf_level_folds_onto_the_leaf_sentinel(self):
        nodes = pt_nodes_from_levels({0: {"v": 1, "w": 0}, 1: {"v": 0}, 2: {"v": 1}}, leaf_level=0)
        self.assertEqual(set(nodes), {LEAF, 1, 2})
        self.assertEqual(nodes[LEAF].attrs, {"v": 1, "w": 0})
        self.assertEqual(nodes[1].attrs, {"v": 0})

    def test_a_superpage_leaf_folds_its_own_level(self):
        # A 2 MiB page's leaf is level 1, so level 0 is not the sentinel here.
        nodes = pt_nodes_from_levels({1: {"v": 1}, 2: {"v": 0}}, leaf_level=1)
        self.assertEqual(set(nodes), {LEAF, 2})
        self.assertEqual(nodes[LEAF].attrs, {"v": 1})

    def test_attrs_are_copied_not_aliased(self):
        levels = {0: {"v": 1}}
        nodes = pt_nodes_from_levels(levels, leaf_level=0)
        levels[0]["v"] = 0
        self.assertEqual(
            nodes[LEAF].attrs["v"],
            1,
            "later caller mutation must not change the declaration",
        )
        with self.assertRaises(TypeError):
            nodes[LEAF].attrs["v"] = 0  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
