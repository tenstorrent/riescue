# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Oracle / property tests for the declared page-table-node walker (C2 semantics).

These assert on the *resolved* result (addresses, PTE bits, interned table identity)
read back through :class:`~riescue.riemap.result.AllocationResult`. They cover attr-aware
sharing, merge-or-error packing, aliasing, recursion, over-pack exhaustion, the secure
two-stage GPA frame, the auto two-stage identity convenience, and seed determinism.

Exercises the walker only (coloring is a separate track)."""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.pagetables import PTTable, PTEntry, PTAttrs
from riescue.riemap.config import PagingParams
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTNode, SameAs, Space, Stage
from tests.riemap.root_policy import declare_vs_root_identity

SV39 = RV.RiscvPagingModes.SV39
_SECURE_BIT = 0x0080000000000000


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _secure_memory():
    return Memory.from_dict(
        {
            "dram": {
                "dram0": {"address": "0x80000000", "size": "0x3FFFF80000000", "cacheable": True, "configurable": True},
                "dram_sec": {"address": "0x4000000000000", "size": "0x4000000000000", "cacheable": True, "configurable": True, "secure": True},
            }
        }
    )


def _leaf_attrs(**extra):
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, **extra}


def _leaf_nodes(**extra):
    return {LEAF: PTNode(attrs=_leaf_attrs(**extra))}


def _table_base(step):
    """Physical base of the 4KB PT frame a walk step landed in (sv39 tables are 512x8)."""
    return step.pte_addr & ~0xFFF


def _child_of(pte_value):
    """The child/target base a PTE points at (PPN reconstructed from the packed value)."""
    return (pte_value >> 10) << 12


def _bases_at(sr, addr):
    """(level -> frame base) for the non-leaf steps of ``addr``'s walk, plus the leaf step."""
    steps, translated = sr.walk(addr)
    return {s.level: _table_base(s) for s in steps}, translated


class TestSharedWhenIdentical(unittest.TestCase):
    """Several mappings whose intermediate nodes carry identical (default) attrs and share
    a VA prefix pack into exactly one interned PTTable / one physical frame per level."""

    def test_prefix_sharing_interns_one_frame_per_level(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        # Three VAs sharing l2=1, l1=0; differ only at l0 (0,1,2).
        srcs, dsts, addrs = [], [], [0x40000000, 0x40001000, 0x40002000]
        for i, a in enumerate(addrs):
            srcs.append(b.add_page(Page(space=va, addr=AddrSpec(exact=a))))
            dsts.append(b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + i * 0x1000))))
            b.add_mapping(Mapping(src=srcs[-1], dst=dsts[-1], pt_nodes=_leaf_nodes()))
        result = b.build()
        sr = result.space(va)

        per_va = [_bases_at(sr, a)[0] for a in addrs]
        l1_bases = {b[1] for b in per_va}
        l0_bases = {b[0] for b in per_va}
        self.assertEqual(len(l1_bases), 1, "identical mappings must share one level-1 frame")
        self.assertEqual(len(l0_bases), 1, "identical mappings must share one level-0 (leaf) frame")

        l1_base, l0_base = l1_bases.pop(), l0_bases.pop()
        # Interning: one PTTable object per physical base, surfaced once by tables().
        self.assertEqual(len([tv for tv in sr.tables() if tv.addr == l1_base]), 1, "level-1 frame must be a single interned table")
        leaf_tables = [tv for tv in sr.tables() if tv.addr == l0_base]
        self.assertEqual(len(leaf_tables), 1)
        # The one shared leaf frame packs all three distinct leaf slots.
        self.assertEqual(len(leaf_tables[0].entries), 3)
        for i, a in enumerate(addrs):
            self.assertEqual(sr.walk(a)[1], 0x80010000 + i * 0x1000)


class TestConflictMustIsolate(unittest.TestCase):
    """Two mappings pinned to the same (frame, slot) with contradictory content raise the
    merge-or-error ValueError -- never silently corrupt."""

    def test_conflicting_child_base_raises(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        shared = b.add_page(Page(space=b.phys))
        # Same l1 slot (0) inside the shared frame, but different l0 -> different child tables.
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))  # l2=1,l1=0
        c = b.add_page(Page(space=va, addr=AddrSpec(exact=0x80000000)))  # l2=2,l1=0
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("conflict", str(cm.exception))

    def test_conflicting_pointer_attrs_raises(self):
        # Two VAs sharing the same root (l2) slot pointing at the same auto level-1 child,
        # but forcing contradictory pointer-PTE attrs (D) on that slot -> a genuine conflict.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))  # l2=1,l1=0
        c = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40200000)))  # l2=1,l1=1
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_c = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"d": 0})}))
        b.add_mapping(Mapping(src=c, dst=pa_c, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"d": 1})}))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("conflict", str(cm.exception))


class TestAliasedTables(unittest.TestCase):
    """Two VAs whose level-1 node is one shared frame Page resolve to a single interned
    PTTable, reached by two distinct parent PTEs."""

    def test_one_frame_two_parent_ptes(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        shared = b.add_page(Page(space=b.phys))
        va_a = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))  # l2=1
        va_b = b.add_page(Page(space=va, addr=AddrSpec(exact=0x80200000)))  # l2=2
        pa_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa_b = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=va_a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        b.add_mapping(Mapping(src=va_b, dst=pa_b, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=shared)}))
        result = b.build()
        sr = result.space(va)
        shared_pa = result.address_of(shared)[1]

        # Both walks route their level-1 step through the shared frame.
        self.assertEqual(_bases_at(sr, 0x40000000)[0][1], shared_pa)
        self.assertEqual(_bases_at(sr, 0x80200000)[0][1], shared_pa)
        # The frame is interned to a single PTTable object (keyed by physical base)...
        pm = sr._page_map
        self.assertIn(shared_pa, pm.tables_by_base)
        interned = pm.tables_by_base[shared_pa]
        # ...reached by two distinct parent (root) PTEs, each pointing at that one object.
        parents = [e for e in pm.basetable.table.values() if not e.leaf and e.basetable.base_addr == shared_pa]
        self.assertEqual(len(parents), 2, "expected two parent PTEs pointing at the shared frame")
        self.assertEqual(len({id(e.basetable) for e in parents}), 1, "aliased frame must be one shared PTTable object")
        self.assertIs(parents[0].basetable, interned)
        self.assertEqual(sr.walk(0x40000000)[1], 0x80010000)
        self.assertEqual(sr.walk(0x80200000)[1], 0x80020000)


class TestRecursiveSelfMap(unittest.TestCase):
    """A frame declared at the top (root) level that is also its own mapping's leaf target
    builds and translates to the frame itself."""

    def test_top_level_self_map_builds_and_translates(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        root = b.add_page(Page(space=b.phys))
        vself = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40001000)))
        b.add_mapping(Mapping(src=vself, dst=root, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(page=root)}))
        result = b.build()
        sr = result.space(va)
        root_pa = result.address_of(root)[1]
        # The root pin is structural: the root page table physically lives at the frame ...
        self.assertEqual(sr.root_addr, root_pa, "root table must physically live at the pinned frame")
        self.assertEqual(sr._page_map.basetable.base_addr, root_pa)
        # ... and the self-map VA translates back to that very frame.
        self.assertEqual(sr.walk(0x40001000)[1], root_pa, "self-map must translate to the frame itself")


class TestOverPackingExhaustion(unittest.TestCase):
    """Packing more distinct entries into a frame than its slot capacity raises cleanly."""

    def test_over_packing_raises(self):
        featmgr = PagingParams(physical_addr_bits=56)
        table = PTTable(base_addr=0x1000, capacity=2)
        for i in range(2):
            attr = PTAttrs(rng=RandNum(seed=1), featmgr=featmgr, level=0, leaf=True)
            table.insert_entry(PTEntry(basetable=PTTable(0x2000 + i * 0x1000, leaf=True), pt_attr=attr, level=0), index=i)
        attr = PTAttrs(rng=RandNum(seed=1), featmgr=featmgr, level=0, leaf=True)
        with self.assertRaises(ValueError) as cm:
            table.insert_entry(PTEntry(basetable=PTTable(0x9000, leaf=True), pt_attr=attr, level=0), index=2)
        self.assertIn("over-packed", str(cm.exception))


class TestSecureGpaFrame(unittest.TestCase):
    """A two-stage pinned G-stage frame whose own g-stage leaf carries a secure (bit-55)
    HPA: the tag rides only the g-stage leaf HPA, never the GPA or the VS pointer PTE."""

    def test_bit55_only_on_gstage_leaf_hpa(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_secure_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, SV39, g)
        # Pin the GPA low so its canonical form cannot itself set bit 55 (isolate the STEE tag).
        gnode = b.add_page(Page(space=g, addr=AddrSpec(exact=0x40000000)))
        hpa_sec = b.add_page(Page(space=b.phys, addr=AddrSpec(qualifiers={RV.AddressQualifiers.ADDRESS_SECURE})))
        b.add_mapping(Mapping(src=gnode, dst=hpa_sec, pt_nodes=_leaf_nodes()))  # gnode's own secure g-stage leaf
        gleaf = b.add_page(Page(space=g, addr=AddrSpec(exact=0x40001000)))
        gleaf_hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80031000)))
        b.add_mapping(Mapping(src=gleaf, dst=gleaf_hpa, pt_nodes=_leaf_nodes()))
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x2000)))
        hpa2 = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80030000)))
        gpa_leaf = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa2))))
        b.add_mapping(
            Mapping(
                src=va,
                dst=gpa_leaf,
                pt_nodes={
                    LEAF: PTNode(attrs=_leaf_attrs()),
                    0: PTNode(page=gleaf),
                    1: PTNode(page=gnode),
                },
            )
        )
        b.add_mapping(Mapping(src=gpa_leaf, dst=hpa2, pt_nodes=_leaf_nodes()))
        result = b.build()

        gnode_gpa, gnode_hpa = result.address_of(gnode)
        gstage_translated = result.space(g).walk(gnode_gpa)[1]
        self.assertFalse(gnode_gpa & _SECURE_BIT, f"GPA 0x{gnode_gpa:x} must stay non-secure")
        self.assertFalse(gnode_hpa & _SECURE_BIT, "the HPA page's own address must stay clean")
        self.assertTrue(gstage_translated & _SECURE_BIT, f"g-stage leaf HPA 0x{gstage_translated:x} must carry bit 55")
        # The VS-stage pointer to the pinned frame uses the clean GPA.
        vs_ptrs = [e for tv in result.space(vs).tables() for e in tv.entries if not e.leaf and _child_of(e.value) == gnode_gpa]
        self.assertTrue(vs_ptrs, "VS pointer PTE to the pinned g-stage frame not found")
        for e in vs_ptrs:
            self.assertFalse(_child_of(e.value) & _SECURE_BIT, "VS pointer to the pinned frame must be non-secure")


class TestAutoTwoStageIdentity(unittest.TestCase):
    """add_two_stage_mapping builds VA -> GPA -> HPA with a GPA == HPA identity g-stage."""

    def test_identity_readback(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = b.add_space(Space(paging_mode=SV39, stage=Stage.G))
        vs = declare_vs_root_identity(b, SV39, g)
        va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        hpa = Page(space=b.phys, addr=AddrSpec(exact=0x80040000))
        gpa = b.add_two_stage_mapping(va_page=va, hpa_page=hpa, gpa_space=g, attrs=_leaf_attrs(), vs_pagesize=RV.RiscvPageSizes.S4KB, gstage_mode=SV39)
        result = b.build()

        gpa_va, gpa_pa = result.address_of(gpa)
        _hpa_va, hpa_pa = result.address_of(hpa)
        self.assertEqual(gpa_pa, hpa_pa, "GPA must be identity-mapped to HPA (GPA == HPA)")
        self.assertEqual(result.space(vs).walk(0x3000)[1], hpa_pa, "VS walk must reach the HPA through the identity g-stage")
        self.assertEqual(result.space(g).walk(gpa_va)[1], hpa_pa, "g-stage walk of the GPA must reach the HPA")


class TestDeterminism(unittest.TestCase):
    """Same seed -> identical resolved addresses and PTE trees for a pt_nodes scenario."""

    def _build(self):
        b = PageTableBuilder(rng=RandNum(seed=7), memory=_memory())
        va = b.add_space(Space(paging_mode=SV39))
        node1 = b.add_page(Page(space=b.phys))
        p1 = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40000000)))
        p2 = b.add_page(Page(space=va, addr=AddrSpec(exact=0x40200000)))
        pa1 = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        pa2 = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=p1, dst=pa1, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 1: PTNode(page=node1)}))
        b.add_mapping(Mapping(src=p2, dst=pa2, pt_nodes=_leaf_nodes()))
        result = b.build()
        sr = result.space(va)
        return (sr.root_addr, sorted(sr.pte_entries()), result.address_of(node1), result.address_of(p1), result.address_of(p2))

    def test_same_seed_identical_trees(self):
        first = self._build()
        second = self._build()
        self.assertEqual(first, second, "identical seed must yield identical resolved addresses and PTE trees")


if __name__ == "__main__":
    unittest.main(verbosity=2)
