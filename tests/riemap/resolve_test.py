# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the shared mid-level resolvers (riescue.riemap.resolve).

These lock the attribute-forcing / canonicalization contract now that the logic
lives in one place and is consumed by the standalone JSON frontend, the output
validator, and (later) RiescueD. They exercise the neutral ``attrs: dict`` +
:class:`PagingParams` policy interface (with explicit paging-mode arguments)
directly, demonstrating the resolvers are usable without any RiescueD state.
"""

import types
import unittest

import riescue.lib.enums as RV
from riescue.riemap import resolve
from riescue.riemap.config import PagingParams


def _config(paging_mode, paging_g_mode, priv_mode=RV.RiscvPrivileges.SUPER):
    """A minimal PagingParams-like policy object for the resolvers (modes passed explicitly)."""
    del paging_mode, paging_g_mode  # modes are explicit args now, not part of the policy object
    return types.SimpleNamespace(
        physical_addr_bits=56,
        priv_mode=priv_mode,
        secure_pt_probability=0,
    )


class TestCanonicalize(unittest.TestCase):
    def test_va_sign_extends_high_sv39(self):
        # bit 38 set -> upper bits all ones
        addr = 1 << 38
        out = resolve.make_canonical_va(addr, RV.RiscvPagingModes.SV39)
        self.assertEqual(out >> 39, (1 << (64 - 39)) - 1)
        self.assertEqual(out & ((1 << 39) - 1), addr)

    def test_va_zero_upper_low_sv39(self):
        addr = 0x1000
        out = resolve.make_canonical_va(addr, RV.RiscvPagingModes.SV39)
        self.assertEqual(out, addr)

    def test_va_sv32_masks_32(self):
        self.assertEqual(resolve.make_canonical_va(0x1_FFFF_FFFF, RV.RiscvPagingModes.SV32), 0xFFFFFFFF)

    def test_va_disable_passthrough(self):
        self.assertEqual(resolve.make_canonical_va(0xDEAD, RV.RiscvPagingModes.DISABLE), 0xDEAD)

    def test_gpa_zero_extends(self):
        # GPA gets an extra 2 bits over VA width; upper bits must be cleared, not sign-extended.
        addr = (1 << 40) | 0x1234
        out = resolve.make_canonical_gpa(addr, RV.RiscvPagingModes.SV39)
        width = RV.RiscvPagingModes.linear_addr_bits(RV.RiscvPagingModes.SV39, gstage=True)
        self.assertEqual(out, addr & ((1 << width) - 1))
        self.assertEqual(out >> width, 0)


class TestPageSizes(unittest.TestCase):
    def test_valid_sizes_per_mode(self):
        self.assertEqual(resolve.get_valid_page_sizes(RV.RiscvPagingModes.SV32), ["4kb", "4mb"])
        self.assertEqual(resolve.get_valid_page_sizes(RV.RiscvPagingModes.SV39), ["4kb", "64kb", "2mb", "1gb"])
        self.assertEqual(resolve.get_valid_page_sizes(RV.RiscvPagingModes.SV48), ["4kb", "64kb", "2mb", "1gb", "512gb"])
        self.assertEqual(resolve.get_valid_page_sizes(RV.RiscvPagingModes.SV57), ["4kb", "64kb", "2mb", "1gb", "512gb", "256tb"])

    def test_filter_scalar_valid(self):
        self.assertEqual(resolve.filter_size_attribute("2mb", RV.RiscvPagingModes.SV39), "2mb")

    def test_filter_scalar_invalid_raises(self):
        with self.assertRaises(ValueError):
            resolve.filter_size_attribute("512gb", RV.RiscvPagingModes.SV39)

    def test_filter_list_drops_invalid(self):
        out = resolve.filter_size_attribute(["4kb", "512gb", "2mb"], RV.RiscvPagingModes.SV39)
        self.assertEqual(out, ["4kb", "2mb"])

    def test_filter_weighted(self):
        spec = [resolve.WeightedValue("4kb", 1.0), resolve.WeightedValue("512gb", 2.0)]
        out = resolve.filter_size_attribute(spec, RV.RiscvPagingModes.SV39)
        self.assertEqual([wv.value for wv in out], ["4kb"])

    def test_filter_empty_list_raises(self):
        with self.assertRaises(ValueError):
            resolve.filter_size_attribute([], RV.RiscvPagingModes.SV39)

    def test_filter_rejects_mixed_weighted_and_uniform_lists(self):
        for spec in (
            ["4kb", resolve.WeightedValue("2mb", 1)],
            [resolve.WeightedValue("4kb", 1), "2mb"],
        ):
            with self.subTest(spec=spec):
                try:
                    resolve.filter_size_attribute(spec, RV.RiscvPagingModes.SV39)
                except Exception as error:
                    self.assertIsInstance(error, ValueError)
                    self.assertRegex(str(error), "weighted")
                else:
                    self.fail("ValueError not raised")


class TestPagingParamsValidation(unittest.TestCase):
    def test_secure_probability_requires_an_integer_percentage(self):
        for value in (-1, 101, True, 1.5, "50"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "secure_pt_probability"):
                PagingParams(secure_pt_probability=value)

    def test_physical_address_width_requires_a_positive_integer(self):
        for value in (0, -1, 65, True, 1.5, "52"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "physical_addr_bits"):
                PagingParams(physical_addr_bits=value)


class TestForcingVocabulary(unittest.TestCase):
    def test_mixed_forms_precede_symbolic(self):
        # apply order must process 1-token mixed forms before 2-token symbolic forms
        # so the higher-priority symbolic writes win on a collision.
        self.assertEqual(
            resolve.GSTAGE_FORCING_ATTRS,
            resolve.GSTAGE_MIXED_LEVEL_ATTRS + resolve.GSTAGE_LEAF_NONLEAF_ATTRS,
        )
        first_symbolic = resolve.GSTAGE_FORCING_ATTRS.index(resolve.GSTAGE_LEAF_NONLEAF_ATTRS[0])
        self.assertEqual(first_symbolic, len(resolve.GSTAGE_MIXED_LEVEL_ATTRS))

    def test_vs_selector(self):
        self.assertEqual(resolve._vs_selector("a_leaf_gleaf"), ("leaf", None))
        self.assertEqual(resolve._vs_selector("a_nonleaf_gnonleaf"), ("nonleaf", None))
        self.assertEqual(resolve._vs_selector("a_level2_gleaf"), ("level", 2))

    def test_g_selector(self):
        self.assertEqual(resolve._g_selector("a_leaf_gleaf"), ("leaf", None))
        self.assertEqual(resolve._g_selector("a_leaf_gnonleaf"), ("nonleaf", None))
        self.assertEqual(resolve._g_selector("a_leaf_glevel3"), ("level", 3))

    def test_insignificant_values(self):
        self.assertEqual(resolve._attr_insignificant_value("a", RV.RiscvPrivileges.SUPER), 1)
        self.assertEqual(resolve._attr_insignificant_value("g", RV.RiscvPrivileges.SUPER), 0)
        self.assertEqual(resolve._attr_insignificant_value("u", RV.RiscvPrivileges.SUPER), 0)
        self.assertEqual(resolve._attr_insignificant_value("u", RV.RiscvPrivileges.USER), 1)


class TestGstageResolvers(unittest.TestCase):
    def test_setup_uwrx_bit_leaf_and_nonleaf(self):
        attrs: dict = {}
        resolve.setup_uwrx_bit(
            "u",
            attrs=attrs,
            paging_mode=RV.RiscvPagingModes.SV39,
            paging_g_mode=RV.RiscvPagingModes.SV39,
            final_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_final_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_final_pagesize=RV.RiscvPageSizes.S4KB,
        )
        # G-stage leaf PTE for the VS leaf level defaults to 1; the level above is a
        # non-leaf and defaults to 0.
        self.assertEqual(attrs["u_level0_glevel0"], 1)
        self.assertEqual(attrs["u_level0_glevel1"], 0)

    def test_randomize_gstage_materializes_concrete_key(self):
        attrs = {"a_leaf_gleaf": 1}
        resolve.randomize_gstage_pt_attrs(
            attr="a_leaf_gleaf",
            attrs=attrs,
            paging_mode_g=RV.RiscvPagingModes.SV39,
            paging_mode_vs=RV.RiscvPagingModes.SV39,
            priv_mode=RV.RiscvPrivileges.SUPER,
            final_pagesize_vs=RV.RiscvPageSizes.S4KB,
            final_pagesize_gleaf=RV.RiscvPageSizes.S4KB,
            final_pagesize_gnonleaf=RV.RiscvPageSizes.S4KB,
        )
        # VS leaf + G leaf both map to level 0 at 4KB.
        self.assertEqual(attrs["a_level0_glevel0"], 1)

    def test_randomize_gstage_none_is_noop(self):
        attrs: dict = {}
        out = resolve.randomize_gstage_pt_attrs(
            attr="a_leaf_gleaf",
            attrs=attrs,
            paging_mode_g=RV.RiscvPagingModes.SV39,
            paging_mode_vs=RV.RiscvPagingModes.SV39,
            priv_mode=RV.RiscvPrivileges.SUPER,
            final_pagesize_vs=RV.RiscvPageSizes.S4KB,
            final_pagesize_gleaf=RV.RiscvPageSizes.S4KB,
            final_pagesize_gnonleaf=RV.RiscvPageSizes.S4KB,
        )
        self.assertEqual(attrs, {})
        # Attribute materialization is in place; per-node pagesize declarations
        # are the independent geometry authority.
        self.assertIsNone(out)

    def test_apply_gstage_materializes_keys_and_returns_nothing(self):
        attrs = {"a_leaf_gleaf": 1}
        cfg = _config(RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.SV39)
        out = resolve.apply_gstage_leaf_nonleaf_attrs(
            attrs=attrs,
            config=cfg,
            paging_mode=RV.RiscvPagingModes.SV39,
            paging_g_mode=RV.RiscvPagingModes.SV39,
            final_pagesize_vs=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_pagesize=RV.RiscvPageSizes.S4KB,
        )
        # The in-place concrete key IS the whole contract; no geometry comes back.
        self.assertIsNone(out)
        self.assertEqual(attrs["a_level0_glevel0"], 1)

    def test_gnonleaf_force_lands_on_the_level_the_gstage_pagesize_implies(self):
        # The ``gstage_vs_*_pagesize`` arguments are inputs that SELECT a level, never
        # geometry the resolver grows. With a 1GB g-stage leaf under SV48 the g-stage walk
        # bottoms out at g-level 2, so ``_gnonleaf`` is the level above it: g-level 3.
        attrs = {"v_leaf_gnonleaf": 1}
        cfg = _config(RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.SV48)
        out = resolve.apply_gstage_leaf_nonleaf_attrs(
            attrs=attrs,
            config=cfg,
            paging_mode=RV.RiscvPagingModes.SV39,
            paging_g_mode=RV.RiscvPagingModes.SV48,
            final_pagesize_vs=RV.RiscvPageSizes.S1GB,
            gstage_vs_leaf_pagesize=RV.RiscvPageSizes.S1GB,
            gstage_vs_nonleaf_pagesize=RV.RiscvPageSizes.S1GB,
        )
        self.assertIsNone(out)
        self.assertEqual(attrs["v_level2_glevel3"], 1)


class TestGstageLeafAttrs(unittest.TestCase):
    """gstage_leaf_attrs_for: the shared GPA -> HPA leaf attr derivation used by both
    the builder's add_two_stage_mapping and RiescueD's translator."""

    def _leaf(self, vs_attrs, leaf_size=RV.RiscvPageSizes.S4KB, nonleaf_size=RV.RiscvPageSizes.S4KB, secure=False):
        return resolve.gstage_leaf_attrs_for(
            vs_attrs,
            vs_paging_mode=RV.RiscvPagingModes.SV57,
            gstage_mode=RV.RiscvPagingModes.SV57,
            vs_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_size=leaf_size,
            gstage_vs_nonleaf_size=nonleaf_size,
            secure=secure,
        )

    def test_leaf_is_user_reachable_by_default(self):
        # A g-stage leaf is user-level, so U/R/W/X/A/D default to 1.
        leaf = self._leaf({"v": 1, "r": 1, "w": 1})
        for base in ("u", "r", "w", "x", "a", "d"):
            self.assertEqual(leaf.get(f"{base}_level0"), 1, f"{base}_level0 should default to 1 on a g-stage leaf")

    def test_forcing_overlays_default(self):
        # A VS mapping's g-level forcing wins over the seeded default and is remapped
        # onto the g-stage leaf's own level (v_level{vs}_glevel{g} -> v_level{g}).
        leaf = self._leaf({"v": 1, "r": 1, "w": 1, "v_level0_glevel0": 0})
        self.assertEqual(leaf["v_level0"], 0)
        # unforced bits keep their user-reachable default
        self.assertEqual(leaf["u_level0"], 1)

    def test_secure_flag(self):
        self.assertEqual(self._leaf({"v": 1}, secure=True).get("secure"), 1)
        self.assertNotIn("secure", self._leaf({"v": 1}, secure=False))


class TestPtAttrs(unittest.TestCase):
    """The public, non-mutating single-stage forcing wrapper."""

    def test_does_not_mutate_input(self):
        attrs = {"w": 1}
        updated = resolve.pt_attrs(
            attr="w",
            attrs=attrs,
            paging_mode=RV.RiscvPagingModes.SV39,
            final_pagesize=RV.RiscvPageSizes.S4KB,
            priv_mode=RV.RiscvPrivileges.SUPER,
        )
        # The caller's dict is untouched; the concrete leaf key lands on the copy.
        self.assertEqual(attrs, {"w": 1})
        self.assertEqual(updated["w_level0"], 1)

    def test_matches_in_place_helper(self):
        in_place = {"w": 1}
        resolve._pt_attrs_helper("w", in_place, RV.RiscvPagingModes.SV39, RV.RiscvPageSizes.S4KB, RV.RiscvPrivileges.SUPER)
        updated = resolve.pt_attrs(
            attr="w",
            attrs={"w": 1},
            paging_mode=RV.RiscvPagingModes.SV39,
            final_pagesize=RV.RiscvPageSizes.S4KB,
            priv_mode=RV.RiscvPrivileges.SUPER,
        )
        # The public wrapper produces the same concrete-key expansion as the in-place
        # helper (no address reservation is derived by either any more).
        self.assertEqual(updated, in_place)

    def test_nonleaf_selector_requires_a_nonleaf_level(self):
        with self.assertRaisesRegex(ValueError, "nonleaf"):
            resolve.pt_attrs(
                attr="v",
                attrs={"v_nonleaf": 0},
                paging_mode=RV.RiscvPagingModes.SV39,
                final_pagesize=RV.RiscvPageSizes.S1GB,
                priv_mode=RV.RiscvPrivileges.SUPER,
            )


class TestPtNodeLevelConverters(unittest.TestCase):
    """C5.2 string-key -> pt_nodes level converters. Priority is already baked into
    the resolved keys these consume, so feeding real expander output proves it carries
    through unchanged."""

    def test_single_stage_direct(self):
        # Plain {base}_level{n} regrouping; _glevel keys are ignored.
        attrs = {"w_level0": 1, "w_level1": 0, "a_level0": 1, "a_level0_glevel0": 0}
        self.assertEqual(resolve.attrs_to_pt_node_levels(attrs), {0: {"w": 1, "a": 1}, 1: {"w": 0}})

    def test_rsw_and_reserved_fold_onto_the_leaf_and_support_levels(self):
        # Both fields pack like every other PTE field and support the same
        # single-stage and VS-by-G-level forcing grammar.
        levels = resolve.pt_node_levels_with_leaf({"rsw": 3, "reserved": 5, "v": 1}, leaf_level=0)
        self.assertEqual(levels[0]["rsw"], 3)
        self.assertEqual(levels[0]["reserved"], 5)
        self.assertIn("rsw", resolve.LEVEL_TYPES)
        self.assertIn("reserved", resolve.LEVEL_TYPES)

        attrs = {
            "rsw_level2_glevel0": 3,
            "reserved_level2_glevel1": 0x7F,
        }
        self.assertEqual(
            resolve.gstage_frame_pt_node_levels(attrs, vs_level=2),
            {0: {"rsw": 3}, 1: {"reserved": 0x7F}},
        )

    def test_single_stage_from_pt_attrs(self):
        # Fed straight from the expander: w=1 seeds the per-level leaf/non-leaf defaults.
        updated = resolve.pt_attrs(
            attr="w",
            attrs={"w": 1},
            paging_mode=RV.RiscvPagingModes.SV39,
            final_pagesize=RV.RiscvPageSizes.S4KB,
            priv_mode=RV.RiscvPrivileges.SUPER,
        )
        levels = resolve.attrs_to_pt_node_levels(updated)
        # leaf (0) = 1, non-leaf (1,2) = 0, matching the string keys.
        self.assertEqual(levels[0]["w"], updated["w_level0"])
        self.assertEqual(levels[1]["w"], updated["w_level1"])
        self.assertEqual(levels[2]["w"], updated["w_level2"])

    def _apply(self, attrs):
        cfg = _config(RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.SV39)
        resolve.apply_gstage_leaf_nonleaf_attrs(
            attrs=attrs,
            config=cfg,
            paging_mode=RV.RiscvPagingModes.SV39,
            paging_g_mode=RV.RiscvPagingModes.SV39,
            final_pagesize_vs=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_pagesize=RV.RiscvPageSizes.S4KB,
        )
        return attrs

    def test_priority_2token_beats_1token_space8(self):
        # space8: 2-token a_leaf_gleaf:0 must win over 1-token a_level0_gleaf:1 -> A=0.
        attrs = self._apply({"a_leaf_gleaf": 0, "a_level0_gleaf": 1})
        self.assertEqual(attrs["a_level0_glevel0"], 0)  # guard: resolved key
        self.assertEqual(resolve.gstage_frame_pt_node_levels(attrs, 0), {0: {"a": 0}})

    def test_priority_insignificant_materializes_space9(self):
        # space9: 2-token a_leaf_gleaf:1 (A=1 is insignificant at SUPER) still materializes
        # and beats the independently-seeded concrete a_level0_glevel0:0 -> A=1.
        attrs = self._apply({"a_leaf_gleaf": 1, "a_level0_glevel0": 0})
        self.assertEqual(attrs["a_level0_glevel0"], 1)  # guard: resolved key
        self.assertEqual(resolve.gstage_frame_pt_node_levels(attrs, 0), {0: {"a": 1}})

    def test_gstage_frame_lattice_per_vs_level(self):
        # u_level{vs}_glevel{g} lattice from setup_uwrx_bit: leaf g-level 1, above 0,
        # regrouped independently per VS frame level.
        attrs: dict = {}
        resolve.setup_uwrx_bit(
            "u",
            attrs=attrs,
            paging_mode=RV.RiscvPagingModes.SV39,
            paging_g_mode=RV.RiscvPagingModes.SV39,
            final_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_final_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_final_pagesize=RV.RiscvPageSizes.S4KB,
        )
        for vs in range(3):
            grouped = resolve.gstage_frame_pt_node_levels(attrs, vs)
            self.assertEqual(grouped, {g: {"u": attrs[f"u_level{vs}_glevel{g}"]} for g in range(3)})
        # vs=0's g-leaf is user-reachable (1), the levels above are non-leaf (0).
        self.assertEqual(resolve.gstage_frame_pt_node_levels(attrs, 0), {0: {"u": 1}, 1: {"u": 0}, 2: {"u": 0}})

    def test_gstage_leaf_reuses_gstage_leaf_attrs_for(self):
        # The leaf converter regroups gstage_leaf_attrs_for's friendly {base}_level{g}
        # dict; a g-level forcing overlays the user-reachable default and lands per-level.
        vs_attrs = {"v": 1, "r": 1, "w": 1, "v_level0_glevel0": 0}
        levels = resolve.gstage_leaf_pt_node_levels(
            vs_attrs,
            vs_paging_mode=RV.RiscvPagingModes.SV57,
            gstage_mode=RV.RiscvPagingModes.SV57,
            vs_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
        )
        self.assertEqual(levels[0]["v"], 0)  # forced overlay
        self.assertEqual(levels[0]["u"], 1)  # default user-reachable
        self.assertEqual(levels[0]["x"], 1)  # bare identity-leaf force folded onto g-leaf

    def _gleaf_levels(self, vs_attrs, gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB):
        return resolve.gstage_leaf_pt_node_levels(
            vs_attrs,
            vs_paging_mode=RV.RiscvPagingModes.SV39,
            gstage_mode=RV.RiscvPagingModes.SV39,
            vs_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_size=gstage_vs_leaf_size,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
        )

    def test_bare_base_never_overrides_an_explicit_gleaf_force(self):
        # A bare base bit is a DEFAULT and a concrete {base}_level{n} is a FORCE, so the force
        # wins -- the same precedence pt_node_levels_with_leaf applies on the VS side and
        # PTAttrs applies when packing the PTE (it always overwrites the bare attr with the
        # level's). Regression: this converter folded the bare bit on with a plain write
        # instead, so it clobbered the force. ``x`` is the only base exposed today (see
        # test_x_is_the_only_bare_base_the_derived_leaf_carries), which made ``x_leaf_gleaf=0``
        # produce an EXECUTABLE g-stage leaf and the fetch the test wanted to fault succeed --
        # hypervisor_tlb_fence SID_HFTLB_07.
        levels = self._gleaf_levels({"v": 1, "r": 1, "w": 1, "x_level0_glevel0": 0})
        self.assertEqual(levels[0]["x"], 0, "x_leaf_gleaf=0 was clobbered by the bare identity-leaf x=1")

    def test_bare_base_still_fills_an_unforced_gleaf(self):
        # The other half of the contract: with no force the bare default must still land, or
        # every synthesized identity leaf loses its executable bit.
        self.assertEqual(self._gleaf_levels({"v": 1, "r": 1, "w": 1})[0]["x"], 1)

    def test_x_is_the_only_bare_base_the_derived_leaf_carries(self):
        # Scope guard for the two tests above. The precedence fix is general (it covers every
        # LEVEL_TYPES base), but only a base that arrives BARE can be clobbered, and today
        # gstage_leaf_pte_attrs seeds exactly one: x. ``secure`` is a mapping flag, not a PTE
        # level bit, so it is deliberately not folded onto a level. If this list grows, the
        # new base inherits the same precedence -- and a regression in it would look like
        # SID_HFTLB_07 all over again.
        leaf = resolve.gstage_leaf_attrs_for(
            {"v": 1, "r": 1, "w": 1},
            vs_paging_mode=RV.RiscvPagingModes.SV39,
            gstage_mode=RV.RiscvPagingModes.SV39,
            vs_pagesize=RV.RiscvPageSizes.S4KB,
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
            secure=True,
        )
        self.assertEqual(sorted(k for k in leaf if "_level" not in k), ["secure", "x"])
        self.assertNotIn("secure", self._gleaf_levels({"v": 1}).get(0, {}))

    def test_gleaf_force_lands_on_a_superpage_gstage_leaf_level(self):
        # The bare bit folds onto pt_leaf_level(gstage_vs_leaf_size), so a 2 MiB g-stage leaf
        # bottoms out at g-level 1 -- the force and the fold have to agree on which level that
        # is, else the force lands one level off and the bare bit wins on the real leaf.
        levels = self._gleaf_levels({"v": 1, "x_level0_glevel1": 0}, gstage_vs_leaf_size=RV.RiscvPageSizes.S2MB)
        self.assertEqual(levels[1]["x"], 0)


class TestGstagePointerSpan(unittest.TestCase):
    """``gstage_pointer_span`` is the one implementation of "bytes governed by the g-stage pointer
    PTE at this level", shared by two callers that must not diverge: a :class:`PTGPage` that forces
    a non-leaf g-level (:func:`resolve.gstage_exclusive_span`), and RiescueD's
    ``modify_leaf_pt`` / ``modify_nonleaf_pt``, which say the runtime rewrites that pointer and so
    imply the same ownership via explicit ``reserve_size``."""

    def test_span_is_the_page_a_leaf_at_that_level_would_cover(self):
        for mode, expected in (
            (RV.RiscvPagingModes.SV39, {0: 0x1000, 1: 0x200000, 2: 0x40000000}),
            (RV.RiscvPagingModes.SV48, {0: 0x1000, 1: 0x200000, 2: 0x40000000, 3: 0x8000000000}),
            (RV.RiscvPagingModes.SV57, {0: 0x1000, 1: 0x200000, 2: 0x40000000, 3: 0x8000000000, 4: 0x1000000000000}),
        ):
            for level, size in expected.items():
                with self.subTest(mode=mode, level=level):
                    self.assertEqual(resolve.gstage_pointer_span(mode, level), (size, (~(size - 1)) & 0xFFFFFFFFFFFFFFFF))

    def test_out_of_range_levels_return_none_rather_than_raising(self):
        # RV.RiscvPagingModes.index_bits RAISES past a mode's top level. Both callers take the
        # level from consumer-supplied data (a declared pt_nodes key, or leaf_level + 1 for a
        # leaf already at the root), so this has to answer "no span" instead of blowing up.
        self.assertIsNone(resolve.gstage_pointer_span(RV.RiscvPagingModes.SV39, 3))
        self.assertIsNone(resolve.gstage_pointer_span(RV.RiscvPagingModes.SV39, -1))
        self.assertIsNone(resolve.gstage_pointer_span(RV.RiscvPagingModes.SV48, 4))
        self.assertIsNone(resolve.gstage_pointer_span(RV.RiscvPagingModes.DISABLE, 0))

    def test_exclusive_span_uses_it_for_a_forced_nonleaf_level(self):
        # A non-default bit on a non-leaf g-level grows the frame's span to that pointer's.
        mode, ps = RV.RiscvPagingModes.SV48, RV.RiscvPageSizes.S2MB
        leaf_level = RV.RiscvPageSizes.pt_leaf_level(ps)
        self.assertEqual(resolve.gstage_exclusive_span(mode, ps, {leaf_level + 1: {"v": 0}}), resolve.gstage_pointer_span(mode, leaf_level + 1))

    def test_exclusive_span_ignores_a_restated_default(self):
        # Declaring a pointer level's natural value (v=1, rest 0) is not a force and must move
        # nothing -- the span stays the frame's own pagesize.
        mode, ps = RV.RiscvPagingModes.SV48, RV.RiscvPageSizes.S2MB
        leaf_level = RV.RiscvPageSizes.pt_leaf_level(ps)
        size, _mask = resolve.gstage_exclusive_span(mode, ps, {leaf_level + 1: {"v": 1, "w": 0}})
        self.assertEqual(size, RV.RiscvPageSizes.memory(ps))

    def test_exclusive_span_tolerates_an_out_of_range_declared_level(self):
        # Same bound-check, reached through the declared-force path.
        mode, ps = RV.RiscvPagingModes.SV39, RV.RiscvPageSizes.S4KB
        size, _mask = resolve.gstage_exclusive_span(mode, ps, {9: {"v": 0}})
        self.assertEqual(size, RV.RiscvPageSizes.memory(ps))


if __name__ == "__main__":
    unittest.main()
