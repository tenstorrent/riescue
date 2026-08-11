# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Where a two-stage map's g-stage geometry is declared.

A :class:`~riescue.riemap.request.Mapping` is ONE stage, so it carries no second-stage
geometry. The g-stage geometry of a two-stage map is declared on the objects that own it:

* the g-stage translation of the leaf's target is ``dst``'s own ``pagesize`` (the
  :attr:`Stage.G` page whose own ``Mapping`` is the GPA -> HPA leaf) -- or, for a
  bare-g-stage source, ``src.pagesize``;
* the g-stage translation of each non-leaf node's frame is that node's
  :class:`~riescue.riemap.request.PTGPage` (or pinned :class:`Page`) ``pagesize``.

``pagesize`` is the only geometry knob: it says how large a page translates the address,
hence how many g-stage levels are walked. ``pt_nodes`` is purely an ATTRIBUTE declaration --
omitting a level means "keep the g-stage defaults" and must never move an address.
"""

import dataclasses
import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTGPage, PTNode, SameAs, Space, Stage
from tests.riemap.root_policy import declare_vs_root_identity

SV39 = RV.RiscvPagingModes.SV39
S4KB = RV.RiscvPageSizes.S4KB
S2MB = RV.RiscvPageSizes.S2MB
VA = 0x3000
# A 2MB-aligned DRAM address, legal for both a 4KB and a 2MB g-stage leaf. Pinned rather than
# free-drawn because GPA == HPA here, and a free 52-bit physical draw would not fit sv39's
# 41-bit guest-physical input.
HPA = 0x80200000


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs(**extra):
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, **extra}


def _two_stage(*, gstage_leaf_pagesize=S4KB, vs_nodes=None, seed=1):
    """A minimal VA -> GPA -> HPA two-stage build, NOT yet built.

    ``gstage_leaf_pagesize`` is declared the only way it can be -- as the GPA/HPA pages' own
    pagesize -- and ``vs_nodes`` are extra ``pt_nodes`` for the VS mapping (where a PTGPage
    declares a non-leaf node's g-stage geometry). Returns ``(builder, vs, g, gpa, hpa)``.
    """
    b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
    g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
    vs = declare_vs_root_identity(b, SV39, g)
    va = b.add_page(Page(space=vs, addr=AddrSpec(exact=VA)))
    hpa = b.add_page(Page(space=b.phys, pagesize=gstage_leaf_pagesize, addr=AddrSpec(exact=HPA)))
    gpa = b.add_page(Page(space=g, pagesize=gstage_leaf_pagesize, addr=AddrSpec(relation=SameAs(hpa))))
    pt_nodes = {LEAF: PTNode(attrs=_leaf_attrs())}
    pt_nodes.update(vs_nodes or {})
    for level in (0, 1):
        pt_nodes.setdefault(
            level,
            PTNode(page=PTGPage(identity=True)),
        )
    b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes=pt_nodes))
    b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs(u=1))}))
    return b, vs, g, gpa, hpa


def _gstage_levels(result, g_space, gpa):
    """The g-stage levels walked to translate ``gpa`` -- the depth its pagesize implies."""
    steps, _translated = result.space(g_space).walk(gpa)
    return [s.level for s in steps]


def _vs_frame_gpas(result, vs_space):
    """The GPAs of the frames this VS walk's non-leaf PTEs point at, deepest node last."""
    steps, _translated = result.space(vs_space).walk(VA)
    return [(s.level, s.ptg_gpa) for s in steps if s.ptg_gpa is not None]


class TestGstageLeafGeometryComesFromDst(unittest.TestCase):
    """The g-stage leaf pagesize is ``dst.pagesize`` -- no mapping field involved."""

    def test_4kb_dst_walks_all_three_gstage_levels(self):
        b, _vs, g, gpa, _hpa = _two_stage(gstage_leaf_pagesize=S4KB)
        result = b.build()
        self.assertEqual(_gstage_levels(result, g, result.address_of(gpa)[1]), [2, 1, 0])

    def test_2mb_dst_shortens_the_gstage_walk_and_aligns_the_hpa(self):
        b, _vs, g, gpa, hpa = _two_stage(gstage_leaf_pagesize=S2MB)
        result = b.build()
        gpa_addr = result.address_of(gpa)[1]
        self.assertEqual(_gstage_levels(result, g, gpa_addr), [2, 1], "a 2MB g-stage leaf must drop the level-0 table")
        self.assertEqual(result.address_of(hpa)[1] & (RV.RiscvPageSizes.memory(S2MB) - 1), 0, "a 2MB g-stage leaf needs a 2MB-aligned HPA")

    def test_bare_gstage_source_reports_its_own_pagesize(self):
        # A bare-VS g-stage source (the guest walks hgatp directly) IS the GPA -> HPA leaf, so
        # its g-stage leaf pagesize is src.pagesize. RiescueD's ";#read_pte g_level=leaf"
        # resolution reads this back off PageMeta, and a None would make it unresolvable.
        for pagesize in (S4KB, S2MB):
            b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
            g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
            gpa = b.add_page(Page(space=g, pagesize=pagesize, addr=AddrSpec(exact=RV.RiscvPageSizes.memory(pagesize))))
            pa = b.add_page(Page(space=b.phys, pagesize=pagesize, addr=AddrSpec(exact=HPA)))
            b.add_mapping(Mapping(src=gpa, dst=pa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
            result = b.build()
            self.assertEqual(result.page_meta(gpa).gstage_vs_leaf_pagesize, pagesize)

    def test_single_stage_mapping_has_no_gstage_leaf_pagesize(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=SV39))
        va = b.add_page(Page(space=va_space, addr=AddrSpec(exact=VA)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=HPA)))
        b.add_mapping(Mapping(src=va, dst=pa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
        result = b.build()
        self.assertIsNone(result.page_meta(va).gstage_vs_leaf_pagesize)


class TestGstageNonLeafGeometryComesFromPTGPage(unittest.TestCase):
    """Each non-leaf node's g-stage geometry is its own ``PTGPage.pagesize``."""

    def _ptg(self, pagesize):
        return PTNode(page=PTGPage(pagesize=pagesize, identity=True))

    def test_2mb_ptgpage_shortens_that_frames_gstage_walk_only(self):
        # The non-leaf pagesize governs the frames holding the VS tables, so the g-stage walks
        # translating the VS PTEs shorten -- while the data page's own g-stage walk (declared by
        # dst.pagesize, still 4KB) keeps all three levels.
        b, vs, g, gpa, _hpa = _two_stage(vs_nodes={0: self._ptg(S2MB), 1: self._ptg(S2MB)})
        result = b.build()
        for level, frame_gpa in _vs_frame_gpas(result, vs):
            self.assertEqual(_gstage_levels(result, g, frame_gpa), [2, 1], f"VS level {level} frame's g-stage walk must be 2 levels")
        self.assertEqual(_gstage_levels(result, g, result.address_of(gpa)[1]), [2, 1, 0], "the data page's own g-stage walk must be untouched")

    def test_geometry_is_per_node_not_per_mapping(self):
        # Two non-leaf levels of ONE mapping with different PTGPage pagesizes get different
        # g-stage depths. A per-mapping scalar could not express this.
        b, vs, g, _gpa, _hpa = _two_stage(vs_nodes={0: self._ptg(S2MB), 1: self._ptg(S4KB)})
        result = b.build()
        depths = {level: _gstage_levels(result, g, frame_gpa) for level, frame_gpa in _vs_frame_gpas(result, vs)}
        self.assertEqual(depths[1], [2, 1], "the level-0 2MB PTGPage node's frame must walk 2 g-stage levels")
        self.assertEqual(depths[2], [2, 1, 0], "the level-1 4KB PTGPage node's frame must walk 3")

    def test_ptgpage_pagesize_is_reported_as_the_nonleaf_pagesize(self):
        # PageMeta.gstage_vs_nonleaf_pagesize backs RiescueD's ";#read_pte g_level=nonleaf".
        b, _vs, _g, _gpa, _hpa = _two_stage(vs_nodes={0: self._ptg(S2MB), 1: self._ptg(S2MB)})
        result = b.build()
        va = next(iter(m.src for m in b.mappings if m.src.space.stage is Stage.VS))
        self.assertEqual(result.page_meta(va).gstage_vs_nonleaf_pagesize, S2MB)


class TestPtNodesAreAttributesOnlyNotGeometry(unittest.TestCase):
    """Declaring a g-level's defaults explicitly must be indistinguishable from omitting it.

    This is the property that rules out ever putting a reservation/isolation knob on
    ``pt_nodes``: if which levels appear could move an address, then spelling out a default
    would silently relocate page tables.
    """

    def _built(self, g_pt_nodes):
        b, vs, g, gpa, hpa = _two_stage(vs_nodes={1: PTNode(page=PTGPage(pt_nodes=g_pt_nodes, pagesize=S4KB, identity=True))})
        result = b.build()
        return (
            result.address_of(gpa)[1],
            result.address_of(hpa)[1],
            sorted(result.space(vs).pte_entries()),
            sorted(result.space(g).pte_entries()),
        )

    def test_explicit_defaults_match_omitting_the_level(self):
        # A g-stage identity node's defaults are v/r/w/x/a/d = 1 and u = 1 (a g-stage access is
        # always a user access). Declaring them changes nothing -- neither addresses nor PTEs.
        omitted = self._built({})
        explicit = self._built({0: PTNode(attrs={"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, "u": 1})})
        self.assertEqual(omitted, explicit, "spelling out a g-level's defaults must be a no-op")

    def test_forcing_a_bit_changes_ptes_but_not_addresses(self):
        # By contrast a real force does change PTE bits -- but still only bits: the addresses
        # are geometry, and geometry lives in pagesize alone.
        omitted_gpa, omitted_hpa, _vs_ptes, omitted_g = self._built({})
        forced_gpa, forced_hpa, _vs2, forced_g = self._built({0: PTNode(attrs={"w": 0})})
        self.assertEqual((omitted_gpa, omitted_hpa), (forced_gpa, forced_hpa), "an attribute force must not move an address")
        self.assertNotEqual(omitted_g, forced_g, "a forced w=0 must actually reach a g-stage PTE")


class TestMappingSurface(unittest.TestCase):
    def test_mapping_declares_one_stage_only(self):
        # Regression guard: a Mapping is exactly one leaf PTE. Second-stage geometry belongs on
        # the destination Page / the nodes' PTGPages, never back on this dataclass.
        self.assertEqual({f.name for f in dataclasses.fields(Mapping)}, {"src", "dst", "pt_nodes"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
