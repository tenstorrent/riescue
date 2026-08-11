# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Tests for attribute-aware PTE coloring.

Coloring pins a few source-VA index bits per attr signature so mappings whose forced
pointer-PTE attrs conflict land in different nodes. Everything here drives the
``pt_nodes`` API directly. With no conflicting forced attrs, coloring pins no
bits because every mapping belongs to one bucket."""

import inspect
import os
import subprocess
import sys
import textwrap
import unittest

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder, _Spill
from riescue.riemap.request import Choice, AddrSpec, LEAF, Mapping, MemoryRegion, OffsetFrom, Page, PTGPage, PTNode, SameAs, Space, Stage
from tests.riemap.root_policy import declare_vs_root_identity


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs():
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}


def _leaf_nodes():
    return {LEAF: PTNode(attrs=_leaf_attrs())}


def _index(va, mode, level):
    return common.bits(va, *RV.RiscvPagingModes.index_bits(mode, level))


def _leaf_table(sr, addr):
    """The PTTable object holding ``addr``'s leaf PTE (walking the object graph from the
    map root), or ``None`` if the walk does not reach a leaf."""
    mode = sr._page_map.paging_mode
    table = sr._page_map.basetable
    for level in range(RV.RiscvPagingModes.max_levels(mode) - 1, -1, -1):
        idx = common.bits(addr, *RV.RiscvPagingModes.index_bits(mode, level))
        entry = table.table.get(idx)
        if entry is None:
            return None
        if entry.leaf:
            return table
        table = entry.basetable
    return None


class TestConflictIsolates(unittest.TestCase):
    """Two VAs that would share a level-1 pointer PTE but force conflicting pointer attrs:
    the walker raises a merge-or-error without coloring; coloring splits their level-1
    index so they build cleanly in distinct nodes."""

    _MODE = RV.RiscvPagingModes.SV39

    def test_shared_slot_conflict_raises_with_exact_vas(self):
        # Two exact VAs share index_2 and index_1 (same level-1 pointer slot) but differ at
        # index_0; forcing d=0 vs d=1 on that shared pointer PTE is a genuine conflict.
        # Coloring never moves exact VAs, so it cannot separate them -- the conflict stands.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40200000)))  # l2=1,l1=1,l0=0
        c = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40201000)))  # l2=1,l1=1,l0=1
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"d": 0})}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"d": 1})}))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("conflict", str(cm.exception))

    def test_coloring_splits_conflicting_pointer_attrs(self):
        # Same conflicting pointer attrs, free VAs, coloring on -> level-1 index is pinned
        # to distinct values so the two build cleanly and translate correctly.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        a = b.add_page(Page(space=va))
        c = b.add_page(Page(space=va))
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"d": 0})}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"d": 1})}))
        result = b.build()
        va_a = result.address_of(a)[0]
        va_c = result.address_of(c)[0]
        self.assertNotEqual(_index(va_a, self._MODE, 1), _index(va_c, self._MODE, 1), "coloring must give conflicting pointer attrs distinct level-1 indices")
        self.assertEqual(result.space(va).walk(va_a)[1], 0x80010000)
        self.assertEqual(result.space(va).walk(va_c)[1], 0x80020000)


class TestAffineRelationColoring(unittest.TestCase):
    """Coloring follows relation chains to the one page whose draw can move them."""

    _MODE = RV.RiscvPagingModes.SV39

    def test_linear_sameas_bare_root_is_colored(self):
        b = PageTableBuilder(rng=RandNum(seed=11), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        bare = b.add_page(Page(space=va))
        related = b.add_page(Page(space=va, addr=AddrSpec(relation=SameAs(bare))))
        sibling = b.add_page(Page(space=va))
        b.add_mapping(
            Mapping(
                src=related,
                dst=b.add_page(Page(space=b.phys)),
                pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"d": 0})},
            )
        )
        b.add_mapping(
            Mapping(
                src=sibling,
                dst=b.add_page(Page(space=b.phys)),
                pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"d": 1})},
            )
        )

        result = b.build()
        related_va = result.address_of(related)[0]
        sibling_va = result.address_of(sibling)[0]
        self.assertTrue(b._page_state[bare].color_pinned)
        self.assertFalse(b._page_state[related].color_pinned)
        self.assertNotEqual(
            _index(related_va, self._MODE, 1),
            _index(sibling_va, self._MODE, 1),
        )

    def test_transitive_gpa_hpa_bare_root_is_colored(self):
        b = PageTableBuilder(rng=RandNum(seed=12), memory=_memory())
        gspace = b.add_space(Space(paging_mode=self._MODE, stage=Stage.G))
        roots = []
        gpas = []
        for forced in (0, 1):
            root = b.add_page(Page(space=b.phys))
            hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(root))))
            gpa = b.add_page(Page(space=gspace, addr=AddrSpec(relation=SameAs(hpa))))
            roots.append(root)
            gpas.append(gpa)
            b.add_mapping(
                Mapping(
                    src=gpa,
                    dst=hpa,
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        1: PTNode(attrs={"d": forced}),
                    },
                )
            )

        result = b.build()
        self.assertTrue(all(b._page_state[root].color_pinned for root in roots))
        self.assertTrue(all(not b._page_state[gpa].color_pinned for gpa in gpas))
        self.assertNotEqual(
            _index(result.address_of(gpas[0])[0], self._MODE, 1),
            _index(result.address_of(gpas[1])[0], self._MODE, 1),
        )

    def test_relation_cycle_has_no_color_root(self):
        b = PageTableBuilder(rng=RandNum(seed=13), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        a = b.add_page(Page(space=va))
        c = b.add_page(Page(space=va, addr=AddrSpec(relation=SameAs(a))))
        # Declaration types are frozen by design; mutating only here creates malformed
        # input that the public constructors cannot otherwise express in one pass.
        object.__setattr__(a.addr, "relation", SameAs(c))
        self.assertIsNone(b._resolve_affine_root(a))
        self.assertIsNone(b._resolve_affine_root(c))

    def test_exact_sameas_root_remains_immovable_conflict(self):
        b = PageTableBuilder(rng=RandNum(seed=14), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        root = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40200000)))
        for forced in (0, 1):
            src = b.add_page(Page(space=va, addr=AddrSpec(relation=SameAs(root))))
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=b.add_page(Page(space=b.phys)),
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        1: PTNode(attrs={"d": forced}),
                    },
                )
            )
        with self.assertRaisesRegex(ValueError, "conflict"):
            b.build()


class TestShareWhenIdentical(unittest.TestCase):
    """Identical forced pointer attrs impose no split, so mappings that share a VA prefix
    keep coalescing into one shared level-1 PTTable (coloring never over-separates)."""

    _MODE = RV.RiscvPagingModes.SV39

    def test_identical_attrs_share_one_table(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        # Share index_2 and index_1, differ at index_0 -> one shared level-1 pointer PTE.
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40200000)))
        c = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40201000)))
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"a": 1})}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"a": 1})}))
        result = b.build()
        sr = result.space(va)
        # Both leaves must live in the SAME level-0 table object (one shared pointer PTE).
        t_a = _leaf_table(sr, 0x40200000)
        t_c = _leaf_table(sr, 0x40201000)
        self.assertIsNotNone(t_a)
        self.assertIs(t_a, t_c, "identical attrs must keep the two leaves in one shared table")
        self.assertEqual(sr.walk(0x40200000)[1], 0x80010000)
        self.assertEqual(sr.walk(0x40201000)[1], 0x80020000)


class TestExactVaSiblingsWithFreeForce(unittest.TestCase):
    """Exact VAs are never moved by coloring. Two default (same-signature) exact VAs that
    occupy different slots at the conflict level must coexist -- coloring pins only the free
    forcing sibling to a distinct slot, and never forces the two exacts to agree."""

    _MODE = RV.RiscvPagingModes.SV39

    def test_free_force_isolates_from_two_default_exacts(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        # Two default exact VAs at DIFFERENT level-1 slots (bit 21) -- same signature, must
        # not be forced onto one index.
        ex1 = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        ex2 = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40200000)))
        # A free-VA sibling forcing a level-1 pointer attr (conflicting signature).
        free = b.add_page(Page(space=va))
        for src in (ex1, ex2):
            p = b.add_page(Page(space=b.phys))
            b.add_mapping(Mapping(src=src, dst=p, pt_nodes=_leaf_nodes()))
        pf = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=free, dst=pf, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(attrs={"d": 0})}))
        result = b.build()  # must not raise
        sr = result.space(va)
        # Both exact VAs stay pinned and reachable; the free-VA force never displaced them.
        self.assertEqual(result.address_of(ex1)[0], 0x40000000)
        self.assertEqual(result.address_of(ex2)[0], 0x40200000)
        self.assertIsNotNone(_leaf_table(sr, 0x40000000))
        self.assertIsNotNone(_leaf_table(sr, 0x40200000))
        # The free forcing sibling also builds and translates.
        self.assertIsNotNone(_leaf_table(sr, result.address_of(free)[0]))


class TestDeterminismAcrossHashSeeds(unittest.TestCase):
    """Colored trees are reproducible: the seq-keyed derived RNG + sorted-tuple signatures
    make index selection independent of ``PYTHONHASHSEED`` and mapping add-order."""

    _SNIPPET = textwrap.dedent(
        """
        import riescue.lib.common as common
        import riescue.lib.enums as RV
        from riescue.lib.rand import RandNum
        from riescue.riemap.memory import Memory
        from riescue.riemap.builder import PageTableBuilder
        from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTNode, Space

        mem = Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})
        b = PageTableBuilder(rng=RandNum(seed=7), memory=mem)
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV48))
        leaf = {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}
        srcs = []
        for i in range(6):
            s = b.add_page(Page(space=va))
            p = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + i * 0x1000)))
            b.add_mapping(Mapping(src=s, dst=p, pt_nodes={LEAF: PTNode(attrs=leaf), 2: PTNode(attrs={"d": i % 2}), 1: PTNode(attrs={"a": i % 3 == 0})}))
            srcs.append(s)
        r = b.build()
        print(";".join("%x" % r.address_of(s)[0] for s in srcs))
        """
    )

    def _run(self, hashseed):
        env = dict(os.environ, PYTHONHASHSEED=hashseed)
        out = subprocess.check_output([sys.executable, "-c", self._SNIPPET], env=env)
        return out.decode().strip()

    def test_identical_under_different_hash_seeds(self):
        a = self._run("0")
        b = self._run("12345")
        c = self._run("random")
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertTrue(a, "snippet produced no output")


class TestSignBoundary(unittest.TestCase):
    """Top-level coloring must never pin the VA sign bit (va_bits-1), across the
    sv39/48/57 boundary, and leaves must stay reachable."""

    def _run_mode(self, mode, top_level):
        b = PageTableBuilder(rng=RandNum(seed=3), memory=_memory())
        va = b.add_space(Space(paging_mode=mode))
        a = b.add_page(Page(space=va))
        c = b.add_page(Page(space=va))
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        # Conflicting forced attrs on the TOP pointer level -> a top-level split.
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), top_level: PTNode(attrs={"d": 0})}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), top_level: PTNode(attrs={"d": 1})}))
        result = b.build()
        va_bits = RV.RiscvPagingModes.linear_addr_bits(mode)
        sign = 1 << (va_bits - 1)
        for page in (a, c):
            st = b._page_state[page]
            self.assertEqual(st.color_clear & sign, 0, f"{mode}: sign bit cleared by coloring")
            self.assertEqual(st.color_or & sign, 0, f"{mode}: sign bit set by coloring")
            self.assertTrue(st.color_pinned, f"{mode}: top-level conflict should pin some bits")
        # A top-level split gives distinct top indices; both still translate.
        self.assertNotEqual(_index(result.address_of(a)[0], mode, top_level), _index(result.address_of(c)[0], mode, top_level))
        self.assertEqual(result.space(va).walk(result.address_of(a)[0])[1], 0x80010000)
        self.assertEqual(result.space(va).walk(result.address_of(c)[0])[1], 0x80020000)

    def test_sv39_top_level(self):
        self._run_mode(RV.RiscvPagingModes.SV39, top_level=2)

    def test_sv57_top_level(self):
        self._run_mode(RV.RiscvPagingModes.SV57, top_level=4)


class TestLinkedFramePinnersStraddleRootEdge(unittest.TestCase):
    """Two ``OffsetFrom``-linked pages (child = anchor + delta) that BOTH pin their own
    per-page PT-node frames share the whole upper walk, so they demand different child
    tables at the same shared slots -- an unavoidable frame conflict when they stay in one
    subtree. Coloring cannot relocate the child (it rides the anchor's draw), so the builder
    deduces a root-region-edge placement for the anchor: anchor + delta then carries into the
    NEXT top-level slot, giving the pair fully disjoint subtrees. This reproduces the
    hypervisor_paging_faults_vs page-crossing (num_pages>1) failure at the engine level."""

    def _frame_nodes(self, b, non_leaf_levels):
        """A distinct pinned physical frame Page at each non-leaf level (mimics modify_pt's
        per-page PT-node frames), plus an auto leaf."""
        nodes = {LEAF: PTNode(attrs=_leaf_attrs())}
        for lvl in non_leaf_levels:
            nodes[lvl] = PTNode(page=b.add_page(Page(space=b.phys)))
        return nodes

    def _run(self, mode):
        top = RV.RiscvPagingModes.max_levels(mode) - 1
        # Pin a distinct per-page frame at every non-leaf level BELOW the root (the root
        # table is the single shared satp, never per-page). Distinct level-(top-1) frames at
        # the shared root slot are what conflict without the edge-placement deduction.
        non_leaf = list(range(1, top))
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=mode))
        anchor = b.add_page(Page(space=va))  # free VA
        child = b.add_page(Page(space=va, addr=AddrSpec(relation=OffsetFrom(anchor, 0x1000))))
        pa_a = b.add_page(Page(space=b.phys))
        pa_c = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=anchor, dst=pa_a, pt_nodes=self._frame_nodes(b, non_leaf)))
        b.add_mapping(Mapping(src=child, dst=pa_c, pt_nodes=self._frame_nodes(b, non_leaf)))
        result = b.build()  # must NOT raise a slot conflict
        va_a = result.address_of(anchor)[0]
        va_c = result.address_of(child)[0]
        # The child sits exactly one page above the anchor (OffsetFrom preserved) ...
        self.assertEqual(va_c, va_a + 0x1000)
        # ... and the anchor was pushed to a root-region edge so the pair lands in DIFFERENT
        # top-level slots -- disjoint subtrees, so their per-page frames never collide.
        self.assertNotEqual(
            _index(va_a, mode, top),
            _index(va_c, mode, top),
            f"{mode}: linked frame-pinning siblings must straddle a top-level PT-region edge",
        )
        # Both remain reachable through their own (now disjoint) trees.
        self.assertIsNotNone(_leaf_table(result.space(va), va_a))
        self.assertIsNotNone(_leaf_table(result.space(va), va_c))

    def test_sv57(self):
        self._run(RV.RiscvPagingModes.SV57)

    def test_sv48(self):
        self._run(RV.RiscvPagingModes.SV48)

    def test_sv39(self):
        self._run(RV.RiscvPagingModes.SV39)

    def test_sameas_base_and_offset_child_color_only_bare_root(self):
        mode = RV.RiscvPagingModes.SV39
        top = RV.RiscvPagingModes.max_levels(mode) - 1
        b = PageTableBuilder(rng=RandNum(seed=15), memory=_memory())
        va = b.add_space(Space(paging_mode=mode))
        bare = b.add_page(Page(space=va))
        base = b.add_page(Page(space=va, addr=AddrSpec(relation=SameAs(bare))))
        child = b.add_page(
            Page(
                space=va,
                addr=AddrSpec(relation=OffsetFrom(bare, 0x1000)),
            )
        )
        non_leaf = list(range(1, top))
        for src in (base, child):
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=b.add_page(Page(space=b.phys)),
                    pt_nodes=self._frame_nodes(b, non_leaf),
                )
            )

        result = b.build()
        base_va = result.address_of(base)[0]
        child_va = result.address_of(child)[0]
        self.assertEqual(child_va, base_va + 0x1000)
        self.assertNotEqual(_index(base_va, mode, top), _index(child_va, mode, top))
        self.assertTrue(b._page_state[bare].color_pinned)
        self.assertFalse(b._page_state[base].color_pinned)
        self.assertFalse(b._page_state[child].color_pinned)

    def test_unsupported_multi_offset_family_remains_a_conflict(self):
        mode = RV.RiscvPagingModes.SV39
        top = RV.RiscvPagingModes.max_levels(mode) - 1
        b = PageTableBuilder(rng=RandNum(seed=16), memory=_memory())
        va = b.add_space(Space(paging_mode=mode))
        root = b.add_page(Page(space=va))
        members = [
            b.add_page(Page(space=va, addr=AddrSpec(relation=SameAs(root)))),
            b.add_page(Page(space=va, addr=AddrSpec(relation=OffsetFrom(root, 0x1000)))),
            b.add_page(Page(space=va, addr=AddrSpec(relation=OffsetFrom(root, 0x2000)))),
        ]
        non_leaf = list(range(1, top))
        for src in members:
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=b.add_page(Page(space=b.phys)),
                    pt_nodes=self._frame_nodes(b, non_leaf),
                )
            )
        with self.assertRaisesRegex(ValueError, "conflicting pinned frames"):
            b.build()


class TestGstageIdentitySignsApart(unittest.TestCase):
    """A VS node's declared g-stage identity (its :class:`PTGPage`) is part of the level's
    sharing signature.

    The frame a level-``L`` pointer PTE targets is shared by every mapping that shares that
    slot, and only the FIRST walk to reach it emits its g-stage identity -- so a mapping whose
    identity differs from a would-be sibling's must land in a different slot. Without that,
    the sibling's default identity silently wins and the force never reaches a PTE:
    hypervisor_tlb_fence SID_HFTLB_78 asked for ``a_nonleaf_gleaf=0`` on the frame holding its
    VS leaf PTE, coalesced with a plain page under the same level-1 node, and read back A=1.
    """

    _MODE = RV.RiscvPagingModes.SV39
    _A_BIT = 1 << 6

    # Two exact VAs sharing index_2 and index_1 -- i.e. one shared level-1 pointer PTE and
    # so one shared level-0 frame -- differing only at index_0. Coloring never moves an
    # exact VA, so these coalesce unless the signature itself keeps them apart.
    _SHARED_PREFIX_VAS = (0x40200000, 0x40201000)

    def _build(self, forced_attrs, other_attrs, exact_vas=None, seed=1, nonleaf_sizes=(None, None)):
        """Two two-stage pages in one VS space, built. Returns
        ``(result, vs_space, g_space, [page_a, page_b])``."""
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        g = b.add_space(Space(paging_mode=self._MODE, stage=Stage.G))
        vs = declare_vs_root_identity(b, self._MODE, g)
        vas = []
        for i, (attrs, nonleaf_size) in enumerate(zip((forced_attrs, other_attrs), nonleaf_sizes)):
            va = b.add_page(Page(space=vs, addr=AddrSpec(exact=exact_vas[i]) if exact_vas else AddrSpec()))
            # add_two_stage_mapping adds the HPA page itself (and mints the GPA SameAs it).
            hpa = Page(space=b.phys, addr=AddrSpec(exact=0x80200000 + i * 0x1000))
            resolved_nonleaf_size = nonleaf_size.preferred if isinstance(nonleaf_size, Choice) else nonleaf_size
            geometry_nodes = (
                {
                    level: PTNode(
                        page=PTGPage(
                            pagesize=nonleaf_size,
                            identity=True,
                        )
                    )
                    for level in range(
                        0,
                        RV.RiscvPagingModes.max_levels(self._MODE) - 1,
                    )
                }
                if nonleaf_size is not None
                else None
            )
            b.add_two_stage_mapping(
                va_page=va,
                hpa_page=hpa,
                gpa_space=g,
                attrs={**_leaf_attrs(), "u": 1, **attrs},
                vs_pagesize=RV.RiscvPageSizes.S4KB,
                gstage_mode=self._MODE,
                gstage_nonleaf_pagesize=resolved_nonleaf_size,
                pt_nodes=geometry_nodes,
            )
            vas.append(va)
        return b.build(), vs, g, vas

    def _leaf_frame_gpa(self, result, vs_space, va):
        """The GPA of the frame holding ``va``'s VS LEAF PTEs -- the node a
        ``*_nonleaf_g*`` force names, surfaced on the level-1 walk step."""
        steps, _ = result.space(vs_space).walk(va)
        (step,) = [s for s in steps if s.level == 1]
        self.assertIsNotNone(step.ptg_gpa, "the level-1 VS node must surface its declared g-stage frame")
        return step.ptg_gpa

    def _gstage_leaf_a_bit(self, result, g_space, gpa):
        sr = result.space(g_space)
        steps, translated = sr.walk(gpa)
        self.assertEqual(translated, gpa, "the frame's g-stage identity must translate GPA -> the same HPA")
        leaf = min(steps, key=lambda s: s.level)
        return (dict(sr.pte_entries())[leaf.pte_addr] & self._A_BIT) >> 6

    def _gstage_leaf_pbmt(self, result, g_space, gpa):
        sr = result.space(g_space)
        steps, _ = sr.walk(gpa)
        leaf = min(steps, key=lambda step: step.level)
        return (dict(sr.pte_entries())[leaf.pte_addr] >> 61) & 3

    def test_forced_frame_identity_survives_a_plain_sibling(self):
        # Free VAs, so coloring is what decides whether the two coalesce. Swept over seeds:
        # with the signature blind to the PTGPage some seeds separate the two anyway and the
        # force survives by luck, so a single seed is not evidence either way.
        for seed in range(8):
            with self.subTest(seed=seed):
                result, vs, g, (forced, plain) = self._build({"a_level1_glevel0": 0}, {}, seed=seed)
                gpa_f = self._leaf_frame_gpa(result, vs, result.address_of(forced)[0])
                gpa_p = self._leaf_frame_gpa(result, vs, result.address_of(plain)[0])
                self.assertNotEqual(gpa_f, gpa_p, "a forced g-stage identity must not share its frame with a default one")
                self.assertEqual(self._gstage_leaf_a_bit(result, g, gpa_f), 0, "a_nonleaf_gleaf=0 lost to the sibling's default identity")
                self.assertEqual(self._gstage_leaf_a_bit(result, g, gpa_p), 1, "the plain sibling must keep the default identity")

    def _shared_prefix_frames(self, forced_attrs, other_attrs):
        result, vs, _g, _pages = self._build(forced_attrs, other_attrs, exact_vas=self._SHARED_PREFIX_VAS)
        return [self._leaf_frame_gpa(result, vs, va) for va in self._SHARED_PREFIX_VAS]

    def test_identical_forces_still_share_a_frame(self):
        # Signing must not over-split: two mappings declaring the SAME g-stage identity
        # produce the same PTE, so they may still coalesce into one frame.
        one, two = self._shared_prefix_frames({"a_level1_glevel0": 0}, {"a_level1_glevel0": 0})
        self.assertEqual(one, two, "identical g-stage identities must keep sharing one frame")

    def test_plain_two_stage_pages_still_share_a_frame(self):
        # The attribute-free PTGPage every non-leaf level carries for geometry alone must
        # stay inert in the signature, or every two-stage page would get its own subtree.
        one, two = self._shared_prefix_frames({}, {})
        self.assertEqual(one, two, "an empty PTGPage must sign identically to an absent node")

    def test_conflicting_identities_on_unmovable_exacts_raise(self):
        # Coloring cannot separate two exact VAs, so the conflict has to SURFACE rather than
        # resolve itself by silently dropping one page's identity.
        with self.assertRaises(ValueError) as cm:
            self._build({"a_level1_glevel0": 0}, {}, exact_vas=self._SHARED_PREFIX_VAS)
        self.assertIn("conflict", str(cm.exception))

    def test_compatible_choices_share_and_resolve_once(self):
        first = Choice(preferred=1, alternatives=(2,))
        second = Choice(preferred=2, alternatives=(1,))
        result, vs, g, _pages = self._build(
            {"pbmt_level1_glevel0": first},
            {"pbmt_level1_glevel0": second},
            exact_vas=self._SHARED_PREFIX_VAS,
        )
        one, two = [self._leaf_frame_gpa(result, vs, va) for va in self._SHARED_PREFIX_VAS]
        self.assertEqual(one, two, "compatible policy domains should not split a fixed shared frame")
        self.assertIn(self._gstage_leaf_pbmt(result, g, one), (1, 2))

    def test_choice_and_exact_pagesizes_share_at_the_common_size(self):
        allowed = Choice(
            preferred=RV.RiscvPageSizes.S2MB,
            alternatives=(RV.RiscvPageSizes.S4KB, RV.RiscvPageSizes.S1GB),
        )
        result, vs, _g, _pages = self._build(
            {},
            {},
            exact_vas=self._SHARED_PREFIX_VAS,
            nonleaf_sizes=(allowed, RV.RiscvPageSizes.S4KB),
        )
        one, two = [self._leaf_frame_gpa(result, vs, va) for va in self._SHARED_PREFIX_VAS]
        self.assertEqual(one, two, "overlapping geometry policies should share the fixed VS node")

    def test_conflicting_explicit_pbmt_values_on_fixed_shared_frame_raise(self):
        with self.assertRaisesRegex(ValueError, "conflict"):
            self._build(
                {"pbmt_level1_glevel0": 1},
                {"pbmt_level1_glevel0": 2},
                exact_vas=self._SHARED_PREFIX_VAS,
            )


class TestExhaustion(unittest.TestCase):
    """A top-level split needing more buckets than the (sign-capped) index field can hold
    raises a precise exhaustion error -- never silent corruption."""

    def test_top_level_overflow_raises(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))  # top field usable = 8 bits = 256
        # 257 distinct forced pointer-attr signatures at the top level -> > capacity.
        for i in range(257):
            s = b.add_page(Page(space=va))
            p = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + i * 0x1000)))
            b.add_mapping(Mapping(src=s, dst=p, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"a": i})}))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("exhausted", str(cm.exception))


class TestMinimalColorLanes(unittest.TestCase):
    def test_masked_range_matcher_carries_to_the_next_valid_prefix(self):
        matcher = PageTableBuilder._masked_value_in_range
        self.assertTrue(
            matcher(
                0b1010,
                0b1100,
                4,
                0b0010,
                0,
                1,
            )
        )
        self.assertTrue(
            matcher(
                0b0111,
                0b1100,
                4,
                0b1010,
                0b1000,
                4,
            )
        )
        self.assertFalse(
            matcher(
                0b0111,
                0b1010,
                4,
                0b1010,
                0b1000,
                4,
            )
        )

    def _colored_pages(self, signatures):
        mode = RV.RiscvPagingModes.SV39
        builder = PageTableBuilder(
            rng=RandNum(seed=17),
            memory=_memory(),
        )
        space = builder.add_space(Space(paging_mode=mode))
        pages = []
        for signature in signatures:
            src = builder.add_page(Page(space=space))
            pages.append(src)
            builder.add_mapping(
                Mapping(
                    src=src,
                    dst=builder.add_page(Page(space=builder.phys)),
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        2: PTNode(attrs={"a": signature}),
                    },
                )
            )
        builder._apply_coloring()
        return builder, mode, pages

    def test_two_signatures_pin_one_index_bit(self):
        builder, mode, pages = self._colored_pages((0, 1))
        hi, lo = RV.RiscvPagingModes.index_bits(mode, 2)
        field_mask = ((1 << (hi - lo + 1)) - 1) << lo
        masks = {builder._page_state[page].color_clear & field_mask for page in pages}
        self.assertEqual(len(masks), 1)
        self.assertEqual(bin(next(iter(masks))).count("1"), 1)

    def test_three_signatures_pin_two_index_bits(self):
        builder, mode, pages = self._colored_pages((0, 1, 2))
        hi, lo = RV.RiscvPagingModes.index_bits(mode, 2)
        field_mask = ((1 << (hi - lo + 1)) - 1) << lo
        masks = {builder._page_state[page].color_clear & field_mask for page in pages}
        self.assertEqual(len(masks), 1)
        self.assertEqual(bin(next(iter(masks))).count("1"), 2)

    def test_nonsecure_same_as_anchor_cannot_reach_secure_lane(self):
        builder = PageTableBuilder(
            rng=RandNum(seed=4),
            memory=_memory(),
        )
        gspace = builder.add_space(
            Space(
                paging_mode=RV.RiscvPagingModes.SV57,
                stage=Stage.G,
            )
        )
        hpa = builder.add_page(Page(space=builder.phys))
        gpa = builder.add_page(Page(space=gspace, addr=AddrSpec(relation=SameAs(hpa))))
        mapping = builder.add_mapping(Mapping(src=gpa, dst=hpa))
        self.assertTrue(
            builder._color_code_reachable(
                mapping,
                (55,),
                0,
                {},
                57,
            )
        )
        self.assertFalse(
            builder._color_code_reachable(
                mapping,
                (55,),
                1,
                {},
                57,
            )
        )

    def test_choice_including_pointer_default_shares_with_plain_mapping(self):
        builder = PageTableBuilder(
            rng=RandNum(seed=5),
            memory=_memory(),
        )
        space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        pages = []
        for node in (
            PTNode(),
            PTNode(attrs={"n": Choice(preferred=1, alternatives=(0,))}),
        ):
            src = builder.add_page(Page(space=space))
            pages.append(src)
            builder.add_mapping(
                Mapping(
                    src=src,
                    dst=builder.add_page(Page(space=builder.phys)),
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        2: node,
                    },
                )
            )
        builder._apply_coloring()
        self.assertEqual(
            [builder._page_state[page].color_clear for page in pages],
            [0, 0],
        )


class TestLocalBacktracking(unittest.TestCase):
    """A multi-signature parent must be able to lend more than one slot to the signature
    whose descendants overflow, while retaining a slot for its sibling signature."""

    def test_descendant_overflow_gets_extra_parent_slot(self):
        mode = RV.RiscvPagingModes.SV39
        b = PageTableBuilder(rng=RandNum(seed=9), memory=_memory())
        va = b.add_space(Space(paging_mode=mode))
        overflowing = []
        # A level-1 field has 512 indices.  These 513 distinct level-1 signatures share
        # one level-2 signature, so that signature needs two level-2 parent slots.
        for i in range(513):
            src = b.add_page(Page(space=va))
            dst = b.add_page(Page(space=b.phys))
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=dst,
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        2: PTNode(attrs={"d": 0}),
                        1: PTNode(attrs={"a": i}),
                    },
                )
            )
            overflowing.append(src)
        sibling = b.add_page(Page(space=va))
        b.add_mapping(
            Mapping(
                src=sibling,
                dst=b.add_page(Page(space=b.phys)),
                pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"d": 1})},
            )
        )

        b._apply_coloring()

        overflow_top = {_index(b._page_state[p].color_or, mode, 2) for p in overflowing}
        sibling_top = _index(b._page_state[sibling].color_or, mode, 2)
        self.assertEqual(len(overflow_top), 2)
        self.assertNotIn(sibling_top, overflow_top)

    def test_failed_coloring_commits_no_partial_masks(self):
        mode = RV.RiscvPagingModes.SV39
        b = PageTableBuilder(rng=RandNum(seed=4), memory=_memory())
        va = b.add_space(Space(paging_mode=mode))
        pages = []
        # The exact pair shares level-2/level-1 indices but has conflicting level-1
        # signatures.  The free top-level sibling is speculatively colored before that
        # hard collision is discovered.
        for addr, lower_attr in ((0x40200000, 0), (0x40201000, 1)):
            src = b.add_page(Page(space=va, addr=AddrSpec(exact=addr)))
            pages.append(src)
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=b.add_page(Page(space=b.phys)),
                    pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"d": 0}), 1: PTNode(attrs={"a": lower_attr})},
                )
            )
        free = b.add_page(Page(space=va))
        pages.append(free)
        b.add_mapping(
            Mapping(
                src=free,
                dst=b.add_page(Page(space=b.phys)),
                pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"d": 1})},
            )
        )

        with self.assertRaises(ValueError):
            b._apply_coloring()
        for page in pages:
            state = b._page_state[page]
            self.assertEqual((state.color_clear, state.color_or, state.color_pinned), (0, 0, False))

    def test_fixed_leaves_do_not_fragment_pointer_color_lane(self):
        mode = RV.RiscvPagingModes.SV39
        b = PageTableBuilder(rng=RandNum(seed=6), memory=_memory())
        va = b.add_space(Space(paging_mode=mode))
        leaves = []
        # Fixed leaves spread over many complete indices. They own their leaf
        # slots, not partial pointer-color codes.
        for index in range(129):
            src = b.add_page(
                Page(
                    space=va,
                    pagesize=RV.RiscvPageSizes.S1GB,
                    addr=AddrSpec(exact=(index + 1) << 30),
                )
            )
            leaves.append(src)
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=b.add_page(
                        Page(
                            space=b.phys,
                            pagesize=RV.RiscvPageSizes.S1GB,
                        )
                    ),
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                    },
                )
            )
        pointers = []
        for dirty in (0, 1):
            pointer = b.add_page(Page(space=va))
            pointers.append(pointer)
            b.add_mapping(
                Mapping(
                    src=pointer,
                    dst=b.add_page(Page(space=b.phys)),
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        2: PTNode(attrs={"d": dirty}),
                    },
                )
            )

        b._apply_coloring()

        self.assertTrue(all(b._page_state[page].color_clear == 0 for page in leaves))
        self.assertNotEqual(
            b._page_state[pointers[0]].color_or,
            b._page_state[pointers[1]].color_or,
        )

    def test_large_leaf_attributes_do_not_create_color_signatures(self):
        b = PageTableBuilder(rng=RandNum(seed=6), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        leaves = []
        for attrs in ({"v": 1, "r": 1, "d": 0}, {"v": 1, "r": 1, "d": 1}):
            src = b.add_page(
                Page(
                    space=va,
                    pagesize=RV.RiscvPageSizes.S1GB,
                )
            )
            leaves.append(src)
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=b.add_page(
                        Page(
                            space=b.phys,
                            pagesize=RV.RiscvPageSizes.S1GB,
                        )
                    ),
                    pt_nodes={LEAF: PTNode(attrs=attrs)},
                )
            )

        b._apply_coloring()

        for page in leaves:
            state = b._page_state[page]
            self.assertEqual(
                (state.color_clear, state.color_or, state.color_pinned),
                (0, 0, False),
            )


class TestLeafTableCapacity(unittest.TestCase):
    """Compatible mappings share until the leaf PTE table is actually full."""

    def _add_capacity_shape(self, builder, space):
        srcs = []
        # This signature needs 33 * 16 = 528 leaf PTE slots.
        for _ in range(33):
            src = builder.add_page(Page(space=space, pagesize=RV.RiscvPageSizes.S64KB))
            dst = builder.add_page(Page(space=builder.phys, pagesize=RV.RiscvPageSizes.S64KB))
            builder.add_mapping(
                Mapping(
                    src=src,
                    dst=dst,
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        2: PTNode(attrs={"d": 0}),
                        1: PTNode(attrs={"a": 0}),
                    },
                )
            )
            srcs.append(src)
        # Conflicts at levels 2 and 1 make coloring choose both SV39
        # ancestor fields for the crowded signature.
        for level, attrs in ((2, {"d": 1}), (1, {"a": 1})):
            src = builder.add_page(Page(space=space))
            dst = builder.add_page(Page(space=builder.phys))
            nodes = {
                LEAF: PTNode(attrs=_leaf_attrs()),
                2: PTNode(attrs={"d": 0}),
                1: PTNode(attrs={"a": 0}),
            }
            nodes[level] = PTNode(attrs=attrs)
            builder.add_mapping(Mapping(src=src, dst=dst, pt_nodes=nodes))
        return srcs

    def test_fully_colored_path_spills_after_512_leaf_slots(self):
        b = PageTableBuilder(rng=RandNum(seed=13), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        srcs = self._add_capacity_shape(b, va)

        result = b.build()
        tables = {_leaf_table(result.space(va), result.address_of(src)[0]) for src in srcs}
        self.assertGreater(len(tables), 1)
        self.assertTrue(all(table is not None and len(table.table) <= 512 for table in tables))
        for src in srcs:
            self.assertIsNotNone(_leaf_table(result.space(va), result.address_of(src)[0]))

    def test_free_sv57_ancestors_do_not_trigger_capacity_coloring(self):
        b = PageTableBuilder(rng=RandNum(seed=13), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV57))
        srcs = self._add_capacity_shape(b, va)

        result = b.build()
        top_fields = 0
        for level in (4, 3):
            hi, lo = RV.RiscvPagingModes.index_bits(va.paging_mode, level)
            top_fields |= ((1 << (hi - lo + 1)) - 1) << lo
        self.assertTrue(all(not (b._page_state[src].color_clear & top_fields) for src in srcs))
        for src in srcs:
            self.assertIsNotNone(_leaf_table(result.space(va), result.address_of(src)[0]))


class TestNonFreeAddressModes(unittest.TestCase):
    """Relation and region allocation modes ignore coloring masks.  A conflict requiring
    such a source to move is rejected instead of pretending it was independently colored."""

    def test_uncontested_nonfree_sources_remain_uncolored(self):
        for address_mode in ("relation", "region"):
            with self.subTest(address_mode=address_mode):
                b = PageTableBuilder(rng=RandNum(seed=2), memory=_memory())
                va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
                if address_mode == "relation":
                    anchor = b.add_page(Page(space=va))
                    spec = AddrSpec(relation=OffsetFrom(anchor, 0x1000))
                else:
                    spec = AddrSpec(region=b.add_region(MemoryRegion(size=0x400000)))
                constrained = b.add_page(Page(space=va, addr=spec))
                b.add_mapping(
                    Mapping(
                        src=constrained,
                        dst=b.add_page(Page(space=b.phys)),
                        pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())},
                    )
                )

                b._apply_coloring()
                state = b._page_state[constrained]
                self.assertEqual((state.color_clear, state.color_or, state.color_pinned), (0, 0, False))

    def test_relation_and_region_sources_do_not_receive_independent_colors(self):
        for address_mode in ("relation", "region"):
            with self.subTest(address_mode=address_mode):
                b = PageTableBuilder(rng=RandNum(seed=2), memory=_memory())
                va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
                if address_mode == "relation":
                    anchor = b.add_page(Page(space=va))
                    spec = AddrSpec(relation=OffsetFrom(anchor, 0x1000))
                else:
                    region = b.add_region(MemoryRegion(size=0x400000))
                    spec = AddrSpec(region=region)
                constrained = b.add_page(Page(space=va, addr=spec))
                free = b.add_page(Page(space=va))
                for src, attr in ((constrained, 0), (free, 1)):
                    b.add_mapping(
                        Mapping(
                            src=src,
                            dst=b.add_page(Page(space=b.phys)),
                            pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"d": attr})},
                        )
                    )

                b._apply_coloring()
                for page in (constrained, free):
                    state = b._page_state[page]
                    self.assertEqual((state.color_clear, state.color_or, state.color_pinned), (0, 0, False))


class TestColoringHelperSignatures(unittest.TestCase):
    """The coloring helpers were threaded a space's target map and paging mode that none of
    them reads -- ``_apply_coloring`` iterates ``self.spaces`` itself, and the partition /
    index-assignment steps work purely on signatures and slot capacity. A parameter no body
    reads is a false claim about what the step depends on, so the signatures are pinned."""

    def _params(self, fn):
        return [p for p in inspect.signature(fn).parameters if p != "self"]

    def test_apply_coloring_takes_nothing(self):
        self.assertEqual(self._params(PageTableBuilder._apply_coloring), [])

    def test_spill_partition_takes_only_what_it_reads(self):
        self.assertEqual(self._params(PageTableBuilder._spill_partition), ["members", "level", "capacity"])

    def test_lane_assignment_does_not_take_a_paging_mode(self):
        self.assertNotIn(
            "mode",
            self._params(PageTableBuilder._assign_lane_codes),
        )

    def test_exhausted_fallback_reraises_original_spill(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        members = []
        for value in (0, 1):
            src = b.add_page(Page(space=space))
            dst = b.add_page(Page(space=b.phys))
            members.append(
                b.add_mapping(
                    Mapping(
                        src=src,
                        dst=dst,
                        pt_nodes={
                            LEAF: PTNode(attrs=_leaf_attrs()),
                            2: PTNode(attrs={"d": value}),
                        },
                    )
                )
            )

        original = b._color_buckets

        def always_spill(*_args, **_kwargs):
            raise _Spill(1)

        b._color_buckets = always_spill
        try:
            with self.assertRaises(_Spill) as raised:
                b._color_bucket(
                    0,
                    RV.RiscvPagingModes.SV39,
                    members,
                    2,
                    39,
                    (),
                )
            self.assertEqual(raised.exception.level, 1)
        finally:
            b._color_buckets = original


class TestExactVaPinnedChildFrameSignsApart(unittest.TestCase):
    """An exact-VA mapping that pins its own level-3 child frame must sign that frame.

    Without the signature, a free 1 GiB sibling pinning a different level-3 frame at the
    same root index falsely shares a bucket and ``build()`` collides on the frame pin.
    """

    _MODE = RV.RiscvPagingModes.SV57
    _TOP = RV.RiscvPagingModes.max_levels(_MODE) - 1
    _LEVEL3 = 3

    def _build(self, seed):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va = b.add_space(Space(paging_mode=self._MODE))
        frame_exact = b.add_page(Page(space=b.phys))
        exact = b.add_page(Page(space=va, addr=AddrSpec(exact=0x0)))
        b.add_mapping(
            Mapping(
                src=exact,
                dst=b.add_page(Page(space=b.phys)),
                pt_nodes={
                    LEAF: PTNode(attrs=_leaf_attrs()),
                    self._LEVEL3: PTNode(page=frame_exact),
                },
            )
        )
        frame_free = b.add_page(Page(space=b.phys))
        free = b.add_page(Page(space=va, pagesize=RV.RiscvPageSizes.S1GB))
        b.add_mapping(
            Mapping(
                src=free,
                dst=b.add_page(Page(space=b.phys)),
                pt_nodes={
                    LEAF: PTNode(attrs=_leaf_attrs()),
                    self._LEVEL3: PTNode(page=frame_free),
                },
            )
        )
        return b.build(), exact, free

    def test_conflicting_level3_pins_at_root_index_zero_separate(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                result, exact, free = self._build(seed)
                exact_va = result.address_of(exact)[0]
                free_va = result.address_of(free)[0]
                self.assertEqual(_index(exact_va, self._MODE, self._TOP), 0)
                self.assertNotEqual(
                    _index(free_va, self._MODE, self._TOP),
                    _index(exact_va, self._MODE, self._TOP),
                    "conflicting level-3 frame pins sharing root index 0 must be separated",
                )


class TestExactVaAdoptsChildBesideFreePinner(unittest.TestCase):
    """A plain exact VA at the same root index as an exact child-frame pinner must share
    that slot (adoption), while a free mapping pinning a *different* child is colored away.

    Greedy signature merge that folds free pinners before exact ones poisons the plain
    exact's bucket and falsely reports ``pinned VAs collide`` for this bfs.s / dijkstras.s
    pattern.
    """

    _MODE = RV.RiscvPagingModes.SV39
    _TOP = RV.RiscvPagingModes.max_levels(_MODE) - 1

    def test_plain_exact_adopts_sibling_pin_free_separates(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
                va = b.add_space(Space(paging_mode=self._MODE))
                # Both exacts share root index 0 (bits 38:30 clear); differ at level-1.
                plain = b.add_page(Page(space=va, addr=AddrSpec(exact=0x5000000)))
                pinned = b.add_page(Page(space=va, addr=AddrSpec(exact=0x6000000)))
                frame_exact = b.add_page(Page(space=b.phys))
                frame_free = b.add_page(Page(space=b.phys))
                b.add_mapping(
                    Mapping(
                        src=plain,
                        dst=b.add_page(Page(space=b.phys)),
                        pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())},
                    )
                )
                b.add_mapping(
                    Mapping(
                        src=pinned,
                        dst=b.add_page(Page(space=b.phys)),
                        pt_nodes={
                            LEAF: PTNode(attrs=_leaf_attrs()),
                            1: PTNode(page=frame_exact),
                        },
                    )
                )
                free = b.add_page(Page(space=va))
                b.add_mapping(
                    Mapping(
                        src=free,
                        dst=b.add_page(Page(space=b.phys)),
                        pt_nodes={
                            LEAF: PTNode(attrs=_leaf_attrs()),
                            1: PTNode(page=frame_free),
                        },
                    )
                )
                result = b.build()
                self.assertEqual(result.address_of(plain)[0], 0x5000000)
                self.assertEqual(result.address_of(pinned)[0], 0x6000000)
                free_va = result.address_of(free)[0]
                self.assertEqual(_index(0x5000000, self._MODE, self._TOP), 0)
                self.assertEqual(_index(0x6000000, self._MODE, self._TOP), 0)
                self.assertNotEqual(
                    _index(free_va, self._MODE, self._TOP),
                    0,
                    "free child-frame pinner must leave root index 0 to the exacts",
                )


class TestReservedLaneCodeProjection(unittest.TestCase):
    """Reserved root slots must be excluded from partial-lane codes using ``lo`` alignment."""

    @staticmethod
    def _project(positions, lo, reserved):
        return PageTableBuilder._reserved_lane_codes(positions, lo, reserved)

    def test_high_contiguous_partial_lane_uses_lo_not_min_positions(self):
        lo = 48
        positions = (55, 54, 53)
        reserved = {32}
        projected = self._project(positions, lo, reserved)
        self.assertEqual(projected, {1})
        wrong = {sum(((index >> (bit - min(positions))) & 1) << code_bit for code_bit, bit in enumerate(reversed(positions))) for index in reserved}
        self.assertEqual(wrong, {0}, "sanity: the old min(positions) shift mis-projects this case")

    def test_full_width_lane_with_nonzero_lo(self):
        lo = 48
        positions = tuple(range(56, lo - 1, -1))
        reserved = {32, 17}
        self.assertEqual(self._project(positions, lo, reserved), {32, 17})

    def test_partial_lane_excludes_only_reaching_codes(self):
        lo = 48
        positions = (55, 54, 53)
        reserved = {0, 32, 96}
        self.assertEqual(self._project(positions, lo, reserved), {0, 1, 3})


if __name__ == "__main__":
    unittest.main()
