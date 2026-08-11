# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the shared PTE attribute schema (riescue.riemap.attributes).

These lock in the split of the historically-duplicated PTAttrs.base_attrs and
Page.attrs dictionaries into one COMMON source plus small explicit overlays, so
that a future edit cannot silently reintroduce drift between the two roles.
"""

import unittest

from riescue.riemap.attributes import (
    COMMON_PTE_ATTRS,
    PTE_BASES,
    pt_attrs_schema,
    page_default_attrs,
)
from riescue.riemap.pagetables import PTAttrs


class TestPteAttributeSchema(unittest.TestCase):
    # The six keys whose default differs between the PTAttrs model and the Page default set.
    DIFFERING_KEYS = {"v", "a_level0", "d_level0", "n", "u", "u_level0"}

    def test_generated_level_matrices_are_complete(self):
        schema = pt_attrs_schema()
        for base in PTE_BASES:
            for level in range(5):
                self.assertIn(f"{base}_level{level}", schema)
        for base in PTE_BASES:
            for vs_level in range(5):
                for g_level in range(5):
                    self.assertIn(
                        f"{base}_level{vs_level}_glevel{g_level}",
                        schema,
                    )

    def test_common_excludes_differing_keys(self):
        for key in self.DIFFERING_KEYS:
            self.assertNotIn(key, COMMON_PTE_ATTRS, f"{key} must be a per-role overlay, not COMMON")

    def test_ptattrs_class_uses_schema(self):
        # The class attribute must be exactly what the builder produces.
        self.assertEqual(PTAttrs.base_attrs, pt_attrs_schema())

    def test_ptattrs_specific_values(self):
        pt = pt_attrs_schema()
        self.assertIsNone(pt["v"])
        self.assertEqual(pt["a_level0"], 0)
        self.assertEqual(pt["d_level0"], 0)
        self.assertEqual(pt["n"], 0)
        self.assertEqual(pt["u"], 0)
        self.assertEqual(pt["u_level0"], 0)

    def test_page_specific_values(self):
        pg = page_default_attrs(1)
        self.assertEqual(pg["v"], 1)
        self.assertEqual(pg["a_level0"], 1)
        self.assertEqual(pg["d_level0"], 1)
        self.assertIsNone(pg["n"])

    def test_page_ubit_is_parameterized(self):
        self.assertEqual(page_default_attrs(1)["u"], 1)
        self.assertEqual(page_default_attrs(1)["u_level0"], 1)
        self.assertEqual(page_default_attrs(0)["u"], 0)
        self.assertEqual(page_default_attrs(0)["u_level0"], 0)


if __name__ == "__main__":
    unittest.main()
