# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.riemap.request import Choice, resolve_common_choice


class TestChoice(unittest.TestCase):
    def test_preferred_is_first_legal_option(self):
        choice = Choice(preferred=2, alternatives=(1, 3))
        self.assertEqual(choice.options, (2, 1, 3))
        self.assertTrue(choice.allows(1))
        self.assertFalse(choice.allows(0))

    def test_duplicate_options_are_rejected(self):
        with self.assertRaises(ValueError):
            Choice(preferred=1, alternatives=(1,))
        with self.assertRaises(ValueError):
            Choice(preferred=1, alternatives=(2, 2))

    def test_shared_choice_maximizes_preferences(self):
        declarations = [
            Choice(preferred=1, alternatives=(2,)),
            Choice(preferred=2, alternatives=(1,)),
            Choice(preferred=2, alternatives=(1,)),
        ]
        self.assertEqual(resolve_common_choice(declarations), 2)

    def test_scalar_is_a_hard_constraint(self):
        self.assertEqual(resolve_common_choice([Choice(preferred=1, alternatives=(2,)), 2]), 2)
        with self.assertRaises(ValueError):
            resolve_common_choice([Choice(preferred=1, alternatives=(2,)), 3])

    def test_correlated_node_variants_are_selected_whole(self):
        first = Choice(
            preferred={"pbmt": 1, "r": 1, "w": 0},
            alternatives=({"pbmt": 2, "r": 0, "w": 1},),
        )
        second = Choice(
            preferred={"pbmt": 2, "r": 0, "w": 1},
            alternatives=({"pbmt": 1, "r": 1, "w": 0},),
        )
        self.assertIn(resolve_common_choice([first, second]), first.options)


if __name__ == "__main__":
    unittest.main()
