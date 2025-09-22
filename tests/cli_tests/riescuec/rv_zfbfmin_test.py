# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC


class RvZfbfminConversion0Test(unittest.TestCase):
    def test_cli(self):
        args = "--json compliance/tests/rv_zfbfmin/rv_zfbfmin_conversion.json --max_instrs 2500 --rpt_cnt 2 -rze --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
