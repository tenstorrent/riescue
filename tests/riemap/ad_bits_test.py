# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""The walker takes a leaf's A/D bits verbatim; it never randomizes them.

PTE-bit policy belongs to the consumer. A declared bit is honored, an
undeclared bit defaults to 1, and one page's leaf attributes are identical for
every PTE in a 64 KiB NAPOT block.
"""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.config import PagingParams
from riescue.riemap.memory import Memory
from riescue.riemap.page_map import Page as WalkerPage, PageMap
from riescue.riemap.pagetables import PTAttrs
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTNode, Space

_A = 1 << 6
_D = 1 << 7


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs(**over):
    attrs = {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}
    attrs.update(over)
    return attrs


def _leaf_pte(pagesize=RV.RiscvPageSizes.S4KB, va=0x40000, pa=0x80040000, **over):
    """Build one mapping and return ``(leaf_pte_value, every_pte_of_its_slot_block)``."""
    b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
    space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
    src = b.add_page(Page(space=space, pagesize=pagesize, addr=AddrSpec(exact=va)))
    dst = b.add_page(Page(space=b.phys, pagesize=pagesize, addr=AddrSpec(exact=pa)))
    b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs(**over))}))
    result = b.build()
    sr = result.space(space)
    steps, _ = sr.walk(va)
    entries = dict(sr.pte_entries())
    leaf = next(s for s in steps if s.leaf)
    # A 64 KiB page occupies a 16-entry aligned slot group; every other pagesize is one PTE.
    if pagesize == RV.RiscvPageSizes.S64KB:
        block = [entries[(leaf.pte_addr & ~0x7F) + 8 * i] for i in range(16)]
    else:
        block = [entries[leaf.pte_addr]]
    return entries[leaf.pte_addr], block


class TestDeclaredAdBitsAreHonored(unittest.TestCase):
    def test_undeclared_ad_default_to_one(self):
        pte, _block = _leaf_pte()
        self.assertTrue(pte & _A)
        self.assertTrue(pte & _D)

    def test_declared_a_zero_reaches_the_leaf(self):
        pte, _block = _leaf_pte(a=0)
        self.assertFalse(pte & _A, f"declared a=0 lost at the leaf ({pte:#x})")
        self.assertTrue(pte & _D)

    def test_declared_d_zero_reaches_the_leaf(self):
        pte, _block = _leaf_pte(d=0)
        self.assertFalse(pte & _D, f"declared d=0 lost at the leaf ({pte:#x})")


class TestPtAttrsIsDeterministic(unittest.TestCase):
    """A ``PTAttrs`` is a pure function of the page's attrs and the level -- the ``rng`` it is
    handed must not move any bit. The walker still threads one (it drives nothing here today,
    and unthreading it would cascade through PageMap/Page/builder), so prove it is inert."""

    def _attrs_value(self, seed, **over):
        config = PagingParams(physical_addr_bits=56)
        page_map = PageMap(paging_mode=RV.RiscvPagingModes.SV39, featmgr=config, addrgen=None)  # type: ignore[arg-type]
        page = WalkerPage(page_map=page_map, featmgr=config, addrgen=None)  # type: ignore[arg-type]
        for base, val in _leaf_attrs(**over).items():
            page.attrs[base] = val
            page.attrs[f"{base}_level0"] = val
        return PTAttrs(rng=RandNum(seed=seed), featmgr=config, level=0, page=page, leaf=True).get_value()

    def test_same_page_same_value_across_seeds(self):
        values = {self._attrs_value(seed) for seed in range(8)}
        self.assertEqual(len(values), 1, f"PTAttrs is rng-dependent: {[hex(v) for v in values]}")

    def test_a_zero_stays_zero_across_seeds(self):
        for seed in range(8):
            self.assertFalse(self._attrs_value(seed, a=0) & _A, f"a=0 flipped on seed {seed}")


class TestNapotBlockIsByteIdentical(unittest.TestCase):
    """Whatever decides a leaf's A/D must be rolled ONCE PER PAGE, never per ``PTAttrs``.

    A 64 KiB page builds 16 separate ``PTAttrs`` objects (one per NAPOT slot) and
    ``_pack_leaf`` compares ``pt_attr.get_value()`` when packing into an occupied slot. A
    per-``PTAttrs`` roll therefore produces a malformed NAPOT block -- 16 PTEs that disagree
    on their permission bits, which hardware reads as one 64 KiB translation -- and spurious
    "leaf PTE slot conflict" failures when a second page packs into the same block. This is
    the guard for that invariant, on both the default and a declared-0 A.
    """

    def test_default_ad_block_is_identical(self):
        _leaf, block = _leaf_pte(pagesize=RV.RiscvPageSizes.S64KB)
        self.assertEqual(len(set(block)), 1, f"NAPOT block disagrees: {[hex(v) for v in block]}")
        self.assertTrue(all(v & _A for v in block))

    def test_declared_a_zero_block_is_identical(self):
        _leaf, block = _leaf_pte(pagesize=RV.RiscvPageSizes.S64KB, a=0)
        self.assertEqual(len(set(block)), 1, f"NAPOT block disagrees: {[hex(v) for v in block]}")
        self.assertTrue(all(not (v & _A) for v in block), "every PTE of the block must carry the declared a=0")


if __name__ == "__main__":
    unittest.main()
