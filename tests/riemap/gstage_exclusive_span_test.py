# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Declared g-stage pointer-PTE bits are attributes, not exclusive spans.

A Stage.G mapping that forces a non-default non-leaf PTE bit (e.g. ``r_level2``)
must NOT own the whole level-L GPA span. Attr-aware coloring keeps a sibling
that demands the DEFAULT out of a forced node; siblings that force the SAME
bits may share the pointer. The JSON frontend / bare-g-stage path relies on
that -- deriving an exclusive 1 GiB span per force over-subscribes configs
that randomize non-leaf bits (mmu_tb dynamic satp).

RiescueD's ``modify_leaf_pt`` / ``modify_nonleaf_pt`` still own the pointer
span, but they state that via explicit ``reserve_size`` (and the PTGPage
synthesized-frame path still feeds :func:`resolve.gstage_exclusive_span`).
Those routes are covered by ``pt_request_builder_test``; this module covers
the declared-attribute contract the JSON frontend needs.
"""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.memory import Memory
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTNode, Space, Stage

SV39 = RV.RiscvPagingModes.SV39
S4KB = RV.RiscvPageSizes.S4KB
# Level-2 pointer span under sv39 is a 1 GiB region.
L2_SPAN = 1 << 30


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs(**extra):
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, "u": 1, **extra}


def _index(addr: int, level: int) -> int:
    hi, lo = RV.RiscvPagingModes.index_bits(SV39, level)
    return (addr >> lo) & ((1 << (hi - lo + 1)) - 1)


class TestDeclaredGstageForceIsNotExclusive(unittest.TestCase):
    """A Stage.G mapping declaration must not grow to the pointer span."""

    def test_identical_level2_forces_do_not_claim_pointer_spans(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        srcs = []
        for _ in range(2):
            src = b.add_page(Page(space=g, pagesize=S4KB))
            dst = b.add_page(Page(space=b.phys, pagesize=S4KB))
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=dst,
                    pt_nodes={
                        LEAF: PTNode(attrs=_leaf_attrs()),
                        2: PTNode(attrs={"r": 1}),
                    },
                )
            )
            srcs.append(src)
        result = b.build()
        for src in srcs:
            # Geometry must stay at the page's own pagesize -- the old path grew
            # alloc_size to the 1 GiB pointer span.
            self.assertEqual(b._page_state[src].alloc_size, RV.RiscvPageSizes.memory(S4KB))
            gpa, hpa = result.address_of(src)
            self.assertEqual(result.space(g).walk(gpa)[1], hpa)

    def test_many_identical_forces_build_cleanly(self):
        # The mmu-tb failure mode under derived exclusivity: many 4 KiB g-stage
        # pages with the same forced non-leaf bit exhaust the one shared span.
        for seed in (1, 41, 140149633):
            with self.subTest(seed=seed):
                b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
                g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
                for _ in range(8):
                    src = b.add_page(Page(space=g, pagesize=S4KB))
                    dst = b.add_page(Page(space=b.phys, pagesize=S4KB))
                    b.add_mapping(
                        Mapping(
                            src=src,
                            dst=dst,
                            pt_nodes={
                                LEAF: PTNode(attrs=_leaf_attrs()),
                                2: PTNode(attrs={"r": 1}),
                            },
                        )
                    )
                result = b.build()
                for mapping in b.mappings:
                    if mapping.src.space is g:
                        gpa, hpa = result.address_of(mapping.src)
                        self.assertEqual(result.space(g).walk(gpa)[1], hpa)

    def test_conflicting_force_vs_default_still_splits(self):
        # Coloring must still isolate a forced pointer from a sibling DATA page
        # that demands the default. (Root/structural self-maps are different:
        # they don't care about pointer bits and reuse/upgrade via the tree build.)
        b = PageTableBuilder(rng=RandNum(seed=7), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        forced = b.add_page(Page(space=g, pagesize=S4KB))
        plain = b.add_page(Page(space=g, pagesize=S4KB))
        for src, nodes in (
            (forced, {LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"r": 1})}),
            (plain, {LEAF: PTNode(attrs=_leaf_attrs())}),
        ):
            b.add_mapping(Mapping(src=src, dst=b.add_page(Page(space=b.phys, pagesize=S4KB)), pt_nodes=nodes))
        result = b.build()
        self.assertNotEqual(
            _index(result.address_of(forced)[0], 2),
            _index(result.address_of(plain)[0], 2),
            "a forced level-2 bit must not share its pointer with a default sibling",
        )

    def test_forced_pointer_coexists_with_root_self_map(self):
        # A data page next to the hgatp root shares its level-2 slot with the root
        # self-map (defaults). Without exclusivity the tree must upgrade the default
        # pointer to the forced bits rather than raise.
        for seed in (41, 153, 140149633):
            with self.subTest(seed=seed):
                b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
                g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
                for i in range(16):
                    src = b.add_page(Page(space=g, pagesize=S4KB))
                    dst = b.add_page(Page(space=b.phys, pagesize=S4KB))
                    nodes = {LEAF: PTNode(attrs=_leaf_attrs())}
                    if i % 3 == 0:
                        nodes[2] = PTNode(attrs={"d": 1})
                    b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=nodes))
                result = b.build()
                for mapping in b.mappings:
                    if mapping.src.space is g:
                        gpa, hpa = result.address_of(mapping.src)
                        self.assertEqual(result.space(g).walk(gpa)[1], hpa)

    def test_explicit_reserve_size_still_widens_the_claim(self):
        # RiescueD's modify_*_pt route: exclusivity is stated as reserve_size, not
        # inferred from declared pointer bits. Two pages each claiming a full
        # level-2 span must land in disjoint 1 GiB windows.
        b = PageTableBuilder(rng=RandNum(seed=3), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        srcs = []
        for _ in range(2):
            src = b.add_page(Page(space=g, pagesize=S4KB, addr=AddrSpec(and_mask=~(L2_SPAN - 1)), reserve_size=L2_SPAN))
            dst = b.add_page(Page(space=b.phys, pagesize=S4KB, addr=AddrSpec(and_mask=~(L2_SPAN - 1)), reserve_size=L2_SPAN))
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=dst,
                    pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"v": 0})},
                )
            )
            srcs.append(src)
        result = b.build()
        gpas = [result.address_of(src)[0] for src in srcs]
        spans = [(gpa & ~(L2_SPAN - 1), (gpa & ~(L2_SPAN - 1)) + L2_SPAN) for gpa in gpas]
        self.assertTrue(
            spans[0][1] <= spans[1][0] or spans[1][1] <= spans[0][0],
            f"explicit reserve_size spans must be disjoint: {spans}",
        )


class TestMmuTbDynamicConfigRepro(unittest.TestCase):
    """End-to-end: the failing mmu_dynamic_satp_test config + seed must generate."""

    def test_seed_140149633_bare_gstage_weighted_level2_forces(self):
        from riescue.riemap.json_frontend import PageTableConfig, generate_page_tables

        cfg = PageTableConfig.from_dict(
            {
                "mmap": [["0x1000000000", "0x8000000000000"], {"low": "0x0", "high": "0x1000000000", "secure": True}],
                "spaces": {
                    "space1": {
                        "twostage": True,
                        "paging_mode": "disable",
                        "gstage_paging_mode": "sv39",
                        "pages": [
                            {
                                "id": "dynamic_pages",
                                "num_pages": 123,
                                "attributes": {
                                    "gstage_vs_leaf_size": [
                                        {"value": "1gb", "weight": 23},
                                        {"value": "2mb", "weight": 39},
                                        {"value": "4kb", "weight": 38},
                                    ],
                                    "gstage_vs_nonleaf_size": [
                                        {"value": "4kb", "weight": 77},
                                        {"value": "1gb", "weight": 23},
                                    ],
                                    "r_level2": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "x_level2": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "d_level2": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "r_level1": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "x_level1": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "d_level1": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "r_level0": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "x_level0": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "d_level0": [{"value": 0, "weight": 98}, {"value": 1, "weight": 2}],
                                    "d": [{"value": 1, "weight": 90}, {"value": 0, "weight": 10}],
                                    "r": [{"value": 1, "weight": 90}, {"value": 0, "weight": 10}],
                                    "x": [{"value": 1, "weight": 90}, {"value": 0, "weight": 10}],
                                    "secure": [{"value": 1, "weight": 10}, {"value": 0, "weight": 90}],
                                },
                            }
                        ],
                    }
                },
            }
        )
        out = generate_page_tables(cfg, seed=140149633)
        self.assertEqual(sum(len(group) for group in out.spaces["space1"].pages.values()), 123)


if __name__ == "__main__":
    unittest.main(verbosity=2)
