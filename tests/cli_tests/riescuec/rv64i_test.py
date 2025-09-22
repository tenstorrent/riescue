# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rv64I(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rv_i/rv64i.json --privilege_mode supervisor --test_env virtualized --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rv_i/rv64i.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rv_i/rv64i.json --num_cpus 2 --mp_mode parallel --parallel_scheduling_mode round_robin --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rv_i/rv64i.json --num_cpus 2 --mp_mode simultaneous --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rv_i/rv64i.json --max_instrs 5000 --rpt_cnt 5 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = "--json compliance/tests/rv_i/rv64i.json --max_instrs 5000 --rpt_cnt 5 --cpuconfig dtest_framework/tests/cpu_config.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/rv_i/rv64i.json --max_instrs 5000 --rpt_cnt 5 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_7(self):
        args = "--json compliance/tests/rv_i/rv64i.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_8(self):
        args = "--json compliance/tests/rv_i/rv64i.json --privilege_mode supervisor --test_env virtualized --max_instrs 5000 --rpt_cnt 5 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_9(self):
        args = "--json compliance/tests/rv_i/rv64i.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
