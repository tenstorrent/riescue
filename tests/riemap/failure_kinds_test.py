# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Drive every production RieMap failure kind and assert the structured diagnostic."""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen import AddrGen
from riescue.riemap.allocator import AllocRequest, BatchAllocationStrategy
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.config import PagingParams
from riescue.riemap.errors import (
    AddressSpaceExhausted,
    AllocationConflict,
    ConstraintConflict,
    FailureKind,
    FailurePhase,
    PlanningExhausted,
    RieMapError,
)
from riescue.riemap.layout import (
    IntentProvenance,
    LeafContract,
    LeafIntent,
    LeafSpan,
    LogicalTable,
    PointerClaim,
    SlotKey,
    TopologyConflict,
    TopologyPlan,
    plan_topology,
)
from riescue.riemap.memory import Memory
from riescue.riemap.page_map import Page as WalkerPage, PageMap
from riescue.riemap.pagetables import PTAttrs, PTEntry, PTTable
from riescue.riemap.planner import JointPlanningError, JointPlanner
from riescue.riemap.request import AddrSpec, LEAF, Mapping, MemoryRegion, OffsetFrom, Page, PTNode, SameAs, Space, Stage


def _memory(size="0x80000000000000"):
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": size, "cacheable": True, "configurable": True}}})


def _addrgen(seed=1, size="0x80000000000000"):
    return AddrGen(RandNum(seed=seed), _memory(size))


def _leaf_attrs():
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}


def _mapping(space, phys, pagesize):
    return Mapping(Page(space, pagesize=pagesize), Page(phys, pagesize=pagesize))


class TestFailureKindDiagnostics(unittest.TestCase):
    """One live raise per production ``FailureKind``, plus a completeness check."""

    def _assert_good(self, error, kind, *, phase, snippets):
        self.assertIsInstance(error, RieMapError)
        self.assertEqual(error.kind, kind)
        self.assertEqual(error.phase, phase)
        self.assertTrue(error.summary)
        self.assertTrue(error.reason)
        self.assertTrue(error.hints)
        text = error.format_diagnostic()
        self.assertIn(f"RieMap failure [{kind.value}]", text)
        self.assertIn("Possible fixes:", text)
        for snippet in snippets:
            self.assertIn(snippet, text)
        return error

    def _raises(self, expected, fn):
        with self.assertRaises(expected) as caught:
            fn()
        return caught.exception

    def test_duplicate_source(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        source = Page(va)
        first = Mapping(source, Page(pa))
        second = Mapping(source, Page(pa))
        error = self._raises(
            TopologyConflict,
            lambda: plan_topology(
                [first, second],
                {source: 0x3000, first.dst: 0x80003000, second.dst: 0x80004000},
            ),
        )
        self._assert_good(
            error,
            FailureKind.DUPLICATE_SOURCE,
            phase=FailurePhase.TOPOLOGY,
            snippets=("two different destinations",),
        )
        self.assertEqual(error.context["first_target"], 0x80003000)
        self.assertEqual(error.context["second_target"], 0x80004000)

    def test_leaf_leaf(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        first = _mapping(va, pa, RV.RiscvPageSizes.S4KB)
        second = _mapping(va, pa, RV.RiscvPageSizes.S4KB)
        error = self._raises(
            TopologyConflict,
            lambda: plan_topology(
                [first, second],
                {
                    first.src: 0x3000,
                    first.dst: 0x80003000,
                    second.src: 0x3000,
                    second.dst: 0x80004000,
                },
            ),
        )
        self._assert_good(
            error,
            FailureKind.LEAF_LEAF,
            phase=FailurePhase.TOPOLOGY,
            snippets=("same page-table entry", "Move one mapping"),
        )
        self.assertEqual(error.site.level, 0)

    def test_pointer_pointer(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        first = _mapping(va, pa, RV.RiscvPageSizes.S4KB)
        second = _mapping(va, pa, RV.RiscvPageSizes.S4KB)
        plan = TopologyPlan()
        plan.intents[first] = LeafIntent(
            mapping=first,
            span=LeafSpan.from_address(va, 0x40000000, RV.RiscvPageSizes.S4KB),
            contract=LeafContract(target=0x80000000, pagesize=RV.RiscvPageSizes.S4KB),
        )
        plan.intents[second] = LeafIntent(
            mapping=second,
            span=LeafSpan.from_address(va, 0x40001000, RV.RiscvPageSizes.S4KB),
            contract=LeafContract(target=0x80001000, pagesize=RV.RiscvPageSizes.S4KB),
        )
        table = LogicalTable(va, 1, 0)
        key = SlotKey(table, 0)
        plan.merge_slot(
            key,
            PointerClaim(mappings=[first], child=LogicalTable(va, 0, 0), level=1, identity=False, provenance=IntentProvenance.EXPLICIT),
        )
        error = self._raises(
            TopologyConflict,
            lambda: plan.merge_slot(
                key,
                PointerClaim(mappings=[second], child=LogicalTable(va, 0, 1), level=1, identity=False, provenance=IntentProvenance.EXPLICIT),
            ),
        )
        self._assert_good(
            error,
            FailureKind.POINTER_POINTER,
            phase=FailurePhase.TOPOLOGY,
            snippets=("two different child tables",),
        )

    def test_leaf_pointer(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        large = _mapping(va, pa, RV.RiscvPageSizes.S2MB)
        small = _mapping(va, pa, RV.RiscvPageSizes.S4KB)
        error = self._raises(
            TopologyConflict,
            lambda: plan_topology(
                [large, small],
                {
                    large.src: 0x400000,
                    large.dst: 0x80000000,
                    small.src: 0x401000,
                    small.dst: 0x90000000,
                },
            ),
        )
        self._assert_good(
            error,
            FailureKind.LEAF_POINTER,
            phase=FailurePhase.TOPOLOGY,
            snippets=("one PTE cannot be both", "Split the coarse mapping"),
        )
        self.assertEqual(error.site.level, 1)
        self.assertEqual(error.site.slot, 2)

    def test_required_translation(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        synthetic = _mapping(va, pa, RV.RiscvPageSizes.S2MB)
        explicit = _mapping(va, pa, RV.RiscvPageSizes.S4KB)
        plan = plan_topology(
            [synthetic, explicit],
            {
                synthetic.src: 0x400000,
                synthetic.dst: 0x400000,
                explicit.src: 0x401000,
                explicit.dst: 0x401000,
            },
            provenance={
                synthetic: IntentProvenance.SYNTHETIC_FRAME,
                explicit: IntentProvenance.EXPLICIT,
            },
            required_addresses={va: {0x401000, 0x402000}},
        )
        error = self._raises(TopologyConflict, plan.validate_required_coverage)
        self._assert_good(
            error,
            FailureKind.REQUIRED_TRANSLATION,
            phase=FailurePhase.TOPOLOGY,
            snippets=("became unreachable", "explicit translations"),
        )

    def test_missing_gstage_translation(self):
        builder = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        vs = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.VS))
        va = builder.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
        hpa = builder.add_page(Page(space=builder.phys))
        gpa = builder.add_page(Page(space=g, addr=AddrSpec(relation=SameAs(hpa))))
        builder.add_mapping(Mapping(src=va, dst=gpa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
        builder.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
        error = self._raises(TopologyConflict, builder.build)
        self._assert_good(
            error,
            FailureKind.MISSING_GSTAGE_TRANSLATION,
            phase=FailurePhase.TOPOLOGY,
            snippets=("no G-stage translation", "PTGPage"),
        )

    def test_claim_overlap(self):
        space = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        first = AllocRequest(page=Page(space), addr_type=RV.AddressType.PHYSICAL, size=0x2000, addr=AddrSpec(exact=0x80000000))
        second = AllocRequest(page=Page(space), addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(exact=0x80001000))
        error = self._raises(
            AllocationConflict,
            lambda: BatchAllocationStrategy().solve([first, second], [], _addrgen(), RandNum(seed=1)),
        )
        self._assert_good(
            error,
            FailureKind.CLAIM_OVERLAP,
            phase=FailurePhase.ALLOCATION,
            snippets=("overlaps an existing", "Move one exact address"),
        )

    def test_unsatisfiable(self):
        space = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        requests = [AllocRequest(page=Page(space), addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000)) for _ in range(2)]
        error = self._raises(
            AllocationConflict,
            lambda: BatchAllocationStrategy().solve(requests, [], _addrgen(size="0x1000"), RandNum(seed=1)),
        )
        self._assert_good(
            error,
            FailureKind.UNSATISFIABLE,
            phase=FailurePhase.ALLOCATION,
            snippets=("No address assignment satisfies",),
        )

    def test_relation_cycle(self):
        space = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        page_a, page_b = Page(space), Page(space)
        root = AllocRequest(page=page_a, addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(exact=0x80000000))
        child = AllocRequest(page=page_b, addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(relation=OffsetFrom(page_a, 0x1000)))
        back_to_root = AllocRequest(page=page_a, addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(relation=OffsetFrom(page_b, 0)))
        error = self._raises(
            AllocationConflict,
            lambda: BatchAllocationStrategy()._bundle_values(
                root,
                0x80000000,
                {page_a: [child], page_b: [back_to_root]},
            ),
        )
        self._assert_good(
            error,
            FailureKind.RELATION_CYCLE,
            phase=FailurePhase.ALLOCATION,
            snippets=("dependency cycle", "Break the cycle"),
        )

    def test_region_exhausted(self):
        region = MemoryRegion(size=0x400000, align=0x1000, base=0x80000000)
        member = AllocRequest(
            page=Page(Space(paging_mode=RV.RiscvPagingModes.DISABLE), pagesize=RV.RiscvPageSizes.S2MB),
            addr_type=RV.AddressType.PHYSICAL,
            size=0x200000,
            addr=AddrSpec(region=region, or_mask=0x1000),
        )
        error = self._raises(
            AddressSpaceExhausted,
            lambda: BatchAllocationStrategy().solve([member], [region], _addrgen(), RandNum(seed=1)),
        )
        self._assert_good(
            error,
            FailureKind.REGION_EXHAUSTED,
            phase=FailurePhase.ALLOCATION,
            snippets=("no remaining base", "Enlarge the region"),
        )

    def test_coloring_exhausted(self):
        builder = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        for index in range(257):
            src = builder.add_page(Page(space=va))
            dst = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80010000 + index * 0x1000)))
            builder.add_mapping(
                Mapping(
                    src=src,
                    dst=dst,
                    pt_nodes={LEAF: PTNode(attrs=_leaf_attrs()), 2: PTNode(attrs={"a": index})},
                )
            )
        error = self._raises(AddressSpaceExhausted, builder.build)
        self._assert_good(
            error,
            FailureKind.COLORING_EXHAUSTED,
            phase=FailurePhase.COLORING,
            snippets=("too few page-table slots", "wider paging mode"),
        )

    def test_search_exhausted(self):
        class Budget16(BatchAllocationStrategy):
            _BACKTRACK_BUDGET = 16

        space = Space(paging_mode=RV.RiscvPagingModes.SV39)
        slot = 0x1000
        addrgen = AddrGen(
            RandNum(seed=2),
            Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": hex(18 * slot), "cacheable": True, "configurable": True}}}),
        )
        addrgen.make_space_pool(space)
        for index in range(17):
            addrgen.reserve_memory(RV.AddressType.LINEAR, 0x80000000 + index * slot, slot, space_key=space)
        identity = AllocRequest(
            page=Page(space),
            addr_type=RV.AddressType.PHYSICAL,
            size=slot,
            space_key=space,
            addr=AddrSpec(and_mask=~(slot - 1), bits=64),
        )
        error = self._raises(PlanningExhausted, lambda: Budget16().solve([identity], [], addrgen, RandNum(seed=2)))
        self._assert_good(
            error,
            FailureKind.SEARCH_EXHAUSTED,
            phase=FailurePhase.PLANNING,
            snippets=("backtracking limit",),
        )

    def test_joint_planning(self):
        def attempt(_addrgen, policy):
            raise AllocationConflict(f"{policy.value} failed", kind=FailureKind.CLAIM_OVERLAP, phase=FailurePhase.ALLOCATION, summary="pinned overlap", reason="overlap", hints=("move it",))

        error = self._raises(JointPlanningError, lambda: JointPlanner(_addrgen()).solve(attempt))
        self._assert_good(
            error,
            FailureKind.JOINT_PLANNING,
            phase=FailurePhase.PLANNING,
            snippets=("Every attempted policy failed", "preferred"),
        )

    def test_pte_slot(self):
        page_map = PageMap(paging_mode=RV.RiscvPagingModes.SV39, featmgr=PagingParams(), addrgen=None)  # type: ignore[arg-type]
        page_map.pinned_sptbr = 0x100000
        page_map.initialize()
        first = WalkerPage(page_map=page_map, featmgr=PagingParams(), addrgen=None)  # type: ignore[arg-type]
        first.lin_addr = 0x40200000
        first.phys_addr = 0x80010000
        first.pinned_frame_bases.update({2: 0x110000, 1: 0x120000})
        second = WalkerPage(page_map=page_map, featmgr=PagingParams(), addrgen=None)  # type: ignore[arg-type]
        second.lin_addr = 0x40201000
        second.phys_addr = 0x80020000
        second.pinned_frame_bases.update({2: 0x110000, 1: 0x130000})
        page_map.add_page(first)
        page_map.add_page(second)
        error = self._raises(ConstraintConflict, lambda: page_map.create_pagetables(RandNum(seed=1)))
        self._assert_good(
            error,
            FailureKind.PTE_SLOT,
            phase=FailurePhase.EMISSION,
            snippets=("two different child-table frames",),
        )

    def test_leaf_deeper(self):
        page_map = PageMap(paging_mode=RV.RiscvPagingModes.SV39, featmgr=PagingParams(), addrgen=None)  # type: ignore[arg-type]
        page_map.pinned_sptbr = 0x100000
        page_map.initialize()
        coarse = WalkerPage(page_map=page_map, featmgr=PagingParams(), addrgen=None, pagesize=RV.RiscvPageSizes.S2MB)  # type: ignore[arg-type]
        coarse.lin_addr = 0x400000
        coarse.phys_addr = 0x80000000
        coarse.pinned_frame_bases[2] = 0x110000
        deeper = WalkerPage(page_map=page_map, featmgr=PagingParams(), addrgen=None, pagesize=RV.RiscvPageSizes.S4KB)  # type: ignore[arg-type]
        deeper.lin_addr = 0x401000
        deeper.phys_addr = 0x90000000
        deeper.pinned_frame_bases.update({2: 0x110000, 1: 0x120000})
        page_map.add_page(coarse)
        page_map.add_page(deeper)
        error = self._raises(ConstraintConflict, lambda: page_map.create_pagetables(RandNum(seed=1)))
        self._assert_good(
            error,
            FailureKind.LEAF_DEEPER,
            phase=FailurePhase.EMISSION,
            snippets=("coarse leaf blocks a finer translation",),
        )

    def test_frame_overpacked(self):
        featmgr = PagingParams(physical_addr_bits=56)
        table = PTTable(base_addr=0x1000, capacity=2)
        for index in range(2):
            attr = PTAttrs(rng=RandNum(seed=1), featmgr=featmgr, level=0, leaf=True)
            table.insert_entry(PTEntry(basetable=PTTable(0x2000 + index * 0x1000, leaf=True), pt_attr=attr, level=0), index=index)
        attr = PTAttrs(rng=RandNum(seed=1), featmgr=featmgr, level=0, leaf=True)
        error = self._raises(
            ConstraintConflict,
            lambda: table.insert_entry(PTEntry(basetable=PTTable(0x9000, leaf=True), pt_attr=attr, level=0), index=2),
        )
        self._assert_good(
            error,
            FailureKind.FRAME_OVERPACKED,
            phase=FailurePhase.EMISSION,
            snippets=("More distinct PTE slots", "separate page-table frame"),
        )

    def test_gstage_identity(self):
        page_map = PageMap(paging_mode=RV.RiscvPagingModes.SV39, featmgr=PagingParams(), addrgen=None)  # type: ignore[arg-type]
        page_map.add_raw_pt_page(0x2000, 0x80002000, pagesize=RV.RiscvPageSizes.S4KB)
        error = self._raises(
            ConstraintConflict,
            lambda: page_map.add_raw_pt_page(0x2000, 0x80003000, pagesize=RV.RiscvPageSizes.S4KB),
        )
        self._assert_good(
            error,
            FailureKind.GSTAGE_IDENTITY,
            phase=FailurePhase.EMISSION,
            snippets=("two different G-stage identity mappings",),
        )

    def test_offset_inside_superpage_reports_leaf_pointer_through_planner(self):
        builder = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        large_src = builder.add_page(Page(space=va, pagesize=RV.RiscvPageSizes.S2MB))
        large_dst = builder.add_page(Page(space=builder.phys, pagesize=RV.RiscvPageSizes.S2MB))
        small_src = builder.add_page(Page(space=va, addr=AddrSpec(relation=OffsetFrom(large_src, 0x1000))))
        small_dst = builder.add_page(Page(space=builder.phys, addr=AddrSpec(relation=OffsetFrom(large_dst, 0x1000))))
        builder.add_mapping(Mapping(src=large_src, dst=large_dst, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
        builder.add_mapping(Mapping(src=small_src, dst=small_dst, pt_nodes={LEAF: PTNode(attrs=_leaf_attrs())}))
        error = self._raises(JointPlanningError, builder.build)
        nested = error.failures[0][1]
        self.assertEqual(nested.kind, FailureKind.LEAF_POINTER)
        self._assert_good(
            error,
            FailureKind.JOINT_PLANNING,
            phase=FailurePhase.PLANNING,
            snippets=("topology.leaf_pointer", "one PTE cannot be both"),
        )

    def test_every_production_failure_kind_is_exercised(self):
        unused = {
            FailureKind.CONSTRAINT,
            FailureKind.ADDRESS_SPACE_EXHAUSTED,
        }
        exercised = {
            FailureKind.DUPLICATE_SOURCE,
            FailureKind.LEAF_LEAF,
            FailureKind.POINTER_POINTER,
            FailureKind.LEAF_POINTER,
            FailureKind.REQUIRED_TRANSLATION,
            FailureKind.MISSING_GSTAGE_TRANSLATION,
            FailureKind.CLAIM_OVERLAP,
            FailureKind.UNSATISFIABLE,
            FailureKind.RELATION_CYCLE,
            FailureKind.REGION_EXHAUSTED,
            FailureKind.COLORING_EXHAUSTED,
            FailureKind.SEARCH_EXHAUSTED,
            FailureKind.JOINT_PLANNING,
            FailureKind.PTE_SLOT,
            FailureKind.LEAF_DEEPER,
            FailureKind.FRAME_OVERPACKED,
            FailureKind.GSTAGE_IDENTITY,
        }
        self.assertEqual(set(FailureKind) - unused, exercised)


if __name__ == "__main__":
    unittest.main()
