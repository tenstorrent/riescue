# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

import riescue.lib.enums as RV
from riescue.dtest_framework.config.candidate import Candidate
from riescue.lib.rand import RandNum


class TestCandidate(unittest.TestCase):
    """
    Test the Candidate class.
    """

    def test_candidate_docstring_example(self):
        "Test examples from docstring"

        rng = RandNum(seed=0)
        c = Candidate("a", "b", "c")
        self.assertEqual(c.choose(rng), "b", "Seed 0 should always pick b")

        rng = RandNum(seed=1)
        c2 = Candidate(1, 2, 3)
        c2.discard(2)
        self.assertEqual(c2.choose(rng), 1, "Seed 1 should always pick 1")

    def test_empty_candidate_choose(self):
        "Check that an empty candidate will raise an error"
        c = Candidate()
        with self.assertRaises(ValueError):
            c.choose(RandNum(seed=0))

    def test_candidate_from_enum(self):
        "Check that a candidate can be created from an enum"
        c = Candidate.from_enum(RV.RiscvPrivileges)
        a = c.choose(RandNum(seed=0))
        self.assertEqual(a, RV.RiscvPrivileges.SUPER)

    def test_candidate_copy(self):
        "Check that a candidate can be copied"
        c = Candidate.from_enum(RV.RiscvPagingModes)
        c2 = c.copy()
        self.assertIsNot(c, c2, "Copy should return a new candidate")

    def test_candidate_only_contains(self):
        "Check that a candidate only contains a specific value"
        c = Candidate.from_enum(RV.RiscvPagingModes)
        self.assertFalse(c.only_contains(RV.RiscvPagingModes.SV39))
        self.assertFalse(c.only_contains(RV.RiscvPagingModes.DISABLE))

        d = Candidate(1)
        self.assertTrue(d.only_contains(1))

    def test_candidate_eq_enum(self):
        "Check that a candidate equals a single value"
        c = Candidate.from_enum(RV.RiscvPagingModes)
        self.assertNotEqual(c, RV.RiscvPagingModes.DISABLE)
        self.assertNotEqual(c, Candidate(1))

        d = Candidate(RV.RiscvPagingModes.SV39)
        self.assertEqual(d, RV.RiscvPagingModes.SV39)

    def test_candidate_eq_list(self):
        "test list comparisons. Candidates should support comparing a list with a Candidate, regardless of order."
        c = Candidate(1, 2, 3)
        self.assertEqual(c, [1, 2, 3], "Should be able to compare candidate to a list")
        self.assertEqual(c, [3, 1, 2], "Order shouldn't matter when comparing a list to a Candidate")

    def test_candidate_len(self):
        "Check that the length of a candidate is correct"
        c = Candidate.from_enum(RV.RiscvPagingModes)
        self.assertEqual(len(c), 5)

    def test_repr(self):
        "Check that the repr is correct"
        c = Candidate.from_enum(RV.RiscvPagingModes)
        self.assertIsInstance(repr(c), str)

    def test_with_weights_distribution(self):
        "Test weighted candidate probability distribution over multiple samples"
        # Create weighted candidate with 20% chance for 'secure', 80% chance for 'non_secure'
        choices = []
        for seed in range(1000):
            c = Candidate.with_weights([(RV.RiscvSecureModes.SECURE, 0.2), (RV.RiscvSecureModes.NON_SECURE, 0.8)])
            choices.append(c.choose(RandNum(seed=seed)))

        secure_count = choices.count(RV.RiscvSecureModes.SECURE)
        secure_ratio = secure_count / len(choices)

        # Should be approximately 20% with some tolerance
        self.assertGreater(secure_ratio, 0.15, "Should have at least 15% secure choices")
        self.assertLess(secure_ratio, 0.25, "Should have at most 25% secure choices")

    def test_with_weights_deterministic(self):
        "Test that weighted candidates are deterministic with fixed seed"
        c1 = Candidate.with_weights([("a", 0.3), ("b", 0.7)])
        c2 = Candidate.with_weights([("a", 0.3), ("b", 0.7)])

        rng1 = RandNum(seed=42)
        rng2 = RandNum(seed=42)

        self.assertEqual(c1.choose(rng1), c2.choose(rng2), "Same seed should produce same result")

    def test_with_weights_pool_structure(self):
        "Test that weighted pool contains expected duplicates"
        c = Candidate.with_weights([("x", 0.1), ("y", 0.9)])
        pool = list(c)

        self.assertIn("x", pool)
        self.assertIn("y", pool)
        self.assertGreater(pool.count("y"), pool.count("x"), "Higher weight should have more entries")

    def test_edge_case_weights_sum_to_1(self):
        "Test that weighted pool contains expected duplicates"
        with self.assertRaises(ValueError):
            c = Candidate.with_weights([("x", -0.1), ("y", 1.1)])

    def test_with_weights_greater_than_1(self):
        "Test that weighted pool contains expected duplicates"
        with self.assertRaises(ValueError):
            Candidate.with_weights([("x", 1.1)])

    def test_with_weights_less_than_1(self):
        "Test that weighted pool contains expected duplicates"
        with self.assertRaises(ValueError):
            Candidate.with_weights([("x", -0.1), ("y", 0.9)])

    def test_with_weight_of_0(self):
        "Test that weighted pool contains expected duplicates"
        with self.assertRaises(ValueError):
            Candidate.with_weights([("x", 0), ("y", 0.9)])
