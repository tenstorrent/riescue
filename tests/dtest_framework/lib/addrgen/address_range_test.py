# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from typing import Iterator

from riescue.dtest_framework.lib.addrgen.address_range import address_range_set, AddressRangeSet, AddressRange


class AddressRangeTest(unittest.TestCase):
    """
    Test the addrgen module. Target AddressRangeSet
    """

    def setUp(self):
        self.range = address_range_set()

    def test_address_range_set(self):
        """
        basic API test, making sure that add, remove, query work as expected
        """
        # address_range_set should return an AddressRangeSet
        self.assertIsInstance(self.range, AddressRangeSet)

        # Check `add` method
        self.range.add((0x1000, 0x2000))
        self.range.add((0x3000, 0x4000))

        # Check `len` method
        self.assertEqual(len(self.range), 2)

        # Check `iter` method
        self.assertIsInstance(iter(self.range), Iterator)
        for i in self.range:
            self.assertIsInstance(i, tuple, f"iterating through range should return tuple, got {type(i)}")  # Replace this if AddressRange

        # Check `str` method has hex addresses
        self.assertIn("0x", str(self.range))
        self.assertIn("(0x1000, 0x2000), (0x3000, 0x4000)", str(self.range))

        # Check `overlap` method
        self.assertEqual(self.range.overlap((0x2000, 0x3000)), [(0x1000, 0x2000), (0x3000, 0x4000)])
        self.assertEqual(self.range.overlap((0x0000, 0x2000)), [(0x1000, 0x2000)])
        self.assertEqual(self.range.overlap((0x3000, 0x5000)), [(0x3000, 0x4000)])

        # Check `remove` method
        self.range.remove((0x1000, 0x2000))
        self.assertEqual(len(self.range), 1, "Should only have 1 set left")
        self.assertNotIn((0x1000, 0x2000), self.range)
        self.assertIn((0x3000, 0x4000), self.range)
        self.assertEqual(self.range.overlap((0x2000, 0x3000)), [(0x3000, 0x4000)])
        # Check that removing a non-existent range raises an error
        with self.assertRaises(KeyError, msg="Legacy behavior is to remove the exact pair from the container. Specify multiple pairs should raise an error."):
            self.range.remove((0x2000, 0x3000))

    def test_address_range_set_zero_length(self):
        """
        Test that adding a zero-length range still works correctly
        """
        self.range.add((0xFFF, 0xFFF))
        self.assertEqual(len(self.range), 1)
        self.assertIn((0xFFF, 0xFFF), self.range)

        self.assertEqual(self.range.overlap((0x0, 0xFFF)), [(0xFFF, 0xFFF)])
        self.range.add((0x0, 0xF00))
        self.assertEqual(self.range.overlap((0x0, 0x1000)), [(0x0, 0xF00), (0xFFF, 0xFFF)])

        self.range.add((0x2000, 0x2FFF))
        self.assertEqual(self.range.overlap((0x0, 0x1000)), [(0x0, 0xF00), (0xFFF, 0xFFF)])

        self.range.remove((0xFFF, 0xFFF))
        self.assertEqual(self.range.overlap((0x0, 0x1000)), [(0x0, 0xF00)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
