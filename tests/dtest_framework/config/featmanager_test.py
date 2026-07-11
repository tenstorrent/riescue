# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from riescue.dtest_framework.config import FeatMgr


class FeatMgrTest(unittest.TestCase):
    """
    Test the FeatMgr module.
    """

    def test_featmgr_builds_no_args(self):
        "FeatMgr is a dataclass that has default values for all fields. No arguments should be required"
        f = FeatMgr()

    def test_hart_ids_default_contiguous(self):
        "With no explicit hart_ids, get_hart_ids returns the contiguous 0..num_cpus-1 and is not discontiguous"
        f = FeatMgr(num_cpus=3)
        self.assertIsNone(f.hart_ids)
        self.assertEqual(f.get_hart_ids(), [0, 1, 2])
        self.assertFalse(f.discontiguous_hartids())

    def test_hart_ids_explicit_contiguous_not_flagged(self):
        "An explicit list equal to the default range is not treated as discontiguous"
        f = FeatMgr(num_cpus=3, hart_ids=[0, 1, 2])
        self.assertEqual(f.get_hart_ids(), [0, 1, 2])
        self.assertFalse(f.discontiguous_hartids())

    def test_hart_ids_discontiguous(self):
        "A non-default hart-id list is reported as discontiguous and returned verbatim"
        f = FeatMgr(num_cpus=3, hart_ids=[0, 2, 4])
        self.assertEqual(f.get_hart_ids(), [0, 2, 4])
        self.assertTrue(f.discontiguous_hartids())

    def test_hart_ids_single_nonzero_discontiguous(self):
        "A single hart with a non-zero mhartid is still discontiguous"
        f = FeatMgr(num_cpus=1, hart_ids=[5])
        self.assertEqual(f.get_hart_ids(), [5])
        self.assertTrue(f.discontiguous_hartids())
