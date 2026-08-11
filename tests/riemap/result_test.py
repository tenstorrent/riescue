# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the AllocationResult read-back surface returned by build().

Driven through the constraint-based :class:`PageTableBuilder` (the only
builder now): a source VA space plus the auto-created physical leaf space,
wired by mappings.
"""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import AddrSpec, LEAF, Mapping, Page, PTGPage, PTNode, SameAs, Space, Stage
from tests.riemap.root_policy import declare_vs_root_identity


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs():
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}


def _leaf_nodes():
    return {LEAF: PTNode(attrs=_leaf_attrs())}


def _single(seed=1, n=1, mode=RV.RiscvPagingModes.SV39):
    """A single-stage builder: ``n`` pages in a fresh source space mapping into phys."""
    b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
    s1 = b.add_space(Space(paging_mode=mode))
    pages = []
    for _ in range(n):
        p = b.add_page(Page(space=s1))
        pa = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
        pages.append((p, pa))
    return b, s1, pages


def _nonidentity_vs_frame():
    """Build a VS walk whose level-1 table has distinct GPA and HPA addresses."""
    b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
    g = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
    vs = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, g)
    frame_gpa = b.add_page(Page(space=g, addr=AddrSpec(exact=0x400000)))
    frame_hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x81000000)))
    va = b.add_page(Page(space=vs, addr=AddrSpec(exact=0x2000)))
    hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80030000)))
    gpa = b.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
    b.add_mapping(
        Mapping(
            src=va,
            dst=gpa,
            pt_nodes={
                LEAF: PTNode(attrs=_leaf_attrs()),
                1: PTNode(page=frame_gpa),
                0: PTNode(page=PTGPage(identity=True)),
            },
        )
    )
    b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes=_leaf_nodes()))
    b.add_mapping(Mapping(src=frame_gpa, dst=frame_hpa, pt_nodes=_leaf_nodes()))
    return b.build(), vs


class TestAllocationResult(unittest.TestCase):
    def test_build_returns_result_with_addresses(self):
        b, s1, pages = _single(n=1)
        p0, p0_pa = pages[0]
        result = b.build()
        va, pa = result.address_of(p0)
        # The PA read back is the physical leaf page's own allocation, and the VS walk
        # of the VA reaches it.
        self.assertEqual(pa, result.address(p0_pa))
        _steps, translated = result.space(s1).walk(va)
        self.assertEqual(translated, pa)

    def test_space_root_and_mode(self):
        b, s1, _pages = _single(n=1)
        result = b.build()
        sr = result.space(s1)
        self.assertEqual(sr.root_addr, sr._page_map.sptbr)
        self.assertEqual(sr.paging_mode, RV.RiscvPagingModes.SV39)
        self.assertFalse(sr.is_gstage)

    def test_tables_and_sections(self):
        b, s1, _pages = _single(n=3)
        result = b.build()
        tables = list(result.space(s1).tables())
        self.assertGreater(len(tables), 0)
        # Every table carries its own space (an object, not a pre-formatted name), an
        # entry_size, and at least one entry -- structured data only.
        for t in tables:
            self.assertIs(t.space, s1)
            self.assertGreater(t.entry_size, 0)
            self.assertGreater(len(t.entries), 0)
        # Each non-leaf table has a unique base address.
        addrs = [t.addr for t in tables]
        self.assertEqual(len(addrs), len(set(addrs)))

    def test_gstage_space_flagged(self):
        # VA -> GPA -> HPA identity: the GPA space is both a mapping target and a source,
        # so it bears a g-stage table and is flagged is_gstage.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        va_space = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, gpa_space)
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80030000)))
        g = b.add_page(Page(space=gpa_space, addr=AddrSpec(relation=SameAs(pa))))
        b.add_mapping(
            Mapping(
                src=p,
                dst=g,
                pt_nodes={
                    **_leaf_nodes(),
                    0: PTNode(page=PTGPage(identity=True)),
                    1: PTNode(page=PTGPage(identity=True)),
                },
            )
        )
        b.add_mapping(Mapping(src=g, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        self.assertTrue(result.space(gpa_space).is_gstage)
        self.assertFalse(result.space(va_space).is_gstage)

    def test_pte_entries_cover_every_table_pte(self):
        b, s1, _pages = _single(n=3)
        result = b.build()
        sr = result.space(s1)
        entries = dict(sr.pte_entries())
        # One (addr, value) per PTE across all tables; addresses unique.
        total = sum(len(t.entries) for t in sr.tables())
        self.assertEqual(len(entries), total)
        # Each entry address equals table addr + entry_size * index.
        for t in sr.tables():
            for e in t.entries:
                self.assertEqual(entries[t.addr + t.entry_size * e.index], e.value)

    def test_walk_reaches_page_pa(self):
        b, s1, pages = _single(n=1)
        p0, _p0_pa = pages[0]
        result = b.build()
        va, pa = result.address_of(p0)
        steps, translated = result.space(s1).walk(va)
        # Walk ends at a leaf and translates the VA to the page's PA.
        self.assertTrue(steps)
        self.assertTrue(steps[-1].leaf)
        self.assertEqual(translated, pa)
        # Every step's PTE address is a real PTE in the tree.
        entries = dict(result.space(s1).pte_entries())
        for step in steps:
            self.assertIn(step.pte_addr, entries)

    def test_sv32_superpage_walk_carries_bit21(self):
        # An sv32 4MB superpage's page offset is 22 bits (10-bit index levels), so VA
        # bit 21 must pass through to the PA. The leaf is at level 1.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        s1 = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV32))
        p = b.add_page(Page(space=s1, pagesize=RV.RiscvPageSizes.S4MB, addr=AddrSpec(exact=0x0080_0000)))
        pa = b.add_page(Page(space=b.phys, pagesize=RV.RiscvPageSizes.S4MB, addr=AddrSpec(exact=0x8040_0000)))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        sr = result.space(s1)
        va, pa_addr = result.address_of(p)
        # Leaf sits at the superpage level (1), not the 4KB level.
        steps, translated = sr.walk(va)
        self.assertTrue(steps[-1].leaf)
        self.assertEqual(steps[-1].level, 1)
        self.assertEqual(translated, pa_addr)
        # Bit 21 is inside the 22-bit page offset and must survive translation.
        _steps, translated_hi = sr.walk(va | (1 << 21))
        self.assertEqual(translated_hi, pa_addr | (1 << 21))

    def test_pte_values_match_tree(self):
        b, s1, _pages = _single(n=1)
        result = b.build()
        # Each PTE value read back equals the tree's own get_value().
        pm = result.space(s1)._page_map
        by_addr = {t.addr: t for t in result.space(s1).tables()}

        def check(table):
            if table is None or table.leaf:
                return
            view = by_addr[table.base_addr]
            values = {e.index: e.value for e in view.entries}
            for index, entry in table.table.items():
                self.assertEqual(values[index], entry.get_value())
                if not entry.leaf and entry.basetable is not None:
                    check(entry.basetable)

        check(pm.basetable)

    def test_walk_pte_addresses_match_pte_entries_keys(self):
        result, vs = _nonidentity_vs_frame()
        steps, _translated = result.space(vs).walk(0x2000)
        nonidentity = [step for step in steps if step.pte_addr != step.pte_backing_addr]
        self.assertTrue(nonidentity, "fixture must contain a table whose input and backing addresses differ")
        emitted_addresses = {addr for addr, _value in result.space(vs).pte_entries()}
        for step in steps:
            self.assertIn(step.pte_addr, emitted_addresses)

    def test_entry_attributes_are_immutable_snapshots(self):
        b, s1, _pages = _single(n=1)
        entry = next(entry for table in b.build().space(s1).tables() for entry in table.entries if entry.leaf)

        with self.assertRaises((AttributeError, TypeError)):
            entry.attrs.x = 0


class TestSharedFrameYieldedOnce(unittest.TestCase):
    """The tree is a DAG: tables are interned by base address, so one frame is reachable
    from every pointer PTE that targets it. ``tables()`` must still yield it once.

    Two exact VAs that pin the SAME leaf-PTE frame but differ at their level-1 index give
    the level-1 table two slots pointing at one child -- a diamond. A pre-order recursion
    with no visited set yields that child once per path and multiplies through nested
    diamonds (hypervisor_paging_faults_vs emitted one frame 16 times). RiescueD names the
    linker section from the frame address, so a repeat re-emits the same section and its
    ``.org`` directives replay from 0: "attempt to move .org backwards", and the test never
    assembles."""

    _MODE = RV.RiscvPagingModes.SV39
    # Same index_2, different index_1, different index_0 (so the two leaf PTEs occupy
    # distinct slots inside the one shared frame).
    _VA_A = 0x40200000
    _VA_B = 0x40401000

    def _build(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        s1 = b.add_space(Space(paging_mode=self._MODE))
        frame = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80100000)))
        for i, va in enumerate((self._VA_A, self._VA_B)):
            src = b.add_page(Page(space=s1, addr=AddrSpec(exact=va)))
            dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + i * 0x1000)))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs(), page=frame)}))
        return b.build(), s1

    def test_the_shared_frame_is_reached_from_two_slots(self):
        # Guard: without the diamond the dedup below would pass vacuously.
        result, s1 = self._build()
        sr = result.space(s1)
        parents = [t for t in sr.tables() if len({e.index for e in t.entries if not e.leaf}) > 1]
        children = {tuple(sorted(e.value for e in t.entries)) for t in parents}
        self.assertTrue(any(len(set(c)) == 1 and len(c) > 1 for c in children), "expected a level-1 table with two slots targeting one child frame")

    def test_tables_yields_each_frame_once(self):
        result, s1 = self._build()
        addrs = [t.addr for t in result.space(s1).tables()]
        self.assertEqual(sorted(addrs), sorted(set(addrs)), "a shared PT frame must be yielded once, not once per path")

    def test_pte_entries_are_not_duplicated(self):
        # pte_entries() is built on tables(), so a repeated frame also repeats its PTEs --
        # what a consumer emitting one .8byte per entry would double.
        result, s1 = self._build()
        pairs = list(result.space(s1).pte_entries())
        self.assertEqual(len(pairs), len({addr for addr, _ in pairs}))

    def test_both_vas_still_translate(self):
        # Dedup must drop only the repeat, never a frame -- both walks still resolve.
        result, s1 = self._build()
        sr = result.space(s1)
        self.assertEqual(sr.walk(self._VA_A)[1], 0x80010000)
        self.assertEqual(sr.walk(self._VA_B)[1], 0x80011000)


class TestSpaceWithoutAPageTable(unittest.TestCase):
    """Asking for the page table of a space that has none is a wiring bug -- a consumer
    always knows which of its spaces it declared paging for -- so ``space()`` raises rather
    than returning None, which would invite skipping the space without notice. What it must not
    do is raise a bare dict ``KeyError`` naming a ``Space`` object, which says nothing about
    why."""

    _WHY = "has no page table"

    def test_a_leaf_space_raises_with_a_reason(self):
        b, _s1, _pages = _single(n=1)
        result = b.build()
        with self.assertRaisesRegex(KeyError, self._WHY):
            result.space(b.phys)  # the physical leaf space is never a mapping source

    def test_a_declared_but_unmapped_space_raises_with_a_reason(self):
        b, s1, _pages = _single(n=1)
        idle = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV48))
        result = b.build()
        with self.assertRaisesRegex(KeyError, self._WHY):
            result.space(idle)
        self.assertIsNotNone(result.space(s1), "the real source space still answers")


if __name__ == "__main__":
    unittest.main()
