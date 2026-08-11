# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.layout import (
    IntentProvenance,
    LeafClaim,
    LeafContract,
    LeafSpan,
    TopologyConflict,
    plan_topology,
)
from riescue.riemap.memory import Memory
from riescue.riemap.request import Mapping, Page, Space, Stage


class TestNodeDemandPlanner(unittest.TestCase):
    def test_pages_with_shared_prefix_share_demands(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        a, b = Page(va), Page(va)
        mappings = [Mapping(a, Page(pa)), Mapping(b, Page(pa))]

        demands = plan_topology(
            mappings,
            {a: 0x1000, b: 0x2000},
        ).frame_demands()

        self.assertEqual({d.key.level for d in demands}, {1, 2})
        self.assertTrue(all(d.mappings == mappings for d in demands))

    def test_distinct_root_indices_produce_distinct_tries(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        a, b = Page(va), Page(va)
        mappings = [Mapping(a, Page(pa)), Mapping(b, Page(pa))]

        demands = plan_topology(
            mappings,
            {a: 0, b: 1 << 30},
        ).frame_demands()

        level2 = [d for d in demands if d.key.level == 2]
        self.assertEqual(len(level2), 2)
        self.assertTrue(all(len(d.mappings) == 1 for d in level2))

    def test_builder_emits_without_late_frame_draws(self):
        memory = Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x2000000", "cacheable": True, "configurable": True}}})
        builder = PageTableBuilder(RandNum(seed=7), memory)
        va = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        source = builder.add_page(Page(va))
        target = builder.add_page(Page(builder.phys))
        builder.add_mapping(Mapping(source, target))

        result = builder.build()
        self.assertIsNotNone(result.address_of(source))
        self.assertGreaterEqual(len(builder._planned_node_frames), 2)


class TestTopologyPlan(unittest.TestCase):
    def _mapping(self, space, phys, pagesize):
        return Mapping(
            Page(space, pagesize=pagesize),
            Page(phys, pagesize=pagesize),
        )

    def test_leaf_pointer_conflict_is_order_independent(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        large = self._mapping(va, pa, RV.RiscvPageSizes.S2MB)
        small = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        addresses = {
            large.src: 0x400000,
            large.dst: 0x80000000,
            small.src: 0x401000,
            small.dst: 0x90000000,
        }

        for mappings in ([large, small], [small, large]):
            with (
                self.subTest(order=mappings),
                self.assertRaisesRegex(
                    ValueError,
                    "leaf/pointer conflict",
                ),
            ):
                plan_topology(mappings, addresses)

    def test_explicit_coarse_identity_conflicts_with_explicit_child(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        large = self._mapping(va, pa, RV.RiscvPageSizes.S2MB)
        small = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        addresses = {
            large.src: 0x400000,
            large.dst: 0x400000,
            small.src: 0x401000,
            small.dst: 0x401000,
        }

        with self.assertRaises(TopologyConflict):
            plan_topology([small, large], addresses)

    def test_explicit_child_suppresses_conditional_coarse_identity(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        synthetic = self._mapping(va, pa, RV.RiscvPageSizes.S2MB)
        explicit = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        addresses = {
            synthetic.src: 0x400000,
            synthetic.dst: 0x400000,
            explicit.src: 0x401000,
            explicit.dst: 0x401000,
        }
        provenance = {
            synthetic: IntentProvenance.SYNTHETIC_FRAME,
            explicit: IntentProvenance.EXPLICIT,
        }

        for mappings in ([synthetic, explicit], [explicit, synthetic]):
            with self.subTest(order=mappings):
                plan = plan_topology(
                    mappings,
                    addresses,
                    provenance=provenance,
                    required_addresses={
                        va: {
                            0x401000,
                            0x402000,
                        }
                    },
                )
                self.assertIn(synthetic, plan.suppressed_mappings)
                self.assertNotIn(synthetic, plan.reachable_mappings())
                self.assertIn(explicit, plan.reachable_mappings())
                self.assertEqual(
                    plan.uncovered_required_addresses(),
                    {va: {0x402000}},
                )
                with self.assertRaisesRegex(
                    TopologyConflict,
                    "required structural addresses",
                ):
                    plan.validate_required_coverage()

    def test_leaf_span_uses_canonical_aligned_gpa(self):
        g = Space(
            paging_mode=RV.RiscvPagingModes.SV39,
            stage=Stage.G,
        )

        span = LeafSpan.from_address(
            g,
            0x8001_8000,
            RV.RiscvPageSizes.S64KB,
        )

        self.assertEqual(
            (span.start, span.end),
            (0x8001_0000, 0x8002_0000),
        )

    def test_64kb_leaf_claims_all_sixteen_slots(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        mapping = self._mapping(
            va,
            pa,
            RV.RiscvPageSizes.S64KB,
        )
        addresses = {
            mapping.src: 0x10000,
            mapping.dst: 0x80010000,
        }

        plan = plan_topology([mapping], addresses)
        leaves = [claim for claim in plan.slots.values() if isinstance(claim, LeafClaim)]

        self.assertEqual(len(leaves), 16)

    def test_equivalent_aliases_merge_complete_leaf_contracts(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        first = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        second = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        addresses = {
            first.src: 0x2000,
            first.dst: 0x80002000,
            second.src: 0x2000,
            second.dst: 0x80002000,
        }
        contract = LeafContract(
            target=0x80002000,
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs=(("r", 1), ("w", 1)),
        )

        plan = plan_topology(
            [first, second],
            addresses,
            contracts={
                first: contract,
                second: contract,
            },
        )
        leaves = [claim for claim in plan.slots.values() if isinstance(claim, LeafClaim)]
        self.assertEqual(len(leaves), 1)
        self.assertEqual(leaves[0].mappings, [first, second])

    def test_incompatible_alias_contracts_are_typed_conflicts(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        first = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        second = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        addresses = {
            first.src: 0x3000,
            first.dst: 0x80003000,
            second.src: 0x3000,
            second.dst: 0x80003000,
        }

        with self.assertRaises(TopologyConflict) as conflict:
            plan_topology(
                [first, second],
                addresses,
                contracts={
                    first: LeafContract(
                        target=0x80003000,
                        pagesize=RV.RiscvPageSizes.S4KB,
                        attrs=(("r", 1),),
                    ),
                    second: LeafContract(
                        target=0x80003000,
                        pagesize=RV.RiscvPageSizes.S4KB,
                        attrs=(("r", 0),),
                    ),
                },
            )
        self.assertEqual(
            set(conflict.exception.mappings),
            {first, second},
        )

    def test_duplicate_source_with_conflicting_contracts_raises(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        source = Page(va)
        target = Page(pa)
        first = Mapping(source, target)
        second = Mapping(source, target)
        addresses = {
            source: 0x3000,
            target: 0x80003000,
        }

        with self.assertRaises(TopologyConflict) as conflict:
            plan_topology(
                [first, second],
                addresses,
                contracts={
                    first: LeafContract(
                        target=0x80003000,
                        pagesize=RV.RiscvPageSizes.S4KB,
                        attrs=(("x", 0),),
                    ),
                    second: LeafContract(
                        target=0x80003000,
                        pagesize=RV.RiscvPageSizes.S4KB,
                        attrs=(("x", 1),),
                    ),
                },
            )
        self.assertEqual(set(conflict.exception.mappings), {first, second})

    def test_compatible_shared_pointer_does_not_suppress_conditional_mapping(self):
        va = Space(paging_mode=RV.RiscvPagingModes.SV39)
        pa = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        explicit = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        structural = self._mapping(va, pa, RV.RiscvPageSizes.S4KB)
        addresses = {
            explicit.src: 0x1000,
            explicit.dst: 0x80001000,
            structural.src: 0x2000,
            structural.dst: 0x80002000,
        }

        plan = plan_topology(
            [explicit, structural],
            addresses,
            provenance={
                explicit: IntentProvenance.EXPLICIT,
                structural: IntentProvenance.SYNTHETIC_FRAME,
            },
        )

        self.assertNotIn(structural, plan.suppressed_mappings)
        leaves = [claim for claim in plan.slots.values() if isinstance(claim, LeafClaim)]
        self.assertEqual(
            {claim.mappings[0] for claim in leaves},
            {explicit, structural},
        )


if __name__ == "__main__":
    unittest.main()
