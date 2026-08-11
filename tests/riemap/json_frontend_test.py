# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the builder-backed JSON frontend (riescue.riemap.json_frontend).

A JSON ``pages`` entry is meant to mean what a RiescueD ``;#page_mapping`` means, so the
classes below mirror tests/dtest_framework/generator/pt_request_builder_test.py and
tests/dtest_framework/generator/pt_request_builder_driver_test.py case for case wherever
the JSON schema can express the same thing. The difference in altitude is deliberate: the
RiescueD tests inspect the *recipes* the translator emits and then drive a build over
them, while the JSON frontend has no recipe surface and always drives a real build -- so
the same semantics are asserted on the generated VAs/PAs and decoded PTEs.

Cases with no JSON equivalent, and why:

- ``TestPageAddrDomains`` / ``TestPhysicalDeriveSource`` / ``TestAddressAndReserved`` /
  ``TestCustomRegion`` and the driver's ``test_in_pma_regions`` /
  ``test_custom_region_pages_land_in_region`` -- JSON has no ``;#random_addr``,
  ``;#reserve_memory``, ``in_pma`` or ``custom_region``. The nearest analogue, "a PA is
  only ever drawn from a declared region", is covered by :class:`TestMemoryMap`.
- ``TestLinkedPages`` and the driver's ``test_random_fixed_linked`` -- no linked/child
  page mappings in the schema.
- ``test_private_maps_add_spaces`` and the driver's ``test_user_page_maps_and_fanout`` /
  ``test_bare_os_map_with_enabled_private_maps_two_stage`` -- no private maps and no
  cross-map page fan-out; multi-space is ``test_multiple_spaces``.
- ``test_page_mapping_va_follows_derive_from`` / ``..._another_page_va`` -- no ``derive_from``.
- ``TestModifyPtNodeFrames`` and the driver's ``test_modify_pt_readback_and_leaf_bits`` /
  ``test_modify_nonleaf_*`` -- ``modify_pt`` / ``modify_nonleaf_pt`` PT-node frames are not
  reachable from the JSON schema. The two concerns of those tests that *are* reachable
  survive: a g-stage force landing on exactly the node it names and nowhere else, and the
  g-stage non-leaf pagesize setting the level of the leaf fronting a VS table, are both in
  :class:`TestGstageForcing`.
- ``test_io_addr_never_secure`` -- no ``io`` attribute.
"""

import json
import tempfile
import unittest
from pathlib import Path

from riescue.riemap.json_frontend import (
    PageAttributes,
    PageEntry,
    PageSpec,
    PageTableConfig,
    PageTableOutput,
    PTEInfo,
    SpaceConfig,
    SpaceOutput,
    generate_page_tables,
)


BASE_MMAP = [["0x80000000", "0x80000000000000"]]
# A secure region has to fit the frontend's fixed 52-bit physical width (see
# generate_page_tables), so it is a low range rather than a bit-55 one.
SECURE_MMAP = [["0x80000000", "0x80000000000000"], {"low": "0x0", "high": "0x40000000", "secure": True}]
SECURE_REGION = (0x0, 0x40000000)
_SECURE_BIT = 0x0080000000000000

# PTE bit positions (Sv39/48/57 leaf + non-leaf layout).
_V, _R, _W, _X, _U, _A, _D = 0, 1, 2, 3, 4, 6, 7


def _config(spaces, mmap=None):
    return PageTableConfig.from_dict({"mmap": BASE_MMAP if mmap is None else mmap, "spaces": spaces})


def _generate(spaces, mmap=None, seed=1):
    return generate_page_tables(_config(spaces, mmap), seed=seed)


def _bit(pte_value, bit):
    return (pte_value >> bit) & 1


def _ppn_addr(pte_value):
    """The physical address a PTE points at (PPN field back to a byte address).

    RV64's PPN is bits [53:10], so the mask matters: an Svnapot leaf sets N at bit 63, which
    would otherwise be shifted into the address."""
    return ((pte_value >> 10) & ((1 << 44) - 1)) << 12


def _only(va_map):
    """The single ``(va, entry)`` of a one-page group."""
    ((va, entry),) = va_map.items()
    return va, entry


def _only_entry(va_map):
    return _only(va_map)[1]


def _leaf_pte(entry, stage=None):
    """The leaf PTE of ``entry``'s walk -- the lowest-level PTE of the requested stage.

    In a two-stage walk the trailing G-stage steps translate the page's own PA, so the
    VS leaf is selected with ``stage=1`` and the final G-stage leaf with ``stage=2``.
    """
    steps = [p for p in entry.ptes if stage is None or p.stage == stage]
    return min(steps, key=lambda p: p.level)


def _split_two_stage(ptes):
    """Split an interleaved two-stage walk into ``(vs_steps, final_gstage_steps)``.

    ``vs_steps`` is ``[(vs_pte, [g_steps translating it]), ...]``; the trailing G-stage
    run after the last VS PTE is the walk of the page's own GPA.
    """
    vs_steps = []
    pending = []
    for pte in ptes:
        if pte.stage == 2:
            pending.append(pte)
        else:
            vs_steps.append((pte, pending))
            pending = []
    return vs_steps, pending


class TestJsonFrontend(unittest.TestCase):
    def test_single_stage_space(self):
        cfg = _config(
            {
                "s": {
                    "twostage": False,
                    "paging_mode": "sv39",
                    "pages": [{"num_pages": 4, "id": "data", "attributes": {"v": 1, "r": 1, "w": 1}}],
                }
            }
        )
        out = generate_page_tables(cfg, seed=1)
        self.assertIn("s", out.spaces)
        space = out.spaces["s"]
        self.assertEqual(space.paging_mode, "sv39")
        self.assertFalse(space.twostage)
        pages = space.pages["data"]
        self.assertEqual(len(pages), 4)
        # Every PTE address in every single-stage walk must be a real entry.
        for va, entry in pages.items():
            for pte in entry.ptes:
                self.assertIn(pte.address, out.entries, f"PTE {pte.address:#x} for VA {va:#x} missing from entries")

    def test_two_stage_space_has_gstage_root(self):
        cfg = _config(
            {
                "v": {
                    "twostage": True,
                    "paging_mode": "sv39",
                    "gstage_paging_mode": "sv48",
                    "pages": [{"num_pages": 2, "id": "d", "attributes": {"v": 1, "r": 1, "w": 1}}],
                }
            }
        )
        out = generate_page_tables(cfg, seed=1)
        space = out.spaces["v"]
        self.assertTrue(space.twostage)
        self.assertEqual(space.gstage_paging_mode, "sv48")
        self.assertIsNotNone(space.top_base_addr)
        self.assertIsNotNone(space.gstage_top_base_addr)
        # Two-stage walks interleave stage 1 and stage 2 PTEs.
        stages = {pte.stage for entry in space.pages["d"].values() for pte in entry.ptes}
        self.assertEqual(stages, {1, 2})

    def test_vs_only_twostage_labels_stage1(self):
        # twostage with g-stage disabled: VS-only walk, PTEs labeled stage 1.
        cfg = _config(
            {
                "v": {
                    "twostage": True,
                    "paging_mode": "sv39",
                    "gstage_paging_mode": "disable",
                    "pages": [{"num_pages": 1, "id": "d", "attributes": {"v": 1, "r": 1, "w": 1}}],
                }
            }
        )
        out = generate_page_tables(cfg, seed=1)
        space = out.spaces["v"]
        self.assertTrue(space.twostage)
        self.assertIsNone(space.gstage_paging_mode)
        stages = {pte.stage for entry in space.pages["d"].values() for pte in entry.ptes}
        self.assertEqual(stages, {1})

    def test_gonly_space_has_gstage_semantics(self):
        # twostage with VS paging disabled: a G-stage-only space. It must be built as a
        # real g-stage map -- 16 KiB aligned root and u=1 leaves -- not a plain satp map.
        cfg = _config(
            {
                "g": {
                    "twostage": True,
                    "paging_mode": "disable",
                    "gstage_paging_mode": "sv39",
                    "pages": [{"num_pages": 2, "id": "d", "attributes": {"v": 1, "r": 1, "w": 1}}],
                }
            }
        )
        out = generate_page_tables(cfg, seed=1)
        space = out.spaces["g"]
        self.assertTrue(space.twostage)
        self.assertEqual(space.paging_mode, "disable")
        self.assertEqual(space.gstage_paging_mode, "sv39")
        # A G-stage-only walk is entirely stage 2.
        stages = {pte.stage for entry in space.pages["d"].values() for pte in entry.ptes}
        self.assertEqual(stages, {2})
        # (a) The g-stage root table is 16 KiB aligned (hgatp requirement).
        self.assertIsNotNone(space.gstage_top_base_addr)
        self.assertEqual(space.gstage_top_base_addr % 0x4000, 0)
        # (a) Every leaf PTE carries u=1 (g-stage leaves are always user).
        u_bit = 1 << 4
        for entry in space.pages["d"].values():
            leaf = min(entry.ptes, key=lambda p: p.level)
            pte_val = out.entries[leaf.address]
            self.assertTrue(pte_val & u_bit, f"leaf PTE {leaf.address:#x} value {pte_val:#x} missing u=1")

    def test_gonly_pinned_high_bit_gpa_zero_extended(self):
        # A pinned GPA with the top sv39 bit (38) set must round-trip zero-extended
        # (GPA canonicalization), not sign-extended the way a VA would be.
        high_gpa = 0x4000001000  # bit 38 set
        cfg = _config(
            {
                "g": {
                    "twostage": True,
                    "paging_mode": "disable",
                    "gstage_paging_mode": "sv39",
                    "pages": [{"num_pages": 1, "id": "hi", "va": hex(high_gpa), "attributes": {"v": 1, "r": 1, "w": 1}}],
                }
            }
        )
        out = generate_page_tables(cfg, seed=1)
        ((va, _),) = out.spaces["g"].pages["hi"].items()
        self.assertEqual(va, high_gpa)
        # Zero-extended: no bits above the 39-bit sv39 GPA width.
        self.assertEqual(va >> 39, 0)

    def test_multiple_spaces(self):
        cfg = _config(
            {
                "single": {"twostage": False, "paging_mode": "sv48", "pages": [{"num_pages": 2, "attributes": {"v": 1, "r": 1}}]},
                "two": {
                    "twostage": True,
                    "paging_mode": "sv39",
                    "gstage_paging_mode": "sv39",
                    "pages": [{"num_pages": 2, "attributes": {"v": 1, "r": 1}}],
                },
            }
        )
        out = generate_page_tables(cfg, seed=3)
        self.assertEqual(set(out.spaces), {"single", "two"})
        self.assertGreater(len(out.entries), 0)

    def test_fixed_va_pa_placed(self):
        cfg = _config(
            {
                "s": {
                    "twostage": False,
                    "paging_mode": "sv39",
                    "pages": [
                        {"num_pages": 1, "id": "fixed", "va": "0x1000", "pa": "0x80010000", "attributes": {"v": 1, "r": 1}},
                        {"num_pages": 2, "id": "dyn", "attributes": {"v": 1, "r": 1}},
                    ],
                }
            }
        )
        out = generate_page_tables(cfg, seed=1)
        fixed_pages = out.spaces["s"].pages["fixed"]
        # The fixed VA is sign-extended canonical; its low bits are preserved and
        # its PA is exactly what was requested.
        ((va, entry),) = fixed_pages.items()
        self.assertEqual(va & 0xFFFFFFFFFFF, 0x1000)
        self.assertEqual(entry.pa, 0x80010000)


class TestPageRequests(unittest.TestCase):
    """Mirrors pt_request_builder_test.TestPageRequests: one page per declared page,
    fixed vs free addresses, masks, secure, and aliasing."""

    def test_one_page_per_num_pages(self):
        # pt_request_builder_test.test_one_request_per_mapping: every page is its own
        # request with a globally unique identity -- here, its own VA and its own PA.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 5, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]}})
        pages = out.spaces["s"].pages["m"]
        self.assertEqual(len(pages), 5)
        self.assertEqual(len({e.pa for e in pages.values()}), 5, "free-drawn pages must not share a PA")

    def test_page_specs_get_separate_ids(self):
        # Each pages[] entry is its own group; an unnamed group falls back to its index.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 1}, {"num_pages": 1, "id": "named"}, {"num_pages": 1}]}})
        self.assertEqual(set(out.spaces["s"].pages), {"0", "named", "2"})

    def test_fixed_va_and_pa_become_exact(self):
        # pt_request_builder_test.test_fixed_va_and_pa_become_exact.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "fx", "va": "0x50000000", "pa": "0x80001000", "attributes": {"v": 1, "r": 1}}]}})
        va, entry = _only(out.spaces["s"].pages["fx"])
        self.assertEqual(va, 0x50000000)
        self.assertEqual(entry.pa, 0x80001000)

    def test_fixed_addr_literals_are_always_hex(self):
        # pt_request_builder_test.test_fixed_addr_pins_underscored_hex_and_decimal covers
        # RiescueD's int(literal, 0) parse (0x -> hex, bare -> decimal). The JSON schema
        # documents va/pa as hex strings and parses base 16 unconditionally, so a bare
        # "1000" is 0x1000 -- not decimal 1000. Pin that, it is the one address-literal
        # semantic that deliberately differs from a page_mapping.
        out = _generate(
            {
                "s": {
                    "paging_mode": "sv39",
                    "pages": [
                        {"id": "bare", "va": "1000", "pa": "80001000", "attributes": {"v": 1, "r": 1}},
                        {"id": "prefixed", "va": "0x2000", "pa": "0x80002000", "attributes": {"v": 1, "r": 1}},
                    ],
                }
            }
        )
        bare_va, bare = _only(out.spaces["s"].pages["bare"])
        pre_va, pre = _only(out.spaces["s"].pages["prefixed"])
        self.assertEqual((bare_va, bare.pa), (0x1000, 0x80001000))
        self.assertEqual((pre_va, pre.pa), (0x2000, 0x80002000))

    def test_free_va_pa_carry_masks(self):
        # pt_request_builder_test.test_free_va_pa_carry_masks: an unpinned side still
        # honors its AND mask. va_and/pa_and are the JSON spelling of address_mask /
        # phys_address_mask.
        out = _generate(
            {
                "s": {
                    "paging_mode": "sv39",
                    "pages": [{"num_pages": 4, "id": "m", "va_and": "0xFFFFFFFFFFE00000", "pa_and": "0xFFFFFFFFFFF00000", "attributes": {"v": 1, "r": 1, "w": 1}}],
                }
            }
        )
        for va, entry in out.spaces["s"].pages["m"].items():
            self.assertEqual(va & 0x1FFFFF, 0, f"VA {va:#x} ignores va_and")
            self.assertEqual(entry.pa & 0xFFFFF, 0, f"PA {entry.pa:#x} ignores pa_and")

    def test_free_va_pa_carry_or_masks(self):
        # va_or/pa_or force bits on after the masked draw (generated = rand & and | or).
        out = _generate(
            {
                "s": {
                    "paging_mode": "sv39",
                    "pages": [
                        {
                            "num_pages": 4,
                            "id": "m",
                            "va_and": "0xFFFFFFFFFFE00000",
                            "va_or": "0x2000",
                            "pa_and": "0xFFFFFFFFFFF00000",
                            "pa_or": "0x3000",
                            "attributes": {"v": 1, "r": 1, "w": 1},
                        }
                    ],
                }
            }
        )
        for va, entry in out.spaces["s"].pages["m"].items():
            self.assertEqual(va & 0x1FFFFF, 0x2000, f"VA {va:#x} ignores va_or")
            self.assertEqual(entry.pa & 0xFFFFF, 0x3000, f"PA {entry.pa:#x} ignores pa_or")

    def test_user_mask_is_combined_with_pagesize_alignment(self):
        # The declared AND mask never loosens the pagesize alignment: a 4 KiB-granular
        # va_and/pa_and on a 2 MiB page still yields 2 MiB-aligned addresses.
        out = _generate(
            {
                "s": {
                    "paging_mode": "sv39",
                    "pages": [{"num_pages": 3, "id": "big", "va_and": "0xFFFFFFFFFFFFF000", "pa_and": "0xFFFFFFFFFFFFF000", "attributes": {"v": 1, "r": 1, "size": "2mb"}}],
                }
            }
        )
        for va, entry in out.spaces["s"].pages["big"].items():
            self.assertEqual(va & 0x1FFFFF, 0, f"VA {va:#x} lost its 2 MiB alignment")
            self.assertEqual(entry.pa & 0x1FFFFF, 0, f"PA {entry.pa:#x} lost its 2 MiB alignment")

    def test_pinned_pa_with_free_va_is_not_identity(self):
        # pt_request_builder_test.test_phys_pinned_unspecified_lin_is_identity asserts the
        # opposite for RiescueD: an MMIO-style mapping with only phys_addr defaults its VA
        # to the PA. The JSON frontend has no such default -- a pinned pa with no va still
        # free-draws the VA. Pinned here so the divergence stays a decision, not a drift.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "mmio", "pa": "0x44000000", "attributes": {"v": 1, "r": 1}}]}})
        va, entry = _only(out.spaces["s"].pages["mmio"])
        self.assertEqual(entry.pa, 0x44000000)
        self.assertNotEqual(va, entry.pa, "a pa-only page must not silently become an identity mapping")

    def test_pinned_pa_aliases_every_page_in_the_group(self):
        # pt_request_builder_test.test_alias_becomes_same_as_on_pa: two mappings sharing
        # one physical page. The JSON spelling is a pinned pa over num_pages > 1 -- every
        # page gets its own VA but they all translate to the same frame.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 3, "id": "al", "pa": "0x80010000", "attributes": {"v": 1, "r": 1, "w": 1}}]}})
        pages = out.spaces["s"].pages["al"]
        self.assertEqual(len(pages), 3, "each aliased page keeps its own VA")
        self.assertEqual({e.pa for e in pages.values()}, {0x80010000})
        for entry in pages.values():
            self.assertEqual(_ppn_addr(out.entries[_leaf_pte(entry).address]), 0x80010000)

    def test_gstage_page_sizes_only_in_two_stage(self):
        # pt_request_builder_test.test_gstage_page_sizes_only_in_two_stage: RiescueD
        # silently drops the g-stage sizes outside a two-stage test and carries them
        # through inside one. The JSON frontend rejects the meaningless combination
        # instead of dropping it (validate_gstage_size_fields), and round-trips it
        # onto the page entry when it is meaningful.
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv39", "pages": [{"attributes": {"v": 1, "gstage_vs_leaf_size": "2mb"}}]}})
        with self.assertRaises(ValueError):
            _generate({"s": {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "disable", "pages": [{"attributes": {"v": 1, "gstage_vs_leaf_size": "2mb"}}]}})
        out = _generate(
            {
                "s": {
                    "twostage": True,
                    "paging_mode": "sv39",
                    "gstage_paging_mode": "sv39",
                    "pages": [{"id": "g", "attributes": {"v": 1, "r": 1, "w": 1, "gstage_vs_leaf_size": "2mb", "gstage_vs_nonleaf_size": "2mb"}}],
                }
            }
        )
        entry = _only_entry(out.spaces["s"].pages["g"])
        self.assertEqual(entry.gstage_vs_leaf_size, "2mb")
        self.assertEqual(entry.gstage_vs_nonleaf_size, "2mb")


class TestPageGeometry(unittest.TestCase):
    """Mirrors pt_request_builder_test.TestPhysReservationPolicy at the level the JSON
    frontend exposes: the ``size`` attribute drives alignment and walk depth, and a
    page's reservation keeps other pages out of its span."""

    def test_pagesize_sets_alignment_and_walk_depth(self):
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 2, "id": "big", "attributes": {"v": 1, "r": 1, "size": "2mb"}}]}})
        for va, entry in out.spaces["s"].pages["big"].items():
            self.assertEqual(entry.size, "2mb")
            self.assertEqual(va & 0x1FFFFF, 0)
            self.assertEqual(entry.pa & 0x1FFFFF, 0)
            # An Sv39 2 MiB leaf sits at level 1, so the walk is root(2) -> leaf(1).
            self.assertEqual([p.level for p in entry.ptes], [2, 1])

    def test_pages_reserve_their_whole_span(self):
        # pt_request_builder_test.test_single_stage_reserves_full_page_by_default: a
        # superpage reserves its full size, so no two pages overlap on either side.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 4, "id": "big", "attributes": {"v": 1, "r": 1, "size": "2mb"}}]}}, seed=7)
        span = 0x200000
        pages = out.spaces["s"].pages["big"]
        for what, bases in (("VA", sorted(pages)), ("PA", sorted(e.pa for e in pages.values()))):
            for lo, hi in zip(bases, bases[1:]):
                self.assertGreaterEqual(hi - lo, span, f"{what}s at {lo:#x} and {hi:#x} overlap")

    def test_size_list_filtered_to_paging_mode(self):
        # An invalid size for the resolved mode is filtered out of a list rather than
        # failing the run (RiescueD resolves pagesize before the translator ever runs).
        out = _generate({"s": {"paging_mode": "sv32", "pages": [{"num_pages": 6, "id": "m", "attributes": {"v": 1, "r": 1, "size": ["4kb", "2mb", "4mb"]}}]}})
        self.assertEqual({e.size for e in out.spaces["s"].pages["m"].values()} - {"4kb", "4mb"}, set())

    def test_invalid_scalar_size_is_rejected(self):
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv32", "pages": [{"attributes": {"v": 1, "size": "1gb"}}]}})

    def test_64kb_napot_bit_defaults_on_and_n_zero_turns_it_off(self):
        # The documented rule: N is auto-set for a 64 KiB page unless the config says n=0.
        napot = 1 << 63
        for declared, expected in ((None, 1), (1, 1), (0, 0)):
            attrs = {"v": 1, "r": 1, "w": 1, "size": "64kb"}
            if declared is not None:
                attrs["n"] = declared
            out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "p", "attributes": attrs}]}})
            entry = _only_entry(out.spaces["s"].pages["p"])
            self.assertEqual(entry.size, "64kb")
            leaf = out.entries[_leaf_pte(entry).address]
            self.assertEqual(1 if leaf & napot else 0, expected, f"n={declared}: leaf {leaf:#x} has the wrong N bit")

    def _napot_block(self, n=None):
        """A 64 KiB page's 16 contiguous leaf PTEs, plus the page's PA."""
        attrs = {"v": 1, "r": 1, "w": 1, "size": "64kb"}
        if n is not None:
            attrs["n"] = n
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "p", "attributes": attrs}]}})
        entry = _only_entry(out.spaces["s"].pages["p"])
        block_base = _leaf_pte(entry).address & ~0x7F
        return [out.entries[block_base + 8 * i] for i in range(16)], entry.pa

    def test_64kb_n_one_block_carries_the_napot_ppn_encoding(self):
        # Svnapot: the block's 16 PTEs are identical and PPN[3:0] holds the 64 KiB encoding
        # 0b1000; hardware substitutes VPN[3:0] for those bits when translating.
        block, pa = self._napot_block(n=1)
        self.assertEqual(len(set(block)), 1, f"an N=1 block's 16 PTEs must be identical: {[hex(v) for v in block]}")
        ppn = _ppn_addr(block[0])
        self.assertEqual(ppn & 0xF000, 0x8000, f"N=1 leaf PPN {ppn:#x} must carry the 64 KiB NAPOT encoding")
        self.assertEqual(ppn & ~0xFFFF, pa & ~0xFFFF, "the NAPOT encoding must sit on the page's own 64 KiB base")

    def test_64kb_n_zero_block_maps_each_4kb_slot_to_its_own_pa(self):
        # Without Svnapot, a 64 KiB declaration is represented as sixteen
        # ordinary 4 KiB mappings.
        block, pa = self._napot_block(n=0)
        base = pa & ~0xFFFF
        for i, value in enumerate(block):
            self.assertEqual(_ppn_addr(value), base + (i << 12), f"n=0 block entry {i} must map its own 4 KiB slot, not a NAPOT-encoded PPN")

    def test_64kb_default_block_is_a_valid_napot_block(self):
        # The default (no declared n) is Svnapot, so it must satisfy the N=1 shape too --
        # identical PTEs with the encoded PPN, never a half-applied mix of the two forms.
        block, pa = self._napot_block()
        self.assertEqual(len(set(block)), 1, "the default 64 KiB block must be a uniform NAPOT block")
        self.assertTrue(block[0] & (1 << 63), "the default 64 KiB block must set N")
        self.assertEqual(_ppn_addr(block[0]), (pa & ~0xFFFF) | 0x8000)


class TestAddressWidths(unittest.TestCase):
    """Mirrors pt_request_builder_test.TestBareAddrWidth: a free-drawn address inherits
    the width its space implies -- the VA the paging mode's linear width (canonically
    sign-extended), the PA the physical width."""

    def test_va_is_canonical_for_paging_mode(self):
        for mode, bits in (("sv39", 39), ("sv48", 48)):
            out = _generate({"s": {"paging_mode": mode, "pages": [{"num_pages": 6, "id": "m", "attributes": {"v": 1, "r": 1}}]}})
            for va in out.spaces["s"].pages["m"]:
                upper = va >> bits
                expected = (1 << (64 - bits)) - 1 if (va >> (bits - 1)) & 1 else 0
                self.assertEqual(upper, expected, f"{mode} VA {va:#x} is not canonically sign-extended")

    def test_gonly_gpa_uses_the_whole_gstage_input_width(self):
        # A bare g-stage space's "VA" is a GPA, which zero-extends -- canonical_va branches
        # on gstage_source and calls make_canonical_gpa. So the top input bit is usable, and
        # a config that asks for it must build.
        for mode, bits in (("sv39", 39), ("sv57", 57)):
            with self.subTest(mode=mode):
                top = 1 << (bits - 1)
                out = _generate(
                    {"g": {"twostage": True, "paging_mode": "disable", "gstage_paging_mode": mode, "pages": [{"num_pages": 4, "id": "m", "va_or": hex(top), "attributes": {"v": 1, "r": 1, "w": 1}}]}}
                )
                for va in out.spaces["g"].pages["m"]:
                    self.assertTrue(va & top, f"GPA {va:#x} lost the requested bit {bits - 1}")
                    self.assertEqual(va >> bits, 0, f"GPA {va:#x} is not zero-extended above the {mode} input width")

    def test_two_stage_pa_fits_the_gstage_input_width(self):
        # Under two-stage the "PA" a page declares is really a GPA fed back into the
        # G-stage walk, so its free draw is capped at the G-stage input width rather than
        # the physical one -- an Sv39 G-stage GPA must fit 39 bits.
        out = _generate({"s": {"twostage": True, "paging_mode": "sv48", "gstage_paging_mode": "sv39", "pages": [{"num_pages": 6, "id": "m", "attributes": {"v": 1, "r": 1}}]}})
        for entry in out.spaces["s"].pages["m"].values():
            self.assertEqual(entry.pa >> 39, 0, f"GPA {entry.pa:#x} exceeds the Sv39 G-stage input width")


class TestMemoryMap(unittest.TestCase):
    """The JSON analogue of pt_request_builder_test.TestCustomRegion: ``mmap`` is the only
    source of physical addresses, so every PA and every page-table node must land inside
    a declared region."""

    MMAP = [["0x80000000", "0xC0000000"], ["0x200000000", "0x240000000"]]

    def _in_mmap(self, addr):
        return any(lo <= addr < hi for lo, hi in ((0x80000000, 0xC0000000), (0x200000000, 0x240000000)))

    def test_page_pas_and_pt_nodes_stay_in_declared_regions(self):
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 6, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]}}, mmap=self.MMAP)
        for entry in out.spaces["s"].pages["m"].values():
            self.assertTrue(self._in_mmap(entry.pa), f"PA {entry.pa:#x} outside every mmap region")
        for pte_addr in out.entries:
            self.assertTrue(self._in_mmap(pte_addr), f"PTE {pte_addr:#x} outside every mmap region")

    def test_disjoint_regions_are_both_usable(self):
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 24, "id": "m", "attributes": {"v": 1, "r": 1}}]}}, mmap=self.MMAP)
        pas = [e.pa for e in out.spaces["s"].pages["m"].values()]
        self.assertTrue(any(p < 0xC0000000 for p in pas))
        self.assertTrue(any(p >= 0x200000000 for p in pas))

    def test_sv39_pt_frames_may_land_in_high_dram_only_mmap(self):
        # Bug A regression: non-secure DRAM starts at 1 TiB (above the Sv39 sign bit)
        # with secure memory below. Structural frames must still place in DRAM -- they
        # are not VA==PA identities and must not be clamped to bits=38.
        mmap = [["0x10000000000", "0x8000000000000"], {"low": "0x0", "high": "0x10000000000", "secure": True}]
        dram_lo, dram_hi = 0x10000000000, 0x8000000000000
        pages = [{"num_pages": 4, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]
        for label, space in (
            ("single-stage", {"paging_mode": "sv39", "pages": pages}),
            ("vs-only", {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "disable", "pages": pages}),
        ):
            with self.subTest(label=label):
                out = _generate({"s": space}, mmap=mmap, seed=1)
                self.assertTrue(out.entries, f"{label}: expected PTEs")
                for pte_addr in out.entries:
                    self.assertTrue(
                        dram_lo <= pte_addr < dram_hi,
                        f"{label}: PTE 0x{pte_addr:x} not in high DRAM",
                    )
                root = out.spaces["s"].top_base_addr
                self.assertIsNotNone(root)
                self.assertTrue(dram_lo <= root < dram_hi, f"{label}: root 0x{root:x} not in high DRAM")


class TestSecure(unittest.TestCase):
    """Mirrors pt_request_builder_test.TestSecureRandomization / test_secure_pa_gets_qualifier:
    a secure page draws from secure memory and its PA carries the STEE bit-55 tag."""

    def test_secure_page_pa_and_leaf_pte_carry_bit55(self):
        out = _generate(
            {
                "s": {
                    "paging_mode": "sv39",
                    "pages": [
                        {"num_pages": 2, "id": "sec", "attributes": {"v": 1, "r": 1, "w": 1, "secure": 1}},
                        {"num_pages": 2, "id": "ns", "attributes": {"v": 1, "r": 1, "w": 1}},
                    ],
                }
            },
            mmap=SECURE_MMAP,
        )
        lo, hi = SECURE_REGION
        for entry in out.spaces["s"].pages["sec"].values():
            self.assertTrue(entry.pa & _SECURE_BIT, f"secure PA {entry.pa:#x} missing bit 55")
            self.assertTrue(lo <= (entry.pa & ~_SECURE_BIT) < hi, f"secure PA {entry.pa:#x} not drawn from the secure region")
            # The leaf PTE points at the same tagged frame the output reports.
            self.assertEqual(_ppn_addr(out.entries[_leaf_pte(entry).address]), entry.pa)

    def test_non_secure_page_has_no_bit55(self):
        # pt_request_builder_test.test_page_pa_not_rolled_when_secure_mode_off: an
        # unmarked page never picks up the secure class on its own.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 4, "id": "ns", "attributes": {"v": 1, "r": 1, "w": 1}}]}}, mmap=SECURE_MMAP)
        for entry in out.spaces["s"].pages["ns"].values():
            self.assertFalse(entry.pa & _SECURE_BIT, f"non-secure PA {entry.pa:#x} sets bit 55")

    def test_secure_page_requires_a_secure_region(self):
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "p", "attributes": {"v": 1, "secure": 1}}]}})
        # A weighted spec that *could* resolve secure is rejected up front too.
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "p", "attributes": {"v": 1, "secure": [{"value": 0, "weight": 9}, {"value": 1, "weight": 1}]}}]}})

    def test_secure_pt_probability_places_nodes_in_secure_memory(self):
        # secure_pt_probability is the JSON knob for RiescueD's secure PT placement; at
        # 100 every non-root table is drawn from secure memory and tagged with bit 55.
        lo, hi = SECURE_REGION
        out = _generate({"s": {"paging_mode": "sv39", "secure_pt_probability": 100, "pages": [{"num_pages": 3, "id": "m", "attributes": {"v": 1, "r": 1}}]}}, mmap=SECURE_MMAP)
        root = out.spaces["s"].top_base_addr
        assert root is not None
        non_root = [a for a in out.entries if not (root <= a < root + 0x1000)]
        self.assertTrue(non_root, "expected page-table nodes below the root")
        for pte_addr in non_root:
            self.assertTrue(pte_addr & _SECURE_BIT, f"PT node {pte_addr:#x} not tagged secure")
            self.assertTrue(lo <= (pte_addr & ~_SECURE_BIT) < hi, f"PT node {pte_addr:#x} not in the secure region")

        clear = _generate({"s": {"paging_mode": "sv39", "secure_pt_probability": 0, "pages": [{"num_pages": 3, "id": "m", "attributes": {"v": 1, "r": 1}}]}}, mmap=SECURE_MMAP)
        for pte_addr in clear.entries:
            self.assertFalse(pte_addr & _SECURE_BIT, f"PT node {pte_addr:#x} tagged secure at probability 0")

    def test_the_documented_secure_region_shape_is_allocatable(self):
        # The documented region is below the fixed 52-bit physical-address
        # limit. A region beginning at bit 56 is not allocatable.
        pages = [{"num_pages": 3, "id": "m", "attributes": {"v": 1, "r": 1}}]
        out = _generate({"s": {"paging_mode": "sv39", "secure_pt_probability": 100, "pages": pages}}, mmap=SECURE_MMAP)
        self.assertTrue(out.entries)
        with self.assertRaises(Exception):
            _generate(
                {"s": {"paging_mode": "sv39", "secure_pt_probability": 100, "pages": pages}},
                mmap=[["0x80000000", "0x80000000000000"], {"low": "0x100000000000000", "high": "0x180000000000000", "secure": True}],
            )

    def test_secure_pt_probability_requires_a_secure_region(self):
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv39", "secure_pt_probability": 50, "pages": [{"attributes": {"v": 1}}]}})

    def test_secure_pt_probability_range_is_validated(self):
        for bad in (-1, 101, "50"):
            with self.assertRaises(ValueError):
                _config({"s": {"paging_mode": "sv39", "secure_pt_probability": bad, "pages": [{}]}})


class TestPerLevelForcing(unittest.TestCase):
    """Mirrors pt_request_builder_test.TestPerLevelForcingForwarded: a ``{base}_level{n}``
    key must reach the PTE at that level. RiescueD asserts the key survives translation;
    here the same guarantee is read back off the decoded PTE, since a dropped force
    silently lets a walk succeed that the test meant to fault."""

    def test_non_leaf_per_level_keys_reach_their_pte(self):
        # A non-leaf level is the one place a {base}_level{n} key is the ONLY way to say
        # anything: no bare base competes for it (see test_base_bits_win_at_the_leaf).
        out = _generate(
            {
                "s": {
                    "paging_mode": "sv39",
                    "pages": [{"id": "f", "attributes": {"v": 1, "r": 1, "w": 1, "x": 1, "v_level1": 0}}],
                }
            }
        )
        entry = _only_entry(out.spaces["s"].pages["f"])
        by_level = {p.level: out.entries[p.address] for p in entry.ptes}
        self.assertEqual(_bit(by_level[1], _V), 0, "v_level1 forcing not applied to the level-1 PTE")
        self.assertEqual(_bit(by_level[2], _V), 1, "v_level1 forcing leaked to the root PTE")

    def test_a_leaf_level_key_reaches_the_leaf_when_no_base_competes(self):
        # With no bare ``u`` declared, u_level0 is the leaf's only source and must land.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "f", "attributes": {"v": 1, "r": 1, "w": 1, "u_level0": 1}}]}})
        entry = _only_entry(out.spaces["s"].pages["f"])
        leaf = out.entries[_leaf_pte(entry).address]
        self.assertEqual(_bit(leaf, _U), 1, "u_level0 forcing not applied to the leaf")

    def test_base_bits_win_at_the_leaf(self):
        # gen_pages' vocabulary: a bare base bit IS the leaf PTE's value, and
        # {base}_level{n} exists to reach the OTHER levels -- so on a collision at the leaf
        # the bare bit wins. Verified against master: {"a": 1, "a_level0": 0} gives A=1.
        # (RiescueD's vocabulary is the reverse, which is why the frontend materializes the
        # bare bit as the leaf force rather than changing the shared resolver's rule.)
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "f", "attributes": {"v": 1, "r": 1, "w": 0, "x": 1, "a": 1, "w_level0": 1, "a_level0": 0}}]}})
        entry = _only_entry(out.spaces["s"].pages["f"])
        leaf = out.entries[_leaf_pte(entry).address]
        self.assertEqual((_bit(leaf, _V), _bit(leaf, _R), _bit(leaf, _X)), (1, 1, 1))
        self.assertEqual(_bit(leaf, _W), 0, "the base w=0 must win over w_level0=1 at the leaf")
        self.assertEqual(_bit(leaf, _A), 1, "the base a=1 must win over a_level0=0 at the leaf")

    def test_a_leaf_level_key_stands_alone_when_no_base_is_declared(self):
        # The other half: with no bare bit to defer to, the leaf key is honored verbatim --
        # so base-wins is precedence, not a blanket override of the per-level vocabulary.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "f", "attributes": {"v": 1, "r": 1, "w": 1, "x_level0": 0}}]}})
        entry = _only_entry(out.spaces["s"].pages["f"])
        self.assertEqual(_bit(out.entries[_leaf_pte(entry).address], _X), 0, "x_level0=0 dropped with no base x to defer to")

    def test_base_bits_win_at_the_leaf_under_two_stage(self):
        # The two-stage path never calls pt_node_levels_with_leaf itself --
        # add_two_stage_mapping computes it internally -- so the rule has to be applied to
        # the attrs before they are handed over, not layered on top of the result.
        out = _generate(
            {
                "s": {
                    "twostage": True,
                    "paging_mode": "sv39",
                    "gstage_paging_mode": "sv39",
                    "pages": [{"id": "f", "attributes": {"v": 1, "r": 1, "w": 1, "a": 1, "a_level0": 0}}],
                }
            }
        )
        entry = _only_entry(out.spaces["s"].pages["f"])
        vs_leaf = out.entries[_leaf_pte(entry, stage=1).address]
        self.assertEqual(_bit(vs_leaf, _A), 1, "the base a=1 must win over a_level0=0 at the VS leaf")

    def test_a_and_d_default_to_one(self):
        # Without hardware A/D update an a=0 leaf faults on first touch, so both default
        # on unless the config says otherwise (mirrors the section-attr default RiescueD
        # applies in _section_attrs).
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "d", "attributes": {"v": 1, "r": 1}}, {"id": "e", "attributes": {"v": 1, "r": 1, "a": 0, "d": 0}}]}})
        defaulted = _only_entry(out.spaces["s"].pages["d"])
        leaf = out.entries[_leaf_pte(defaulted).address]
        self.assertEqual((_bit(leaf, _A), _bit(leaf, _D)), (1, 1))
        explicit = _only_entry(out.spaces["s"].pages["e"])
        leaf = out.entries[_leaf_pte(explicit).address]
        self.assertEqual((_bit(leaf, _A), _bit(leaf, _D)), (0, 0))


class TestRswAndReserved(unittest.TestCase):
    """``rsw`` (bits 9:8) and ``reserved`` (bits 60:54) are ordinary leaf-PTE fields the
    schema has always accepted and no frontend has ever honored -- gen_pages dropped a bare
    one too (its leaf pass iterated ``level_types``, which omits both), so master emits
    rsw=0 reserved=0 for a config asking for 3 and 5. Honoring them is a deliberate
    improvement, not a restoration."""

    def _leaf(self, attrs):
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "p", "attributes": {"v": 1, "r": 1, "w": 1, **attrs}}]}})
        entry = _only_entry(out.spaces["s"].pages["p"])
        return out, entry, {p.level: out.entries[p.address] for p in entry.ptes}

    def test_bare_rsw_and_reserved_reach_the_leaf_pte(self):
        _out, _entry, by_level = self._leaf({"rsw": 3, "reserved": 5})
        self.assertEqual((by_level[0] >> 8) & 0x3, 3, "bare rsw did not reach the leaf PTE's bits 9:8")
        self.assertEqual((by_level[0] >> 54) & 0x7F, 5, "bare reserved did not reach the leaf PTE's bits 60:54")

    def test_the_level_suffixed_form_still_targets_that_level_alone(self):
        # rsw_level{n} already worked on both engines; adding the bare form must not make it
        # leak to the leaf.
        _out, _entry, by_level = self._leaf({"rsw_level1": 1})
        self.assertEqual((by_level[1] >> 8) & 0x3, 1, "rsw_level1 did not reach the level-1 PTE")
        self.assertEqual((by_level[0] >> 8) & 0x3, 0, "rsw_level1 leaked onto the leaf")

    def test_undeclared_fields_stay_zero(self):
        _out, _entry, by_level = self._leaf({})
        self.assertEqual((by_level[0] >> 8) & 0x3, 0)
        self.assertEqual((by_level[0] >> 54) & 0x7F, 0)


class TestGstageForcing(unittest.TestCase):
    """The JSON-reachable half of pt_request_builder_test.TestModifyPtNodeFrames: a
    g-stage forcing attribute has to land on the g-stage PTE it names and on no other,
    or the walk fails (or succeeds) for a reason the test did not ask for. The pinned
    PT-node frames the RiescueD class also covers need ``modify_pt``, which the JSON
    schema does not expose."""

    def _two_stage(self, attrs, seed=1):
        out = _generate(
            {"s": {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "sv39", "pages": [{"id": "p", "attributes": {"v": 1, "r": 1, "w": 1, **attrs}}]}},
            seed=seed,
        )
        entry = _only_entry(out.spaces["s"].pages["p"])
        return out, entry

    def test_gleaf_force_rides_only_the_final_gstage_leaf(self):
        # w_leaf_gleaf names the G-stage leaf under the VS leaf -- i.e. the g-stage walk
        # of the page's own GPA. The g-stage leaves translating the VS *PTEs* must keep
        # their defaults, else unrelated table accesses fault too.
        out, entry = self._two_stage({"w_leaf_gleaf": 0})
        vs_steps, final_g = _split_two_stage(entry.ptes)
        final_leaf = out.entries[min(final_g, key=lambda p: p.level).address]
        self.assertEqual(_bit(final_leaf, _W), 0, "w_leaf_gleaf did not reach the page's g-stage leaf")
        for vs_pte, g_steps in vs_steps:
            leaf = out.entries[min(g_steps, key=lambda p: p.level).address]
            self.assertEqual(_bit(leaf, _W), 1, f"w_leaf_gleaf leaked to the g-stage leaf of VS level {vs_pte.level}")

    def test_x_gleaf_force_beats_the_identity_executable_default(self):
        # Unlike every other base bit, ``x`` also arrives at the derived g-stage leaf as a
        # BARE default ("identity data pages stay executable"). Regression: folding that bare
        # bit onto the g-leaf level overwrote the force, so x_leaf_gleaf=0 produced an
        # EXECUTABLE g-stage leaf and the instruction guest-page fault the test wanted never
        # fired (hypervisor_tlb_fence SID_HFTLB_07). w_leaf_gleaf above cannot catch this --
        # only ``x`` has a bare default to lose to.
        out, entry = self._two_stage({"x_leaf_gleaf": 0})
        _vs, final_g = _split_two_stage(entry.ptes)
        leaf = out.entries[min(final_g, key=lambda p: p.level).address]
        self.assertEqual(_bit(leaf, _X), 0, "x_leaf_gleaf=0 was clobbered by the identity leaf's default x=1")
        for name, bit in (("v", _V), ("r", _R), ("w", _W), ("u", _U), ("a", _A), ("d", _D)):
            self.assertEqual(_bit(leaf, bit), 1, f"g-stage leaf default {name} lost")

    def test_unforced_gstage_leaf_stays_executable(self):
        # The other half: with no force the bare default must still land.
        out, entry = self._two_stage({})
        _vs, final_g = _split_two_stage(entry.ptes)
        self.assertEqual(_bit(out.entries[min(final_g, key=lambda p: p.level).address], _X), 1)

    def test_x_gleaf_force_stays_off_the_vs_frame_gstage_leaves(self):
        # ...and the force must not leak onto the g-stage leaves translating the VS PTEs,
        # which would make the guest's own table walk fault.
        out, entry = self._two_stage({"x_leaf_gleaf": 0})
        vs_steps, _final = _split_two_stage(entry.ptes)
        for vs_pte, g_steps in vs_steps:
            leaf = out.entries[min(g_steps, key=lambda p: p.level).address]
            self.assertEqual(_bit(leaf, _X), 1, f"x_leaf_gleaf leaked to the g-stage leaf of VS level {vs_pte.level}")

    def test_gstage_leaves_keep_their_identity_defaults(self):
        # The forced bit must not cost the leaf its other defaults, or the frame is
        # unreachable for reasons other than the one being forced.
        out, entry = self._two_stage({"w_leaf_gleaf": 0})
        _vs, final_g = _split_two_stage(entry.ptes)
        leaf = out.entries[min(final_g, key=lambda p: p.level).address]
        for name, bit in (("v", _V), ("r", _R), ("u", _U), ("a", _A), ("d", _D)):
            self.assertEqual(_bit(leaf, bit), 1, f"g-stage leaf default {name} lost")

    def _gstage_v_bits(self, entry, out):
        """``{(vs_level, g_level): v}`` over the g-stage walks translating the VS PTEs."""
        vs_steps, _final = _split_two_stage(entry.ptes)
        return {(vs.level, g.level): _bit(out.entries[g.address], _V) for vs, steps in vs_steps for g in steps}

    def test_numeric_glevel_force_names_the_frame_not_the_pte(self):
        # A {base}_level{n}_glevel{g} force names a VS PT-node FRAME by the level of the
        # pointer PTE that targets it (vs == level - 1 as the walk reports it), so
        # v_level1_glevel1 lands on the g-stage level-1 node of the walk translating the
        # VS *level-0* PTE. Same convention pt_request_builder_test.TestModifyPtNodeFrames
        # documents for the RiescueD side; getting it wrong silently faults the wrong node.
        out, entry = self._two_stage({"v_level1_glevel1": 0})
        bits = self._gstage_v_bits(entry, out)
        self.assertEqual(bits[(0, 1)], 0, "v_level1_glevel1 did not reach the VS level-0 frame's g-stage level-1 node")
        for key, value in bits.items():
            if key != (0, 1):
                self.assertEqual(value, 1, f"v_level1_glevel1 leaked to VS {key[0]} / G {key[1]}")

    def test_rsw_and_reserved_reach_the_final_gstage_leaf(self):
        out, entry = self._two_stage(
            {
                "rsw_level0_glevel0": 3,
                "reserved_level0_glevel0": 0x7F,
            }
        )
        _vs, final_g = _split_two_stage(entry.ptes)
        leaf = out.entries[min(final_g, key=lambda p: p.level).address]
        self.assertEqual((leaf >> 8) & 0x3, 3)
        self.assertEqual((leaf >> 54) & 0x7F, 0x7F)

    def test_rsw_and_reserved_reach_a_vs_frame_gstage_leaf(self):
        # VS level 1 names the frame holding the VS level-0 PTE. Its glevel-0
        # PTE belongs to the synthesized identity represented by a PTGPage.
        out, entry = self._two_stage(
            {
                "rsw_level1_glevel0": 3,
                "reserved_level1_glevel0": 0x7F,
            }
        )
        vs_steps, final_g = _split_two_stage(entry.ptes)
        target_steps = next(steps for vs_pte, steps in vs_steps if vs_pte.level == 0)
        target_leaf = out.entries[min(target_steps, key=lambda p: p.level).address]
        self.assertEqual((target_leaf >> 8) & 0x3, 3)
        self.assertEqual((target_leaf >> 54) & 0x7F, 0x7F)

        for vs_pte, steps in vs_steps:
            if vs_pte.level == 0:
                continue
            leaf = out.entries[min(steps, key=lambda p: p.level).address]
            self.assertEqual((leaf >> 8) & 0x3, 0)
            self.assertEqual((leaf >> 54) & 0x7F, 0)
        final_leaf = out.entries[min(final_g, key=lambda p: p.level).address]
        self.assertEqual((final_leaf >> 8) & 0x3, 0)
        self.assertEqual((final_leaf >> 54) & 0x7F, 0)

    def test_symbolic_and_numeric_spellings_agree(self):
        # v_nonleaf_gleaf and v_level1_glevel0 name the same node: the g-stage leaf of the
        # frame holding the VS leaf PTE.
        sym_out, sym = self._two_stage({"v_nonleaf_gleaf": 0})
        num_out, num = self._two_stage({"v_level1_glevel0": 0})
        self.assertEqual(self._gstage_v_bits(sym, sym_out), self._gstage_v_bits(num, num_out))
        self.assertEqual(self._gstage_v_bits(sym, sym_out)[(0, 0)], 0)

    def test_leaf_spelling_targets_the_page_gstage_walk(self):
        # By contrast a {base}_leaf_g* force names the page's own GPA walk, leaving every
        # VS PT frame's g-stage walk untouched.
        out, entry = self._two_stage({"v_leaf_gnonleaf": 0})
        _vs, final_g = _split_two_stage(entry.ptes)
        self.assertEqual({p.level: _bit(out.entries[p.address], _V) for p in final_g}[1], 0)
        self.assertEqual(set(self._gstage_v_bits(entry, out).values()), {1})

    def test_gstage_forcing_rejected_outside_two_stage(self):
        # pt_request_builder_test drops g-stage inputs outside a two-stage test; the JSON
        # frontend rejects them (validate_gstage_size_fields).
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv39", "pages": [{"attributes": {"v": 1, "a_leaf_gleaf": 0}}]}})
        with self.assertRaises(ValueError):
            _generate({"s": {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "disable", "pages": [{"attributes": {"v": 1, "a_leaf_gleaf": 0}}]}})

    def test_gstage_vs_leaf_size_shortens_the_page_gstage_walk(self):
        # The g-stage leaf size is the geometry of the frame fronting the page's GPA:
        # 2 MiB drops the g-stage walk of that GPA to two levels and aligns the HPA.
        _out, base = self._two_stage({})
        _vs, base_final = _split_two_stage(base.ptes)
        _out, sized = self._two_stage({"gstage_vs_leaf_size": "2mb"})
        _vs, sized_final = _split_two_stage(sized.ptes)
        self.assertEqual([p.level for p in base_final], [2, 1, 0])
        self.assertEqual([p.level for p in sized_final], [2, 1])
        self.assertEqual(sized.pa & 0x1FFFFF, 0, "a 2 MiB g-stage leaf must give a 2 MiB-aligned HPA")

    def test_gstage_vs_nonleaf_size_shortens_the_vs_pte_gstage_walks(self):
        # The non-leaf size governs the frames holding the VS-stage tables, so the
        # g-stage walks translating the VS PTEs shorten instead.
        _out, entry = self._two_stage({"gstage_vs_nonleaf_size": "2mb"})
        vs_steps, _final = _split_two_stage(entry.ptes)
        depths = {vs.level: [g.level for g in steps] for vs, steps in vs_steps}
        self.assertEqual(depths[1], [2, 1])
        self.assertEqual(depths[0], [2, 1])


class TestGstageForcingWithSiblings(unittest.TestCase):
    """A ``*_nonleaf_g*`` force names the g-stage identity of a VS PT-node FRAME, which is
    shared by every page that shares that node -- and only the first walk to reach a frame
    emits its identity. So a forcing page must not coalesce with a plain sibling, or the
    sibling's default identity silently wins and the force reaches no PTE.

    TestGstageForcing above declares one page per space and therefore cannot see this:
    hypervisor_tlb_fence SID_HFTLB_78 read A=1 back from a frame it had asked to be A=0,
    because a plain page in the same test had already emitted that frame's identity."""

    def _pages(self, forced_attrs, num_plain=3, seed=1):
        """One forced page plus ``num_plain`` plain ones in a single two-stage space."""
        pages = [{"id": "forced", "attributes": {"v": 1, "r": 1, "w": 1, **forced_attrs}}]
        pages.append({"num_pages": num_plain, "id": "plain", "attributes": {"v": 1, "r": 1, "w": 1}})
        out = _generate({"s": {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "sv39", "pages": pages}}, seed=seed)
        space = out.spaces["s"]
        return out, _only_entry(space.pages["forced"]), list(space.pages["plain"].values())

    def _vs_frame_gstage_leaf_bit(self, out, entry, vs_level, bit):
        """The bit in the g-stage LEAF PTE translating the frame that holds ``entry``'s
        VS level-``vs_level`` PTE."""
        vs_steps, _final = _split_two_stage(entry.ptes)
        ((_vs, g_steps),) = [(vs, g) for vs, g in vs_steps if vs.level == vs_level]
        return _bit(out.entries[min(g_steps, key=lambda p: p.level).address], bit)

    # Whether a plain sibling lands under the forcing page's PT node is a coloring outcome,
    # so a single seed proves nothing: with the identity left out of the sharing signature
    # some seeds separate the two anyway and the force survives by luck. Sweep.
    _SEEDS = range(8)

    def test_weighted_forces_are_preferences_on_one_reachable_root_slot(self):
        def policy(preferred):
            other = 2 if preferred == 0 else 0
            return [
                {"value": preferred, "weight": 1},
                {"value": other, "weight": 0},
            ]

        pages = [
            {
                "id": f"preferred_{preferred}",
                "attributes": {
                    "v": 1,
                    "r": 1,
                    "secure": 1,
                    "pbmt_level0_glevel4": policy(preferred),
                },
            }
            for preferred in (0, 2)
        ]
        out = _generate(
            {
                "s": {
                    "twostage": True,
                    "paging_mode": "sv39",
                    "gstage_paging_mode": "sv57",
                    "pages": pages,
                }
            },
            mmap=SECURE_MMAP,
            seed=7,
        )
        entries = [_only_entry(out.spaces["s"].pages[f"preferred_{value}"]) for value in (0, 2)]
        roots = [_split_two_stage(entry.ptes)[1][0] for entry in entries]
        self.assertEqual(roots[0].address, roots[1].address)

    def test_a_nonleaf_gleaf_survives_plain_siblings(self):
        for seed in self._SEEDS:
            with self.subTest(seed=seed):
                out, forced, plain = self._pages({"a_nonleaf_gleaf": 0}, seed=seed)
                self.assertEqual(self._vs_frame_gstage_leaf_bit(out, forced, 0, _A), 0, "a_nonleaf_gleaf=0 lost to a plain sibling's default identity")
                for i, entry in enumerate(plain):
                    self.assertEqual(self._vs_frame_gstage_leaf_bit(out, entry, 0, _A), 1, f"the force leaked onto plain sibling {i}")

    def _leaf_frame_gpa(self, out, entry):
        vs_steps, _final = _split_two_stage(entry.ptes)
        ((_vs, g_steps),) = [(vs, g) for vs, g in vs_steps if vs.level == 0]
        return _ppn_addr(out.entries[min(g_steps, key=lambda p: p.level).address])

    def test_forced_frame_is_not_shared_with_a_plain_sibling(self):
        # The structural half of the same statement: the forced page's VS leaf-PTE frame is
        # a different GPA from every plain page's, so no first-writer-wins race exists.
        for seed in self._SEEDS:
            with self.subTest(seed=seed):
                out, forced, plain = self._pages({"a_nonleaf_gleaf": 0}, seed=seed)
                self.assertNotIn(self._leaf_frame_gpa(out, forced), {self._leaf_frame_gpa(out, e) for e in plain})

    def test_v_nonleaf_gnonleaf_survives_plain_siblings(self):
        # Same rule one g-level up: the force names the g-stage POINTER above the frame's
        # leaf, which is shared just as widely.
        for seed in self._SEEDS:
            with self.subTest(seed=seed):
                out, forced, plain = self._pages({"v_nonleaf_gnonleaf": 0}, seed=seed)
                vs_steps, _final = _split_two_stage(forced.ptes)
                ((_vs, g_steps),) = [(vs, g) for vs, g in vs_steps if vs.level == 0]
                self.assertEqual({p.level: _bit(out.entries[p.address], _V) for p in g_steps}[1], 0)
                for i, entry in enumerate(plain):
                    self.assertEqual(self._vs_frame_gstage_leaf_bit(out, entry, 0, _V), 1, f"the force leaked onto plain sibling {i}")

    def test_identical_forces_may_still_share(self):
        # Signing the identity into the sharing decision must not over-split: pages that
        # declare the SAME g-stage identity produce the same PTE and may coalesce. Assert the
        # semantics (every page gets A=0), not a particular layout.
        out = _generate(
            {
                "s": {
                    "twostage": True,
                    "paging_mode": "sv39",
                    "gstage_paging_mode": "sv39",
                    "pages": [{"num_pages": 4, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1, "a_nonleaf_gleaf": 0}}],
                }
            }
        )
        for i, entry in enumerate(out.spaces["s"].pages["m"].values()):
            self.assertEqual(self._vs_frame_gstage_leaf_bit(out, entry, 0, _A), 0, f"page {i} lost its a_nonleaf_gleaf force")


class TestGonlyForcing(unittest.TestCase):
    """A ``twostage`` space with ``paging_mode: "disable"`` is a bare g-stage walk: the
    guest indexes hgatp itself, so the page IS its own GPA -> HPA leaf. The g-stage forcing
    shorthand works there too. ``resolve.randomize_gstage_pt_attrs`` implements
    the VS-disabled semantics: every VS selector collapses onto a single-stage
    ``{base}_level{n}`` key."""

    def _gonly(self, attrs, size=None, mode="sv39", seed=1):
        page = {"id": "p", "attributes": {"v": 1, "r": 1, "w": 1, **attrs}}
        if size is not None:
            page["attributes"]["size"] = size
        out = _generate({"g": {"twostage": True, "paging_mode": "disable", "gstage_paging_mode": mode, "pages": [page]}}, seed=seed)
        entry = _only_entry(out.spaces["g"].pages["p"])
        return out, entry, {p.level: out.entries[p.address] for p in entry.ptes}

    def test_v_nonleaf_gnonleaf_invalidates_the_pointer_pte(self):
        # The level-1 pointer goes invalid while the leaf stays valid, so the
        # walk faults at the pointer.
        _out, _entry, by_level = self._gonly({"v_nonleaf_gnonleaf": 0})
        self.assertEqual(_bit(by_level[1], _V), 0, "v_nonleaf_gnonleaf=0 never reached the level-1 pointer PTE")
        self.assertEqual(_bit(by_level[0], _V), 1, "the force leaked onto the leaf")

    def test_a_leaf_gleaf_reaches_the_leaf(self):
        _out, _entry, by_level = self._gonly({"a_leaf_gleaf": 0})
        self.assertEqual(_bit(by_level[0], _A), 0, "a_leaf_gleaf=0 never reached the leaf PTE")

    def test_an_explicit_vs_level_collapses_onto_the_leaf(self):
        # With no VS stage there is no VS level to name, so a mixed form's VS index is
        # nominal: every VS selector resolves to the g-stage leaf.
        _out, _entry, by_level = self._gonly({"a_level2_gleaf": 0})
        self.assertEqual(_bit(by_level[0], _A), 0, "a_level2_gleaf=0 did not collapse onto the g-stage leaf")

    def test_gleaf_level_follows_the_pages_own_size(self):
        # The divergence from master: with no VS stage the g-stage leaf geometry is the
        # page's OWN pagesize. Master selected the level from the 4 KiB default, so on a
        # 2 MiB page the force landed on level 0 while the real leaf is level 1 -- it
        # reached no PTE at all.
        _out, entry, by_level = self._gonly({"a_leaf_gleaf": 0}, size="2mb")
        self.assertEqual(min(by_level), 1, "a 2 MiB g-only page must bottom its walk out at level 1")
        self.assertEqual(_bit(by_level[1], _A), 0, "a_leaf_gleaf=0 did not reach the level-1 leaf of a 2 MiB page")

    def test_a_nonleaf_force_leaves_the_leaf_user_reachable(self):
        # Every g-stage access is a user access, so the leaf's U must survive a *_nonleaf_*
        # force -- resolving under SUPER would seed u_level{leaf}=0 and fault every access.
        _out, _entry, by_level = self._gonly({"u_nonleaf_gnonleaf": 0})
        self.assertEqual(_bit(by_level[0], _U), 1, "the g-only leaf lost its user bit")
        self.assertEqual(_bit(by_level[1], _U), 0, "u_nonleaf_gnonleaf=0 never reached the level-1 pointer PTE")

    def test_every_forced_pte_is_a_real_entry(self):
        # A force must not invent a PTE address outside the built tree.
        out, entry, _by_level = self._gonly({"a_leaf_gleaf": 0, "v_nonleaf_gnonleaf": 0})
        for pte in entry.ptes:
            self.assertEqual(pte.stage, 2, "a g-only walk is entirely stage 2")
            self.assertIn(pte.address, out.entries)


class TestAttributeRandomization(unittest.TestCase):
    """Randomized attribute specs -- the JSON frontend's own layer, with no RiescueD
    translator counterpart (RiescueD resolves randomization before the translator runs).
    Included because every attribute above accepts these forms."""

    def _leaf_bits(self, attrs, bit, num_pages=24, seed=1):
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": num_pages, "id": "m", "attributes": {"v": 1, "r": 1, **attrs}}]}}, seed=seed)
        return [_bit(out.entries[_leaf_pte(e).address], bit) for e in out.spaces["s"].pages["m"].values()]

    def test_weighted_list_produces_a_mix(self):
        self.assertEqual(set(self._leaf_bits({"u": [{"value": 1, "weight": 5}, {"value": 0, "weight": 5}]}, _U)), {0, 1})

    def test_uniform_list_produces_a_mix(self):
        self.assertEqual(set(self._leaf_bits({"w": [0, 1]}, _W)), {0, 1})

    def test_single_valued_list_is_deterministic(self):
        self.assertEqual(set(self._leaf_bits({"w": [1]}, _W)), {1})

    def test_empty_attribute_list_is_rejected(self):
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv39", "pages": [{"attributes": {"v": 1, "w": []}}]}})

    def test_paging_mode_accepts_a_weighted_list(self):
        out = _generate({"s": {"paging_mode": [{"value": "sv48", "weight": 1}], "pages": [{"attributes": {"v": 1, "r": 1}}]}})
        self.assertEqual(out.spaces["s"].paging_mode, "sv48")

    def test_same_seed_is_reproducible_and_seeds_differ(self):
        spaces = {"s": {"paging_mode": "sv39", "pages": [{"num_pages": 6, "id": "m", "attributes": {"v": 1, "r": 1, "size": ["4kb", "2mb"], "u": [0, 1]}}]}}
        first = _generate(spaces, seed=5)
        again = _generate(spaces, seed=5)
        other = _generate(spaces, seed=6)
        self.assertEqual(first.to_dict(), again.to_dict())
        self.assertNotEqual(first.to_dict(), other.to_dict())


class TestBuiltTreeConsistency(unittest.TestCase):
    """Mirrors pt_request_builder_driver_test.TestBuildDriver._check_consistent, which
    drives a real build and checks the allocation hangs together. The JSON frontend
    always drives a real build, so the same invariants are checked on its output: every
    PTE a walk names exists, the walk chain actually reaches the reported PA, and no two
    pages' physical spans overlap."""

    SIZES = {"4kb": 0x1000, "64kb": 0x10000, "2mb": 0x200000, "1gb": 0x40000000}

    def _pages(self, out):
        for space in out.spaces.values():
            for va_map in space.pages.values():
                yield from va_map.items()

    def _assert_chain(self, out, root, steps, final, label):
        """Every PTE in ``steps`` (root first) points at the table holding the next, and
        the last points at ``final``."""
        self.assertEqual(steps[0].address & ~0xFFF, root, f"{label}: walk does not start at the declared root")
        for step, nxt in zip(steps, steps[1:]):
            self.assertIn(step.address, out.entries, f"{label}: PTE {step.address:#x} missing from entries")
            self.assertEqual(_ppn_addr(out.entries[step.address]), nxt.address & ~0xFFF, f"{label}: level-{step.level} PTE does not point at the level-{nxt.level} table")
        self.assertIn(steps[-1].address, out.entries, f"{label}: leaf PTE {steps[-1].address:#x} missing from entries")
        self.assertEqual(_ppn_addr(out.entries[steps[-1].address]), final, f"{label}: leaf PTE does not point at the reported address")

    def test_single_stage_walk_reaches_the_reported_pa(self):
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 4, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]}})
        root = out.spaces["s"].top_base_addr
        for va, entry in out.spaces["s"].pages["m"].items():
            self._assert_chain(out, root, entry.ptes, entry.pa, f"VA {va:#x}")

    def test_gonly_walk_reaches_the_reported_pa(self):
        out = _generate({"g": {"twostage": True, "paging_mode": "disable", "gstage_paging_mode": "sv39", "pages": [{"num_pages": 4, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]}})
        root = out.spaces["g"].gstage_top_base_addr
        for gpa, entry in out.spaces["g"].pages["m"].items():
            self._assert_chain(out, root, entry.ptes, entry.pa, f"GPA {gpa:#x}")

    def test_two_stage_walk_is_consistent_and_gpa_equals_hpa(self):
        # pt_request_builder_driver_test.test_two_stage_identity_gstage: the two-stage
        # idiom is an identity G-stage (GPA == HPA). Each VS PTE's PPN is a GPA, and the
        # G-stage sub-walk that precedes the next VS PTE resolves it -- so the chain only
        # closes if the identity holds, at every VS level and at the page itself.
        out = _generate({"v": {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "sv39", "pages": [{"num_pages": 3, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]}})
        space = out.spaces["v"]
        for va, entry in space.pages["m"].items():
            vs_steps, final_g = _split_two_stage(entry.ptes)
            label = f"VA {va:#x}"
            # Every G-stage sub-walk starts at hgatp and lands on the VS PTE it fronts.
            for vs_pte, g_steps in vs_steps:
                self._assert_chain(out, space.gstage_top_base_addr, g_steps, vs_pte.address & ~0xFFF, f"{label} g-walk of VS level {vs_pte.level}")
            self._assert_chain(out, space.gstage_top_base_addr, final_g, entry.pa, f"{label} g-walk of the page GPA")
            # ...and the VS chain itself closes on the reported PA.
            self._assert_chain(out, space.top_base_addr, [p for p, _ in vs_steps], entry.pa, f"{label} VS walk")

    def test_every_pte_address_is_a_real_entry(self):
        for label, space in (
            ("single-stage", {"paging_mode": "sv48"}),
            ("two-stage", {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "sv48"}),
            ("vs-only", {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "disable"}),
            ("g-only", {"twostage": True, "paging_mode": "disable", "gstage_paging_mode": "sv39"}),
        ):
            out = _generate({"s": {**space, "pages": [{"num_pages": 3, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]}})
            for va, entry in out.spaces["s"].pages["m"].items():
                for pte in entry.ptes:
                    self.assertIn(pte.address, out.entries, f"{label}: PTE {pte.address:#x} for {va:#x} missing from entries")

    def test_physical_spans_do_not_overlap_across_spaces(self):
        # The driver test's core allocation invariant, over mixed page sizes and several
        # spaces at once: nothing is handed out twice.
        out = _generate(
            {
                "a": {"paging_mode": "sv48", "pages": [{"num_pages": 4, "id": "m", "attributes": {"v": 1, "r": 1, "size": ["4kb", "2mb", "1gb"]}}]},
                "b": {"paging_mode": "sv39", "pages": [{"num_pages": 4, "id": "m", "attributes": {"v": 1, "r": 1, "size": ["4kb", "2mb"]}}]},
                "c": {"twostage": True, "paging_mode": "sv39", "gstage_paging_mode": "sv39", "pages": [{"num_pages": 4, "id": "m", "attributes": {"v": 1, "r": 1}}]},
            },
            seed=11,
        )
        spans = sorted((e.pa, e.pa + self.SIZES[e.size]) for _va, e in self._pages(out))
        for (_lo, end), (start, _hi) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start, f"physical spans overlap at {start:#x}")

    def test_page_frames_do_not_collide_with_page_table_nodes(self):
        # A PT node landing inside a data page (or vice versa) corrupts both.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 8, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1, "size": ["4kb", "2mb"]}}]}}, seed=3)
        spans = [(e.pa, e.pa + self.SIZES[e.size]) for _va, e in self._pages(out)]
        for pte_addr in out.entries:
            for lo, hi in spans:
                self.assertFalse(lo <= pte_addr < hi, f"PT node {pte_addr:#x} lands inside the page frame [{lo:#x}, {hi:#x})")


class TestEmptyPageGroups(unittest.TestCase):
    """``num_pages: 0`` is a useful way to disable one group while its siblings stay live,
    so it must build. It is only an error when it empties the whole space:
    such a space originates no mapping and therefore bears no page table."""

    def test_a_space_whose_every_group_is_empty_is_rejected(self):
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 0}]}})
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 0, "id": "a"}, {"num_pages": 0, "id": "b"}]}})

    def test_one_empty_group_beside_a_live_one_builds(self):
        out = _generate(
            {
                "s": {
                    "paging_mode": "sv39",
                    "pages": [
                        {"num_pages": 0, "id": "off", "attributes": {"v": 1, "r": 1}},
                        {"num_pages": 2, "id": "on", "attributes": {"v": 1, "r": 1, "w": 1}},
                    ],
                }
            }
        )
        space = out.spaces["s"]
        self.assertNotIn("off", space.pages, "a disabled group must contribute no pages")
        self.assertEqual(len(space.pages["on"]), 2)
        self.assertIsNotNone(space.top_base_addr, "the space still bears a page table")


class TestDuplicatePinnedVa(unittest.TestCase):
    """Two pages at one VA in one space is a declaration error.

    A pinned PA shared by several pages remains the supported alias idiom.
    """

    def test_a_pinned_va_over_several_pages_is_rejected(self):
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{"num_pages": 2, "id": "dup", "va": "0x1000"}]}})

    def test_two_specs_pinning_one_va_are_rejected(self):
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{"id": "a", "va": "0x1000"}, {"id": "b", "va": "0x1000"}]}})

    def test_the_comparison_is_on_the_parsed_value(self):
        # va is base-16 whether or not it is 0x-prefixed, so these are the same address.
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{"id": "a", "va": "1000"}, {"id": "b", "va": "0x1000"}]}})

    def test_the_same_va_in_two_spaces_is_fine(self):
        # Distinct spaces are distinct address domains.
        attrs = {"v": 1, "r": 1, "w": 1}
        out = _generate(
            {
                "a": {"paging_mode": "sv39", "pages": [{"id": "p", "va": "0x50000000", "attributes": attrs}]},
                "b": {"paging_mode": "sv48", "pages": [{"id": "p", "va": "0x50000000", "attributes": attrs}]},
            }
        )
        for space_id in ("a", "b"):
            self.assertEqual(list(out.spaces[space_id].pages["p"]), [0x50000000])


class TestPagingDisabledSpace(unittest.TestCase):
    """A space whose paging mode is ``disable`` has no page table: VA == PA, accessed
    directly.

    The output shape is an empty shell rather than pages carrying ``ptes: []``: the
    frontend draws such a space's VA and PA independently, so reporting a ``pa`` for an
    untranslated ``va`` would publish a relationship that does not exist."""

    def _disabled(self, **space):
        out = _generate({"s": {**space, "pages": [{"num_pages": 2, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]}})
        return out, out.spaces["s"]

    def test_a_single_stage_disabled_space_builds_an_empty_shell(self):
        out, space = self._disabled(paging_mode="disable")
        self.assertEqual(space.paging_mode, "disable")
        self.assertEqual(space.pages, {})
        self.assertIsNone(space.top_base_addr)
        self.assertIsNone(space.gstage_paging_mode)
        self.assertEqual(out.entries, {}, "a paging-disabled space must emit no PTE")

    def test_a_twostage_space_with_both_modes_disabled_builds_an_empty_shell(self):
        out, space = self._disabled(twostage=True, paging_mode="disable", gstage_paging_mode="disable")
        self.assertEqual(space.pages, {})
        self.assertIsNone(space.top_base_addr)
        self.assertEqual(out.entries, {})

    def test_a_disabled_space_leaves_its_neighbours_alone(self):
        # The real risk: skipping one space's tree must not perturb another's.
        pages = [{"num_pages": 2, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]
        both = _generate({"live": {"paging_mode": "sv39", "pages": pages}, "off": {"paging_mode": "disable", "pages": pages}})
        alone = _generate({"live": {"paging_mode": "sv39", "pages": pages}})
        live = both.spaces["live"]
        self.assertEqual(len(live.pages["m"]), 2)
        self.assertIsNotNone(live.top_base_addr)
        # Every PTE in the merged entries belongs to the live space's own walks.
        walked = {pte.address for entry in live.pages["m"].values() for pte in entry.ptes}
        self.assertTrue(walked <= set(both.entries))
        self.assertEqual(len(both.entries), len(alone.entries), "the disabled space contributed PTEs")


class TestSerializationSurface(unittest.TestCase):
    """The JSON frontend reads configs and reads or writes generated output."""

    def test_a_config_is_read_only(self):
        for name in ("from_dict", "from_json_file"):
            self.assertTrue(hasattr(PageTableConfig, name), f"PageTableConfig lost {name}")
        for name in ("to_dict", "to_json_file"):
            self.assertFalse(hasattr(PageTableConfig, name), f"PageTableConfig regrew {name}")
        for name in ("to_dict",):
            self.assertFalse(hasattr(SpaceConfig, name), f"SpaceConfig regrew {name}")
            self.assertFalse(hasattr(PageSpec, name), f"PageSpec regrew {name}")
            self.assertFalse(hasattr(PageAttributes, name), f"PageAttributes regrew {name}")

    def test_an_output_supports_serialization_and_deserialization(self):
        for name in ("from_dict", "from_json_file", "to_dict", "to_json_file"):
            self.assertTrue(hasattr(PageTableOutput, name), f"PageTableOutput lost {name}")
        for cls in (SpaceOutput, PageEntry, PTEInfo):
            for name in ("from_dict", "to_dict"):
                self.assertTrue(hasattr(cls, name), f"{cls.__name__} lost {name}")

    def test_the_live_directions_still_round_trip_through_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            out_path = Path(tmp) / "out.json"
            cfg_path.write_text(json.dumps({"mmap": BASE_MMAP, "spaces": {"s": {"paging_mode": "sv39", "pages": [{"id": "p", "attributes": {"v": 1, "r": 1}}]}}}))
            out = generate_page_tables(PageTableConfig.from_json_file(cfg_path), seed=1)
            out.to_json_file(out_path)
            written = json.loads(out_path.read_text())
            self.assertEqual(set(written), {"entries", "spaces"})
            self.assertIn("p", written["spaces"]["s"]["pages"])
            self.assertEqual(PageTableOutput.from_dict(written), out)
            self.assertEqual(PageTableOutput.from_json_file(out_path), out)


class TestConfigRejection(unittest.TestCase):
    """Schema guardrails the JSON frontend owns; RiescueD's equivalents are parser-side."""

    def _assert_value_error(self, callback, pattern):
        try:
            callback()
        except Exception as error:
            self.assertIsInstance(error, ValueError)
            self.assertRegex(str(error), pattern)
        else:
            self.fail("ValueError not raised")

    def test_twostage_requires_an_exact_boolean(self):
        for value in (0, 1, "false", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "twostage"):
                _config({"s": {"twostage": value, "paging_mode": "sv39", "pages": [{}]}})

    def test_secure_flags_require_exact_booleans(self):
        for value in (0, 1, "false", None):
            with self.subTest(location="mmap", value=value), self.assertRaisesRegex(ValueError, "secure"):
                _config(
                    {"s": {"paging_mode": "sv39", "pages": [{}]}},
                    mmap=[{"low": "0x0", "high": "0x1000", "secure": value}],
                )
            with self.subTest(location="page", value=value), self.assertRaisesRegex(ValueError, "secure"):
                _config(
                    {
                        "s": {
                            "paging_mode": "sv39",
                            "pages": [{"attributes": {"secure": value}}],
                        }
                    }
                )

    def test_page_ids_must_be_strings(self):
        for value in (0, True, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "id"):
                _config({"s": {"paging_mode": "sv39", "pages": [{"id": value}]}})

    def test_page_ids_must_be_unique_in_a_space(self):
        for pages in (
            [{"id": "dup"}, {"id": "dup"}],
            [{}, {"id": "0"}],
        ):
            with self.subTest(pages=pages), self.assertRaisesRegex(ValueError, "id"):
                _config({"s": {"paging_mode": "sv39", "pages": pages}})

    def test_unknown_pte_attribute_names_are_warned_about_and_dropped(self):
        # A name the schema does not define is not fatal: it is logged and left out, so
        # the surrounding config still builds and the resolver never sees the stray key.
        with self.assertLogs("riescue.riemap.json_frontend", level="WARNING") as logs:
            config = _config(
                {
                    "s": {
                        "paging_mode": "sv39",
                        "pages": [{"id": "p", "attributes": {"v": 1, "r": 1, "definitely_not_a_pte_field": 1}}],
                    }
                }
            )
        self.assertTrue(any("definitely_not_a_pte_field" in line for line in logs.output), f"the dropped attribute was not named in the warning: {logs.output}")
        attrs = config.spaces["s"].pages[0].attributes.attrs
        self.assertEqual(attrs, {"v": 1, "r": 1}, "the unknown attribute survived parsing")
        self.assertIn("p", generate_page_tables(config, seed=1).spaces["s"].pages)

    def test_pte_attribute_values_fit_their_fields(self):
        for name, value in (
            ("v", 2),
            ("u_level0", -1),
            ("pbmt", 4),
            ("rsw", 4),
            ("reserved", 128),
        ):
            with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                _config(
                    {
                        "s": {
                            "paging_mode": "sv39",
                            "pages": [{"attributes": {name: value}}],
                        }
                    }
                )

    def test_mmap_bounds_have_strict_numeric_types(self):
        for value in (True, 1.5, None):
            with self.subTest(bound="low", value=value):
                self._assert_value_error(
                    lambda: _config(
                        {"s": {"paging_mode": "sv39", "pages": [{}]}},
                        mmap=[[value, "0x1000"]],
                    ),
                    "mmap",
                )
            with self.subTest(bound="high", value=value):
                self._assert_value_error(
                    lambda: _config(
                        {"s": {"paging_mode": "sv39", "pages": [{}]}},
                        mmap=[["0x0", value]],
                    ),
                    "mmap",
                )

    def test_mmap_dict_rejects_unknown_keys(self):
        with self.assertRaisesRegex(ValueError, "mmap"):
            _config(
                {"s": {"paging_mode": "sv39", "pages": [{}]}},
                mmap=[{"low": "0x0", "high": "0x1000", "bogus": 1}],
            )

    def test_mmap_bounds_are_nonnegative_and_fit_52_bits(self):
        for mmap in (
            [[-1, 0x1000]],
            [[0, (1 << 52) + 1]],
            [[1 << 52, (1 << 52) + 1]],
        ):
            with self.subTest(mmap=mmap), self.assertRaisesRegex(ValueError, "mmap"):
                _config({"s": {"paging_mode": "sv39", "pages": [{}]}}, mmap=mmap)

    def test_choice_lists_cannot_mix_weighted_and_uniform_forms(self):
        for value in (
            [0, {"value": 1, "weight": 1}],
            [{"value": 0, "weight": 1}, 1],
        ):
            with self.subTest(value=value):
                self._assert_value_error(
                    lambda: _config(
                        {
                            "s": {
                                "paging_mode": "sv39",
                                "pages": [{"attributes": {"v": value}}],
                            }
                        }
                    ),
                    "weighted",
                )

    def test_paging_mode_choice_lists_are_homogeneous(self):
        for value in (
            ["sv39", {"value": "sv48", "weight": 1}],
            [{"value": "sv39", "weight": 1}, "sv48"],
        ):
            with self.subTest(value=value):
                self._assert_value_error(
                    lambda: _config({"s": {"paging_mode": value, "pages": [{}]}}),
                    "weighted",
                )

    def test_weighted_choices_require_valid_weights(self):
        for weight in (-1, True, "1", float("nan"), float("inf")):
            with self.subTest(weight=weight), self.assertRaisesRegex(ValueError, "weight"):
                _config(
                    {
                        "s": {
                            "paging_mode": "sv39",
                            "pages": [{"attributes": {"v": [{"value": 1, "weight": weight}]}}],
                        }
                    }
                )
        with self.assertRaisesRegex(ValueError, "weight"):
            _config(
                {
                    "s": {
                        "paging_mode": "sv39",
                        "pages": [
                            {
                                "attributes": {
                                    "v": [
                                        {"value": 0, "weight": 0},
                                        {"value": 1, "weight": 0},
                                    ]
                                }
                            }
                        ],
                    }
                }
            )

    def test_paging_mode_weighted_choices_require_valid_weights(self):
        for choices in (
            [{"value": "sv39", "weight": -1}],
            [{"value": "sv39", "weight": float("nan")}],
            [
                {"value": "sv39", "weight": 0},
                {"value": "sv48", "weight": 0},
            ],
        ):
            with self.subTest(choices=choices), self.assertRaisesRegex(ValueError, "weight"):
                _config({"s": {"paging_mode": choices, "pages": [{}]}})

    def test_exact_addresses_are_mutually_exclusive_with_masks(self):
        for page in (
            {"va": "0x1000", "va_and": "0xfffff000"},
            {"va": "0x1000", "va_or": "0x1000"},
            {"pa": "0x80000000", "pa_and": "0xfffff000"},
            {"pa": "0x80000000", "pa_or": "0x1000"},
        ):
            with self.subTest(page=page), self.assertRaisesRegex(ValueError, "mask|va|pa"):
                _config({"s": {"paging_mode": "sv39", "pages": [page]}})

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{"bogus": 1}]}})
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "bogus": 1, "pages": [{}]}})
        with self.assertRaises(ValueError):
            PageTableConfig.from_dict({"mmap": BASE_MMAP, "spaces": {"s": {"pages": [{}]}}, "bogus": 1})

    def test_comment_fields_are_ignored(self):
        cfg = PageTableConfig.from_dict(
            {
                "_comment": "top",
                "mmap": BASE_MMAP,
                "spaces": {"s": {"_comment": "space", "paging_mode": "sv39", "pages": [{"_comment": "page", "id": "p", "attributes": {"v": 1, "r": 1}}]}},
            }
        )
        self.assertIn("p", generate_page_tables(cfg, seed=1).spaces["s"].pages)

    def test_a_page_id_may_contain_a_double_underscore(self):
        # '__' was once the frontend's internal id separator; it is not any more (a page's
        # id is a plain dict key), so the restriction was a rule about nothing. The comment
        # asserting it still applied was already false when it was written.
        out = _generate({"s": {"paging_mode": "sv39", "pages": [{"id": "a__b", "attributes": {"v": 1, "r": 1}}]}})
        self.assertIn("a__b", out.spaces["s"].pages)

    def test_a_space_needs_at_least_one_page(self):
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": []}})

    def test_num_pages_cannot_be_negative_or_boolean(self):
        for value in (-1, True):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    ValueError,
                    "num_pages",
                ),
            ):
                _config(
                    {
                        "s": {
                            "paging_mode": "sv39",
                            "pages": [{"num_pages": value}],
                        }
                    }
                )

    def test_secure_pt_probability_cannot_be_boolean(self):
        with self.assertRaisesRegex(ValueError, "secure_pt_probability"):
            _config(
                {
                    "s": {
                        "paging_mode": "sv39",
                        "secure_pt_probability": True,
                        "pages": [{}],
                    }
                }
            )

    def test_invalid_paging_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            _generate({"s": {"paging_mode": "sv99", "pages": [{}]}})

    def test_mmap_must_be_non_empty_and_ordered(self):
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{}]}}, mmap=[])
        with self.assertRaises(ValueError):
            _config({"s": {"paging_mode": "sv39", "pages": [{}]}}, mmap=[["0x1000", "0x100"]])


if __name__ == "__main__":
    unittest.main()
