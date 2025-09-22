# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rv32D(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rv_d/rv32d.json --max_instrs 2500 --rpt_cnt 2 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rv_d/rv32d.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rv_d/rv32d_double_precision_load_store.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rv_d/rv32d_double_precision_convert_move.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rv_d/rv32d_double_precision_classify.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = "--json compliance/tests/rv_d/rv32d_double_precision_reg.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/rv_d/rv32d_double_precision_compare.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_7(self):
        args = "--json compliance/tests/rv_d/rv32d_double_precision_reg_reg_reg.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_8(self):
        args = "--json compliance/tests/rv_d/rv32d_double_precision_reg_reg.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
