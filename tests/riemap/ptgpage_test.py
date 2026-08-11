# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""PTGPage semantics (C5 point 4): a g-stage PT-node frame declared by PT NODES + PAGESIZE.

A :class:`~riescue.riemap.request.PTGPage` is usable only as a :class:`PTNode.page`. It
declares the g-stage identity of a VS-stage walk's *synthesized* non-leaf node -- RieMap
allocates + shares the frame, the consumer never pins its GPA -- as ``pt_nodes`` keyed by
level in that identity's own g-stage tree (the same vocabulary as
:attr:`~riescue.riemap.request.Mapping.pt_nodes`). These assert the validation rules, the
forced-bit / default behaviour at both g-leaf and g-nonleaf levels, the :data:`LEAF`
sentinel, that ``pagesize`` drives the g-stage frame geometry, the ``identity`` contract, and
the walk read-back.
"""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen import AddrGen
from riescue.riemap.config import PagingParams
from riescue.riemap.memory import Memory
from riescue.riemap.page_map import PageMap
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import Choice, AddrSpec, LEAF, Mapping, Page, PTGPage, PTNode, SameAs, Space, Stage
from tests.riemap.root_policy import declare_vs_root_identity

SV39 = RV.RiscvPagingModes.SV39
S4KB = RV.RiscvPageSizes.S4KB
S2MB = RV.RiscvPageSizes.S2MB


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs(**extra):
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, **extra}


def _two_stage(seed=1, vs_node=None):
    """A minimal VA -> GPA -> HPA two-stage build. ``vs_node`` (if given) is attached as the
    VS mapping's non-leaf node at level 1 (where the synthesized g-stage identity lives).
    Returns (builder, vs_space, g_space, hpa_page) with the builder NOT yet built."""
    return _add_two_stage(PageTableBuilder(rng=RandNum(seed=seed), memory=_memory()), vs_node)


def _add_two_stage(b, vs_node=None):
    """:func:`_two_stage` onto an existing builder -- for a test that must name
    ``b.phys`` pages (a pinned g-stage PT frame) before the mappings are declared."""
    g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
    vs = declare_vs_root_identity(b, SV39, g)
    va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
    hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000)))
    gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
    pt_nodes = {LEAF: PTNode(attrs=_leaf_attrs())}
    if vs_node is not None:
        pt_nodes[1] = vs_node
    for level in (0, 1):
        pt_nodes.setdefault(
            level,
            PTNode(page=PTGPage(identity=True)),
        )
    b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes=pt_nodes))
    b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs(u=1))}))
    return b, vs, g, hpa


def _gstage_leaf_bytes(result, g_space):
    """The permission low-byte of every g-stage leaf PTE."""
    return [e.value & 0xFF for tv in result.space(g_space).tables() for e in tv.entries if e.leaf]


class TestPTGPageValidation(unittest.TestCase):
    def test_default_is_nonidentity(self):
        self.assertFalse(PTGPage().identity)

    def test_add_page_rejects_ptgpage(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        with self.assertRaises(ValueError):
            b.add_page(PTGPage(pt_nodes={0: PTNode(attrs={"w": 0})}))  # type: ignore[arg-type]

    def test_mapping_src_dst_rejects_ptgpage(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        vs = b.add_space(Space(paging_mode=SV39, stage=Stage.VS))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000)))
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        with self.assertRaises(ValueError):
            b.add_mapping(Mapping(src=PTGPage(), dst=pa))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            b.add_mapping(Mapping(src=va, dst=PTGPage()))  # type: ignore[arg-type]

    def test_string_grammar_keys_rejected(self):
        b, vs, g, hpa = _two_stage()
        # An inner _level/_glevel string key is the consumer-boundary grammar, never a PTGPage key.
        with self.assertRaises(ValueError):
            b.add_mapping(Mapping(src=hpa, dst=hpa, pt_nodes={1: PTNode(page=PTGPage(pt_nodes={0: PTNode(attrs={"w_level0": 0})}))}))

    def test_noninteger_glevel_key_rejected(self):
        b, vs, g, hpa = _two_stage()
        with self.assertRaises(ValueError):
            b.add_mapping(Mapping(src=hpa, dst=hpa, pt_nodes={1: PTNode(page=PTGPage(pt_nodes={"0": PTNode(attrs={"w": 0})}))}))

    def test_non_ptnode_value_rejected(self):
        # Each g-stage level value must be an explicit PTNode declaration.
        b, vs, g, hpa = _two_stage()
        with self.assertRaises(ValueError):
            b.add_mapping(Mapping(src=hpa, dst=hpa, pt_nodes={1: PTNode(page=PTGPage(pt_nodes={0: {"w": 0}}))}))  # type: ignore[dict-item]

    def test_nested_ptgpage_rejected(self):
        # The g-stage is the last stage: a g-stage node's frame has no g-stage of its own.
        b, vs, g, hpa = _two_stage()
        with self.assertRaises(ValueError):
            b.add_mapping(Mapping(src=hpa, dst=hpa, pt_nodes={1: PTNode(page=PTGPage(pt_nodes={0: PTNode(page=PTGPage())}))}))

    def test_gpa_frame_for_a_gstage_pte_rejected(self):
        # A g-stage table is walked in the HOST physical domain, so the frame holding a g-stage
        # PTE must be a leaf-domain Page -- a Stage.G page would name a GPA.
        b, vs, g, hpa = _two_stage()
        gpa_frame = Page(space=g)
        with self.assertRaises(ValueError):
            b.add_mapping(Mapping(src=hpa, dst=hpa, pt_nodes={1: PTNode(page=PTGPage(pt_nodes={0: PTNode(page=gpa_frame)}))}))


class TestPTGPageForcing(unittest.TestCase):
    def test_unforced_gstage_leaves_are_writable(self):
        b, vs, g, hpa = _two_stage()
        result = b.build()
        bytes_ = _gstage_leaf_bytes(result, g)
        self.assertTrue(bytes_, "expected g-stage identity leaves")
        self.assertTrue(all(v & 0x04 for v in bytes_), f"every unforced g-stage leaf must be writable (W=1): {[hex(v) for v in bytes_]}")

    def test_forced_w0_produces_a_nonwritable_gstage_leaf(self):
        node = PTNode(page=PTGPage(pt_nodes={0: PTNode(attrs={"w": 0})}, pagesize=S4KB, identity=True))
        b, vs, g, hpa = _two_stage(vs_node=node)
        result = b.build()
        bytes_ = _gstage_leaf_bytes(result, g)
        self.assertTrue(any(not (v & 0x04) for v in bytes_), f"a W=0 g-stage identity leaf must appear when forced: {[hex(v) for v in bytes_]}")

    def test_leaf_sentinel_matches_the_explicit_g_leaf_level(self):
        # LEAF resolves to pt_leaf_level(PTGPage.pagesize) -- level 0 for a 4KB frame -- so the two
        # spellings of the same force must produce identical g-stage PTE bits.
        by_int = PTNode(page=PTGPage(pt_nodes={0: PTNode(attrs={"w": 0})}, pagesize=S4KB, identity=True))
        by_leaf = PTNode(page=PTGPage(pt_nodes={LEAF: PTNode(attrs={"w": 0})}, pagesize=S4KB, identity=True))
        b_int, _vs, g_int, _hpa = _two_stage(vs_node=by_int)
        b_leaf, _vs2, g_leaf, _hpa2 = _two_stage(vs_node=by_leaf)
        self.assertEqual(_gstage_leaf_bytes(b_int.build(), g_int), _gstage_leaf_bytes(b_leaf.build(), g_leaf))

    def test_forced_g_nonleaf_level_reaches_the_gstage_pointer_pte(self):
        # The per-g-level keying is load-bearing: a force at g-level 1 lands on the g-stage
        # NON-leaf pointer PTE of the frame's identity tree, which a flat base->value dict
        # could never address (it would collide with the g-leaf's own bits).
        node = PTNode(page=PTGPage(pt_nodes={1: PTNode(attrs={"v": 0})}, pagesize=S4KB, identity=True))
        b, vs, g, hpa = _two_stage(vs_node=node)
        result = b.build()
        nonleaf = [(e.level, e.value) for tv in result.space(g).tables() for e in tv.entries if not e.leaf]
        self.assertTrue(nonleaf, "expected g-stage non-leaf pointer PTEs")
        invalid = [(lvl, v) for lvl, v in nonleaf if not (v & 0x1)]
        self.assertTrue(invalid, f"a V=0 g-stage pointer PTE must appear when forced at g-level 1: {[(lvl, hex(v)) for lvl, v in nonleaf]}")
        self.assertTrue(all(lvl == 1 for lvl, _v in invalid), f"only the forced g-level 1 pointer may be invalidated: {[(lvl, hex(v)) for lvl, v in invalid]}")


class TestPinnedGstageFrameUserReachable(unittest.TestCase):
    """A pinned ``PTNode(page=frame)`` whose frame lives in the ``Stage.G`` space and is not
    itself a mapping source (a modify_pt page-table frame) is auto-identity-mapped GPA -> HPA
    by the builder. That synthesized leaf MUST be user-reachable (U=1): a g-stage access is
    always a user access, so a U=0 leaf faults every guest access. Regression: a supervisor
    two-stage guest took an instruction guest-page fault because the auto-identity leaf omitted
    U and fell back to the frame's VS single-index u (0 for a supervisor guest)."""

    def test_generated_identity_frame_leaf_is_user(self):
        b, vs, g, _hpa = _two_stage()
        result = b.build()
        frame_gpas = {step.ptg_gpa for step in result.space(vs).walk(0x3000)[0] if step.ptg_gpa is not None}
        self.assertTrue(frame_gpas)
        frame_leaves = [e.value for tv in result.space(g).tables() for e in tv.entries if e.leaf and ((e.value >> 10) << 12) in frame_gpas]
        self.assertEqual(len(frame_leaves), len(frame_gpas))
        # It must be user-reachable (U bit, 0x10): a g-stage access is always a user access,
        # so a U=0 leaf would fault every guest access to the frame.
        self.assertTrue(frame_leaves[0] & 0x10, f"pinned-frame g-stage leaf must be user-reachable (U=1): 0x{frame_leaves[0]:x}")
        # More broadly, NO g-stage leaf may be U=0 -- neither the pinned frame nor the g-stage
        # root table's own self-map (which defaulted to the supervisor VS u=0 before the fix).
        bytes_ = _gstage_leaf_bytes(result, g)
        self.assertTrue(all(v & 0x10 for v in bytes_), f"every g-stage leaf must be user-reachable (U=1): {[hex(v) for v in bytes_]}")


class TestPTGPageSize(unittest.TestCase):
    def test_size_drives_gstage_frame_alignment(self):
        # A 2MB PTGPage frame's GPA is 2MB-aligned; a 4KB one need only be 4KB-aligned. The
        # synthesized identity for the VS node at level 1 uses the PTGPage.pagesize for its frame.
        node2m = PTNode(page=PTGPage(pt_nodes={0: PTNode(attrs={"w": 1})}, pagesize=S2MB, identity=True))
        b, vs, g, hpa = _two_stage(vs_node=node2m)
        result = b.build()
        # The VS-stage node's frame GPA is 2MB-aligned: read the VS pointer PTE targets (the
        # frames the walk allocated) and assert the forced node's frame is among the aligned ones.
        gpa_targets = [((e.value >> 10) << 12) for tv in result.space(vs).tables() for e in tv.entries if not e.leaf]
        self.assertTrue(any((t & (RV.RiscvPageSizes.memory(S2MB) - 1)) == 0 for t in gpa_targets), f"a 2MB-aligned VS-node frame GPA must exist: {[hex(t) for t in gpa_targets]}")

    def test_pagesize_choice_reports_emitted_geometry(self):
        choice = Choice(preferred=S2MB, alternatives=(S4KB,))
        node = PTNode(page=PTGPage(pagesize=choice, identity=True))
        builder, vs, g, _hpa = _two_stage(vs_node=node)
        result = builder.build()
        steps, _ = result.space(vs).walk(0x3000)
        frame_gpa = next(step.ptg_gpa for step in steps if step.ptg_page is node.page)
        self.assertEqual(result.space(g).gstage_identity(frame_gpa).pagesize, S2MB)

    def test_pagesize_choice_uses_smaller_alternative_under_pressure(self):
        memory = Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x300000", "cacheable": True, "configurable": True}}})
        builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)
        g = builder.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(builder, SV39, g)
        va = builder.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80280000)))
        gpa = builder.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
        policy = PTGPage(
            pagesize=Choice(preferred=S2MB, alternatives=(S4KB,)),
            identity=True,
        )
        builder.add_mapping(
            Mapping(
                src=va,
                dst=gpa,
                pt_nodes={
                    LEAF: PTNode(attrs=_leaf_attrs()),
                    1: PTNode(page=policy),
                    0: PTNode(page=PTGPage(identity=True)),
                },
            )
        )
        builder.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs(u=1))}))

        result = builder.build()
        steps, _ = result.space(vs).walk(0x3000)
        frame_gpa = next(step.ptg_gpa for step in steps if step.ptg_page is policy)
        self.assertEqual(result.space(g).gstage_identity(frame_gpa).pagesize, S4KB)


class TestPTGPageIdentity(unittest.TestCase):
    def test_identity_false_allocates_independent_gpa_and_hpa(self):
        node = PTNode(page=PTGPage(pt_nodes={0: PTNode(attrs={"w": 0})}, pagesize=S4KB, identity=False))
        b, vs, g, hpa = _two_stage(vs_node=node)
        result = b.build()
        steps, translated = result.space(vs).walk(0x3000)
        frame_gpa = next(step.ptg_gpa for step in steps if step.ptg_page is node.page)
        frame = result.space(g).gstage_identity(frame_gpa)

        self.assertNotEqual(frame.gpa, frame.hpa)
        self.assertEqual(frame.attrs["w_level0"], 0)
        self.assertEqual(result.space(g).walk(frame.gpa)[1], frame.hpa)
        self.assertEqual(translated, result.address_of(hpa)[1])

    def test_identity_true_is_gpa_equals_hpa(self):
        node = PTNode(page=PTGPage(pt_nodes={0: PTNode(attrs={"w": 0})}, pagesize=S4KB, identity=True))
        b, vs, g, hpa = _two_stage(vs_node=node)
        result = b.build()
        steps, translated = result.space(vs).walk(0x3000)
        frame_gpa = next(step.ptg_gpa for step in steps if step.ptg_page is node.page)
        frame = result.space(g).gstage_identity(frame_gpa)

        self.assertEqual(frame.gpa, frame.hpa)
        self.assertEqual(translated, result.address_of(hpa)[1], "VS walk must reach the HPA through the identity g-stage")

    def test_identity_and_nonidentity_policies_do_not_share_a_frame(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, SV39, g)
        declarations = []
        for index, identity in enumerate((True, False)):
            va = b.add_page(Page(space=vs, addr=AddrSpec(exact=index * 0x40000000)))
            hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000 + index * 0x1000)))
            gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
            policy = PTGPage(identity=identity)
            b.add_mapping(
                Mapping(
                    src=va,
                    dst=gpa,
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        1: PTNode(page=policy),
                        0: PTNode(page=PTGPage(identity=True)),
                    },
                )
            )
            b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs(u=1))}))
            declarations.append((va, policy, identity))

        result = b.build()
        frames = []
        for va, policy, identity in declarations:
            steps, _translated = result.space(vs).walk(result.address_of(va)[0])
            frame_gpa = next(step.ptg_gpa for step in steps if step.ptg_page is policy)
            frame = result.space(g).gstage_identity(frame_gpa)
            self.assertEqual(frame.gpa == frame.hpa, identity)
            frames.append(frame.gpa)
        self.assertNotEqual(*frames)


class TestPTGPagePinnedGstageFrame(unittest.TestCase):
    """A ``PTNode.page`` inside a :class:`PTGPage` pins the host-physical frame that holds
    that g-level's PTEs -- the same meaning as in a :class:`Mapping`, one stage down. RieMap
    still chooses the VS frame's GPA; only the g-stage table placement is the consumer's."""

    def _pinned(self, g_pt_nodes, seed=1):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        frames = {key: Page(space=b.phys) for key in g_pt_nodes}
        ptg = PTGPage(pt_nodes={key: PTNode(attrs=dict(attrs), page=frames[key]) for key, attrs in g_pt_nodes.items()}, pagesize=S4KB, identity=True)
        _b, vs, g, hpa = _add_two_stage(b, PTNode(page=ptg))
        return b, vs, g, ptg, frames

    @staticmethod
    def _frame_gpa(result, vs_space, ptg=None):
        """The GPA RieMap drew for the VS node that declared the PTGPage."""
        steps, _translated = result.space(vs_space).walk(0x3000)
        return [s.ptg_gpa for s in steps if s.ptg_page is not None and (ptg is None or s.ptg_page is ptg)][0]

    def test_gstage_leaf_pte_lives_in_the_pinned_frame(self):
        b, vs, g, ptg, frames = self._pinned({0: {"w": 0}})
        result = b.build()
        frame_pa = result.address_of(frames[0])[1]
        gpa = self._frame_gpa(result, vs, ptg)
        g_steps, translated = result.space(g).walk(gpa)
        self.assertEqual(translated, gpa, "the identity must still be GPA == HPA")
        leaf = [s for s in g_steps if s.leaf]
        self.assertEqual(len(leaf), 1, f"the frame GPA 0x{gpa:x} must resolve to one g-stage leaf")
        self.assertEqual(leaf[0].level, 0, "a 4KB PTGPage's identity bottoms out at g-level 0")
        self.assertEqual(leaf[0].pte_addr & ~0xFFF, frame_pa, f"the g-leaf PTE must live in the pinned frame 0x{frame_pa:x}, not 0x{leaf[0].pte_addr & ~0xFFF:x}")

    def test_pinning_a_g_nonleaf_frame(self):
        # The pin is per g-level: level 1's frame holds the pointer PTEs one step above the leaf.
        b, vs, g, ptg, frames = self._pinned({1: {"v": 1}})
        result = b.build()
        frame_pa = result.address_of(frames[1])[1]
        g_steps, _translated = result.space(g).walk(self._frame_gpa(result, vs, ptg))
        at_level1 = [s for s in g_steps if s.level == 1]
        self.assertEqual(len(at_level1), 1)
        self.assertEqual(at_level1[0].pte_addr & ~0xFFF, frame_pa, "the g-level-1 PTE must live in the frame pinned for level 1")

    def _two_mappings_sharing_a_frame(self, seed):
        """Two VS mappings whose PTGPages pin the SAME g-stage frame, so both synthesized
        identities' g-leaf PTEs must pack into the one table living there."""
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        frame = b.add_page(Page(space=b.phys))
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, SV39, g)
        for i, va_addr in enumerate((0x3000, 0x40003000)):
            va = b.add_page(Page(space=vs, addr=AddrSpec(exact=va_addr)))
            hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000 + i * 0x1000)))
            gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
            ptg = PTGPage(pt_nodes={0: PTNode(page=frame)}, pagesize=S4KB, identity=True)
            b.add_mapping(
                Mapping(
                    src=va,
                    dst=gpa,
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        1: PTNode(page=ptg),
                        0: PTNode(page=PTGPage(identity=True)),
                    },
                )
            )
            b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs(u=1))}))
        return b, g, frame

    def test_two_ptgpages_sharing_one_frame_pack_into_it(self):
        b, g, frame = self._two_mappings_sharing_a_frame(seed=1)
        result = b.build()
        frame_pa = result.address_of(frame)[1]
        shared = [tv for tv in result.space(g).tables() if tv.addr == frame_pa]
        self.assertTrue(shared, f"expected a g-stage table at the pinned frame 0x{frame_pa:x}")
        leaves = {(e.index, e.value) for tv in shared for e in tv.entries if e.leaf}
        self.assertGreaterEqual(len(leaves), 2, f"both VS nodes' g-stage identity leaves must pack into the shared frame: {leaves}")

    def test_an_auto_sibling_adopts_the_pinned_frame(self):
        """A pinned g-stage frame and an automatic sibling share one node.

        All sharers are resolved together before tree construction, so the
        unpinned declaration adopts the pinned frame: one pointer PTE and one
        child table.
        """
        for seed in range(1, 25):
            with self.subTest(seed=seed):
                b, g, frame = self._two_mappings_sharing_a_frame(seed=seed)
                result = b.build()
                frame_pa = result.address_of(frame)[1]
                self.assertTrue([tv for tv in result.space(g).tables() if tv.addr == frame_pa], "the declared frame must back a g-stage table")

    def test_pinning_the_gstage_root_raises(self):
        # The g-stage root table is one per space; no single VS node may place it.
        b, vs, g, _ptg, _frames = self._pinned({2: {"v": 1}})
        with self.assertRaises(ValueError):
            b.build()

    def test_conflicting_pins_for_one_frame_gpa_raise(self):
        # The identity of a given GPA is emitted once (deduped on it); a second emission
        # demanding DIFFERENT g-stage frames is a contradiction, not a silent no-op.
        featmgr, addrgen = PagingParams(), AddrGen(RandNum(seed=1), _memory())
        g_map = PageMap(paging_mode=SV39, addrgen=addrgen, featmgr=featmgr, g_map=True)
        g_map.add_raw_pt_page(linear_addr=0x80001000, physical_addr=0x80001000, pinned_frame_bases={1: 0x80002000})
        g_map.add_raw_pt_page(linear_addr=0x80001000, physical_addr=0x80001000, pinned_frame_bases={1: 0x80002000})  # idempotent
        with self.assertRaises(ValueError):
            g_map.add_raw_pt_page(linear_addr=0x80001000, physical_addr=0x80001000, pinned_frame_bases={1: 0x80003000})


class TestRawStructuralIdentityDedup(unittest.TestCase):
    def setUp(self):
        featmgr, addrgen = PagingParams(), AddrGen(RandNum(seed=1), _memory())
        self.g_map = PageMap(paging_mode=SV39, addrgen=addrgen, featmgr=featmgr, g_map=True)
        self.gpa = 0x80001000

    def _add(self, **overrides):
        args = {
            "linear_addr": self.gpa,
            "physical_addr": self.gpa,
            "attrs": {"w": 0},
            "pagesize": S4KB,
            "pinned_frame_bases": {1: 0x80002000},
        }
        args.update(overrides)
        self.g_map.add_raw_pt_page(**args)

    def test_identical_duplicate_is_idempotent(self):
        self._add()
        page = self.g_map.pt_pages[self.gpa]
        self._add()
        self.assertIs(self.g_map.pt_pages[self.gpa], page)
        self.assertEqual(len(self.g_map.emitted_gstage_identities), 1)

    def test_conflicting_pagesize_raises(self):
        self._add()
        with self.assertRaises(ValueError):
            self._add(pagesize=S2MB)

    def test_conflicting_attrs_raise(self):
        self._add()
        with self.assertRaises(ValueError):
            self._add(attrs={"w": 1})

    def test_conflicting_physical_target_raises(self):
        self._add()
        with self.assertRaises(ValueError):
            self._add(physical_addr=0x80003000)

    def test_conflicting_pins_raise_including_missing_pins(self):
        self._add()
        with self.assertRaises(ValueError):
            self._add(pinned_frame_bases={1: 0x80003000})
        with self.assertRaises(ValueError):
            self._add(pinned_frame_bases=None)


class TestPTGPageReadBack(unittest.TestCase):
    """``WalkStep.ptg_page`` / ``ptg_gpa`` hand a consumer that walked a VA back the very
    :class:`PTGPage` object it declared, plus the GPA RieMap chose for that frame."""

    def test_walk_exposes_the_declared_ptgpage_and_its_frame_gpa(self):
        ptg = PTGPage(pt_nodes={0: PTNode(attrs={"w": 0})}, pagesize=S4KB, identity=True)
        b, vs, g, hpa = _two_stage(vs_node=PTNode(page=ptg))
        result = b.build()
        steps, _translated = result.space(vs).walk(0x3000)
        declared = [s for s in steps if s.ptg_page is ptg]
        self.assertEqual(len(declared), 1, f"exactly one walk step must carry the declared PTGPage: {steps}")
        step = declared[0]
        self.assertIs(step.ptg_page, ptg, "the read-back must be the declared PTGPage object itself")
        self.assertEqual(
            step.level,
            2,
            "the level-1 table frame is reached by the level-2 pointer",
        )
        emitted = result.space(g).gstage_identity(step.ptg_gpa)
        self.assertEqual(emitted.gpa, step.ptg_gpa)
        self.assertEqual(emitted.hpa, step.ptg_gpa)
        self.assertEqual(emitted.pagesize, S4KB)
        self.assertEqual(emitted.attrs["w_level0"], 0, "read-back must expose the attrs of the identity that was actually emitted")
        # The frame's GPA is identity-mapped in the g-stage space (GPA == HPA).
        g_leaf_targets = {((e.value >> 10) << 12) for tv in result.space(g).tables() for e in tv.entries if e.leaf}
        self.assertIn(step.ptg_gpa, g_leaf_targets, f"the frame GPA 0x{step.ptg_gpa:x} must carry a g-stage identity leaf")

    def test_shared_frame_clears_arbitrary_declaration_but_keeps_emitted_gpa(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, SV39, g)
        for i, va_addr in enumerate((0x40200000, 0x40201000)):
            va = b.add_page(Page(space=vs, addr=AddrSpec(exact=va_addr)))
            b.add_two_stage_mapping(
                va_page=va,
                hpa_page=Page(space=b.phys, addr=AddrSpec(exact=0x80200000 + i * 0x1000)),
                gpa_space=g,
                attrs={**_leaf_attrs(), "u": 1, "a_level1_glevel0": 0},
                vs_pagesize=S4KB,
                gstage_mode=SV39,
            )
        result = b.build()
        shared = []
        for va_addr in (0x40200000, 0x40201000):
            step = [s for s in result.space(vs).walk(va_addr)[0] if s.level == 1][0]
            self.assertIsNone(step.ptg_page, "an interned edge must not claim whichever declaration constructed it first")
            self.assertIsNotNone(step.ptg_gpa, "the emitted frame identity remains unambiguous")
            shared.append(step.ptg_gpa)
        self.assertEqual(shared[0], shared[1])
        self.assertEqual(result.space(g).gstage_identity(shared[0]).attrs["a_level0"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
