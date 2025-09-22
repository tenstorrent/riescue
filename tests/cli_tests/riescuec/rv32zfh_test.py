# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rv32Zfh(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rv_zfh/rv32zfh.json --max_instrs 2500 --rpt_cnt 2 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rv_zfh/rv32zfh.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rv_zfh/rv32zfh.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rv_zfh/rv32zfh.json --max_instrs 2500 --rpt_cnt 2 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rv_zfh/rv32zfh_half_precision_classify.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
