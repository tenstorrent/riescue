# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for riescue.mem_order_gen.config."""

import json
import tempfile
import unittest
from pathlib import Path

from riescue.mem_order_gen.config import TestGroup, load_groups


def _write_json(payload) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(payload, f)
    f.close()
    return Path(f.name)


class TestEmptyDefaults(unittest.TestCase):
    """An empty group {"count": 1} should produce a TestGroup whose every
    field equals the pre-refactor CLI default. This is the load-bearing test
    for byte-identical default generation."""

    def test_minimal_group_has_default_fields(self):
        path = _write_json([{"count": 1}])
        groups = load_groups(path)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g.count, 1)
        self.assertEqual(g.ops_per_hart, 10)
        self.assertEqual(g.fence_prob, 0.15)
        self.assertEqual(g.amo_prob, 0.0)
        self.assertEqual(g.lr_sc_prob, 0.0)
        self.assertEqual(g.lr_sc_interleave_prob, 0.0)
        self.assertEqual(g.addr_dep_prob, 0.0)
        self.assertEqual(g.data_dep_prob, 0.0)
        self.assertEqual(g.ctrl_dep_prob, 0.0)
        self.assertEqual(g.vector_load_prob, 0.0)
        self.assertEqual(g.vector_store_prob, 0.0)
        self.assertEqual(g.vector_stride_modes, ["unit"])
        self.assertEqual(g.vector_fof_prob, 0.0)
        self.assertEqual(g.vector_mask_prob, 0.0)
        self.assertEqual(g.vector_segment_prob, 0.0)
        self.assertEqual(g.vector_alias_prob, 0.0)
        self.assertEqual(g.vector_zero_stride_prob, 0.0)
        self.assertEqual(g.access_size_weights, (0, 0, 0, 1))
        self.assertEqual(g.vector_element_size_weights, (0, 0, 0, 1))
        self.assertEqual(g.misaligned_prob, 0.0)
        # Region size: legacy default (preserves byte-identical generation).
        self.assertEqual(g.shared_mem_bytes, 0x800)
        # Layout / partition / saturation knobs are per-group too.
        self.assertEqual(g.p_split_8, 0.0)
        self.assertEqual(g.p_split_4, 0.0)
        self.assertEqual(g.p_split_2, 0.0)
        self.assertEqual(g.layout_min_counts, (0, 0, 0, 0))
        self.assertEqual(g.saturation_fallback_threshold, 0.05)
        # Loop knobs default to disabled; ranges to plausible values.
        self.assertEqual(g.loop_prob, 0.0)
        self.assertEqual(g.loop_iterations_range, (4, 16))
        self.assertEqual(g.loop_body_ops_range, (2, 6))
        # AMO/LR-SC ordering-bit probabilities default to 50/50,
        # preserving the pre-knob hardcoded behaviour.
        self.assertEqual(g.aq_prob, 0.5)
        self.assertEqual(g.rl_prob, 0.5)


class TestLoopConfigKeys(unittest.TestCase):
    """The three new loop knobs round-trip through JSON config."""

    def test_loop_config_round_trip(self):
        path = _write_json(
            [
                {
                    "count": 1,
                    "loop_prob": 0.25,
                    "loop_iterations_range": [2, 8],
                    "loop_body_ops_range": [1, 4],
                }
            ]
        )
        groups = load_groups(path)
        g = groups[0]
        self.assertEqual(g.loop_prob, 0.25)
        self.assertEqual(g.loop_iterations_range, (2, 8))
        self.assertEqual(g.loop_body_ops_range, (1, 4))

    def test_negative_loop_prob_rejected(self):
        path = _write_json([{"count": 1, "loop_prob": -0.1}])
        with self.assertRaisesRegex(ValueError, r"loop_prob: must be in \[0\.0, 1\.0\]"):
            load_groups(path)

    def test_loop_iterations_range_inverted_rejected(self):
        path = _write_json([{"count": 1, "loop_iterations_range": [10, 4]}])
        with self.assertRaisesRegex(ValueError, "loop_iterations_range: hi=4 must be >= lo=10"):
            load_groups(path)

    def test_loop_body_ops_range_zero_lo_rejected(self):
        path = _write_json([{"count": 1, "loop_body_ops_range": [0, 4]}])
        with self.assertRaisesRegex(ValueError, "loop_body_ops_range\\[0\\]: must be >= 1"):
            load_groups(path)


class TestAqRlConfigKeys(unittest.TestCase):
    """The ``aq_prob`` / ``rl_prob`` ordering-bit knobs round-trip through JSON."""

    def test_aq_rl_prob_round_trip(self):
        path = _write_json([{"count": 1, "aq_prob": 0.9, "rl_prob": 0.1}])
        groups = load_groups(path)
        g = groups[0]
        self.assertEqual(g.aq_prob, 0.9)
        self.assertEqual(g.rl_prob, 0.1)

    def test_aq_prob_above_one_rejected(self):
        path = _write_json([{"count": 1, "aq_prob": 1.5}])
        with self.assertRaisesRegex(ValueError, r"aq_prob: must be in \[0\.0, 1\.0\]"):
            load_groups(path)

    def test_rl_prob_negative_rejected(self):
        path = _write_json([{"count": 1, "rl_prob": -0.1}])
        with self.assertRaisesRegex(ValueError, r"rl_prob: must be in \[0\.0, 1\.0\]"):
            load_groups(path)


class TestFullSpecRoundTrip(unittest.TestCase):
    """Setting every key to a non-default value should round-trip cleanly."""

    def test_full_spec(self):
        path = _write_json(
            [
                {
                    "count": 5,
                    "ops_per_hart": 20,
                    "fence_prob": 0.3,
                    "amo_prob": 0.2,
                    "lr_sc_prob": 0.2,
                    "lr_sc_interleave_prob": 0.15,
                    "addr_dep_prob": 0.4,
                    "data_dep_prob": 0.4,
                    "ctrl_dep_prob": 0.4,
                    "vector_load_prob": 0.2,
                    "vector_store_prob": 0.1,
                    "vector_stride_modes": ["unit", "strided", "indexed_u"],
                    "vector_fof_prob": 0.5,
                    "vector_mask_prob": 0.5,
                    "vector_segment_prob": 0.5,
                    "vector_alias_prob": 0.5,
                    "vector_zero_stride_prob": 0.5,
                    "access_size_weights": [1, 2, 3, 4],
                    "vector_element_size_weights": [4, 3, 2, 1],
                    "misaligned_prob": 0.25,
                }
            ]
        )
        groups = load_groups(path)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g.count, 5)
        self.assertEqual(g.ops_per_hart, 20)
        self.assertEqual(g.access_size_weights, (1, 2, 3, 4))
        self.assertEqual(g.vector_element_size_weights, (4, 3, 2, 1))
        self.assertEqual(g.vector_stride_modes, ["unit", "strided", "indexed_u"])


class TestMultipleGroups(unittest.TestCase):
    def test_two_groups_distinct_options(self):
        path = _write_json(
            [
                {"count": 3, "ops_per_hart": 8},
                {"count": 2, "ops_per_hart": 12, "amo_prob": 0.5},
            ]
        )
        groups = load_groups(path)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].count, 3)
        self.assertEqual(groups[0].ops_per_hart, 8)
        self.assertEqual(groups[0].amo_prob, 0.0)  # default
        self.assertEqual(groups[1].count, 2)
        self.assertEqual(groups[1].ops_per_hart, 12)
        self.assertEqual(groups[1].amo_prob, 0.5)


class TestValidationFailures(unittest.TestCase):
    """Each per-rule failure must raise ValueError with the field path."""

    def _assert_load_fails(self, payload, *, contains: str):
        path = _write_json(payload)
        with self.assertRaises(ValueError) as ctx:
            load_groups(path)
        self.assertIn(contains, str(ctx.exception))

    def test_top_level_must_be_list(self):
        self._assert_load_fails({"count": 1}, contains="top-level must be a JSON list")

    def test_empty_list_rejected(self):
        self._assert_load_fails([], contains="at least one group")

    def test_group_must_be_object(self):
        self._assert_load_fails(["not an object"], contains="must be a JSON object")

    def test_count_must_be_positive(self):
        self._assert_load_fails([{"count": 0}], contains="groups[0].count: must be >= 1")

    def test_ops_per_hart_must_be_positive(self):
        self._assert_load_fails(
            [{"count": 1, "ops_per_hart": 0}],
            contains="groups[0].ops_per_hart: must be >= 1",
        )

    def test_prob_out_of_range(self):
        self._assert_load_fails(
            [{"count": 1, "fence_prob": 1.5}],
            contains="groups[0].fence_prob",
        )

    def test_lr_sc_interleave_prob_strict_upper(self):
        self._assert_load_fails(
            [{"count": 1, "lr_sc_interleave_prob": 1.0}],
            contains="groups[0].lr_sc_interleave_prob",
        )

    def test_amo_lrsc_sum_constraint(self):
        self._assert_load_fails(
            [{"count": 1, "amo_prob": 0.6, "lr_sc_prob": 0.6}],
            contains="amo_prob + lr_sc_prob",
        )

    def test_vector_sum_constraint(self):
        self._assert_load_fails(
            [
                {
                    "count": 1,
                    "amo_prob": 0.3,
                    "lr_sc_prob": 0.3,
                    "vector_load_prob": 0.3,
                    "vector_store_prob": 0.3,
                }
            ],
            contains="vector_load_prob + vector_store_prob",
        )

    def test_lr_sc_reservation_bytes_rejected_in_json(self):
        # Moved to a CLI flag; the JSON loader must reject it as an unknown key.
        self._assert_load_fails(
            [{"count": 1, "lr_sc_reservation_bytes": 64}],
            contains="unknown key",
        )

    def test_weight_quad_wrong_length(self):
        self._assert_load_fails(
            [{"count": 1, "access_size_weights": [1, 2, 3]}],
            contains="access_size_weights: must have exactly 4",
        )

    def test_weight_quad_negative(self):
        self._assert_load_fails(
            [{"count": 1, "access_size_weights": [1, -1, 1, 1]}],
            contains="access_size_weights[1]",
        )

    def test_weight_quad_all_zero(self):
        self._assert_load_fails(
            [{"count": 1, "access_size_weights": [0, 0, 0, 0]}],
            contains="at least one positive entry",
        )

    def test_unknown_stride_mode(self):
        self._assert_load_fails(
            [{"count": 1, "vector_stride_modes": ["foobar"]}],
            contains="vector_stride_modes",
        )

    def test_empty_stride_modes(self):
        self._assert_load_fails(
            [{"count": 1, "vector_stride_modes": []}],
            contains="vector_stride_modes",
        )

    def test_unknown_key_rejected(self):
        self._assert_load_fails(
            [{"count": 1, "fence-prob": 0.3}],  # hyphen instead of underscore
            contains="unknown key",
        )

    def test_p_split_out_of_range(self):
        self._assert_load_fails(
            [{"count": 1, "p_split_8": 1.5}],
            contains="groups[0].p_split_8",
        )

    def test_layout_min_counts_wrong_shape(self):
        self._assert_load_fails(
            [{"count": 1, "layout_min_counts": [1, 2, 3]}],
            contains="layout_min_counts: must have exactly 4",
        )

    def test_saturation_threshold_out_of_range(self):
        self._assert_load_fails(
            [{"count": 1, "saturation_fallback_threshold": 1.1}],
            contains="saturation_fallback_threshold",
        )

    def test_unknown_key_at_index_2(self):
        path = _write_json(
            [
                {"count": 1},
                {"count": 1},
                {"count": 1, "bogus_key": 42},
            ]
        )
        with self.assertRaises(ValueError) as ctx:
            load_groups(path)
        self.assertIn("groups[2]", str(ctx.exception))
        self.assertIn("bogus_key", str(ctx.exception))


class TestDirectFromDict(unittest.TestCase):
    """TestGroup.from_dict can be called directly without a temp file."""

    def test_from_dict_basic(self):
        g = TestGroup.from_dict({"count": 3, "amo_prob": 0.4}, index=0)
        self.assertEqual(g.count, 3)
        self.assertEqual(g.amo_prob, 0.4)


class TestSharedMemBytes(unittest.TestCase):
    """The shared_mem_bytes per-group option drives MemoryPartitioning.region_size."""

    def test_minimum_accepted(self):
        g = TestGroup.from_dict({"count": 1, "shared_mem_bytes": 8}, index=0)
        self.assertEqual(g.shared_mem_bytes, 8)

    def test_maximum_accepted(self):
        g = TestGroup.from_dict({"count": 1, "shared_mem_bytes": 2048}, index=0)
        self.assertEqual(g.shared_mem_bytes, 2048)

    def test_non_multiple_of_8_accepted(self):
        # Greedy default tiling handles arbitrary region sizes; the
        # config layer doesn't enforce alignment.
        g = TestGroup.from_dict({"count": 1, "shared_mem_bytes": 13}, index=0)
        self.assertEqual(g.shared_mem_bytes, 13)

    def test_below_minimum_rejected(self):
        with self.assertRaisesRegex(ValueError, r"shared_mem_bytes.*must be in \[8, 2048\]"):
            TestGroup.from_dict({"count": 1, "shared_mem_bytes": 7}, index=0)

    def test_above_maximum_rejected(self):
        with self.assertRaisesRegex(ValueError, r"shared_mem_bytes.*must be in \[8, 2048\]"):
            TestGroup.from_dict({"count": 1, "shared_mem_bytes": 2049}, index=0)

    def test_string_rejected(self):
        with self.assertRaisesRegex(ValueError, "shared_mem_bytes.*must be an integer"):
            TestGroup.from_dict({"count": 1, "shared_mem_bytes": "0x800"}, index=0)

    def test_bool_rejected(self):
        with self.assertRaisesRegex(ValueError, "shared_mem_bytes.*must be an integer"):
            TestGroup.from_dict({"count": 1, "shared_mem_bytes": True}, index=0)

    def test_layout_min_counts_overflows_region(self):
        # 16 size-8 partitions = 128 bytes total, which exceeds the
        # 64-byte region.
        with self.assertRaisesRegex(ValueError, "layout_min_counts totals"):
            TestGroup.from_dict(
                {
                    "count": 1,
                    "shared_mem_bytes": 64,
                    "layout_min_counts": [0, 0, 0, 16],
                },
                index=0,
            )

    def test_layout_min_counts_fits_within_region(self):
        # 8 size-8 partitions = 64 bytes, exactly fills the 64-byte region.
        g = TestGroup.from_dict(
            {
                "count": 1,
                "shared_mem_bytes": 64,
                "layout_min_counts": [0, 0, 0, 8],
            },
            index=0,
        )
        self.assertEqual(g.shared_mem_bytes, 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
