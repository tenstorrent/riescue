# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the constraint-based mapping page table builder."""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.addrgen.types import ExcludedRegion
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import AddrSpec, Choice, DerivedFrom, LEAF, Mapping, MemoryRegion, OffsetFrom, Page, PTGPage, PTNode, SameAs, Space, Stage
from riescue.riemap import resolve
from tests.riemap.root_policy import (
    declare_vs_root_identities,
    declare_vs_root_identity,
)


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _leaf_attrs():
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1}


def _leaf_nodes():
    """The leaf PTE bits as a ``pt_nodes`` dict (base bits folded onto the leaf)."""
    return {LEAF: PTNode(attrs=_leaf_attrs())}


def _vs_leaf_nodes():
    return {
        **_leaf_nodes(),
        0: PTNode(page=PTGPage(identity=True)),
        1: PTNode(page=PTGPage(identity=True)),
    }


def _nodes_from_attrs(attrs, pagesize=RV.RiscvPageSizes.S4KB):
    """Split a resolved attrs dict the way the producers do: base + {base}_level{n}
    fold into pt_nodes; the {base}_level{vs}_glevel{g} forces become PTGPages on the
    matching VS levels."""
    leaf = RV.RiscvPageSizes.pt_leaf_level(pagesize)
    levels = resolve.pt_node_levels_with_leaf(attrs, leaf)
    pt_nodes = {(LEAF if lvl == leaf else lvl): PTNode(attrs=dict(a)) for lvl, a in levels.items()}
    resolve.attach_gstage_ptgpages(pt_nodes, attrs, leaf, None)
    return pt_nodes


def _count_ptes(table):
    total = 0
    for entry in table.table.values():
        total += 1
        if not entry.leaf and entry.basetable:
            total += _count_ptes(entry.basetable)
    return total


class TestSingleStageMapping(unittest.TestCase):
    def _build(self, seed=1):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        return b, va_space

    def test_va_translates_to_pa(self):
        b, va_space = self._build()
        p0 = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        p0_pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=p0, dst=p0_pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        sr = result.space(va_space)
        self.assertIsNotNone(sr.root_addr)
        self.assertGreater(_count_ptes(sr._page_map.basetable), 0)
        _steps, translated = sr.walk(0x1000)
        self.assertEqual(translated, 0x80010000)

    def test_free_pages_get_addresses(self):
        b, va_space = self._build()
        p0 = b.add_page(Page(space=va_space))
        p0_pa = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=p0, dst=p0_pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        va, pa = result.address_of(p0)
        self.assertIsNotNone(va)
        self.assertIsNotNone(pa)
        _steps, translated = result.space(va_space).walk(va)
        self.assertEqual(translated, pa)

    def test_same_va_in_two_spaces_different_pa(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_a = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        va_b = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        a = b.add_page(Page(space=va_a, addr=AddrSpec(exact=0x40000000)))
        bp = b.add_page(Page(space=va_b, addr=AddrSpec(exact=0x40000000)))
        a_pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b_pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=a, dst=a_pa, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=bp, dst=b_pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        _s, ta = result.space(va_a).walk(0x40000000)
        _s, tb = result.space(va_b).walk(0x40000000)
        self.assertEqual(ta, 0x80010000)
        self.assertEqual(tb, 0x80020000)


class TestTwoStageIdentity(unittest.TestCase):
    """VA -> GPA -> HPA where the g-stage is identity, expressed purely by constraints:
    the GPA destination is constrained ``SameAs`` its HPA (GPA == HPA), so the consumer's
    GPA -> HPA leaf is identity and the structural emitter makes the intermediate g-stage
    PT nodes identity too. This is how RiescueD keeps its identity G-stage with no flag."""

    def test_va_gpa_identity_builds_both_tables(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        va_space = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, gpa_space)
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80030000)))
        # The GPA is constrained SameAs its HPA -> GPA == HPA (identity g-stage).
        g = b.add_page(Page(space=gpa_space, addr=AddrSpec(relation=SameAs(pa))))
        b.add_mapping(Mapping(src=p, dst=g, pt_nodes=_vs_leaf_nodes()))
        b.add_mapping(Mapping(src=g, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        va_sr = result.space(va_space)
        gpa_sr = result.space(gpa_space)
        self.assertGreater(_count_ptes(va_sr._page_map.basetable), 0)
        self.assertGreater(_count_ptes(gpa_sr._page_map.basetable), 0, "identity G table should be populated")
        # Stage 1: VA -> GPA (== the PA value, since g == pa).
        _steps, gpa = va_sr.walk(0x2000)
        self.assertEqual(gpa, 0x80030000)
        # Stage 2 (identity): GPA -> HPA == GPA.
        _steps, hpa = gpa_sr.walk(0x80030000)
        self.assertEqual(hpa, 0x80030000)

    def test_free_identity_anchor_is_constrained_to_gpa_width(self):
        """The HPA anchor of GPA==HPA must be drawable in the supported G-stage domain."""
        for seed in range(20):
            with self.subTest(seed=seed):
                b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
                gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
                va_space = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, gpa_space)
                va = b.add_page(Page(space=va_space))
                hpa = b.add_page(Page(space=b.phys))
                gpa = b.add_page(Page(space=gpa_space, addr=AddrSpec(relation=SameAs(hpa))))
                b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes=_vs_leaf_nodes()))
                b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes=_leaf_nodes()))

                result = b.build()
                _, resolved_gpa = result.address_of(gpa)
                self.assertLess(resolved_gpa, 1 << 39)
                self.assertEqual(result.space(gpa_space).walk(resolved_gpa)[1], resolved_gpa)


class TestDeclarationValidation(unittest.TestCase):
    def test_same_source_cannot_map_to_different_destinations(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.VS))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x4000)))
        dst_a = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        dst_b = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80020000)))
        b.add_mapping(Mapping(src=src, dst=dst_a, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=src, dst=dst_b, pt_nodes=_leaf_nodes()))

        with self.assertRaisesRegex(ValueError, "source.*different destination"):
            b.build()

    def test_duplicate_va_pa_cannot_disagree_on_leaf_attributes(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        for executable in (0, 1):
            src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x4000)))
            attrs = _leaf_attrs()
            attrs["x"] = executable
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs=attrs)}))

        with self.assertRaisesRegex(ValueError, "duplicate.*attributes"):
            b.build()

    def test_repeated_source_cannot_disagree_on_leaf_attributes(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x4000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs={**_leaf_attrs(), "x": 0})}))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs={**_leaf_attrs(), "x": 1})}))

        with self.assertRaisesRegex(ValueError, "source.*attributes|conflicting.*attributes"):
            b.build()

    def test_exact_superpage_must_be_aligned(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(
            Page(
                space=va_space,
                pagesize=RV.RiscvPageSizes.S2MB,
                addr=AddrSpec(exact=0x201000),
            )
        )
        dst = b.add_page(
            Page(
                space=b.phys,
                pagesize=RV.RiscvPageSizes.S2MB,
                addr=AddrSpec(exact=0x80200000),
            )
        )
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))

        with self.assertRaisesRegex(AddrGenError, "align"):
            b.build()

    def test_exact_superpage_destination_must_be_aligned(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, pagesize=RV.RiscvPageSizes.S2MB, addr=AddrSpec(exact=0x400000)))
        # The destination page's own 4 KiB geometry permits this address, but the
        # source's 2 MiB leaf PTE still requires its target PPN to be 2 MiB-aligned.
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80201000)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))

        with self.assertRaisesRegex(AddrGenError, "align"):
            b.build()

    def test_pagesize_must_be_supported_by_paging_mode(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV32))
        src = b.add_page(Page(space=va_space, pagesize=RV.RiscvPageSizes.S2MB))
        dst = b.add_page(Page(space=b.phys, pagesize=RV.RiscvPageSizes.S2MB))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))

        with self.assertRaisesRegex(ValueError, "pagesize.*SV32"):
            b.build()

    def test_mapping_pages_must_be_declared(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        declared = b.add_page(Page(space=va_space))
        undeclared = Page(space=b.phys)

        with self.assertRaisesRegex(ValueError, "not.*added|undeclared"):
            b.add_mapping(Mapping(src=declared, dst=undeclared))

    def test_pt_node_levels_must_exist_in_the_source_mode(self):
        for level in (-1, 3):
            with self.subTest(level=level):
                b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
                va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
                src = b.add_page(Page(space=va_space))
                dst = b.add_page(Page(space=b.phys))
                with self.assertRaisesRegex(ValueError, "level"):
                    b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={level: PTNode()}))

    def test_pt_node_attribute_names_are_validated(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space))
        dst = b.add_page(Page(space=b.phys))

        with self.assertRaisesRegex(ValueError, "attribute|write"):
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes={LEAF: PTNode(attrs={"write": 1})}))

    def test_leaf_sentinel_cannot_conflict_with_numeric_leaf(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space))
        dst = b.add_page(Page(space=b.phys))

        with self.assertRaisesRegex(ValueError, "leaf|level"):
            b.add_mapping(
                Mapping(
                    src=src,
                    dst=dst,
                    pt_nodes={
                        LEAF: PTNode(attrs={"x": 1}),
                        0: PTNode(attrs={"x": 0}),
                    },
                )
            )


class TestDirectDeclarationGeometry(unittest.TestCase):
    def test_space_secure_probability_is_a_percentage(self):
        for probability in (-1, 101):
            with self.subTest(probability=probability), self.assertRaisesRegex(ValueError, "probability"):
                Space(paging_mode=RV.RiscvPagingModes.SV39, secure_pt_probability=probability)

    def test_memory_region_geometry_is_positive_and_aligned(self):
        for kwargs in ({"size": 0}, {"size": -1}, {"size": 0x1000, "align": 0}, {"size": 0x1000, "align": 0x3000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                MemoryRegion(**kwargs)

    def test_page_reservation_size_is_positive(self):
        space = Space(paging_mode=RV.RiscvPagingModes.SV39)
        for reserve_size in (0, -1):
            with self.subTest(reserve_size=reserve_size), self.assertRaisesRegex(ValueError, "reserve_size"):
                Page(space=space, reserve_size=reserve_size)


class TestWholeNodeChoice(unittest.TestCase):
    def test_mapping_valued_choice_builds(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x4000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        node_choice = Choice(
            preferred={"v": 1, "r": 0, "w": 0},
            alternatives=({"v": 1, "r": 1, "w": 0},),
        )
        b.add_mapping(
            Mapping(
                src=src,
                dst=dst,
                pt_nodes={
                    1: PTNode(choice=node_choice),
                    LEAF: PTNode(attrs=_leaf_attrs()),
                },
            )
        )

        self.assertEqual(b.build().space(va_space).walk(0x4000)[1], 0x80010000)


class TestBuilderLifecycle(unittest.TestCase):
    def test_failed_build_can_be_retried(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        original = b._resolve_targets
        b._resolve_targets = lambda: (_ for _ in ()).throw(RuntimeError("injected failure"))
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            b.build()
        b._resolve_targets = original

        self.assertEqual(b.build().space(va_space).walk(0x1000)[1], 0x80010000)

    def test_declarations_cannot_change_after_successful_build(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space))
        dst = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        b.build()

        mutations = (
            lambda: b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV48)),
            lambda: b.add_page(Page(space=b.phys)),
            lambda: b.add_mapping(Mapping(src=src, dst=dst)),
            lambda: b.add_region(MemoryRegion(size=0x1000)),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaisesRegex(RuntimeError, "build|built"):
                mutate()


class TestImmutableDeclarations(unittest.TestCase):
    def test_ptnode_copies_and_freezes_attribute_mapping(self):
        attrs = {"v": 1}
        node = PTNode(attrs=attrs)
        attrs["v"] = 0

        self.assertEqual(node.attrs["v"], 1)
        with self.assertRaises(TypeError):
            node.attrs["v"] = 0

    def test_mapping_copies_and_freezes_node_mapping(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = Page(space=space)
        dst = Page(space=b.phys)
        nodes = {LEAF: PTNode(attrs=_leaf_attrs())}
        mapping = Mapping(src=src, dst=dst, pt_nodes=nodes)
        nodes.clear()

        self.assertIn(LEAF, mapping.pt_nodes)
        with self.assertRaises(TypeError):
            mapping.pt_nodes[0] = PTNode()

    def test_addrspec_copies_and_freezes_qualifiers(self):
        qualifiers = {RV.AddressQualifiers.ADDRESS_DRAM}
        spec = AddrSpec(qualifiers=qualifiers)
        qualifiers.clear()

        self.assertIn(RV.AddressQualifiers.ADDRESS_DRAM, spec.qualifiers)
        with self.assertRaises(AttributeError):
            spec.qualifiers.add(RV.AddressQualifiers.ADDRESS_MMIO)


class TestTwoStageNonIdentity(unittest.TestCase):
    """VA -> GPA -> PA where the GPA space is NOT identity: the consumer declares an
    explicit GPA -> PA leaf, so the G table translates the GPA to a different PA. The VS
    table's structural PT nodes are still identity-mapped in the G space so the walk
    reaches the leaf, but the leaf itself is the consumer's mapping."""

    def test_explicit_gpa_leaf_translates_to_different_pa(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))  # non-identity
        va_space = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, gpa_space)
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        g = b.add_page(Page(space=gpa_space, addr=AddrSpec(exact=0x40000000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000)))
        b.add_mapping(Mapping(src=p, dst=g, pt_nodes=_vs_leaf_nodes()))
        b.add_mapping(Mapping(src=g, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        # Stage 1: VA -> GPA.
        _s, gpa = result.space(va_space).walk(0x2000)
        self.assertEqual(gpa, 0x40000000)
        # Stage 2 (non-identity): GPA -> PA, and PA != GPA.
        _s, pa_out = result.space(gpa_space).walk(0x40000000)
        self.assertEqual(pa_out, 0x80040000)


class TestSwitchHgatp(unittest.TestCase):
    """One VS-stage table (same vsatp) walked under two G-stages (switch hgatp): the same
    VA->GPA declared once per G space, and the shared GPA mapped to a different PA in each
    G table. This is the TLB-testing pattern the linkage-based design deferred."""

    def test_shared_gpa_maps_to_different_pa_per_gstage(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g0_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        g1_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        va_space = declare_vs_root_identities(
            b,
            RV.RiscvPagingModes.SV39,
            g0_space,
            g1_space,
        )
        # One VA, one shared GPA value, present in both G spaces.
        va_p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x3000)))
        gpa0 = b.add_page(Page(space=g0_space, addr=AddrSpec(exact=0x40000000)))
        gpa1 = b.add_page(Page(space=g1_space, addr=AddrSpec(exact=0x40000000)))
        pa0 = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80050000)))
        pa1 = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80060000)))
        # Same VA->GPA, once per G space -> the VS table fans its PT nodes into both.
        b.add_mapping(Mapping(src=va_p, dst=gpa0, pt_nodes=_vs_leaf_nodes()))
        b.add_mapping(Mapping(src=va_p, dst=gpa1, pt_nodes=_vs_leaf_nodes()))
        # The shared GPA resolves to a different PA in each G table.
        b.add_mapping(Mapping(src=gpa0, dst=pa0, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=gpa1, dst=pa1, pt_nodes=_leaf_nodes()))
        result = b.build()
        # One VS-stage leaf: VA -> the shared GPA.
        _s, gpa = result.space(va_space).walk(0x3000)
        self.assertEqual(gpa, 0x40000000)
        # Same GPA, different PA per G stage.
        _s, out0 = result.space(g0_space).walk(0x40000000)
        _s, out1 = result.space(g1_space).walk(0x40000000)
        self.assertEqual(out0, 0x80050000)
        self.assertEqual(out1, 0x80060000)


class TestGstageSourceGpaCanonical(unittest.TestCase):
    """A g-stage source space (bare VS: the guest walks hgatp directly) draws GPAs, not
    VAs. A GPA with the narrow mode's sign bit set (bit 38 in sv39) must zero-extend to
    the g-stage width, not sign-extend, or the leaf lands at a corrupted address."""

    def test_gpa_above_sign_bit_zero_extends(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        # GPA with bit 38 set (>= 2^38): sign bit under sv39, but a GPA must zero-extend.
        gpa = b.add_page(Page(space=g_space, addr=AddrSpec(exact=0x4000000000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000)))
        b.add_mapping(Mapping(src=gpa, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        # Read-back GPA must stay zero-extended; sign-extension would corrupt bits 39-63.
        gpa_addr, hpa_addr = result.address_of(gpa)
        self.assertEqual(gpa_addr, 0x4000000000)
        self.assertEqual(hpa_addr, 0x80040000)
        _s, walked = result.space(g_space).walk(0x4000000000)
        self.assertEqual(walked, 0x80040000)

    def test_identity_free_draw_stays_within_gstage_width(self):
        # A bare-VS identity page (dst SameAs src) draws a single physical value that
        # doubles as the GPA in the g-stage source space. With DRAM extending well past
        # 2^39 the free physical draw could land above the sv39 g-stage input width, and
        # canonical_va would then truncate the GPA so GPA != HPA -- breaking identity for
        # OS structures shared by raw pointer between M-mode (HPA) and VS-mode (GPA), e.g.
        # hart_context. The draw must be capped to the g-stage input width so GPA == HPA.
        gwidth = RV.RiscvPagingModes.linear_addr_bits(RV.RiscvPagingModes.SV39, gstage=True)
        for seed in range(8):
            b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
            g_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
            gpa = b.add_page(Page(space=g_space, addr=AddrSpec()))
            hpa = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(gpa))))
            b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes=_leaf_nodes()))
            result = b.build()
            gpa_addr, hpa_addr = result.address_of(gpa)
            self.assertEqual(gpa_addr, hpa_addr, f"identity broken (seed={seed}): GPA != HPA")
            self.assertLess(gpa_addr, 1 << gwidth, f"identity draw exceeds g-stage width (seed={seed})")
            _s, walked = result.space(g_space).walk(gpa_addr)
            self.assertEqual(walked, hpa_addr, f"g-stage walk mismatch (seed={seed})")


class TestBareGstageLeafAttrs(unittest.TestCase):
    """A bare-VS g-stage source (the guest walks hgatp directly) is itself the GPA -> HPA
    g-stage leaf. That leaf is always reached as a user-level access, so its U/R/W/X/A/D
    must default to 1 -- the page's plain base VS bits (a supervisor / non-readable guest
    page) must not reach it -- while an explicit ``{base}_level{leaf}`` force (a g-stage
    permission / invalid-PTE fault test) must still win."""

    def _leaf_entry(self, sr, addr):
        from riescue.lib import common

        mode = sr._page_map.paging_mode
        table = sr._page_map.basetable
        for level in range(RV.RiscvPagingModes.max_levels(mode) - 1, -1, -1):
            idx = common.bits(addr, *RV.RiscvPagingModes.index_bits(mode, level))
            if table is None or idx not in table.table:
                return None
            entry = table.table[idx]
            if entry.basetable is not None and not entry.leaf:
                table = entry.basetable
                continue
            return entry
        return None

    def _build_leaf(self, mapping_attrs, pagesize=RV.RiscvPageSizes.S4KB, gpa=None):
        if gpa is None:
            gpa = RV.RiscvPageSizes.memory(pagesize)  # one pagesize in, so it is size-aligned
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G, priv_mode=RV.RiscvPrivileges.SUPER))
        gpa_page = b.add_page(Page(space=g_space, pagesize=pagesize, addr=AddrSpec(exact=gpa)))
        pa = b.add_page(Page(space=b.phys, pagesize=pagesize, addr=AddrSpec(exact=0x80200000)))
        pt_nodes = _nodes_from_attrs(mapping_attrs, pagesize)
        # No g-stage geometry is declared: a bare-VS g-stage source IS its own GPA -> HPA leaf,
        # so the builder derives the g-stage leaf pagesize from ``src.pagesize``.
        b.add_mapping(Mapping(src=gpa_page, dst=pa, pt_nodes=pt_nodes))
        result = b.build()
        entry = self._leaf_entry(result.space(g_space), gpa)
        self.assertIsNotNone(entry, "no leaf PTE reached for the bare-VS g-stage GPA")
        return entry.pt_attr

    def _vs_intent_attrs(self, pagesize, **base):
        """Mimic what RiescueD hands the builder for a bare-VS page: base bits plus the
        single-index ``{base}_level{n}`` keys generator.randomize_pt_attrs derives from
        them. For R/W/X/A/D the leaf level carries the base value and non-leaf levels are 0;
        the valid bit is valid at every level (a live page). This is the one g-stage's
        declared permissions -- except the VS-stage u bit, which must not reach the leaf."""
        attrs = dict(base)
        leaf = RV.RiscvPageSizes.pt_leaf_level(pagesize)
        for bit in ("u", "r", "w", "x", "a", "d"):
            val = int(base.get(bit, 0))
            for level in range(5):
                attrs[f"{bit}_level{level}"] = val if level == leaf else 0
        vval = int(base.get("v", 1))
        for level in range(5):
            attrs[f"v_level{level}"] = vval
        return attrs

    def test_declared_permissions_are_honored(self):
        # Bare-VS has one stage, so the guest's declared R/W/X ARE the g-stage leaf's
        # permissions. A no-execute page (x=0) must keep x=0 so a fetch faults; U stays
        # user-reachable (hypervisor_paging_permissions_023/024 depend on this).
        attr = self._build_leaf(self._vs_intent_attrs(RV.RiscvPageSizes.S4KB, v=1, r=1, w=1, x=0, a=1, d=1))
        self.assertEqual(attr.r, 1)
        self.assertEqual(attr.w, 1)
        self.assertEqual(attr.x, 0, "declared no-execute g-stage leaf must stay x=0")
        self.assertEqual(attr.u, 1, "g-stage leaf is always user-reachable")

    def test_read_only_permission_is_honored(self):
        attr = self._build_leaf(self._vs_intent_attrs(RV.RiscvPageSizes.S4KB, v=1, r=1, w=0, x=0, a=1, d=1))
        self.assertEqual(attr.r, 1)
        self.assertEqual(attr.w, 0, "declared read-only g-stage leaf must stay w=0")
        self.assertEqual(attr.x, 0)
        self.assertEqual(attr.u, 1)

    def test_vs_stage_u_does_not_leak_to_leaf(self):
        # Regression: the VS-stage u bit (undeclared -> u_level{leaf}=0, or an explicit
        # supervisor u=0) must NOT make the g-stage leaf U=0 -- that faults every guest
        # access. A 2 MB bare-VS data page (lin1 in the hypervisor test_vs) took a load
        # guest-page fault this way. R/W stay as declared.
        for u_decl in ({}, {"u": 0}):  # undeclared u, and an explicit supervisor u=0
            for pagesize in (RV.RiscvPageSizes.S4KB, RV.RiscvPageSizes.S2MB):
                attrs = self._vs_intent_attrs(pagesize, v=1, r=1, w=1, x=1, a=1, d=1, **u_decl)
                attr = self._build_leaf(attrs, pagesize=pagesize)
                self.assertEqual(attr.u, 1, f"g-stage leaf must stay user-reachable ({pagesize}, u={u_decl})")
                self.assertEqual(attr.r, 1)
                self.assertEqual(attr.w, 1)

    def test_unforced_leaf_is_fully_permissive(self):
        # A fully-permissive guest page (R=W=X=A=D=1, u undeclared) yields a fully
        # permissive, user-reachable g-stage leaf.
        attr = self._build_leaf(self._vs_intent_attrs(RV.RiscvPageSizes.S4KB, v=1, r=1, w=1, x=1, a=1, d=1))
        for bit in ("u", "r", "w", "x", "a", "d", "v"):
            self.assertEqual(getattr(attr, bit), 1, f"{bit} must be 1")

    def test_explicit_user_fault_force_is_preserved(self):
        # A genuine g-stage user-access fault test forces the leaf U=0 via the two-stage
        # glevel form (never the VS single-index); this must win over the user-reachable
        # default (finding #2).
        attr = self._build_leaf({"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, "u_level0_glevel0": 0})
        self.assertEqual(attr.u, 0, "forced g-stage user-fault leaf must stay u=0")

    def test_explicit_invalid_leaf_force_is_preserved(self):
        # A g-stage invalid-PTE fault test forces the leaf valid bit to 0 (single-index, the
        # one stage); the expected guest-page fault only fires if the leaf really is v=0.
        attrs = self._vs_intent_attrs(RV.RiscvPageSizes.S4KB, v=1, r=1, w=1, x=1, a=1, d=1)
        attrs["v_level0"] = 0  # invalidate only the leaf level; non-leaf pointers stay valid
        attr = self._build_leaf(attrs)
        self.assertEqual(attr.v, 0, "forced invalid g-stage leaf must stay v=0")


class TestExplicitRemapNotShadowed(unittest.TestCase):
    """A fixed structural superpage cannot silently disappear under a subpage."""

    def test_unrelated_explicit_gstage_subpage_is_rejected_during_allocation(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        gpa_space = b.add_space(
            Space(
                paging_mode=RV.RiscvPagingModes.SV39,
                stage=Stage.G,
            )
        )
        va_space = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, gpa_space)
        broad_gpa = b.add_page(
            Page(
                space=gpa_space,
                pagesize=RV.RiscvPageSizes.S1GB,
                addr=AddrSpec(exact=0x40000000),
            )
        )
        broad_hpa = b.add_page(
            Page(
                space=b.phys,
                pagesize=RV.RiscvPageSizes.S1GB,
                addr=AddrSpec(exact=0x80000000),
            )
        )
        narrow_gpa = b.add_page(
            Page(
                space=gpa_space,
                addr=AddrSpec(exact=0x40001000),
            )
        )
        narrow_hpa = b.add_page(
            Page(
                space=b.phys,
                addr=AddrSpec(exact=0xC0000000),
            )
        )
        b.add_mapping(
            Mapping(
                src=broad_gpa,
                dst=broad_hpa,
                pt_nodes=_leaf_nodes(),
            )
        )
        b.add_mapping(
            Mapping(
                src=narrow_gpa,
                dst=narrow_hpa,
                pt_nodes=_leaf_nodes(),
            )
        )
        va = b.add_page(
            Page(
                space=va_space,
                addr=AddrSpec(exact=0x1000),
            )
        )
        b.add_mapping(
            Mapping(
                src=va,
                dst=broad_gpa,
                pt_nodes={
                    1: PTNode(page=narrow_gpa),
                    **_leaf_nodes(),
                },
            )
        )

        with self.assertRaisesRegex(
            AddrGenError,
            "incompatible backing or coverage claim",
        ):
            b.build()

    def test_fixed_structural_superpage_conflicts_with_explicit_subpage(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))  # non-identity
        va_space = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, gpa_space)
        # A VS mapping whose destination GPA is a 2MB page and which no consumer mapping
        # translates: the builder synthesizes its g-stage identity leaf as a 2MB superpage
        # spanning [0x40000000, 0x40200000). The geometry is declared as the destination page's
        # own pagesize -- there is no mapping-level g-stage size to set.
        covering = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        g_super = b.add_page(Page(space=gpa_space, pagesize=RV.RiscvPageSizes.S2MB, addr=AddrSpec(exact=0x80000000)))
        hpa_super = b.add_page(Page(space=b.phys, pagesize=RV.RiscvPageSizes.S2MB, addr=AddrSpec(exact=0x80000000)))
        b.add_mapping(Mapping(src=covering, dst=g_super, pt_nodes=_vs_leaf_nodes()))
        b.add_mapping(
            Mapping(
                src=g_super,
                dst=hpa_super,
                pt_nodes=_leaf_nodes(),
            )
        )
        # A second VS mapping whose GPA is pinned at a 4KB sub-page INSIDE that 2MB span, and
        # which the consumer does explicitly translate. The explicit leaf must win.
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        g = b.add_page(Page(space=gpa_space, addr=AddrSpec(exact=0x80001000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80400000)))
        b.add_mapping(Mapping(src=p, dst=g, pt_nodes=_vs_leaf_nodes()))
        b.add_mapping(Mapping(src=g, dst=pa, pt_nodes=_leaf_nodes()))
        with self.assertRaisesRegex(
            AddrGenError,
            "incompatible backing or coverage claim",
        ):
            b.build()


class TestSharedVaBitsCap(unittest.TestCase):
    """A SameAs VA group spanning differing paging-mode widths must draw a value that is
    canonical in every member space. The free draw is capped to (narrowest width - 1)
    bits so the narrow mode's sign bit is never set -- including when the root itself is
    the narrowest member (sv39 root, sv48 member)."""

    def test_root_narrowest_group_is_capped(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va39 = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))  # narrowest == root
        va48 = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV48))  # wider member
        root = b.add_page(Page(space=va39))
        member = b.add_page(Page(space=va48, addr=AddrSpec(relation=SameAs(root))))
        root_pa = b.add_page(Page(space=b.phys))
        member_pa = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=root, dst=root_pa, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=member, dst=member_pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        # Both members capped to sv39_width - 1 = 38 bits, so the sign bit can never be set.
        self.assertEqual(b._page_state[root].alloc_bits, 38)
        self.assertEqual(b._page_state[member].alloc_bits, 38)
        # The single drawn value is then identical-canonical in both spaces.
        self.assertEqual(result.address_of(root)[0], result.address_of(member)[0])

    def test_homogeneous_group_uncapped(self):
        # A SameAs group whose members share one paging-mode width needs no cap: each page
        # keeps its full width.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        space_a = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV48))
        space_b = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV48))
        root = b.add_page(Page(space=space_a))
        member = b.add_page(Page(space=space_b, addr=AddrSpec(relation=SameAs(root))))
        root_pa = b.add_page(Page(space=b.phys))
        member_pa = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=root, dst=root_pa, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=member, dst=member_pa, pt_nodes=_leaf_nodes()))
        b.build()
        full = RV.RiscvPagingModes.linear_addr_bits(RV.RiscvPagingModes.SV48)
        self.assertEqual(b._page_state[root].alloc_bits, full)
        self.assertEqual(b._page_state[member].alloc_bits, full)


class TestGpaSpacePools(unittest.TestCase):
    """G-stage spaces have their own GPA pools, like VA spaces: the same GPA may be
    held independently in two G spaces (each remapping it to its own PA), and GPAs
    do not consume the VA universe bare linear draws come from."""

    def test_same_gpa_in_two_gstage_spaces(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g_spaces = [b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G)) for _ in range(2)]
        for g_space, pa_val in zip(g_spaces, (0x80040000, 0x80080000)):
            gpa = b.add_page(Page(space=g_space, addr=AddrSpec(exact=0x40001000)))
            pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=pa_val)))
            b.add_mapping(Mapping(src=gpa, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        _s, pa0 = result.space(g_spaces[0]).walk(0x40001000)
        _s, pa1 = result.space(g_spaces[1]).walk(0x40001000)
        self.assertEqual(pa0, 0x80040000)
        self.assertEqual(pa1, 0x80080000)


class TestInRegionSuperpageAlignment(unittest.TestCase):
    """An in-region superpage member must keep its superpage alignment: the resolved
    alignment is folded into the spec's mask so the region placement step is the
    superpage size, not the default 4KB."""

    def test_in_region_2mb_page_stays_2mb_aligned(self):
        for seed in range(20):
            b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
            va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
            region = b.add_region(MemoryRegion(base=0x80000000, size=0x400000))
            p = b.add_page(Page(space=va_space, pagesize=RV.RiscvPageSizes.S2MB))
            pa = b.add_page(Page(space=b.phys, pagesize=RV.RiscvPageSizes.S2MB, addr=AddrSpec(region=region)))
            b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
            result = b.build()
            _va, pa_out = result.address_of(pa)
            self.assertEqual(pa_out % 0x200000, 0, f"seed {seed}: PA 0x{pa_out:x} not 2MB aligned")


class TestPhysSpaceAutoCreated(unittest.TestCase):
    """The engine owns the physical leaf domain: a consumer references ``builder.phys``
    for a leaf page without adding its own paging-DISABLE space."""

    def test_leaf_page_uses_auto_created_phys_space(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        _s, translated = result.space(va_space).walk(0x1000)
        self.assertEqual(translated, 0x80010000)
        # The leaf domain bears no page table, so it never appears in the read-back.
        self.assertNotIn(b.phys, {sr.space for sr in result.spaces()})

    def test_explicit_disable_space_still_allowed(self):
        # A second, distinct DISABLE space keeps working alongside builder.phys -- no
        # id collision is possible since spaces are identity-keyed objects.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        other_phys = b.add_space(Space(paging_mode=RV.RiscvPagingModes.DISABLE))
        self.assertIn(other_phys, b.spaces)
        self.assertIn(b.phys, b.spaces)
        self.assertIsNot(other_phys, b.phys)


class TestAddTwoStageMapping(unittest.TestCase):
    """The two-stage VA -> GPA -> HPA identity convenience: it creates the GPA page
    SameAs the physical HPA, both mappings, and the g-stage leaf attrs in one call."""

    def test_builds_identity_two_stage_walk(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        hpa = Page(space=b.phys, addr=AddrSpec(exact=0x80030000))
        b.add_two_stage_mapping(
            va_page=p,
            hpa_page=hpa,
            gpa_space=gpa_space,
            attrs=_leaf_attrs(),
            vs_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_mode=RV.RiscvPagingModes.SV39,
        )
        result = b.build()
        # Stage 1: VA -> GPA (== HPA since GPA is SameAs HPA).
        _s, gpa = result.space(va_space).walk(0x2000)
        self.assertEqual(gpa, 0x80030000)
        # Stage 2 (identity): GPA -> HPA == GPA.
        _s, hpa_out = result.space(gpa_space).walk(0x80030000)
        self.assertEqual(hpa_out, 0x80030000)

    def _gstage_leaf_pte(self, attrs, hpa_addr=0x80030000):
        """The GPA -> HPA leaf PTE value this call derives for the data page."""
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        b.add_two_stage_mapping(
            va_page=p,
            hpa_page=Page(space=b.phys, addr=AddrSpec(exact=hpa_addr)),
            gpa_space=gpa_space,
            attrs={**_leaf_attrs(), **attrs},
            vs_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_mode=RV.RiscvPagingModes.SV39,
        )
        result = b.build()
        sr = result.space(gpa_space)
        steps, _ = sr.walk(hpa_addr)
        return dict(sr.pte_entries())[min(steps, key=lambda s: s.level).pte_addr]

    def test_gleaf_x_force_beats_the_identity_executable_default(self):
        # The derived g-stage leaf carries a BARE ``x=1`` default ("identity data pages stay
        # executable"). Regression: folding that bare bit onto the g-leaf level overwrote a
        # real ``x_leaf_gleaf=0`` force, so the leaf stayed executable and the instruction
        # guest-page fault the test wanted never fired (hypervisor_tlb_fence SID_HFTLB_07).
        pte = self._gstage_leaf_pte({"x_level0_glevel0": 0})
        self.assertEqual((pte >> 3) & 1, 0, "x_leaf_gleaf=0 was clobbered by the identity leaf's default x=1")
        # The force must cost the leaf none of its other defaults, or the page faults for
        # a reason the caller did not ask for.
        for name, bit in (("v", 0), ("r", 1), ("w", 2), ("u", 4), ("a", 6), ("d", 7)):
            self.assertEqual((pte >> bit) & 1, 1, f"g-stage leaf default {name} lost")

    def test_unforced_gleaf_stays_executable(self):
        self.assertEqual((self._gstage_leaf_pte({}) >> 3) & 1, 1)


class TestIdentityPage(unittest.TestCase):
    """An identity page (VA == PA) is expressed by pinning a mapping's destination
    ``SameAs`` its source -- no flag. The source is drawn once in the physical pool and
    its value reserved in both the physical pool and its own space's linear pool."""

    def test_identity_page_va_equals_pa_reserved_in_both_pools(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x80030000)))
        # dst pinned SameAs src -> identity (VA == PA).
        p_pa = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(p))))
        b.add_mapping(Mapping(src=p, dst=p_pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        va, pa = result.address_of(p)
        self.assertEqual(va, pa, "identity page must have VA == PA")
        self.assertEqual(va, 0x80030000)
        _s, translated = result.space(va_space).walk(0x80030000)
        self.assertEqual(translated, 0x80030000)

        # The single drawn value is reserved in BOTH the physical pool and the source
        # space's own linear pool (dual reserve), so nothing else can land on it.
        def covered(intervals):
            return any(start <= 0x80030000 < end for start, end in intervals)

        self.assertTrue(covered(result.physical_intervals()), "value not reserved in the physical pool")
        self.assertTrue(covered(result.linear_intervals()), "identity value not reserved in the source space's linear pool")

    def test_identity_va_may_share_exact_pa_with_disable_mmio_leaf(self):
        # IMSIC/ACLINT shape: a DISABLE/phys MMIO leaf at an exact PA, plus an identity
        # VA==PA mapping of the same address in a paging space. Different spaces, one PA.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        mmio = 0x40000000
        phys_leaf = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=mmio), reserve_size=0x1000))
        va = b.add_page(Page(space=va_space, addr=AddrSpec(exact=mmio)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(va))))
        b.add_mapping(Mapping(src=va, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        self.assertEqual(result.address(va), mmio)
        self.assertEqual(result.address(pa), mmio)
        self.assertEqual(result.address(phys_leaf), mmio)
        _s, translated = result.space(va_space).walk(mmio)
        self.assertEqual(translated, mmio)


class TestStageValidation(unittest.TestCase):
    """A ``Stage`` that contradicts the mapping structure raises loudly. ``Stage`` is now
    explicit on every ``Space`` -- there is no structural inference of a g-stage domain
    from an undeclared space's mapping shape anymore."""

    def test_gstage_space_that_is_two_stage_source_raises(self):
        # A g-stage domain cannot itself walk into another table-bearing target.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        t_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))  # table-bearing target
        gp = b.add_page(Page(space=g_space, addr=AddrSpec(exact=0x2000)))
        tp = b.add_page(Page(space=t_space, addr=AddrSpec(exact=0x40000000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000)))
        b.add_mapping(Mapping(src=gp, dst=tp, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=tp, dst=pa, pt_nodes=_leaf_nodes()))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("cannot be a two-stage source", str(cm.exception))

    def test_target_space_with_wrong_stage_raises(self):
        # A space that is a mapping target AND bears its own table (a two-stage GPA
        # domain) must be declared stage=G; leaving it at the SINGLE default raises.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        g_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))  # stage=SINGLE (wrong)
        vp = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        gp = b.add_page(Page(space=g_space, addr=AddrSpec(exact=0x40000000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80040000)))
        b.add_mapping(Mapping(src=vp, dst=gp, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=gp, dst=pa, pt_nodes=_leaf_nodes()))
        with self.assertRaises(ValueError) as cm:
            b.build()
        self.assertIn("requires stage=G", str(cm.exception))


class TestPartialSelectDerivedFrom(unittest.TestCase):
    """A partial-select ``DerivedFrom`` (``random_mask`` set) pins the selected bits to
    the source (XOR ``not_mask``, OR ``or_mask``) and draws the unselected bits as a
    fresh, collision-free free address. A fully-selecting derivation (``random_mask`` 0)
    stays a deterministic forced value, byte-identical to before."""

    def _resolve(self, seed, rel_factory, src_exact=0xABCDE000, and_mask=0xFFFFFFFFFFFFF000, bits=32):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        # A throwaway single-stage mapping purely so va_space gets its own linear pool
        # (a space only gets a pool once something maps out of it); the src/drv pages
        # below are still bare -- no Mapping references either of them -- the former
        # AddressRequest/PageRequest concept is now just a Page with no Mapping.
        anchor = b.add_page(Page(space=va_space))
        anchor_pa = b.add_page(Page(space=b.phys))
        b.add_mapping(Mapping(src=anchor, dst=anchor_pa, pt_nodes=_leaf_nodes()))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=src_exact)))
        rel = rel_factory(src)
        drv = b.add_page(Page(space=va_space, addr=AddrSpec(relation=rel, and_mask=and_mask, bits=bits)))
        result = b.build()
        return result.address(src), result.address(drv)

    def test_selected_bits_pinned_unselected_randomized(self):
        # Top 16 bits selected (copied from source); low 16 bits randomized (4KB-aligned).
        seen_low = set()
        for seed in range(30):
            src, drv = self._resolve(seed, lambda s: DerivedFrom(s, and_mask=0xFFFF0000, random_mask=0x0000FFFF))
            self.assertEqual(drv & 0xFFFF0000, src & 0xFFFF0000, f"seed {seed}: selected bits not pinned to source")
            self.assertEqual(drv & 0xFFF, 0, f"seed {seed}: not 4KB-aligned")
            self.assertNotEqual(drv, src, f"seed {seed}: collided with the reserved source span")
            seen_low.add(drv & 0xF000)
        self.assertGreater(len(seen_low), 1, "unselected bits never varied across seeds")

    def test_not_mask_flips_selected_bit_or_mask_forces_bit(self):
        # not_mask flips selected bit 31 (source bit is 1 -> pinned 0); or_mask forces bit 13.
        for seed in range(10):
            src, drv = self._resolve(
                seed,
                lambda s: DerivedFrom(s, and_mask=0xFFFF0000, or_mask=0x2000, not_mask=0x80000000, random_mask=0x0000FFFF),
            )
            self.assertEqual(drv & 0x80000000, 0, "not_mask did not flip selected bit 31 to 0")
            self.assertEqual(drv & 0x7FFF0000, src & 0x7FFF0000, "other selected bits not pinned")
            self.assertEqual(drv & 0x2000, 0x2000, "or_mask bit not forced")

    def test_full_select_stays_deterministic(self):
        # random_mask 0 -> the deterministic forced value ((src & and) ^ not) | or, seed-invariant.
        expected = ((0xABCDE000 & 0xFFFFFFFFFFFFFFFF) ^ 0x4000) | 0x2000
        for seed in range(5):
            _src, drv = self._resolve(
                seed,
                lambda s: DerivedFrom(s, and_mask=0xFFFFFFFFFFFFFFFF, or_mask=0x2000, not_mask=0x4000, random_mask=0),
            )
            self.assertEqual(drv, expected, f"seed {seed}: deterministic derivation changed")


class TestSingleStageIdentity(unittest.TestCase):
    """A single-stage identity page (dst SameAs src) in a paging-enabled space draws one
    value used as both VA and PA. The VA is sign-extended, so the draw must stay below the
    space's VA sign bit or canonical_va lifts the VA into the upper canonical half while
    the physical dst keeps the raw value -- VA != PA. This is the io_htif tohost page,
    whose faulting VA breaks HTIF (it reads the tohost symbol as a physical address)."""

    def test_identity_free_draw_keeps_va_equal_pa(self):
        sign_bit = RV.RiscvPagingModes.linear_addr_bits(RV.RiscvPagingModes.SV39) - 1
        for seed in range(8):
            b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
            va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
            va = b.add_page(Page(space=va_space, addr=AddrSpec()))
            pa = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(va))))
            b.add_mapping(Mapping(src=va, dst=pa, pt_nodes=_leaf_nodes()))
            result = b.build()
            va_addr, pa_addr = result.address_of(va)
            self.assertEqual(va_addr, pa_addr, f"identity broken (seed={seed}): VA != PA")
            self.assertLess(va_addr, 1 << sign_bit, f"identity draw at/above VA sign bit (seed={seed}): {va_addr:#x}")
            _s, walked = result.space(va_space).walk(va_addr)
            self.assertEqual(walked, pa_addr, f"walk mismatch (seed={seed})")


class TestLeafFoldForcing(unittest.TestCase):
    """An explicit per-level leaf force takes priority over the page's base bit.

    The base bit is the page's VS-stage intent. A mapping can force the leaf
    directly, such as an execute-denied g-stage leaf with ``x_level0=0`` while
    its identity data page keeps base ``x=1``.
    """

    def test_explicit_leaf_force_wins_over_base(self):
        # 4KB page -> leaf level 0. Base x=1 but the leaf is explicitly forced X=0.
        levels = resolve.pt_node_levels_with_leaf({"x": 1, "x_level0": 0, "v": 1}, leaf_level=0)
        self.assertEqual(levels[0]["x"], 0, "explicit x_level0=0 clobbered by base x=1")

    def test_base_bit_still_fills_unforced_leaf(self):
        # No explicit leaf force -> the base bit still populates the leaf level.
        levels = resolve.pt_node_levels_with_leaf({"x": 1}, leaf_level=0)
        self.assertEqual(levels[0]["x"], 1, "base x=1 should fill the unforced leaf level")


class TestEntriesPerTable(unittest.TestCase):
    """A table's slot capacity is derived from the mode's index-field width, not assumed
    512: Sv32 indexes 10 bits, so its tables hold 1024 four-byte PTEs. There is no
    ``bits is None`` fallback to exercise -- ``index_bits`` raises for a level the mode does
    not have, and for DISABLE, which has no levels at all."""

    def _page_map(self, mode):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=mode))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        return b.build().space(va_space)._page_map

    def test_capacity_follows_the_modes_index_width(self):
        self.assertEqual(self._page_map(RV.RiscvPagingModes.SV32)._entries_per_table(0), 1024)
        self.assertEqual(self._page_map(RV.RiscvPagingModes.SV39)._entries_per_table(0), 512)

    def test_a_level_the_mode_does_not_have_raises(self):
        with self.assertRaises(ValueError):
            self._page_map(RV.RiscvPagingModes.SV32)._entries_per_table(2)


class TestRootTableWidth(unittest.TestCase):
    """The root page table draws its physical address from physical_addr_bits, like every
    other physical page. Drawing it at a hardcoded 56 bits let it land on bit 55 -- the
    STEE secure marker -- so the linker stripped bit 55 from the load address while satp
    still pointed at the bit-55 address, walking into unbacked memory (an unmapped fetch)."""

    def test_root_respects_physical_addr_bits(self):
        for seed in range(8):
            b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory(), physical_addr_bits=52)
            va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
            va = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
            pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
            b.add_mapping(Mapping(src=va, dst=pa, pt_nodes=_leaf_nodes()))
            result = b.build()
            root = result.space(va_space).root_addr
            self.assertLess(root, 1 << 52, f"root exceeds physical_addr_bits (seed={seed}): {root:#x}")
            self.assertFalse(root & (1 << 55), f"root sets STEE bit 55 (seed={seed}): {root:#x}")


class TestNapot64KB(unittest.TestCase):
    """A 64 KiB page's N bit defaults on and must turn off when the leaf forces ``n=0``.

    The decision is split across two files -- ``_install_pt_node_attrs`` resolves it,
    ``_create_pt_leaf`` reads it -- and they agree only on the base ``n`` key. Writing that
    key in the "on" direction only left a forced ``n=0`` invisible to the reader, which fell
    back to auto-on: the 16 NAPOT PTEs came out with N=1 and a test asking for a plain 64 KiB
    leaf silently got a NAPOT one. Both consumers (RiescueD's translator and the JSON
    frontend) fold the base ``n`` onto the leaf node, so this is the single fix point.
    """

    _N = 1 << 63
    _VA = 0x40000  # 64 KiB-aligned, so the 16-entry block starts at slot 0
    _PA = 0x80040000

    def _leaf_pte(self, n=None):
        """Build one 64 KiB mapping and return ``(leaf_pte_value, all_16_napot_values)``."""
        attrs = dict(_leaf_attrs())
        if n is not None:
            attrs["n"] = n
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, pagesize=RV.RiscvPageSizes.S64KB, addr=AddrSpec(exact=self._VA)))
        dst = b.add_page(Page(space=b.phys, pagesize=RV.RiscvPageSizes.S64KB, addr=AddrSpec(exact=self._PA)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_nodes_from_attrs(attrs, pagesize=RV.RiscvPageSizes.S64KB)))
        result = b.build()
        space = result.space(va_space)
        va, _pa = result.address_of(src)
        steps, _ = space.walk(va)
        entries = dict(space.pte_entries())
        leaf = next(s for s in steps if s.leaf)
        # The NAPOT block replicates the leaf across its 16-entry aligned slot group.
        block_base = leaf.pte_addr & ~0x7F
        napot = [entries[block_base + 8 * i] for i in range(16)]
        return entries[leaf.pte_addr], napot

    def test_napot_defaults_on(self):
        leaf, napot = self._leaf_pte()
        self.assertTrue(leaf & self._N, f"64 KiB leaf {leaf:#x} should default to N=1")
        self.assertTrue(all(v & self._N for v in napot), "every PTE of the NAPOT block must carry N")

    def test_explicit_n_one_stays_on(self):
        leaf, _napot = self._leaf_pte(n=1)
        self.assertTrue(leaf & self._N, f"explicit n=1 lost the N bit ({leaf:#x})")

    def test_forced_n_zero_turns_napot_off(self):
        leaf, napot = self._leaf_pte(n=0)
        self.assertFalse(leaf & self._N, f"forced n=0 ignored -- leaf {leaf:#x} still sets N")
        for i, value in enumerate(napot):
            self.assertFalse(value & self._N, f"NAPOT block entry {i} ({value:#x}) still sets N after n=0")

    @staticmethod
    def _ppn(pte_value):
        """The physical page address a leaf PTE points at (RV64 PPN is bits [53:10], so the
        N bit at 63 must be masked off rather than shifted into the address)."""
        return ((pte_value >> 10) & ((1 << 44) - 1)) << 12

    def test_n_one_block_carries_the_napot_ppn_encoding(self):
        # Svnapot: every PTE of the block is identical and its PPN[3:0] holds the 64 KiB
        # encoding 0b1000 -- hardware substitutes VPN[3:0] for those bits when translating.
        _leaf, napot = self._leaf_pte(n=1)
        self.assertEqual(len(set(napot)), 1, f"an N=1 block's 16 PTEs must be identical: {[hex(v) for v in napot]}")
        ppn = self._ppn(napot[0])
        self.assertEqual(ppn & 0xF000, 0x8000, f"N=1 leaf PPN {ppn:#x} must carry the 64 KiB NAPOT encoding")
        self.assertEqual(ppn & ~0xFFFF, self._PA, "the NAPOT encoding must sit on the page's own 64 KiB base")

    def test_n_one_page_translates_every_offset_correctly(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(
            Page(
                space=va_space,
                pagesize=RV.RiscvPageSizes.S64KB,
                addr=AddrSpec(exact=self._VA),
            )
        )
        dst = b.add_page(
            Page(
                space=b.phys,
                pagesize=RV.RiscvPageSizes.S64KB,
                addr=AddrSpec(exact=self._PA),
            )
        )
        b.add_mapping(
            Mapping(
                src=src,
                dst=dst,
                pt_nodes=_nodes_from_attrs(
                    _leaf_attrs(),
                    pagesize=RV.RiscvPageSizes.S64KB,
                ),
            )
        )
        space = b.build().space(va_space)
        for i in range(16):
            _steps, translated = space.walk(self._VA + (i << 12))
            self.assertEqual(
                translated,
                self._PA + (i << 12),
                f"VA offset {i << 12:#x} of an N=1 64 KiB page mistranslated",
            )

    def test_n_zero_block_maps_each_4kb_slot_to_its_own_pa(self):
        # Without Svnapot, the 64 KiB declaration is encoded as sixteen
        # ordinary 4 KiB mappings.
        _leaf, napot = self._leaf_pte(n=0)
        for i, value in enumerate(napot):
            self.assertEqual(self._ppn(value), self._PA + (i << 12), f"n=0 block entry {i} must map its own 4 KiB slot, not a NAPOT-encoded PPN")

    def test_n_zero_page_translates_every_offset_correctly(self):
        # The same defect seen through the walk read-back: each 4 KiB slot of the 64 KiB page
        # must resolve to its own physical offset.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, pagesize=RV.RiscvPageSizes.S64KB, addr=AddrSpec(exact=self._VA)))
        dst = b.add_page(Page(space=b.phys, pagesize=RV.RiscvPageSizes.S64KB, addr=AddrSpec(exact=self._PA)))
        attrs = dict(_leaf_attrs())
        attrs["n"] = 0
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_nodes_from_attrs(attrs, pagesize=RV.RiscvPageSizes.S64KB)))
        result = b.build()
        space = result.space(va_space)
        for i in range(16):
            _steps, translated = space.walk(self._VA + (i << 12))
            self.assertEqual(translated, self._PA + (i << 12), f"VA offset {i << 12:#x} of an n=0 64 KiB page mistranslated")

    def test_n_is_ignored_on_a_non_napot_pagesize(self):
        # N is only defined for the 64 KiB NAPOT encoding; a 4 KiB leaf never sets it.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_nodes_from_attrs(_leaf_attrs())))
        result = b.build()
        space = result.space(va_space)
        va, _pa = result.address_of(src)
        steps, _ = space.walk(va)
        leaf = next(s for s in steps if s.leaf)
        self.assertFalse(dict(space.pte_entries())[leaf.pte_addr] & self._N)


class TestDuplicateVaRejected(unittest.TestCase):
    """One VA in one space can carry only one leaf-PTE declaration."""

    def _two_at(self, va, space=None, seed=1):
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va_space = space if space is not None else b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        for i in range(2):
            src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=va)))
            dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + i * 0x1000)))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        return b

    def test_two_pages_pinned_to_one_va_raise(self):
        b = self._two_at(0x1000)
        with self.assertRaisesRegex(ValueError, "two distinct pages resolve to VA"):
            b.build()

    def test_the_same_va_in_two_spaces_is_fine(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        spaces = [b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39)) for _ in range(2)]
        for i, sp in enumerate(spaces):
            src = b.add_page(Page(space=sp, addr=AddrSpec(exact=0x1000)))
            dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000 + i * 0x1000)))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        result = b.build()
        for i, sp in enumerate(spaces):
            self.assertEqual(result.space(sp).walk(0x1000)[1], 0x80010000 + i * 0x1000)

    def test_one_src_declared_twice_still_builds(self):
        # The switch-hgatp shape: the SAME src page declares a VA -> GPA mapping once per
        # G space. That is one VS leaf fanned structurally, not a duplicate, so the
        # object-identity dedup must still absorb it.
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        g_spaces = [b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G)) for _ in range(2)]
        va_space = declare_vs_root_identities(b, RV.RiscvPagingModes.SV39, *g_spaces)
        va_p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x3000)))
        for i, g in enumerate(g_spaces):
            gpa = b.add_page(Page(space=g, addr=AddrSpec(exact=0x40000000)))
            pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80050000 + i * 0x10000)))
            b.add_mapping(Mapping(src=va_p, dst=gpa, pt_nodes=_vs_leaf_nodes()))
            b.add_mapping(Mapping(src=gpa, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        self.assertEqual(result.space(va_space).walk(0x3000)[1], 0x40000000)

    def test_aliases_with_equivalent_effective_leaf_attrs_build(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x40000000)))
        src_alias = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x40000000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        dst_alias = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=src_alias, dst=dst_alias, pt_nodes={LEAF: PTNode(attrs={"a": 1, "d": 1})}))

        result = b.build()

        self.assertEqual(result.space(va_space).walk(0x40000000)[1], 0x80010000)

    def test_aliases_with_different_effective_leaf_attrs_raise(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x40000000)))
        src_alias = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x40000000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        dst_alias = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=0x80010000)))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        b.add_mapping(Mapping(src=src_alias, dst=dst_alias, pt_nodes={LEAF: PTNode(attrs={"a": 1, "d": 1, "x": 0})}))

        with self.assertRaisesRegex(ValueError, "conflicting effective leaf attributes"):
            b.build()


class TestExcludedRegionsPtFrameParity(unittest.TestCase):
    """``PageTableBuilder(excluded_regions=...)`` must keep every AUTO-allocated PT-node
    frame (and free-drawn data page) out of the excluded window, in single-stage,
    two-stage (VS/G), and secure trees alike -- while an ordinary, non-excluded region a
    caller explicitly places into is unaffected, and an EXPLICITLY pinned (exact) frame
    that falls inside the excluded window fails loudly rather than being silently
    relocated (RieMap never second-guesses an exact placement)."""

    # A small DRAM so the excluded window is a large enough fraction of the address
    # space that an un-excluded random draw would almost certainly land in it (a huge
    # DRAM would make a real regression here statistically invisible).
    _DRAM_BASE = 0x80000000
    _DRAM_SIZE = 0x10000000  # 256 MiB
    _DECOY_START = 0x84000000
    _DECOY_SIZE = 0x08000000  # 128 MiB -- half the DRAM

    def _memory_small(self):
        return Memory.from_dict({"dram": {"dram0": {"address": hex(self._DRAM_BASE), "size": hex(self._DRAM_SIZE), "cacheable": True, "configurable": True}}})

    def _memory_small_secure(self):
        return Memory.from_dict(
            {
                "dram": {
                    "dram0": {"address": hex(self._DRAM_BASE), "size": hex(self._DRAM_SIZE), "cacheable": True, "configurable": True},
                    "secure0": {"address": hex(self._DRAM_BASE), "size": hex(self._DRAM_SIZE), "secure": True},
                }
            }
        )

    def _decoy(self):
        window = (self._DECOY_START, self._DECOY_START + self._DECOY_SIZE)
        return ExcludedRegion.from_interval(*window), window

    def _assert_no_table_in_window(self, result, window):
        start, end = window
        hits = [table.addr for space in result.spaces() for table in space.tables() if start <= table.addr < end]
        self.assertEqual(hits, [], f"PT-node frame(s) {[hex(a) for a in hits]} land inside excluded window [0x{start:x}, 0x{end:x})")

    def test_single_stage_auto_frames_miss_excluded_decoy(self):
        decoy, window = self._decoy()
        b = PageTableBuilder(rng=RandNum(seed=3), memory=self._memory_small(), excluded_regions=[decoy])
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        for _ in range(60):
            p = b.add_page(Page(space=va_space))
            pa = b.add_page(Page(space=b.phys))
            b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        self._assert_no_table_in_window(result, window)

    def test_vs_g_two_stage_auto_frames_miss_excluded_decoy(self):
        decoy, window = self._decoy()
        b = PageTableBuilder(rng=RandNum(seed=5), memory=self._memory_small(), excluded_regions=[decoy])
        gpa_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        va_space = declare_vs_root_identity(b, RV.RiscvPagingModes.SV39, gpa_space)
        for _ in range(30):
            va = b.add_page(Page(space=va_space))
            hpa = b.add_page(Page(space=b.phys))
            gpa = b.add_page(Page(space=gpa_space, addr=AddrSpec(relation=SameAs(hpa))))
            b.add_mapping(Mapping(src=va, dst=gpa, pt_nodes=_vs_leaf_nodes()))
            b.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes=_leaf_nodes()))
        result = b.build()
        self._assert_no_table_in_window(result, window)

    def test_secure_tree_auto_frames_miss_excluded_decoy(self):
        # secure_pt_probability=100 forces every auto PT-node frame to draw from the
        # ADDRESS_SECURE range; the decoy sits inside that same range, so parity must
        # hold there too, not just for the default ADDRESS_DRAM draw.
        decoy, window = self._decoy()
        b = PageTableBuilder(rng=RandNum(seed=7), memory=self._memory_small_secure(), excluded_regions=[decoy])
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, secure_pt_probability=100))
        for _ in range(60):
            p = b.add_page(Page(space=va_space))
            pa = b.add_page(Page(space=b.phys))
            b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        self._assert_no_table_in_window(result, window)

    def test_ordinary_region_is_not_auto_excluded(self):
        # A caller-declared MemoryRegion that does not overlap any excluded window is
        # unaffected by excluded_regions: a member explicitly placed in_region still
        # lands inside its own region, even with a global exclusion list active.
        decoy, _window = self._decoy()
        ordinary = MemoryRegion(size=0x1000, base=0x81000000)
        b = PageTableBuilder(rng=RandNum(seed=1), memory=self._memory_small(), excluded_regions=[decoy])
        b.add_region(ordinary)
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        p = b.add_page(Page(space=va_space))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(region=ordinary)))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        base = result.region_base(ordinary)
        self.assertEqual(base, ordinary.base)
        _va, resolved_pa = result.address_of(pa)
        self.assertTrue(base <= resolved_pa < base + ordinary.size)

    def test_explicit_pin_inside_excluded_window_is_honored_not_relocated(self):
        # A page pinned to an EXACT address (the modify_pt / modify_nonleaf_pt shape) is a
        # declared, consumer-owned placement: an exclusion list filters free/random draws,
        # never a hard pin. RieMap must place it exactly where asked -- even inside an
        # excluded window -- rather than silently relocating it elsewhere.
        decoy, window = self._decoy()
        pinned_addr = window[0] + 0x1000
        b = PageTableBuilder(rng=RandNum(seed=1), memory=self._memory_small(), excluded_regions=[decoy])
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        p = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        pa = b.add_page(Page(space=b.phys, addr=AddrSpec(exact=pinned_addr)))
        b.add_mapping(Mapping(src=p, dst=pa, pt_nodes=_leaf_nodes()))
        result = b.build()
        _va, resolved_pa = result.address_of(pa)
        self.assertEqual(resolved_pa, pinned_addr, "an explicit pin must be honored exactly, not relocated out of an excluded window")


class TestBarePageInNonSourceSpace(unittest.TestCase):
    """A bare Page (no Mapping) in a non-physical space still needs an address pool.

    Nightly floatingpoint2: under paging DISABLE, mapped pages collapse into phys and
    emit no Mapping, but a bare linear random_addr remains in map_os. Pool creation that
    keys only on ``_is_source`` left that space without a pool and allocation raised
    ``KeyError`` on ``_space_pools[space]``.
    """

    def test_bare_page_in_disable_non_source_space_allocates(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        scratch = b.add_space(Space(paging_mode=RV.RiscvPagingModes.DISABLE))
        # Physical pages only -- no Mapping originates from ``scratch``.
        phys_page = b.add_page(Page(space=b.phys))
        bare = b.add_page(Page(space=scratch))
        self.assertFalse(b.mappings, "fixture must have zero mappings so scratch is not a source")
        result = b.build()
        self.assertIsNotNone(result.address(bare))
        self.assertIsNotNone(result.address(phys_page))


class TestReserveGranule(unittest.TestCase):
    GRANULE = 1 << 30

    def _builder(self, seed=1):
        builder = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        return builder, space

    @staticmethod
    def _map(builder, src, pa=None):
        if pa is None:
            pa = builder.add_page(Page(space=builder.phys))
        builder.add_mapping(Mapping(src=src, dst=pa, pt_nodes=_leaf_nodes()))
        return pa

    def test_free_granule_owner_colors_away_from_fixed_root_slot(self):
        builder, space = self._builder()
        fixed = builder.add_page(Page(space=space, addr=AddrSpec(exact=0x1000)))
        owner = builder.add_page(Page(space=space, reserve_granule=self.GRANULE))
        self._map(builder, fixed)
        self._map(builder, owner)

        result = builder.build()
        self.assertNotEqual(
            result.address(owner) // self.GRANULE,
            result.address(fixed) // self.GRANULE,
        )

    def test_exact_cross_boundary_claim_rejects_both_root_slots(self):
        builder, space = self._builder()
        owner = builder.add_page(
            Page(
                space=space,
                addr=AddrSpec(exact=self.GRANULE - 0x1000),
                reserve_size=0x3000,
                reserve_granule=self.GRANULE,
            )
        )
        unrelated = builder.add_page(
            Page(
                space=space,
                addr=AddrSpec(exact=self.GRANULE + 0x1000),
            )
        )
        self._map(builder, owner)
        self._map(builder, unrelated)

        with self.assertRaises(AddrGenError):
            builder.build()

    def test_offset_family_may_live_inside_anchor_claim(self):
        builder, space = self._builder()
        anchor = builder.add_page(
            Page(
                space=space,
                addr=AddrSpec(exact=self.GRANULE + 0x1000),
                reserve_size=0x4000,
                reserve_granule=self.GRANULE,
            )
        )
        child = builder.add_page(
            Page(
                space=space,
                addr=AddrSpec(relation=OffsetFrom(anchor, 0x1000)),
            )
        )
        self._map(builder, anchor)
        self._map(builder, child)

        result = builder.build()
        self.assertEqual(result.address(child), result.address(anchor) + 0x1000)

    def test_same_as_gpa_claim_does_not_expand_hpa_backing(self):
        builder = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        gpa_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        hpa = builder.add_page(
            Page(
                space=builder.phys,
                addr=AddrSpec(exact=0x80001000),
                reserve_size=0x1000,
            )
        )
        gpa = builder.add_page(
            Page(
                space=gpa_space,
                addr=AddrSpec(relation=SameAs(hpa)),
                reserve_size=0x1000,
                reserve_granule=self.GRANULE,
            )
        )
        nearby_hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80002000)))
        self._map(builder, gpa, hpa)

        result = builder.build()
        self.assertEqual(result.address(gpa), 0x80001000)
        self.assertEqual(result.address(nearby_hpa), 0x80002000)

    def test_identity_claim_is_linear_only_and_keeps_page_backing(self):
        builder, space = self._builder()
        source = builder.add_page(
            Page(
                space=space,
                addr=AddrSpec(exact=0x80001000),
                reserve_size=0x1000,
                reserve_granule=self.GRANULE,
            )
        )
        destination = builder.add_page(
            Page(
                space=builder.phys,
                addr=AddrSpec(relation=SameAs(source)),
                reserve_size=0x1000,
            )
        )
        nearby_hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80002000)))
        self._map(builder, source, destination)

        result = builder.build()
        self.assertEqual(result.address(source), 0x80001000)
        self.assertEqual(result.address(destination), 0x80001000)
        self.assertEqual(result.address(nearby_hpa), 0x80002000)

    def test_fixed_region_member_claims_containing_granule(self):
        builder, space = self._builder()
        region = builder.add_region(MemoryRegion(base=self.GRANULE, size=self.GRANULE))
        owner = builder.add_page(
            Page(
                space=space,
                addr=AddrSpec(region=region),
                reserve_granule=self.GRANULE,
            )
        )
        self._map(builder, owner)

        result = builder.build()
        self.assertTrue(self.GRANULE <= result.address(owner) < 2 * self.GRANULE)

    def test_derived_gpa_keeps_raw_address_and_claims_its_domain(self):
        builder = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        gpa_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
        base = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80000000)))
        gpa = builder.add_page(
            Page(
                space=gpa_space,
                addr=AddrSpec(relation=DerivedFrom(base, or_mask=0x1000)),
                reserve_granule=self.GRANULE,
            )
        )
        target = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x90000000)))
        self._map(builder, gpa, target)

        result = builder.build()
        self.assertEqual(result.address(gpa), 0x80001000)

    def test_absent_granule_does_not_force_root_coloring(self):
        builder, space = self._builder()
        plain_a = builder.add_page(Page(space=space))
        plain_b = builder.add_page(Page(space=space))
        self._map(builder, plain_a)
        self._map(builder, plain_b)
        # No reserve_granule: root coloring signature stays attribute-only, so two
        # compatible free pages may share a root slot when the allocator places them.
        for mapping in builder.mappings:
            if mapping.src.space is space:
                root_level = RV.RiscvPagingModes.max_levels(space.paging_mode) - 1
                sig = builder._color_sig(mapping, root_level)
                self.assertFalse(any(part[0] == "reservation_granule" for part in sig))

    def test_non_root_sized_granule_does_not_color(self):
        builder, space = self._builder()
        owner = builder.add_page(Page(space=space, reserve_granule=0x200000))
        self._map(builder, owner)
        mapping = next(m for m in builder.mappings if m.src is owner)
        root_level = RV.RiscvPagingModes.max_levels(space.paging_mode) - 1
        sig = builder._color_sig(mapping, root_level)
        self.assertFalse(any(part[0] == "reservation_granule" for part in sig))


if __name__ == "__main__":
    unittest.main()
