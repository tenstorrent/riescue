# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC


class ZicondTest(unittest.TestCase):
    "Runs ZICOND test plan"

    def test_cli(self):
        args = "--mode tp --test_plan zkt"
        RiescueC.run_cli(args=args.split())
