# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Root-level ``PTNode`` frame pinning (recursive / self-referencing page tables).

A top-level ``PTNode(page=frame)`` makes the space's *root* page table structural: the
root table must physically live at that frame's resolved base, not at a freshly drawn
sptbr. These tests cover placement, sharing, conflicts, and unpinned roots."""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTGPage, PTNode, SameAs, Space, Stage
from tests.riemap.root_policy import declare_vs_root_identity

SV39 = RV.RiscvPagingModes.SV39


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs(**extra):
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, **extra}


def _leaf_node():
    """The leaf PTE node (leaf bits folded onto the LEAF sentinel)."""
    return PTNode(attrs=_leaf_attrs())


class TestRootSelfMapStructural(unittest.TestCase):
    """A top-level ``PTNode(page=root)`` whose mapping also targets ``root`` places the root
    table at ``root``'s frame and translates the self-map VA to that same frame."""

    def test_root_table_lives_at_pinned_frame(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        root = b.add_page(Page(space=b.phys))
        vself = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40001000)))
        b.add_mapping(Mapping(src=vself, dst=root, pt_nodes={LEAF: _leaf_node(), 2: PTNode(page=root)}))
        result = b.build()
        sr = result.space(va)
        root_pa = result.address_of(root)[1]

        # The root page table base (sptbr) physically IS the pinned frame ...
        self.assertEqual(sr.root_addr, root_pa, "root table must live at the pinned frame's PA")
        pm = sr._page_map
        self.assertEqual(pm.pinned_sptbr, root_pa)
        self.assertEqual(pm.basetable.base_addr, root_pa)
        self.assertIn(root_pa, pm.tables_by_base)
        self.assertIs(pm.tables_by_base[root_pa], pm.basetable)
        # ... and the self-map VA translates back to that very frame (structural recursion).
        self.assertEqual(sr.walk(0x40001000)[1], root_pa, "self-map must translate to the frame itself")


class TestSharedRoot(unittest.TestCase):
    """Two mappings pinning the *same* root frame share one root table; both walks succeed."""

    def test_shared_root_both_walks(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        root = b.add_page(Page(space=b.phys))
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        c = b.add_page(Page(space=va, addr=AddrSpec(exact=0x80000000)))
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: _leaf_node(), 2: PTNode(page=root)}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: _leaf_node(), 2: PTNode(page=root)}))
        result = b.build()
        sr = result.space(va)
        root_pa = result.address_of(root)[1]

        self.assertEqual(sr.root_addr, root_pa)
        # One interned root table at the pinned frame, packing both root-slot pointers.
        self.assertIs(sr._page_map.tables_by_base[root_pa], sr._page_map.basetable)
        self.assertEqual(sr.walk(0x40000000)[1], 0x80010000)
        self.assertEqual(sr.walk(0x80000000)[1], 0x80020000)


class TestConflictingRoots(unittest.TestCase):
    """Two mappings pinning *different* root frames in one space raise a precise ValueError."""

    def test_different_root_frames_raise(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        root1 = b.add_page(Page(space=b.phys))
        root2 = b.add_page(Page(space=b.phys))
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        c = b.add_page(Page(space=va, addr=AddrSpec(exact=0x80000000)))
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: _leaf_node(), 2: PTNode(page=root1)}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: _leaf_node(), 2: PTNode(page=root2)}))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("root pinned to both", str(cm.exception))


class TestNoRootPinUsesTheEngineRootFrame(unittest.TestCase):
    """With no CONSUMER root pin the root still comes from the solve: the builder declares its
    own root-frame page per table-bearing space (``_declare_root_frames``) and hands its solved
    address to ``pinned_sptbr``. The walker draws no root of its own -- a root self-mapped at
    VA == PA must be free in the space's linear pool too, which a post-solve draw cannot see."""

    def _build(self):
        b = PageTableBuilder(rng=RandNum(seed=3), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: _leaf_node()}))
        result = b.build()
        sr = result.space(va)
        return b, va, sr._page_map.pinned_sptbr, sr.root_addr

    def test_root_comes_from_the_engine_declared_frame(self):
        b, va, pinned, root = self._build()
        self.assertIsNotNone(pinned, "the root frame is placed by the solve, not drawn in the walk")
        self.assertEqual(pinned, root, "the root table must live at its declared frame's address")
        frame = b._root_frames[va]
        self.assertEqual(b._page_state[frame].allocated, root, "root_addr must be the declared frame's solved address")

    def test_root_placement_is_deterministic(self):
        _b1, _va1, _pinned1, root_first = self._build()
        _b2, _va2, _pinned2, root_second = self._build()
        self.assertEqual(root_first, root_second, "root placement must be deterministic for a fixed seed")

    def test_root_is_not_an_implicit_identity_page(self):
        b, va, _pinned, root = self._build()
        self.assertNotIn(b._root_frames[va], b._identity_pages)
        self.assertNotIn(root, b._page_maps[va].pages)


class TestRootFrameGeometryAndVisibility(unittest.TestCase):
    """Root frames satisfy their geometry and are visible to structural emission."""

    def _two_stage(self, seed=1):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, SV39, g)
        va = b.add_page(Page(space=vs))
        hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(bits=32)))
        gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
        b.add_mapping(
            Mapping(
                src=va,
                dst=gpa,
                pt_nodes={
                    LEAF: _leaf_node(),
                    0: PTNode(page=PTGPage(identity=True)),
                    1: PTNode(page=PTGPage(identity=True)),
                },
            )
        )
        b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: _leaf_node()}))
        return b, vs, g, b.build()

    def test_hgatp_root_is_2mb_aligned(self):
        # RieMap currently places its logical 4 KiB g-stage root on a 2 MiB boundary.
        _b, _vs, g, result = self._two_stage()
        self.assertEqual(result.space(g).root_addr & (0x200000 - 1), 0, "a g-stage root must stay 2 MiB aligned")

    def test_vs_root_is_4kb_aligned_and_a_valid_gpa(self):
        # A VS root under a g-stage is itself a GPA, so it must fit the g-stage input width
        # (SV39 g-stage: 41 bits) as well as being page aligned.
        _b, vs, _g, result = self._two_stage()
        root = result.space(vs).root_addr
        self.assertEqual(root & 0xFFF, 0)
        self.assertLess(
            root,
            1 << (RV.RiscvPagingModes.linear_addr_bits(SV39) - 1),
            f"VS root 0x{root:x} must be unchanged by SV39 canonicalization",
        )
        self.assertLess(root, 1 << RV.RiscvPagingModes.linear_addr_bits(SV39, gstage=True), f"VS root 0x{root:x} must be a valid SV39 GPA")

    def test_root_spans_are_declared_to_the_structural_emitter(self):
        # The root self-map is part of the accepted topology rather than a
        # side-channel interval used only during late emission.
        b, vs, _g, _result = self._two_stage()
        root_frame = vs.root_frame
        base = b._page_state[root_frame].allocated
        self.assertTrue(
            any(intent.span.space is _g and intent.span.start <= base < intent.span.end for intent in b._topology_plan.intents.values()),
            f"root GPA 0x{base:x} must appear in the accepted G-stage topology",
        )

    def test_structural_emitter_obeys_the_accepted_plan(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        emitted = []
        g = Space(paging_mode=SV39, stage=Stage.G)

        class Target:
            def add_raw_pt_page(self, **kwargs):
                emitted.append(kwargs)

        b._planned_explicit_spans = {
            g: [
                (
                    0x8001_0000,
                    0x8001_1000,
                )
            ]
        }
        identity_key = (
            g,
            0x8001_0000,
            RV.RiscvPageSizes.S64KB,
        )
        b._planned_structural_identities = {identity_key}
        b._planned_structural_targets = {
            identity_key: 0x8001_0000,
        }
        emitter = b._make_structural_emitter(
            [
                (
                    g,
                    Target(),
                )
            ]
        )
        emitter(
            gpa=0x8001_8000,
            hpa=0x8001_8000,
            attrs={},
            pagesize=RV.RiscvPageSizes.S64KB,
        )
        self.assertEqual(emitted, [])

        b._planned_explicit_spans.clear()
        emitter(
            gpa=0x8001_8000,
            hpa=0x8001_8000,
            attrs={},
            pagesize=RV.RiscvPageSizes.S64KB,
        )
        self.assertEqual(emitted[0]["linear_addr"], 0x8001_0000)

        emitter(
            gpa=0x8002_8000,
            hpa=0x8002_8000,
            attrs={},
            pagesize=RV.RiscvPageSizes.S64KB,
        )
        self.assertEqual(
            len(emitted),
            1,
            "late emission must not invent an identity absent from the plan",
        )


class TestRootFrameDoesNotCreateALeaf(unittest.TestCase):
    """An engine root is only a table frame; mapped superpages remain intact."""

    def _build(self, seed):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV57))
        srcs = []
        # A mix of 1 GiB and 2 MiB superpages exercises broad VA spans.
        for ps in (RV.RiscvPageSizes.S1GB, RV.RiscvPageSizes.S2MB, RV.RiscvPageSizes.S1GB, RV.RiscvPageSizes.S2MB):
            src = b.add_page(Page(space=va, pagesize=ps))
            dst = b.add_page(Page(space=b.phys, pagesize=ps))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: _leaf_node()}))
            srcs.append(src)
        result = b.build()
        return b, va, srcs, result

    def test_root_is_not_installed_as_a_page(self):
        b, va, _srcs, result = self._build(seed=1)
        self.assertNotIn(result.space(va).root_addr, b._page_maps[va].pages)

    def test_every_superpage_still_translates(self):
        # The tree builds and each superpage resolves -- i.e. no slot was stolen by the root.
        for seed in range(1, 25):
            with self.subTest(seed=seed):
                _b, va, srcs, result = self._build(seed)
                for src in srcs:
                    lin, phys = result.address_of(src)
                    self.assertEqual(result.space(va).walk(lin)[1], phys)


class TestPinnedRootFrameNeverLandsInsideAMappedSpan(unittest.TestCase):
    """The same guarantee applies when a consumer pins the root frame.

    A ``modify_pt`` map pins its root so the runtime can write live PTEs
    through a read-back window. Although that frame is declared in the
    physical domain, it must also avoid the rooted space's VA spans.
    """

    ROOT_LEVEL = RV.RiscvPagingModes.max_levels(RV.RiscvPagingModes.SV57) - 1

    def _build(self, seed):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV57))
        root = b.add_page(Page(space=b.phys))
        srcs = []
        for ps in (RV.RiscvPageSizes.S1GB, RV.RiscvPageSizes.S2MB, RV.RiscvPageSizes.S1GB, RV.RiscvPageSizes.S2MB):
            src = b.add_page(Page(space=va, pagesize=ps))
            dst = b.add_page(Page(space=b.phys, pagesize=ps))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: _leaf_node()}))
            srcs.append(src)
        # One page pins the root frame -- that is enough to make the whole space use it, exactly
        # as a single modify_pt map does among many plain ones.
        pinner = b.add_page(Page(space=va))
        pinner_pa = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=pinner, dst=pinner_pa, pt_nodes={LEAF: _leaf_node(), self.ROOT_LEVEL: PTNode(page=root)}))
        return b, va, root, srcs, b.build()

    def test_root_is_the_pinned_frame(self):
        b, va, root, _srcs, result = self._build(seed=1)
        self.assertEqual(result.space(va).root_addr, result.address_of(root)[1])
        self.assertNotIn(va, b._root_frames, "a consumer-pinned root must suppress the engine-declared one")

    def test_pinned_frame_knows_the_space_it_roots(self):
        b, va, root, _srcs, _result = self._build(seed=1)
        self.assertEqual(b._pinned_root_frame_spaces().get(root), [va], "the frame's rooted space must reach the solve")

    def test_pinned_root_is_not_installed_as_a_page(self):
        b, va, _root, _srcs, result = self._build(seed=1)
        self.assertNotIn(result.space(va).root_addr, b._page_maps[va].pages)

    def test_every_superpage_still_translates(self):
        for seed in range(1, 25):
            with self.subTest(seed=seed):
                _b, va, _root, srcs, result = self._build(seed)
                for src in srcs:
                    lin, phys = result.address_of(src)
                    self.assertEqual(result.space(va).walk(lin)[1], phys)

    def test_root_does_not_reserve_an_implicit_linear_translation(self):
        b, va, _root, _srcs, result = self._build(seed=1)
        root_addr = result.space(va).root_addr
        self.assertFalse(b.addrgen.linear_overlap(root_addr, 0x1000, va))


class TestSynthesizedVsRootFrame(unittest.TestCase):
    """``Space.root_frame`` is optional under a table-bearing G space. A VS root register
    holds a GPA, so the builder cannot use the plain physical root frame it gives an
    satp/hgatp; it synthesizes a GPA page plus the identity GPA -> HPA leaf that makes the
    guest's first fetch reachable. A consumer that declares a root still owns it."""

    VS_NODES = {
        0: PTNode(page=PTGPage(identity=True)),
        1: PTNode(page=PTGPage(identity=True)),
    }

    def _two_stage(self, seed=1, declare_root=False):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, SV39, g) if declare_root else b.add_space(Space(paging_mode=SV39, stage=Stage.VS))
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        hpa = b.add_page(Page(space=b.phys))
        gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
        b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes={LEAF: _leaf_node(), **self.VS_NODES}))
        b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: _leaf_node()}))
        return b, vs, g, va, b.build()

    def test_two_stage_builds_without_a_declared_root(self):
        _b, vs, _g, va, result = self._two_stage()
        self.assertIsNotNone(result.space(vs).root_addr)
        self.assertEqual(result.space(vs).walk(0x3000)[1], result.address_of(va)[1])

    def test_synthesized_root_is_a_gpa_in_the_target_g_space(self):
        b, vs, g, _va, result = self._two_stage()
        root = result.space(vs).root_addr
        self.assertEqual(root & 0xFFF, 0, "a VS root frame is page aligned")
        self.assertLess(root, 1 << RV.RiscvPagingModes.linear_addr_bits(SV39, gstage=True), f"VS root 0x{root:x} must be a valid SV39 GPA")
        self.assertIs(b._declared_roots[vs].space, g, "the synthesized root frame lives in the target G space")

    def test_synthesized_root_translates_identity_in_the_g_space(self):
        # Without this leaf the guest's first fetch through vsatp would g-stage fault.
        _b, vs, g, _va, result = self._two_stage()
        root = result.space(vs).root_addr
        self.assertEqual(result.space(g).walk(root)[1], root, "the root GPA must g-stage translate to itself")

    def test_synthesized_root_suppresses_the_physical_root_frame(self):
        b, vs, _g, _va, _result = self._two_stage()
        self.assertNotIn(vs, b._root_frames, "a VS root comes from the G space, never from _declare_root_frames")

    def test_placement_is_deterministic(self):
        _b1, vs1, _g1, _va1, first = self._two_stage(seed=7)
        _b2, vs2, _g2, _va2, second = self._two_stage(seed=7)
        self.assertEqual(first.space(vs1).root_addr, second.space(vs2).root_addr)

    def test_a_declared_root_frame_still_wins(self):
        b, vs, _g, _va, result = self._two_stage(declare_root=True)
        self.assertIs(b._declared_roots[vs], vs.root_frame, "an explicitly declared root must not be replaced")
        self.assertEqual(result.space(vs).root_addr, b._page_state[vs.root_frame].allocated)

    def test_a_root_level_pin_still_wins(self):
        # A recursive VS table pins its own root; nothing is synthesized for it.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = b.add_space(Space(paging_mode=SV39, stage=Stage.VS))
        root_gpa = b.add_page(Page(space=g))
        root_hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(root_gpa))))
        b.add_mapping(Mapping(src=root_gpa, dst=root_hpa, pt_nodes={LEAF: _leaf_node()}))
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        hpa = b.add_page(Page(space=b.phys))
        gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
        b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: _leaf_node()}))
        b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes={LEAF: _leaf_node(), **self.VS_NODES, 2: PTNode(page=root_gpa)}))
        result = b.build()
        self.assertIs(b._declared_roots.get(vs), None, "a root-level pin needs no declared root")
        self.assertEqual(result.space(vs).root_addr, b._page_state[root_gpa].allocated)

    def test_vs_only_two_stage_keeps_its_physical_root(self):
        # No table-bearing G target, so the root is an ordinary satp-style physical frame.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        vs = b.add_space(Space(paging_mode=SV39, stage=Stage.VS))
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        pa = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=va, dst=pa, pt_nodes={LEAF: _leaf_node()}))
        result = b.build()
        self.assertIn(vs, b._root_frames)
        self.assertNotIn(vs, b._declared_roots)
        self.assertEqual(result.space(vs).root_addr, b._page_state[b._root_frames[vs]].allocated)

    def test_a_failed_build_does_not_leave_synthesized_declarations_behind(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = b.add_space(Space(paging_mode=SV39, stage=Stage.VS))
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        hpa = b.add_page(Page(space=b.phys))
        gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
        # No PTGPage identities on the non-leaf nodes, so the topology solve rejects this.
        b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes={LEAF: _leaf_node()}))
        b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: _leaf_node()}))
        pages_before, mappings_before = len(b.pages), len(b.mappings)
        with self.assertRaises(Exception):
            b.build()
        self.assertEqual((len(b.pages), len(b.mappings)), (pages_before, mappings_before), "a failed build must roll back the synthesized root declarations")
        self.assertNotIn(vs, b._declared_roots)


if __name__ == "__main__":
    unittest.main(verbosity=2)
