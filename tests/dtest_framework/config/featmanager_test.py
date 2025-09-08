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
