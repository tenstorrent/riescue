# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC


class ZfhArith0Test(unittest.TestCase):
    def test_1(self):
        args = "--json compliance/tests/rv_zfh/fp_bringup/zfh_arith.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rv_zfh/fp_bringup/zfh_compare.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rv_zfh/fp_bringup/zfh_convert_moves.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = "--json compliance/tests/rv_zfh/fp_bringup/zfh_fma.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/rv_zfh/fp_bringup/zfh_load.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_7(self):
        args = "--json compliance/tests/rv_zfh/fp_bringup/zfh_sqrt.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_8(self):
        args = "--json compliance/tests/rv_zfh/fp_bringup/zfh_conversion.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
