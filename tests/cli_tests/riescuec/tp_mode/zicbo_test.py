# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC


class ZicboTest(unittest.TestCase):
    "Runs ZICBOM/ZICBOZ/ZICBOP/ZIC64B test plan"

    def test_cli(self):
        args = "--mode tp --test_plan zicbom_zicboz_zicbop_zic64b --deleg_excp_to machine"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
