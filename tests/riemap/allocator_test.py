# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the order-independent constraint solver (BatchAllocationStrategy.solve).

These exercise the constraint vocabulary directly on the solver: exact pins,
free draws, relations (same_as / offset_from / derived_from), tagged-region
placement + membership, bare address requests, and the contract that request
*order* does not affect correctness (any order yields a valid result -- exact
addresses may differ).

``AllocRequest`` is one-per-declaration-``Page`` engine plumbing (see
``riescue.riemap.allocator``): a "page with both a VA and a PA side" from the
pre-refactor API is now just two independent ``Page``s (and two ``AllocRequest``s,
one LINEAR and one PHYSICAL) -- there is no bundling type anymore.
"""

import ast
import inspect
import random
import textwrap
import time
import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import DramRange, Memory
from riescue.riemap.addrgen import AddrGen
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.addrgen.types import ExcludedRegion
from riescue.riemap.allocator import (
    AllocRequest,
    BatchAllocationStrategy,
    ClaimKind,
    SpanClaim,
    _Reservation,
    _SpanIndex,
    _has_sparse_domain,
)
from riescue.riemap.request import AddrSpec, DerivedFrom, MemoryRegion, OffsetFrom, Page, SameAs, Space, Stage


def _memory():
    return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}})


def _addrgen(seed=1):
    return AddrGen(RandNum(seed=seed), _memory())


# The allocator never reads a Page's own .space (only AllocRequest.space_key matters,
# and these tests pass none / a shared dummy) -- one placeholder space is enough for
# every Page built directly in this file.
_SPACE = Space(paging_mode=RV.RiscvPagingModes.DISABLE)


def _page(size=RV.RiscvPageSizes.S4KB):
    return Page(space=_SPACE, pagesize=size)


def _req(page, addr_type, size=None, addr=None):
    size_bytes = size if size is not None else RV.RiscvPageSizes.memory(page.pagesize)
    return AllocRequest(page=page, addr_type=addr_type, size=size_bytes, addr=addr or AddrSpec())


def _linear_req(size=RV.RiscvPageSizes.S4KB, addr=None):
    return _req(_page(size), RV.AddressType.LINEAR, addr=addr)


def _phys_req(size=RV.RiscvPageSizes.S4KB, addr=None):
    return _req(_page(size), RV.AddressType.PHYSICAL, addr=addr)


def _spans_overlap(spans):
    ordered = sorted(spans)
    for (_, e1), (s2, _) in zip(ordered, ordered[1:]):
        if s2 < e1:
            return True
    return False


class TestAddrSpecValidation(unittest.TestCase):
    def test_rejects_multiple_placement_modes(self):
        target = _page()
        region = MemoryRegion(size=0x1000, align=0x1000)
        contradictory = (
            {"exact": 0x8000_0000, "relation": SameAs(target)},
            {"exact": 0x8000_0000, "region": region},
            {"relation": SameAs(target), "region": region},
        )

        for kwargs in contradictory:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "placement mode"):
                    AddrSpec(**kwargs)


class TestSparseDomainClassification(unittest.TestCase):
    """``_has_sparse_domain`` selects the solver's cautious placement policy.

    A spec classified sparse keeps competing roots on the rollback path and makes their
    rejected candidates retire only one alignment unit, so the few bases the sparse request
    can reach survive. Misclassifying a dense spec as sparse only costs search time;
    misclassifying a sparse one as dense loses placements, so these pin both directions.
    """

    def test_plain_alignment_masks_are_dense(self):
        for align in (0x1000, 0x20_0000, 0x4000_0000):
            with self.subTest(align=align):
                self.assertFalse(_has_sparse_domain(AddrSpec(and_mask=~(align - 1))))

    def test_default_mask_is_dense(self):
        self.assertFalse(_has_sparse_domain(AddrSpec()))

    def test_mask_clearing_an_interior_field_is_sparse(self):
        # 4KB alignment plus a pinned index field higher up: legal bases are scattered
        # rather than "every aligned address".
        and_mask = ~(0xFFF | (0x1FF << 21))
        self.assertTrue(_has_sparse_domain(AddrSpec(and_mask=and_mask)))

    def test_density_is_judged_within_the_declared_width(self):
        # Inside 32 bits this is an ordinary 4KB alignment mask; read as a 64-bit mask the
        # same value also forces the whole upper half to zero, which is not a low run.
        narrow = AddrSpec(and_mask=0xFFFF_F000, bits=32)
        wide = AddrSpec(and_mask=0xFFFF_F000, bits=64)
        self.assertFalse(_has_sparse_domain(narrow))
        self.assertTrue(_has_sparse_domain(wide))


class TestGranuleSpanClaim(unittest.TestCase):
    def test_absent_granule_keeps_ordinary_origin_and_offset(self):
        claim = SpanClaim(
            RV.AddressType.LINEAR,
            _SPACE,
            0x2000,
            ClaimKind.RESERVATION,
            offset=-0x1000,
        )
        self.assertEqual(claim.span(0x12345000), (0x12344000, 0x12346000))

    def test_claims_aligned_granule_containing_exact_address(self):
        claim = SpanClaim(
            RV.AddressType.LINEAR,
            _SPACE,
            0x1000,
            ClaimKind.RESERVATION,
            granule=0x200000,
        )
        self.assertEqual(claim.span(0x12345000), (0x12200000, 0x12400000))

    def test_footprint_crossing_boundary_claims_both_granules(self):
        claim = SpanClaim(
            RV.AddressType.LINEAR,
            _SPACE,
            0x3000,
            ClaimKind.RESERVATION,
            granule=0x200000,
        )
        self.assertEqual(claim.span(0x123FF000), (0x12200000, 0x12600000))

    def test_granule_must_be_power_of_two(self):
        with self.assertRaisesRegex(ValueError, "positive power of two"):
            SpanClaim(
                RV.AddressType.LINEAR,
                _SPACE,
                0x1000,
                ClaimKind.RESERVATION,
                granule=0x3000,
            )


class TestSpanIndex(unittest.TestCase):
    def test_wide_query_scans_populated_cells(self):
        index = _SpanIndex()
        reservations = [
            _Reservation(
                owner=i,
                addr_type=RV.AddressType.LINEAR,
                space=_SPACE,
                start=address,
                end=address + 0x1000,
            )
            for i, address in enumerate((0x1000, 1 << 40))
        ]
        for reservation in reservations:
            index.add(reservation)

        self.assertEqual(
            index.overlapping(0, 1 << 48),
            reservations,
        )


class TestSolverBasics(unittest.TestCase):
    def test_free_pages_get_distinct_nonoverlapping_addrs(self):
        reqs = [_phys_req() for _ in range(8)]
        BatchAllocationStrategy().solve(reqs, [], _addrgen(), RandNum(seed=1))
        pas = [(r.allocated, r.allocated + r.size) for r in reqs]
        self.assertFalse(_spans_overlap(pas), "physical spans overlap")
        for r in reqs:
            self.assertIsNotNone(r.allocated)

    def test_exact_pin_is_honored(self):
        va_req = _linear_req(addr=AddrSpec(exact=0x8000_0000))
        pa_req = _phys_req(addr=AddrSpec(exact=0x9000_0000))
        BatchAllocationStrategy().solve([va_req, pa_req], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(va_req.allocated, 0x8000_0000)
        self.assertEqual(pa_req.allocated, 0x9000_0000)

    def test_alignment_mask_respected(self):
        mask = RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S2MB)
        req = _phys_req(size=RV.RiscvPageSizes.S2MB, addr=AddrSpec(and_mask=mask))
        BatchAllocationStrategy().solve([req], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(req.allocated & (0x200000 - 1), 0, "2MB page not 2MB-aligned")

    def test_bare_address_request(self):
        # A bare (unmapped) address -- no Mapping references this Page.
        req = _phys_req()
        BatchAllocationStrategy().solve([req], [], _addrgen(), RandNum(seed=1))
        self.assertIsNotNone(req.allocated)

    def test_failed_solve_is_transactional(self):
        addrgen = _addrgen()
        before = addrgen.allocated_physical_intervals()
        pin = _phys_req(addr=AddrSpec(exact=0x8000_0000))
        source = _phys_req(addr=AddrSpec(exact=0x8000_1000))
        collision = _phys_req(
            addr=AddrSpec(
                relation=DerivedFrom(
                    source.page,
                    and_mask=0,
                    or_mask=pin.addr.exact,
                )
            )
        )

        with self.assertRaises(AddrGenError):
            BatchAllocationStrategy().solve([pin, source, collision], [], addrgen, RandNum(seed=1))

        self.assertEqual(addrgen.allocated_physical_intervals(), before)
        self.assertIsNone(pin.allocated)
        self.assertIsNone(source.allocated)
        self.assertIsNone(collision.allocated)

    def test_partial_derived_relation_randomizes_only_random_mask_bits(self):
        for seed in range(20):
            source = _phys_req(addr=AddrSpec(exact=0xABCD_0000, bits=32))
            derived = _phys_req(
                addr=AddrSpec(
                    relation=DerivedFrom(
                        source.page,
                        and_mask=0xFFFF_0000,
                        random_mask=0x0000_3000,
                    ),
                    and_mask=0xFFFF_F000,
                    bits=32,
                )
            )

            BatchAllocationStrategy().solve(
                [source, derived],
                [],
                _addrgen(seed),
                RandNum(seed),
            )

            self.assertEqual(derived.allocated & 0xFFFF_0000, 0xABCD_0000)
            self.assertEqual(
                derived.allocated & 0x0000_CFFF,
                0,
                f"seed {seed}: bits outside random_mask changed",
            )

    def test_free_root_backtracks_when_it_takes_a_later_roots_only_slot(self):
        base = 0x8000_0000
        for seed in range(20):
            addrgen = AddrGen(
                RandNum(seed=seed),
                Memory(
                    dram_ranges=[
                        DramRange(start=base, size=0x2000),
                    ]
                ),
            )
            flexible = AllocRequest(
                page=_page(),
                addr_type=RV.AddressType.PHYSICAL,
                size=0x1000,
                addr=AddrSpec(
                    and_mask=0x1000,
                    or_mask=base,
                    bits=32,
                    pinned=True,
                ),
                place_first=True,
            )
            constrained = AllocRequest(
                page=_page(),
                addr_type=RV.AddressType.PHYSICAL,
                size=0x1000,
                addr=AddrSpec(
                    # Bit 13 is nominally free, but the two-page memory
                    # window leaves only ``base`` reachable.
                    and_mask=0x2000,
                    or_mask=base,
                    bits=32,
                    pinned=True,
                ),
            )

            BatchAllocationStrategy().solve(
                [flexible, constrained],
                [],
                addrgen,
                RandNum(seed),
            )

            self.assertEqual(constrained.allocated, base)
            self.assertEqual(flexible.allocated, base + 0x1000)

    def test_greedy_root_backtracks_for_later_masked_domain(self):
        base = 0x8000_0000
        addrgen = AddrGen(
            RandNum(seed=0),
            Memory(dram_ranges=[DramRange(start=base, size=0x4000)]),
        )
        flexible = AllocRequest(
            page=_page(),
            addr_type=RV.AddressType.PHYSICAL,
            size=0x3000,
            addr=AddrSpec(and_mask=0xFFFF_F000, bits=32),
        )
        only_first_slot = AllocRequest(
            page=_page(),
            addr_type=RV.AddressType.PHYSICAL,
            size=0x1000,
            addr=AddrSpec(
                and_mask=base | 0x4000,
                or_mask=base,
                bits=32,
            ),
        )

        BatchAllocationStrategy().solve(
            [flexible, only_first_slot],
            [],
            addrgen,
            RandNum(seed=0),
        )

        self.assertEqual(only_first_slot.allocated, base)
        self.assertEqual(flexible.allocated, base + 0x1000)

    def test_exact_claim_must_fit_declared_bit_domain(self):
        request = AllocRequest(
            page=_page(),
            addr_type=RV.AddressType.PHYSICAL,
            size=0x2000,
            addr=AddrSpec(exact=0xF000, bits=16),
        )

        with self.assertRaisesRegex(AddrGenError, "16-bit address domain"):
            BatchAllocationStrategy().solve(
                [request],
                [],
                _addrgen(),
                RandNum(seed=1),
            )

    def test_physical_coverage_does_not_consume_backing(self):
        leaf_page = _page(RV.RiscvPageSizes.S2MB)
        structural_page = _page()
        leaf = AllocRequest(
            page=leaf_page,
            addr_type=RV.AddressType.PHYSICAL,
            size=0x1000,
            addr=AddrSpec(exact=0x8000_0000),
            claims=(
                SpanClaim(
                    RV.AddressType.PHYSICAL,
                    None,
                    0x1000,
                    ClaimKind.BACKING,
                    "leaf",
                ),
                SpanClaim(
                    RV.AddressType.PHYSICAL,
                    None,
                    0x20_0000,
                    ClaimKind.COVERAGE,
                    "leaf",
                ),
            ),
        )
        structural = AllocRequest(
            page=structural_page,
            addr_type=RV.AddressType.PHYSICAL,
            size=0x1000,
            addr=AddrSpec(exact=0x8000_1000),
            claims=(
                SpanClaim(
                    RV.AddressType.PHYSICAL,
                    None,
                    0x1000,
                    ClaimKind.BACKING,
                    "structural",
                ),
            ),
        )
        addrgen = _addrgen()

        BatchAllocationStrategy().solve(
            [leaf, structural],
            [],
            addrgen,
            RandNum(seed=1),
        )

        allocated = addrgen.allocated_physical_intervals()
        self.assertEqual(
            sum(end - start for start, end in allocated),
            0x2000,
        )

    def test_large_coverage_is_placed_before_small_random_spans(self):
        base = 0x8000_0000
        addrgen = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=base, size=0x20_0000)]),
        )
        large = AllocRequest(
            page=_page(),
            addr_type=RV.AddressType.PHYSICAL,
            size=0x1000,
            addr=AddrSpec(and_mask=~(0x10_0000 - 1), bits=64),
            claims=(
                SpanClaim(
                    RV.AddressType.PHYSICAL,
                    None,
                    0x1000,
                    ClaimKind.BACKING,
                    "large",
                ),
                SpanClaim(
                    RV.AddressType.PHYSICAL,
                    None,
                    0x10_0000,
                    ClaimKind.COVERAGE,
                    "large",
                ),
            ),
        )
        small = []
        for index in range(64):
            page = _page()
            small.append(
                AllocRequest(
                    page=page,
                    addr_type=RV.AddressType.PHYSICAL,
                    size=0x1000,
                    addr=AddrSpec(),
                    claims=(
                        SpanClaim(
                            RV.AddressType.PHYSICAL,
                            None,
                            0x1000,
                            ClaimKind.BACKING,
                            index,
                        ),
                        SpanClaim(
                            RV.AddressType.PHYSICAL,
                            None,
                            0x1000,
                            ClaimKind.COVERAGE,
                            index,
                        ),
                    ),
                )
            )

        BatchAllocationStrategy().solve(
            small + [large],
            [],
            addrgen,
            RandNum(seed=1),
        )

        large_span = (large.allocated, large.allocated + 0x10_0000)
        self.assertTrue(all(req.allocated + req.size <= large_span[0] or req.allocated >= large_span[1] for req in small))

    def test_relation_family_does_not_authorize_coverage_overlap(self):
        space = Space(paging_mode=RV.RiscvPagingModes.SV39)
        parent_page, child_page = Page(space), Page(space)
        parent = AllocRequest(
            page=parent_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(exact=0x4000_0000),
            space_key=space,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    space,
                    0x20_0000,
                    ClaimKind.COVERAGE,
                    "parent-leaf",
                ),
            ),
        )
        child = AllocRequest(
            page=child_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(relation=OffsetFrom(parent_page, 0x1000)),
            space_key=space,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    space,
                    0x1000,
                    ClaimKind.COVERAGE,
                    "child-leaf",
                ),
            ),
        )

        addrgen = _addrgen()
        addrgen.make_space_pool(space)
        with self.assertRaisesRegex(
            AddrGenError,
            "overlaps already-reserved",
        ):
            BatchAllocationStrategy().solve(
                [parent, child],
                [],
                addrgen,
                RandNum(seed=1),
            )

    def test_conditional_structural_claim_may_be_contained_by_explicit_coverage(self):
        space = Space(
            paging_mode=RV.RiscvPagingModes.SV39,
            stage=Stage.G,
        )
        broad_page = Page(space, pagesize=RV.RiscvPageSizes.S1GB)
        structural_page = Page(space)
        broad = AllocRequest(
            page=broad_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(exact=0x40000000),
            space_key=space,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    space,
                    0x40000000,
                    ClaimKind.COVERAGE,
                    broad_page,
                ),
            ),
        )
        structural = AllocRequest(
            page=structural_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(exact=0x40001000),
            space_key=space,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    space,
                    0x1000,
                    ClaimKind.STRUCTURAL,
                    structural_page,
                    allow_contained_share=True,
                ),
            ),
        )
        addrgen = _addrgen()
        addrgen.make_space_pool(space)

        BatchAllocationStrategy().solve(
            [broad, structural],
            [],
            addrgen,
            RandNum(seed=1),
        )

    def test_structural_translation_claims_may_not_nest(self):
        """A synthetic g-stage frame may not sit inside another frame's leaf span.

        Both frames advertise occupancy under the shared ``translation`` key. Letting the
        4 KB frame land inside the 64 KB frame's span puts it in a PTE slot the 64 KB leaf
        already owns, which only surfaces later as a non-retriable ``TopologyConflict``.
        """
        space = Space(
            paging_mode=RV.RiscvPagingModes.SV57,
            stage=Stage.G,
        )
        share_key = (ClaimKind.COVERAGE, space, "translation")
        big_page = Page(space, pagesize=RV.RiscvPageSizes.S64KB)
        small_page = Page(space)
        big = AllocRequest(
            page=big_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1_0000,
            addr=AddrSpec(exact=0x4000_0000),
            space_key=space,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    space,
                    0x1_0000,
                    ClaimKind.STRUCTURAL,
                    share_key,
                ),
            ),
        )
        small = AllocRequest(
            page=small_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(exact=0x4000_1000),
            space_key=space,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    space,
                    0x1000,
                    ClaimKind.STRUCTURAL,
                    share_key,
                ),
            ),
        )
        addrgen = _addrgen()
        addrgen.make_space_pool(space)

        with self.assertRaisesRegex(AddrGenError, "incompatible backing or coverage claim"):
            BatchAllocationStrategy().solve(
                [big, small],
                [],
                addrgen,
                RandNum(seed=1),
            )


class TestRelations(unittest.TestCase):
    def test_same_as_shares_physical(self):
        owner_va, owner_pa = _linear_req(), _phys_req()
        alias_va = _linear_req()
        alias_pa = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(relation=SameAs(owner_pa.page)))
        BatchAllocationStrategy().solve([owner_va, owner_pa, alias_va, alias_pa], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(alias_pa.allocated, owner_pa.allocated)
        self.assertNotEqual(alias_va.allocated, owner_va.allocated)

    def test_offset_from_places_linked_child(self):
        parent_va, parent_pa = _linear_req(), _phys_req()
        child_va = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(relation=OffsetFrom(parent_va.page, 0x1000)))
        child_pa = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(relation=OffsetFrom(parent_pa.page, 0x1000)))
        BatchAllocationStrategy().solve([parent_va, parent_pa, child_va, child_pa], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(child_va.allocated, parent_va.allocated + 0x1000)
        self.assertEqual(child_pa.allocated, parent_pa.allocated + 0x1000)

    def test_derived_from_masks_source(self):
        src = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(exact=0x1_0000_0000))
        and_mask = ~0x1_0000_0000 & 0xFFFFFFFFFFFFFFFF
        drv = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(relation=DerivedFrom(src.page, and_mask=and_mask, or_mask=0x2000_0000)))
        BatchAllocationStrategy().solve([src, drv], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(drv.allocated, (0x1_0000_0000 & and_mask) | 0x2000_0000)

    def test_derived_from_not_mask_flips_bits(self):
        src = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(exact=0x1_0000_0000))
        drv = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(relation=DerivedFrom(src.page, not_mask=0x1000, or_mask=0x2000)))
        BatchAllocationStrategy().solve([src, drv], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(drv.allocated, (0x1_0000_0000 ^ 0x1000) | 0x2000)

    def test_cycle_is_detected(self):
        # Forward-referencing cycle: each request's own .addr (not its Page's frozen
        # .addr) carries the relation, so both Page objects can exist before either
        # AllocRequest's constraint references the other.
        page_a, page_b = _page(), _page()
        a = _req(page_a, RV.AddressType.PHYSICAL, addr=AddrSpec(relation=SameAs(page_b)))
        b = _req(page_b, RV.AddressType.PHYSICAL, addr=AddrSpec(relation=SameAs(page_a)))
        with self.assertRaises(Exception):
            BatchAllocationStrategy().solve([a, b], [], _addrgen(), RandNum(seed=1))


class TestRelationOverlap(unittest.TestCase):
    """A forced relation value must not silently double-reserve an unrelated span."""

    def test_derived_onto_unrelated_reservation_raises(self):
        # A pinned page owns 0x1_0000_0000; a derived value forced onto it must fail loudly.
        unrelated = _phys_req(addr=AddrSpec(exact=0x1_0000_0000))
        src = _phys_req(addr=AddrSpec(exact=0x2_0000_0000))
        drv = _phys_req(addr=AddrSpec(relation=DerivedFrom(src.page, and_mask=0, or_mask=0x1_0000_0000)))
        with self.assertRaises(AddrGenError) as cm:
            BatchAllocationStrategy().solve([unrelated, src, drv], [], _addrgen(), RandNum(seed=1))
        # No id/name in the message anymore -- assert on the actual raised text.
        self.assertIn("overlaps already-reserved", str(cm.exception))

    def test_child_inside_own_anchor_span_is_allowed(self):
        # A 4KB child carved inside its 2MB parent's own reservation is legitimate.
        parent_va, parent_pa = _linear_req(size=RV.RiscvPageSizes.S2MB), _phys_req(size=RV.RiscvPageSizes.S2MB)
        child_va = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(relation=OffsetFrom(parent_va.page, 0x1000)))
        child_pa = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(relation=OffsetFrom(parent_pa.page, 0x1000)))
        BatchAllocationStrategy().solve([parent_va, parent_pa, child_va, child_pa], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(child_pa.allocated, parent_pa.allocated + 0x1000)
        self.assertEqual(child_va.allocated, parent_va.allocated + 0x1000)

    def test_sibling_children_of_one_anchor_may_overlap(self):
        # Two linked children of one anchor with enlarged (e.g. g-stage superpage window)
        # reservations overlap each other legitimately: same relation family.
        anchor = _phys_req(addr=AddrSpec(exact=0x1_4020_0000))
        kid1 = _req(_page(), RV.AddressType.PHYSICAL, size=0x200000, addr=AddrSpec(relation=OffsetFrom(anchor.page, 0x1000)))
        kid2 = _req(_page(), RV.AddressType.PHYSICAL, size=0x200000, addr=AddrSpec(relation=OffsetFrom(anchor.page, 0x2000)))
        BatchAllocationStrategy().solve([anchor, kid1, kid2], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(kid1.allocated, 0x1_4020_1000)
        self.assertEqual(kid2.allocated, 0x1_4020_2000)

    def test_derived_onto_caller_reserved_span_raises(self):
        # Caller-reserved spans (RiescueD section LMAs, ;#reserve_memory) are off-limits too.
        src = _phys_req(addr=AddrSpec(exact=0x2_0000_0000))
        drv = _phys_req(addr=AddrSpec(relation=DerivedFrom(src.page, and_mask=0, or_mask=0x1_0000_0000)))
        reserved = [(RV.AddressType.PHYSICAL, 0x1_0000_0000, 0x1000)]
        with self.assertRaises(AddrGenError):
            BatchAllocationStrategy().solve([src, drv], [], _addrgen(), RandNum(seed=1), reserved_spans=reserved)

    def test_exact_alias_may_cover_caller_reserved_span(self):
        # An identity destination aliases its exact source. If that source intentionally
        # names caller-reserved MMIO, the alias must be admitted at the same address.
        src = _phys_req(addr=AddrSpec(exact=0x1_0000_0000))
        alias = _phys_req(addr=AddrSpec(relation=SameAs(src.page)))
        reserved = [(RV.AddressType.PHYSICAL, 0x1_0000_0000, 0x1000)]

        BatchAllocationStrategy().solve([src, alias], [], _addrgen(), RandNum(seed=1), reserved_spans=reserved)

        self.assertEqual(alias.allocated, src.allocated)

    def test_non_overlapping_derivation_unchanged(self):
        # A derived value landing in free space is placed with no error (baseline behavior).
        src = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(exact=0x1_0000_0000))
        and_mask = ~0x1_0000_0000 & 0xFFFFFFFFFFFFFFFF
        drv = _req(_page(), RV.AddressType.LINEAR, addr=AddrSpec(relation=DerivedFrom(src.page, and_mask=and_mask, or_mask=0x2000_0000)))
        BatchAllocationStrategy().solve([src, drv], [], _addrgen(), RandNum(seed=1))
        self.assertEqual(drv.allocated, 0x2000_0000)

    def test_free_anchor_moves_when_derived_child_hits_a_pin(self):
        """A free anchor and its forced child are one placement decision."""
        for seed in range(16):
            exact = _phys_req(addr=AddrSpec(exact=0x8000_1000))
            anchor_page = _page()
            anchor = AllocRequest(
                page=anchor_page,
                addr_type=RV.AddressType.PHYSICAL,
                size=0x1000,
                addr=AddrSpec(and_mask=0xFFFFFFFFFFFFE000),
            )
            child = _phys_req(
                addr=AddrSpec(
                    relation=DerivedFrom(
                        anchor_page,
                        and_mask=0xFFFFFFFFFFFFFFFF,
                        or_mask=0x1000,
                    )
                )
            )
            BatchAllocationStrategy().solve([exact, anchor, child], [], _addrgen(seed), RandNum(seed))
            self.assertEqual(child.allocated, anchor.allocated | 0x1000)
            self.assertNotEqual(child.allocated, exact.allocated)


class TestSuperpageShadow(unittest.TestCase):
    """A large (superpage) free draw whose reserve_size is smaller than its pagesize must
    not place a leaf whose full aligned span shadows an unrelated reserved/fixed window
    (e.g. the code/runtime VAs) sitting past the reserved head -- while a page that merely
    has a large *alignment* (but a small mapped size / no leaf) keeps its small footprint."""

    @staticmethod
    def _mem(size):
        return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": size, "cacheable": True, "configurable": True}}})

    def _free_2mb(self):
        # 2MB pagesize, small (4KB) reserve_size, 2MB alignment -- the vulnerable leaf shape.
        page = _page(RV.RiscvPageSizes.S2MB)
        mask = RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S2MB)
        return AllocRequest(page=page, addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(and_mask=mask))

    def test_superpage_leaf_never_shadows_reserved_window(self):
        # Four 2MB slots; a reserved "code" window sits inside the first slot, past a free
        # 4KB head. The 2MB leaf must land in a clean slot and never cover the window.
        # Without the fix the draw (sized to reserve_size) can land on the first slot's free
        # head, whose 2MB leaf shadows the window.
        code_lo, code_hi = 0x8000_1000, 0x8000_5000
        reserved = [(RV.AddressType.PHYSICAL, code_lo, code_hi - code_lo)]
        super_bytes = RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S2MB)
        for seed in range(16):
            addrgen = AddrGen(RandNum(seed=seed), self._mem("0x800000"))
            addrgen.reserve_memory(address_type=RV.AddressType.PHYSICAL, start_address=code_lo, size=code_hi - code_lo)
            req = self._free_2mb()
            BatchAllocationStrategy().solve([req], [], addrgen, RandNum(seed=seed), reserved_spans=reserved)
            leaf_lo, leaf_hi = req.allocated, req.allocated + super_bytes
            self.assertFalse(leaf_lo < code_hi and code_lo < leaf_hi, f"seed {seed}: 2MB leaf [0x{leaf_lo:x},0x{leaf_hi:x}) shadows reserved [0x{code_lo:x},0x{code_hi:x})")

    def test_superpage_reserves_only_footprint_not_full_span(self):
        # A 2MB-pagesize page with a 4KB reserve_size must reserve only its 4KB footprint,
        # leaving the rest of its 2MB span free for later draws (no over-reserving). Both a
        # 2MB page and a 4KB page must fit inside a single 2MB region -- impossible if the
        # 2MB page had reserved the full 2MB.
        addrgen = AddrGen(RandNum(seed=3), self._mem("0x200000"))  # exactly one 2MB slot
        big, small = self._free_2mb(), _phys_req()
        BatchAllocationStrategy().solve([big, small], [], addrgen, RandNum(seed=3))
        self.assertIsNotNone(big.allocated)
        self.assertIsNotNone(small.allocated)
        self.assertNotEqual(big.allocated, small.allocated)

    def test_large_alignment_small_page_keeps_footprint(self):
        # A bare 4KB page pinned to 1GB *alignment* (large and_mask, no leaf) must keep its
        # 4KB footprint -- never inflated to a 1GB draw that cannot fit small DRAM.
        # (Regression: sizing the draw by the and-mask granule broke test_vs.s's phys3.)
        addrgen = AddrGen(RandNum(seed=1), self._mem("0x200000"))  # 2MB DRAM, 1GB-aligned base
        one_gb_align = 0xFFFFFFFFC0000000
        req = AllocRequest(page=_page(RV.RiscvPageSizes.S4KB), addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(and_mask=one_gb_align))
        BatchAllocationStrategy().solve([req], [], addrgen, RandNum(seed=1))
        self.assertEqual(req.allocated, 0x8000_0000)  # only 1GB-aligned address in DRAM


class TestForcedChainBeatsFreeAnchor(unittest.TestCase):
    """A forced placement -- a page whose *own* address is a relation (SameAs/OffsetFrom/
    DerivedFrom) -- must be resolved before a *free anchor*: a free-drawn page that merely
    happens to be some relation's target. Otherwise a large free anchor sorts ahead by
    size and its span shadows the forced window.

    This is the two-stage VS-mode code-page bug: the VS-stage (linear) code section is
    ``OffsetFrom`` a PA-only runtime section pinned at ``reset_pc`` -- so its VA is forced
    to ``anchor_pa + delta`` even though anchor and code live in different pools. A larger
    free VS-stage data page (itself a relation target, so a free *anchor*) was drawn first
    and its span covered the forced code VA, so the guest fetched zeros from the code page
    and ran to a fault."""

    _WINDOW_LO = 0x8000_0000
    _WINDOW_HI = 0x8080_0000  # four 2MB slots
    _CODE_VA = 0x8000_1000  # forced code page, inside the first 2MB slot
    _S2MB = RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S2MB)

    def _addrgen_lin_window(self, seed):
        # Confine the (otherwise huge) linear pool to a handful of 2MB slots so the free VA
        # anchor genuinely contends with the code VA, mirroring RiescueD's DRAM-mirrored
        # VS-stage pool. The forced code page lives in the first slot; the free anchor must
        # take one of the others.
        addrgen = _addrgen(seed)
        addrgen.reserve_memory(address_type=RV.AddressType.LINEAR, start_address=0, size=self._WINDOW_LO)
        addrgen.reserve_memory(address_type=RV.AddressType.LINEAR, start_address=self._WINDOW_HI, size=(1 << 57) - self._WINDOW_HI)
        return addrgen

    def test_free_anchor_never_shadows_forced_offset_chain(self):
        s2mb_mask = RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S2MB)
        for seed in range(32):
            addrgen = self._addrgen_lin_window(seed)
            # PA-only anchor pinned at the reset base (stands in for the runtime section);
            # physical, so it does not itself reserve anything in the linear VA pool.
            anchor = _page()
            anchor_req = AllocRequest(page=anchor, addr_type=RV.AddressType.PHYSICAL, size=0x1000, addr=AddrSpec(exact=self._WINDOW_LO))
            # Forced (linear) code page: VA == anchor_pa + delta -- a small footprint in the
            # first 2MB slot. It lives in a different pool than its anchor (VA vs PA).
            code = _page()
            code_req = AllocRequest(page=code, addr_type=RV.AddressType.LINEAR, size=0x1000, addr=AddrSpec(relation=OffsetFrom(anchor, self._CODE_VA - self._WINDOW_LO)))
            # A 2MB free linear page that is itself a relation anchor. The
            # forced code chain must be committed first so this broad free
            # draw cannot shadow the code VA.
            data = _page(RV.RiscvPageSizes.S2MB)
            data_req = AllocRequest(page=data, addr_type=RV.AddressType.LINEAR, size=self._S2MB, addr=AddrSpec(and_mask=s2mb_mask))
            dep = _page()
            dep_req = AllocRequest(page=dep, addr_type=RV.AddressType.LINEAR, size=0x1000, addr=AddrSpec(relation=OffsetFrom(data, 0x1000)))
            BatchAllocationStrategy().solve([anchor_req, code_req, data_req, dep_req], [], addrgen, RandNum(seed=seed))
            self.assertEqual(code_req.allocated, self._CODE_VA, f"seed {seed}: forced code VA moved")
            clo, chi = code_req.allocated, code_req.allocated + code_req.size
            dlo, dhi = data_req.allocated, data_req.allocated + data_req.size
            self.assertFalse(dlo < chi and clo < dhi, f"seed {seed}: free data anchor [0x{dlo:x},0x{dhi:x}) shadows forced code [0x{clo:x},0x{chi:x})")


class TestRegions(unittest.TestCase):
    def test_floating_region_cannot_steal_exact_physical_span(self):
        base = 0x8000_0000
        region = MemoryRegion(size=0x1000, align=0x1000)
        exact = _req(
            _page(),
            RV.AddressType.PHYSICAL,
            size=0x2000,
            addr=AddrSpec(exact=base),
        )
        member = _req(
            _page(),
            RV.AddressType.PHYSICAL,
            addr=AddrSpec(region=region),
        )
        addrgen = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=base, size=0x3000)]),
        )

        result = BatchAllocationStrategy().solve(
            [exact, member],
            [region],
            addrgen,
            RandNum(seed=1),
        )

        self.assertEqual(exact.allocated, base)
        self.assertEqual(result.region_bases[region], base + 0x2000)
        self.assertEqual(member.allocated, base + 0x2000)

    def test_member_lands_inside_floating_region(self):
        region = MemoryRegion(size=0x10000, align=0x10000)
        members = [_req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region)) for _ in range(4)]
        res = BatchAllocationStrategy().solve(members, [region], _addrgen(), RandNum(seed=1))
        base = res.region_bases[region]
        self.assertEqual(base & (0x10000 - 1), 0, "region base not aligned")
        pas = []
        for m in members:
            self.assertTrue(base <= m.allocated < base + 0x10000, "member outside region")
            pas.append((m.allocated, m.allocated + m.size))
        self.assertFalse(_spans_overlap(pas), "members overlap inside region")

    def test_duplicate_floating_region_is_one_declaration(self):
        base = 0x8000_0000
        region = MemoryRegion(size=0x1000, align=0x1000)
        addrgen = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=base, size=region.size)]),
        )

        result = BatchAllocationStrategy().solve(
            [],
            [region, region],
            addrgen,
            RandNum(seed=1),
        )

        self.assertEqual(result.region_bases[region], base)

    def test_fixed_region_base_is_pinned(self):
        region = MemoryRegion(size=0x10000, align=0x10000, base=0x1_0000_0000)
        m = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region))
        res = BatchAllocationStrategy().solve([m], [region], _addrgen(), RandNum(seed=1))
        self.assertEqual(res.region_bases[region], 0x1_0000_0000)
        self.assertTrue(0x1_0000_0000 <= m.allocated < 0x1_0001_0000)

    def test_fixed_region_describes_a_range_without_reserving_every_byte(self):
        base = 0x1_0000_0000
        region = MemoryRegion(size=0x10000, align=0x1000, base=base)
        exact = _req(
            _page(),
            RV.AddressType.PHYSICAL,
            addr=AddrSpec(exact=base, bits=56),
        )
        member = _req(
            _page(),
            RV.AddressType.PHYSICAL,
            addr=AddrSpec(region=region, bits=56),
        )

        BatchAllocationStrategy().solve(
            [exact, member],
            [region],
            _addrgen(),
            RandNum(seed=1),
        )
        self.assertEqual(exact.allocated, base)
        self.assertTrue(base <= member.allocated < base + region.size)
        self.assertNotEqual(member.allocated, exact.allocated)

    def test_exactly_full_region_never_fails_from_random_retries(self):
        """Every aligned slot exists and is required, so every seed is feasible."""
        for seed in range(16):
            region = MemoryRegion(size=0x40000, align=0x1000, base=0x1_0000_0000)
            members = [_req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region)) for _ in range(64)]
            BatchAllocationStrategy().solve(members, [region], _addrgen(seed), RandNum(seed))
            self.assertEqual(
                {m.allocated for m in members},
                {region.base + offset for offset in range(0, region.size, 0x1000)},
            )

    def test_member_structural_claim_cannot_overlap_exact_linear_page(self):
        space = Space(paging_mode=RV.RiscvPagingModes.SV39)
        base = 0x8000_0000
        region = MemoryRegion(size=0x1000, align=0x1000, base=base)
        exact_page = Page(space)
        exact = AllocRequest(
            page=exact_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(exact=base, bits=39),
            space_key=space,
        )
        member_page = Page(_SPACE)
        member = AllocRequest(
            page=member_page,
            addr_type=RV.AddressType.PHYSICAL,
            size=0x1000,
            addr=AddrSpec(region=region, bits=56),
            claims=(
                SpanClaim(
                    RV.AddressType.PHYSICAL,
                    None,
                    0x1000,
                    ClaimKind.BACKING,
                    "member",
                ),
                SpanClaim(
                    RV.AddressType.LINEAR,
                    space,
                    0x1000,
                    ClaimKind.STRUCTURAL,
                    "member",
                ),
            ),
        )
        addrgen = _addrgen()
        addrgen.make_space_pool(space)

        with self.assertRaises(AddrGenError):
            BatchAllocationStrategy().solve(
                [exact, member],
                [region],
                addrgen,
                RandNum(seed=1),
            )


class TestRegionReservationSymmetry(unittest.TestCase):
    """Solve-time AddrGen reservations must match what ``_commit_solution`` replays.

    Floating regions already book their whole window in AddrGen, so members only
    enter the ledger. Fixed regions book nothing, so members must reserve their
    own spans during the solve -- otherwise AddrGen keeps offering those bytes to
    free draws and the ledger burns a backtrack on every collision.
    """

    def test_fixed_region_member_is_reserved_in_addrgen(self):
        base = 0x9000_0000
        region = MemoryRegion(size=0x1000, align=0x1000, base=base)
        member = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region))
        addrgen = _addrgen()
        BatchAllocationStrategy().solve([member], [region], addrgen, RandNum(seed=1))
        self.assertEqual(member.allocated, base)
        self.assertTrue(addrgen.physical_overlap(base, 0x1000))

    def test_fixed_region_member_outside_dram_is_still_reservable(self):
        # reserve_memory maps any address to a cluster by bit_length; a decoy above
        # every DRAM/MMIO segment must not raise when the solve books its member.
        base = 0x6147_DB84_00000
        region = MemoryRegion(size=0x1000, align=0x1000, base=base)
        member = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region))
        addrgen = _addrgen()
        BatchAllocationStrategy().solve([member], [region], addrgen, RandNum(seed=1))
        self.assertTrue(addrgen.physical_overlap(member.allocated, member.size))

    def test_floating_region_member_does_not_double_reserve_in_addrgen(self):
        """Members of a floating region stay ledger-only; the window is already booked."""
        region = MemoryRegion(size=0x10000, align=0x10000)
        member = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region))
        addrgen = _addrgen()
        reserved = []
        original = addrgen.reserve_memory

        def spy(address_type, start_address, size, interesting_address=False, space_key=None):
            reserved.append((address_type, start_address, size))
            return original(address_type, start_address, size, interesting_address=interesting_address, space_key=space_key)

        addrgen.reserve_memory = spy
        res = BatchAllocationStrategy().solve([member], [region], addrgen, RandNum(seed=1))
        base = res.region_bases[region]
        # The floating window itself is reserved once; the member's span is not a
        # separate AddrGen reservation (commit and solve agree on that).
        self.assertIn((RV.AddressType.PHYSICAL, base, region.size), reserved)
        self.assertNotIn((RV.AddressType.PHYSICAL, member.allocated, member.size), reserved)

    def test_solve_time_fixed_member_blocks_addrgen_before_commit(self):
        """The working clone must see the member mid-solve, not only at commit."""
        base = 0x9000_0000
        region = MemoryRegion(size=0x1000, align=0x1000, base=base)
        member = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region))
        strategy = BatchAllocationStrategy()
        addrgen = _addrgen()
        seen = []

        original_commit = strategy._commit_region_claims

        def wrap(req, value, region_arg, ag, reservations):
            original_commit(req, value, region_arg, ag, reservations)
            seen.append(ag.physical_overlap(value, req.size))

        strategy._commit_region_claims = wrap
        strategy.solve([member], [region], addrgen, RandNum(seed=1))
        self.assertEqual(seen, [True])


class TestExclusionOverride(unittest.TestCase):
    """Fixed ``in_region`` members place by geometry; free draws still honor exclusions.

    Fixed region members select among free bases inside the window and never
    re-intersect AddrGen exclusions. ``exclude=()`` remains the RiescueD contract
    on adopted-decoy AddrSpecs.
    """

    _BASE = 0x8000_0000
    _SIZE = 0x1000

    def _setup(self):
        mem = Memory(dram_ranges=[DramRange(start=self._BASE, size=self._SIZE)])
        excluded = ExcludedRegion.from_interval(self._BASE, self._BASE + self._SIZE)
        addrgen = AddrGen(RandNum(seed=1), mem, excluded_regions=[excluded])
        region = MemoryRegion(size=self._SIZE, align=0x1000, base=self._BASE)
        return addrgen, region

    def test_region_member_with_empty_exclude_places_inside_window(self):
        addrgen, region = self._setup()
        member = _req(
            _page(),
            RV.AddressType.PHYSICAL,
            addr=AddrSpec(region=region, exclude=()),
        )
        BatchAllocationStrategy().solve([member], [region], addrgen, RandNum(seed=1))
        self.assertTrue(self._BASE <= member.allocated < self._BASE + self._SIZE)

    def test_fixed_region_member_places_despite_overlapping_exclusion(self):
        # Geometry-only: a fixed region that coincides with an exclusion still receives
        # its members (adopted decoy / in_pma parity with master).
        addrgen, region = self._setup()
        member = _req(
            _page(),
            RV.AddressType.PHYSICAL,
            addr=AddrSpec(region=region),
        )
        BatchAllocationStrategy().solve([member], [region], addrgen, RandNum(seed=1))
        self.assertTrue(self._BASE <= member.allocated < self._BASE + self._SIZE)

    def test_ordinary_free_draw_still_hits_exclusion(self):
        addrgen, region = self._setup()
        free = _phys_req()
        with self.assertRaises(AddrGenError):
            BatchAllocationStrategy().solve([free], [], addrgen, RandNum(seed=1))


class _RejectFirstN(BatchAllocationStrategy):
    """Rejects the first ``_REJECTIONS`` candidates, then behaves normally."""

    _REJECTIONS = 0

    def __init__(self):
        self._seen = 0

    def _claims_fit(self, *args, **kwargs):
        self._seen += 1
        if self._seen <= self._REJECTIONS:
            self._last_claim_conflict = "forced rejection"
            return False
        return super()._claims_fit(*args, **kwargs)


class _RejectEverything(BatchAllocationStrategy):
    _REGION_REJECTION_BUDGET = 4

    def _claims_fit(self, *args, **kwargs):
        self._last_claim_conflict = "forced rejection"
        return False


class TestRegionPlacementCost(unittest.TestCase):
    """Placing inside a region must not cost anything per candidate address.

    A 1 GiB region holds 262144 4 KiB slots; the members below would each pay that
    (times the occupancy) if selection enumerated candidates instead of counting
    them, and would pay it again for every rejected candidate.
    """

    _BASE = 0x1_0000_0000
    _GIB = 0x4000_0000

    def _region(self, size=None):
        return MemoryRegion(size=size or self._GIB, align=0x1000, base=self._BASE)

    def test_many_members_in_a_huge_region(self):
        region = self._region()
        members = [_req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region)) for _ in range(150)]
        started = time.monotonic()
        BatchAllocationStrategy().solve(members, [region], _addrgen(), RandNum(seed=1))
        elapsed = time.monotonic() - started
        placed = sorted((m.allocated, m.allocated + m.size) for m in members)
        self.assertEqual(len({m.allocated for m in members}), len(members))
        self.assertFalse(_spans_overlap(placed))
        for start, end in placed:
            self.assertTrue(self._BASE <= start and end <= self._BASE + region.size)
        self.assertLess(elapsed, 30.0, "region placement scaled with region size, not occupancy")

    def test_superpage_member_without_a_folded_alignment_mask(self):
        """The builder folds pagesize alignment into ``and_mask``; the solver cannot rely on it.

        Without folding it here, a 2 MiB member in a 1 GiB region would draw 4 KiB
        candidates and have 511 of every 512 rejected by ``_claims_fit``.
        """
        for pagesize in (RV.RiscvPageSizes.S2MB, RV.RiscvPageSizes.S1GB):
            alignment = RV.RiscvPageSizes.memory(pagesize)
            region = MemoryRegion(size=4 * alignment, align=0x1000, base=self._BASE)
            member = _req(_page(pagesize), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region))
            BatchAllocationStrategy().solve([member], [region], _addrgen(), RandNum(seed=1))
            self.assertEqual(member.allocated % alignment, 0, f"{pagesize.name} member is misaligned")
            self.assertTrue(self._BASE <= member.allocated < self._BASE + region.size)

    def test_selection_does_not_loop_over_candidate_addresses(self):
        """A structural guard: the selector must not iterate a candidate range.

        Scoped to ``_place_in_region`` on purpose -- ``range`` is legitimate
        elsewhere in this module (``_SpanIndex`` walks cells on every claim check).
        """
        source = textwrap.dedent(inspect.getsource(BatchAllocationStrategy._place_in_region))
        tree = ast.parse(source)
        called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertNotIn("range", called)
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))], "candidate selection must not loop")


class TestRegionMemberGeometry(unittest.TestCase):
    """A region member honors its own masks, width, and pagesize alignment -- nothing else."""

    _BASE = 0x1_0000_0000

    def _place(self, addr_kw, pagesize=RV.RiscvPageSizes.S4KB, region_size=0x40000, seeds=8, size=None, reserve_size=None):
        placed = set()
        for seed in range(seeds):
            region = MemoryRegion(size=region_size, align=0x1000, base=self._BASE)
            page = Page(space=_SPACE, pagesize=pagesize, reserve_size=reserve_size)
            member = _req(page, RV.AddressType.PHYSICAL, size=size, addr=AddrSpec(region=region, **addr_kw))
            BatchAllocationStrategy().solve([member], [region], _addrgen(seed), RandNum(seed=seed))
            placed.add(member.allocated)
        return placed

    def test_and_mask_coarsens_placement(self):
        placed = self._place({"and_mask": ~0xFFFF & 0xFFFFFFFFFFFFFFFF})
        self.assertEqual(placed, {self._BASE, self._BASE + 0x10000, self._BASE + 0x20000, self._BASE + 0x30000})

    def test_non_contiguous_and_mask_is_honored(self):
        and_mask = 0xFFFFFFFFFFFFFFFF & ~0x2000 & ~0xFFF
        placed = self._place({"and_mask": and_mask})
        for value in placed:
            self.assertEqual(value & 0x2000, 0, f"0x{value:x} set a bit the and_mask cleared")
        self.assertGreater(len(placed), 1)

    def test_or_mask_forces_bits(self):
        placed = self._place({"or_mask": 0x20000})
        for value in placed:
            self.assertEqual(value & 0x20000, 0x20000, f"0x{value:x} dropped the or_mask bit")
        self.assertGreater(len(placed), 1)

    def test_bits_bounds_placement(self):
        base = 0x1_0000
        region = MemoryRegion(size=0x40000, align=0x1000, base=base)
        for seed in range(8):
            member = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region, bits=17))
            BatchAllocationStrategy().solve([member], [region], _addrgen(seed), RandNum(seed=seed))
            self.assertLess(member.allocated, 1 << 17, "placement exceeded the requested address width")

    def test_or_mask_conflicting_with_pagesize_alignment_is_rejected(self):
        region = MemoryRegion(size=0x400000, align=0x1000, base=self._BASE)
        member = _req(_page(RV.RiscvPageSizes.S2MB), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region, or_mask=0x1000))
        with self.assertRaisesRegex(AddrGenError, "no free base for member"):
            BatchAllocationStrategy().solve([member], [region], _addrgen(), RandNum(seed=1))

    def test_reserve_size_larger_than_pagesize_still_fits_without_overlap(self):
        # Room to spare: a 0x2000 span placed on a 4 KiB grid can strand the 4 KiB
        # below it, so an exactly-sized region would only admit the perfect packing.
        region = MemoryRegion(size=0x10000, align=0x1000, base=self._BASE)
        members = [
            _req(
                Page(space=_SPACE, pagesize=RV.RiscvPageSizes.S4KB, reserve_size=0x2000),
                RV.AddressType.PHYSICAL,
                size=0x2000,
                addr=AddrSpec(region=region),
            )
            for _ in range(4)
        ]
        BatchAllocationStrategy().solve(members, [region], _addrgen(), RandNum(seed=1))
        self.assertFalse(_spans_overlap([(m.allocated, m.allocated + m.size) for m in members]))

    def test_reserve_size_smaller_than_pagesize_keeps_pagesize_alignment(self):
        alignment = RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S2MB)
        placed = self._place({}, pagesize=RV.RiscvPageSizes.S2MB, region_size=4 * alignment, size=0x1000, reserve_size=0x1000)
        for value in placed:
            self.assertEqual(value % alignment, 0, f"0x{value:x} is not 2 MiB aligned")


class TestRegionCandidateRejection(unittest.TestCase):
    """A rejected candidate is one unusable base, not unusable memory."""

    _BASE = 0x1_0000_0000

    def test_rejections_do_not_consume_region_capacity(self):
        """Four members, four slots, two forced rejections: still feasible.

        Charging a rejection to the region (as occupancy) would leave one slot for
        three members.
        """

        class _RejectTwo(_RejectFirstN):
            _REJECTIONS = 2

        region = MemoryRegion(size=0x4000, align=0x1000, base=self._BASE)
        members = [_req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region)) for _ in range(4)]
        _RejectTwo().solve(members, [region], _addrgen(), RandNum(seed=1))
        self.assertEqual(
            {m.allocated for m in members},
            {self._BASE + offset for offset in range(0, region.size, 0x1000)},
        )

    def test_rejected_base_does_not_block_a_neighbour_whose_span_covers_it(self):
        """Excluding base 0x2000 must leave base 0x1000 selectable for a 0x2000-wide span."""
        region = MemoryRegion(size=0x4000, align=0x1000, base=0x1000)
        page = Page(space=_SPACE, pagesize=RV.RiscvPageSizes.S4KB, reserve_size=0x2000)
        req = _req(page, RV.AddressType.PHYSICAL, size=0x2000, addr=AddrSpec(region=region))
        strategy = BatchAllocationStrategy()
        rng = RandNum(seed=1)
        seen = {strategy._place_in_region(req, 0x1000, region, [], [0x2000], rng) for _ in range(100)}
        self.assertEqual(seen, {0x1000, 0x3000})

    def test_rejection_budget_raises_without_touching_the_search_budget(self):
        region = MemoryRegion(size=0x40000, align=0x1000, base=self._BASE)
        member = _req(_page(), RV.AddressType.PHYSICAL, addr=AddrSpec(region=region))
        with self.assertRaisesRegex(AddrGenError, "candidates rejected in region"):
            _RejectEverything().solve([member], [region], _addrgen(), RandNum(seed=1))
        self.assertEqual(_RejectEverything._BACKTRACK_BUDGET, BatchAllocationStrategy._BACKTRACK_BUDGET)


class _Budget16Strategy(BatchAllocationStrategy):
    _BACKTRACK_BUDGET = 16


class TestSearchTerminates(unittest.TestCase):
    """Search must stop after a bounded number of backtracks."""

    _BASE = 0x8000_0000
    _SLOT = 0x1000
    _SPACE = Space(paging_mode=RV.RiscvPagingModes.SV39)

    @staticmethod
    def _mem(n_slots):
        return Memory(
            dram_ranges=[
                DramRange(start=TestSearchTerminates._BASE, size=n_slots * TestSearchTerminates._SLOT),
            ]
        )

    def _identity_setup(self, n_slots, n_reserved, seed):
        addrgen = AddrGen(RandNum(seed=seed), self._mem(n_slots))
        addrgen.make_space_pool(self._SPACE)
        for index in range(n_reserved):
            addrgen.reserve_memory(
                RV.AddressType.LINEAR,
                self._BASE + index * self._SLOT,
                self._SLOT,
                space_key=self._SPACE,
            )
        identity = AllocRequest(
            page=Page(self._SPACE),
            addr_type=RV.AddressType.PHYSICAL,
            size=self._SLOT,
            space_key=self._SPACE,
            addr=AddrSpec(and_mask=~(self._SLOT - 1), bits=64),
        )
        return addrgen, identity

    def test_local_candidate_rejection_exhausts_budget(self):
        """Greedy roots charge the budget when ``_bundle_fits`` rejects a candidate."""
        addrgen, identity = self._identity_setup(18, 17, seed=2)
        with self.assertRaisesRegex(AddrGenError, "search budget exhausted"):
            _Budget16Strategy().solve([identity], [], addrgen, RandNum(seed=2))

    def test_recursive_subtree_failure_exhausts_budget(self):
        """Non-greedy rollback roots charge the budget when a subtree search fails."""
        anchor_page = Page(Space(paging_mode=RV.RiscvPagingModes.DISABLE))
        anchor = AllocRequest(
            page=anchor_page,
            addr_type=RV.AddressType.PHYSICAL,
            size=self._SLOT,
            addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000, bits=64),
        )
        derived = AllocRequest(
            page=Page(Space(paging_mode=RV.RiscvPagingModes.DISABLE)),
            addr_type=RV.AddressType.PHYSICAL,
            size=self._SLOT,
            addr=AddrSpec(
                relation=DerivedFrom(
                    anchor_page,
                    and_mask=0xFFFFFFFFFFFFF000,
                    random_mask=0xFFF,
                ),
                and_mask=0xFFFFFFFFFFFFF000,
                bits=64,
            ),
        )
        blockers = [
            AllocRequest(
                page=Page(Space(paging_mode=RV.RiscvPagingModes.DISABLE)),
                addr_type=RV.AddressType.PHYSICAL,
                size=self._SLOT,
                addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000, bits=64),
            )
            for _ in range(17)
        ]
        with self.assertRaisesRegex(AddrGenError, "search budget exhausted"):
            _Budget16Strategy().solve(
                [anchor, derived, *blockers],
                [],
                AddrGen(RandNum(seed=0), self._mem(18)),
                RandNum(seed=0),
            )

    def test_success_just_below_budget(self):
        """A feasible solve must succeed when failures stay under the budget."""

        class _Budget17Strategy(BatchAllocationStrategy):
            _BACKTRACK_BUDGET = 17

        addrgen, identity = self._identity_setup(18, 17, seed=0)
        _Budget17Strategy().solve([identity], [], addrgen, RandNum(seed=0))
        self.assertEqual(identity.allocated, self._BASE + 17 * self._SLOT)


class TestOrderIndependentCorrectness(unittest.TestCase):
    """Any request order yields a valid result; addresses may differ between orders."""

    def _build_set(self):
        """Build the named entities as plain Python locals -- no id/name lookup is
        needed even for shuffled re-identification, since the Page/AllocRequest
        objects themselves are held directly."""
        entities = {}

        def add(name, va_addr=None, pa_addr=None):
            va_req = _req(_page(), RV.AddressType.LINEAR, addr=va_addr or AddrSpec())
            pa_req = _req(_page(), RV.AddressType.PHYSICAL, addr=pa_addr or AddrSpec())
            entities[name] = (va_req, pa_req)
            return va_req.page, pa_req.page

        parent_va, parent_pa = add("parent")
        add("child", va_addr=AddrSpec(relation=OffsetFrom(parent_va, 0x1000)), pa_addr=AddrSpec(relation=OffsetFrom(parent_pa, 0x1000)))
        add("alias", pa_addr=AddrSpec(relation=SameAs(parent_pa)))
        add("pinned", va_addr=AddrSpec(exact=0x8000_0000), pa_addr=AddrSpec(exact=0x9000_0000))
        for i in range(5):
            add(f"f{i}")

        reqs = []
        for va_req, pa_req in entities.values():
            reqs.extend([va_req, pa_req])
        return entities, reqs

    def test_all_orders_produce_valid_result(self):
        for trial in range(6):
            entities, reqs = self._build_set()
            rnd = random.Random(trial)
            rnd.shuffle(reqs)
            BatchAllocationStrategy().solve(reqs, [], _addrgen(seed=7), RandNum(seed=7))

            def va_of(name):
                return entities[name][0].allocated

            def pa_of(name):
                return entities[name][1].allocated

            # Relations hold regardless of the order requests were added.
            self.assertEqual(va_of("child"), va_of("parent") + 0x1000)
            self.assertEqual(pa_of("child"), pa_of("parent") + 0x1000)
            self.assertEqual(pa_of("alias"), pa_of("parent"))
            self.assertEqual(va_of("pinned"), 0x8000_0000)
            # Physical spans (minus the shared alias) do not overlap.
            pas = []
            for name, (_va_req, pa_req) in entities.items():
                if name == "alias":
                    continue
                pas.append((pa_req.allocated, pa_req.allocated + pa_req.size))
            self.assertFalse(_spans_overlap(pas), f"overlap on trial {trial}")


class TestGranuleAwareSolver(unittest.TestCase):
    """Granule claims expand after the draw; search retires whole preimages."""

    GRANULE = 1 << 30

    def _addrgen(self, seed=1):
        addrgen = _addrgen(seed=seed)
        addrgen.make_space_pool(_SPACE)
        return addrgen

    def _linear_with_granule(self, addr=None, size=0x1000, granule=None, page=None):
        page = page or _page()
        granule = self.GRANULE if granule is None else granule
        return AllocRequest(
            page=page,
            addr_type=RV.AddressType.LINEAR,
            size=size,
            addr=addr or AddrSpec(and_mask=0xFFFFFFFFFFFFF000),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    size,
                    ClaimKind.ADDRESS,
                    page,
                ),
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    size,
                    ClaimKind.RESERVATION,
                    page,
                    allow_contained_share=True,
                    granule=granule,
                ),
            ),
        )

    def test_granule_affects_mrv_but_not_candidate_draw_size(self):
        req = self._linear_with_granule()
        strategy = BatchAllocationStrategy()
        self.assertEqual(strategy._reservation_span(req), 0x1000)
        self.assertEqual(strategy._validation_span(req), self.GRANULE)

    def test_mrv_includes_fixed_descendant_coverage(self):
        root_page = _page()
        child_page = _page(RV.RiscvPageSizes.S1GB)
        root = _req(
            root_page,
            RV.AddressType.PHYSICAL,
            size=0x1000,
        )
        child = AllocRequest(
            page=child_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(relation=SameAs(root_page)),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    self.GRANULE,
                    ClaimKind.COVERAGE,
                    child_page,
                ),
            ),
        )
        small = _phys_req()
        strategy = BatchAllocationStrategy()
        children = {root_page: [child]}

        self.assertEqual(strategy._reservation_span(root), 0x1000)
        self.assertLess(
            strategy._variable_key(root, children),
            strategy._variable_key(small, children),
        )

    def test_commit_solution_replays_actual_granule_span(self):
        exact = self.GRANULE + 0x1000
        req = self._linear_with_granule(addr=AddrSpec(exact=exact))
        addrgen = self._addrgen()
        BatchAllocationStrategy().solve([req], [], addrgen, RandNum(seed=1))
        # AddrGen may split a wide reserve across clusters; the union must still
        # cover the dynamic granule span (end - start), not merely claim.size.
        self.assertTrue(
            addrgen.linear_overlap(self.GRANULE, self.GRANULE, _SPACE),
            "committed linear pool must contain the full containing granule",
        )
        self.assertFalse(
            addrgen.linear_overlap(self.GRANULE - 0x1000, 0x1000, _SPACE),
            "bytes below the containing granule must stay free",
        )

    def test_free_granule_owner_avoids_exact_root_slot(self):
        pinned = self._linear_with_granule(addr=AddrSpec(exact=0x1000))
        free = self._linear_with_granule()
        started = time.monotonic()
        BatchAllocationStrategy().solve(
            [pinned, free],
            [],
            self._addrgen(),
            RandNum(seed=1),
        )
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertNotEqual(
            free.allocated // self.GRANULE,
            pinned.allocated // self.GRANULE,
        )

    def test_same_as_preimage_retires_child_granule_at_root(self):
        root_page = _page()
        child_page = _page()
        root = AllocRequest(
            page=root_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.ADDRESS,
                    root_page,
                ),
            ),
        )
        child = AllocRequest(
            page=child_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(relation=SameAs(root_page)),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.RESERVATION,
                    root_page,
                    allow_contained_share=True,
                    granule=self.GRANULE,
                ),
            ),
        )
        strategy = BatchAllocationStrategy()
        value = self.GRANULE + 0x234000
        intervals = strategy._root_retire_intervals(
            root,
            value,
            {root_page: value, child_page: value},
            {root_page: root, child_page: child},
            {root_page: [child]},
        )
        self.assertEqual(intervals, [(self.GRANULE, self.GRANULE)])

    def test_offset_from_preimage_translates_back_by_delta(self):
        root_page = _page()
        child_page = _page()
        delta = 0x1000
        root = AllocRequest(
            page=root_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.ADDRESS,
                    root_page,
                ),
            ),
        )
        child = AllocRequest(
            page=child_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(relation=OffsetFrom(root_page, delta)),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.RESERVATION,
                    root_page,
                    allow_contained_share=True,
                    granule=self.GRANULE,
                ),
            ),
        )
        strategy = BatchAllocationStrategy()
        root_value = self.GRANULE - delta
        child_value = root_value + delta
        intervals = strategy._root_retire_intervals(
            root,
            root_value,
            {root_page: root_value, child_page: child_value},
            {root_page: root, child_page: child},
            {root_page: [child]},
        )
        # Child claims [GRANULE, 2*GRANULE); translated back by delta.
        self.assertEqual(intervals, [(self.GRANULE - delta, self.GRANULE)])

    def test_invertible_derived_from_retires_high_granule_bits(self):
        root_page = _page()
        child_page = _page()
        # Preserve all bits except force bit 12; granule bits stay invertible.
        root = AllocRequest(
            page=root_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.ADDRESS,
                    root_page,
                ),
            ),
        )
        child = AllocRequest(
            page=child_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(relation=DerivedFrom(root_page, or_mask=0x1000)),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.RESERVATION,
                    root_page,
                    allow_contained_share=True,
                    granule=self.GRANULE,
                ),
            ),
        )
        strategy = BatchAllocationStrategy()
        root_value = self.GRANULE + 0x2000
        child_value = root_value | 0x1000
        intervals = strategy._root_retire_intervals(
            root,
            root_value,
            {root_page: root_value, child_page: child_value},
            {root_page: root, child_page: child},
            {root_page: [child]},
        )
        self.assertEqual(len(intervals), 1)
        start, size = intervals[0]
        self.assertEqual(size, self.GRANULE)
        self.assertEqual(start & ~(self.GRANULE - 1), self.GRANULE)

    def test_noninvertible_derived_from_raises_unsupported_preimage(self):
        root_page = _page()
        child_page = _page()
        root = AllocRequest(
            page=root_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.ADDRESS,
                    root_page,
                ),
            ),
        )
        child = AllocRequest(
            page=child_page,
            addr_type=RV.AddressType.LINEAR,
            size=0x1000,
            addr=AddrSpec(relation=DerivedFrom(root_page, and_mask=0xFFF)),
            space_key=_SPACE,
            claims=(
                SpanClaim(
                    RV.AddressType.LINEAR,
                    _SPACE,
                    0x1000,
                    ClaimKind.RESERVATION,
                    root_page,
                    allow_contained_share=True,
                    granule=self.GRANULE,
                ),
            ),
        )
        strategy = BatchAllocationStrategy()
        with self.assertRaisesRegex(AddrGenError, "unsupported-preimage"):
            strategy._root_retire_intervals(
                root,
                0x12345000,
                {root_page: 0x12345000, child_page: 0x5000},
                {root_page: root, child_page: child},
                {root_page: [child]},
            )

    def test_region_member_rejects_whole_conflicting_granule(self):
        region = MemoryRegion(base=0, size=2 * self.GRANULE, align=0x1000)
        blocker = self._linear_with_granule(addr=AddrSpec(exact=0x1000))
        member = self._linear_with_granule(addr=AddrSpec(region=region))
        started = time.monotonic()
        BatchAllocationStrategy().solve(
            [blocker, member],
            [region],
            self._addrgen(seed=2),
            RandNum(seed=2),
        )
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertGreaterEqual(member.allocated, self.GRANULE)
        self.assertLess(member.allocated, 2 * self.GRANULE)

    def test_free_draw_keeps_original_page_alignment_mask(self):
        # A free granule owner must still be allowed to land at a non-granule-aligned
        # page address; only the claim expands around it.
        req = self._linear_with_granule(addr=AddrSpec(and_mask=0xFFFFFFFFFFFFF000, or_mask=0x1000))
        BatchAllocationStrategy().solve([req], [], self._addrgen(seed=3), RandNum(seed=3))
        self.assertEqual(req.allocated & 0x1000, 0x1000)
        self.assertEqual(req.allocated & 0xFFF, 0)


if __name__ == "__main__":
    unittest.main()
