# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Builder wiring requirements and construction-sequence determinism.

The reference-based engine has no page names, so construction sequence is its
stable tiebreak among otherwise equivalent requests. Identical declarations,
construction order, and seed therefore reproduce identical addresses.
"""

import unittest

import riescue.lib.enums as RV
import riescue.riemap.pagetables as pagetables
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen import AddrGen
from riescue.riemap.allocator import AllocRequest, BatchAllocationStrategy
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.config import PagingParams
from riescue.riemap.memory import Memory
from riescue.riemap.page_map import Page, PageMap
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page as DeclPage, PTNode, Space


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs():
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}


def _leaf_nodes():
    return {LEAF: PTNode(attrs=_leaf_attrs())}


def _env(seed=1):
    return PagingParams(), AddrGen(RandNum(seed=seed), _memory())


class TestGstageEmitterRequired(unittest.TestCase):
    """``pagetables.py``'s structural g-stage identity emitter is wired by the builder
    onto ``PageMap.gstage_emitter``; a VS-stage map with no emitter set is a builder
    wiring bug, not a silent no-op or a magic-name fallback -- it raises loudly."""

    def _vs_map_and_pagetables(self, featmgr, addrgen):
        vs = PageMap(paging_mode=RV.RiscvPagingModes.SV39, paging_g_mode=RV.RiscvPagingModes.SV39, addrgen=addrgen, featmgr=featmgr)
        page = Page(page_map=vs, featmgr=featmgr, addrgen=addrgen)
        page.lin_addr = 0x1000
        page.phys_addr = 0x80001000
        pt = pagetables.Pagetables(page=page, page_map=vs, featmgr=featmgr, addrgen=addrgen)
        return vs, pt

    def test_unwired_emitter_raises(self):
        featmgr, addrgen = _env()
        vs, pt = self._vs_map_and_pagetables(featmgr, addrgen)
        self.assertIsNone(vs.gstage_emitter)
        with self.assertRaises(RuntimeError):
            pt._emit_gstage_identity(base_addr=0x1000, backing_addr=0x80001000, pt_level=0, pagesize=RV.RiscvPageSizes.S4KB, secure=False)

    def test_wired_emitter_is_invoked(self):
        featmgr, addrgen = _env()
        vs, pt = self._vs_map_and_pagetables(featmgr, addrgen)
        seen = {}
        vs.gstage_emitter = lambda **kw: seen.update(kw)
        pt._emit_gstage_identity(base_addr=0x2000, backing_addr=0x80002000, pt_level=0, pagesize=RV.RiscvPageSizes.S4KB, secure=False)
        self.assertEqual(seen["gpa"], 0x2000)
        self.assertEqual(seen["hpa"], 0x80002000)


class TestSptbrRootFrame(unittest.TestCase):
    """A root is a table frame, not an implicit address-space leaf."""

    def test_pinned_root_initializes_without_a_host_map(self):
        featmgr, addrgen = _env()
        pm = PageMap(paging_mode=RV.RiscvPagingModes.SV48, addrgen=addrgen, featmgr=featmgr)
        pm.pinned_sptbr = 0x80001000
        pm.initialize()
        self.assertEqual(pm.sptbr, 0x80001000)

    def test_missing_root_frame_raises(self):
        featmgr, addrgen = _env()
        pm = PageMap(paging_mode=RV.RiscvPagingModes.SV48, addrgen=addrgen, featmgr=featmgr)
        self.assertIsNone(pm.pinned_sptbr)
        with self.assertRaises(RuntimeError):
            pm.initialize()

    def test_pinned_root_is_not_inserted_as_a_leaf(self):
        featmgr, addrgen = _env()
        pm = PageMap(paging_mode=RV.RiscvPagingModes.SV48, addrgen=addrgen, featmgr=featmgr)
        pm.pinned_sptbr = 0x80001000
        pm.initialize()
        self.assertEqual(pm.sptbr, 0x80001000)
        self.assertNotIn(pm.sptbr, pm.pages)


class TestConstructionSequenceDeterminism(unittest.TestCase):
    """With no id/name anywhere, the solver's tiebreak among equally-ready candidates
    is each page's construction sequence (the order ``add_page`` was called) -- not
    any name. The same declarations, added in the same order, with the same seed,
    must reproduce identical addresses."""

    def _build(self, seed, n):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        mappings = []
        for _ in range(n):
            p = b.add_page(DeclPage(space=va))
            pa = b.add_page(DeclPage(space=b.phys))
            mappings.append(b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes())))
        result = b.build()
        return [result.address_of(m.src) for m in mappings]

    def test_same_seed_same_order_is_deterministic(self):
        first = self._build(seed=5, n=6)
        second = self._build(seed=5, n=6)
        self.assertEqual(first, second)

    def test_seq_not_submission_order_drives_the_solver_tiebreak(self):
        """Two equally-ready free requests are drawn in construction (``seq``) order,
        regardless of the order they are handed to ``solve()`` -- the list position a
        caller happens to submit them in carries no meaning, only ``seq`` (assigned at
        construction time) does."""
        space = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        page_a = DeclPage(space=space)
        page_b = DeclPage(space=space)
        req_a = AllocRequest(page=page_a, addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(), seq=0)
        req_b = AllocRequest(page=page_b, addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(), seq=1)

        def draw(order):
            addrgen = AddrGen(RandNum(seed=3), _memory())
            BatchAllocationStrategy().solve(list(order), [], addrgen, RandNum(seed=3))
            return req_a.allocated, req_b.allocated

        forward = draw([req_a, req_b])
        # Reset for a second solve with the identical requests handed in reverse order.
        req_a.allocated = None
        req_b.allocated = None
        reversed_order = draw([req_b, req_a])
        self.assertEqual(forward, reversed_order, "submission order changed the draw despite identical seq")


if __name__ == "__main__":
    unittest.main()
