# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rv32F(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rv_f/rv32f.json --max_instrs 2500 --rpt_cnt 2 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rv_f/rv32f.json --max_instrs 5000 --rpt_cnt 5 -cct 1 --force_alignment --first_pass_iss spike --second_pass_iss spike --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rv_f/rv32f.json --max_instrs 2500 --rpt_cnt 2 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rv_f/rv32f.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rv_f/rv32f.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = "--json compliance/tests/rv_f/rv32f.json --num_cpus 2 --mp_mode simultaneous --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/rv_f/rv32f.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_7(self):
        args = "--json compliance/tests/rv_f/rv32f.json --max_instrs 2500 --rpt_cnt 2 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_8(self):
        args = "--json compliance/tests/rv_f/rv32f.json --max_instrs 2500 --rpt_cnt 2 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
