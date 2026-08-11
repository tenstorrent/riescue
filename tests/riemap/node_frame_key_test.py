# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""How ``_place_pt_node_frames`` identifies a page-table node.

The walker reaches a node by the *index fields* of the address
(``Pagetables.calc_index_from_va`` reads bits ``[hi:lo]`` per level), so address bits above
the mode's top index field are invisible to it: two pages differing only there land in the
same slot of the same frame and therefore MUST be given the same child frame.

The builder has to key its node buckets the same way. Keyed on the raw shifted address it
does not, and the page that exposes it is the root's own self-map (``PageMap.create_sptbr``
adds a page whose ``lin_addr`` IS the root frame's physical base -- under SV39 a 39-bit VA
but a much wider PA). It formed its own bucket, drew its own child frame, and then collided
with the real VA it aliases:

    ValueError: page-table non-leaf slot conflict at index 0x2 in frame 0x500837bd000 (level 2)

which is how ``tests/cli_tests/riescuec/tp_mode:svnapot_test`` failed."""

import unittest

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTNode, Space

SV39 = RV.RiscvPagingModes.SV39

# An SV39 root frame whose PA is wider than the 39-bit VA it gets self-mapped at: bit 39 is
# set, so the raw address distinguishes it from any real VA, while its index fields do not.
ROOT_PA = 0x80C0000000
# A real VA sharing every SV39 index field with ROOT_PA except the leaf one -- same root and
# level-1 slots (so a disagreement on either frame is a conflict), different leaf slot (so a
# correct build packs them and does not trip the leaf-slot check instead).
ALIASED_VA = 0xC0001000
ALIASED_PA = 0x81000000


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_node():
    return PTNode(attrs={"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1})


def _build():
    """SV39 space whose root table is pinned at ``ROOT_PA``, plus a mapping for ``ALIASED_VA``."""
    b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
    va = b.add_space(Space(paging_mode=SV39))
    root = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=ROOT_PA)))
    src = b.add_page(Page(space=va, addr=AddrSpec(exact=ALIASED_VA)))
    dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=ALIASED_PA)))
    b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: _leaf_node(), 2: PTNode(page=root)}))
    return b, va


class TestIndexFieldAliasing(unittest.TestCase):
    """The scenario's premise: the two addresses alias in every index field but the leaf."""

    def test_addresses_alias_above_the_leaf(self):
        for level, same in ((2, True), (1, True), (0, False)):
            hi, lo = RV.RiscvPagingModes.index_bits(SV39, level)
            root_index = common.bits(value=ROOT_PA, bit_hi=hi, bit_lo=lo)
            va_index = common.bits(value=ALIASED_VA, bit_hi=hi, bit_lo=lo)
            self.assertEqual(root_index == va_index, same, f"level {level}: 0x{root_index:x} vs 0x{va_index:x}")
        # ... and they differ above bit 38, which is exactly what the walker cannot see.
        self.assertNotEqual(ROOT_PA >> 39, ALIASED_VA >> 39)


class TestPinnedRootIsNotImplicitlyMapped(unittest.TestCase):
    """Pinning a root frame does not create a leaf at VA == frame address."""

    def test_build_succeeds(self):
        b, va = _build()
        result = b.build()  # raised "non-leaf slot conflict at index 0x3 ... (level 2)" before the fix
        self.assertEqual(result.space(va).walk(ALIASED_VA)[1], ALIASED_PA)

    def test_root_frame_is_not_added_as_a_page(self):
        b, va = _build()
        pm = b.build().space(va)._page_map
        self.assertNotIn(ROOT_PA, pm.pages)
        self.assertIn(ALIASED_VA, pm.pages)

    def test_real_mapping_uses_the_pinned_root(self):
        b, va = _build()
        pm = b.build().space(va)._page_map
        self.assertEqual(pm.basetable.base_addr, ROOT_PA)
        root_hi, root_lo = RV.RiscvPagingModes.index_bits(SV39, 2)
        root_index = common.bits(value=ALIASED_VA, bit_hi=root_hi, bit_lo=root_lo)
        assert pm.basetable is not None
        root_entry = pm.basetable.get_entry(root_index)
        self.assertFalse(root_entry.leaf)
        self.assertEqual(pm.pages[ALIASED_VA].phys_addr, ALIASED_PA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
