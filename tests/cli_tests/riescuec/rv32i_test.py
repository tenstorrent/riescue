# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rv32I(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 0 --fe_tb -tnt --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 0 --fe_tb -tnt --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 1 --fe_tb --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 1 --fe_tb -tnt --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 0 --fe_tb --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 0 --fe_tb --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 1 --fe_tb --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_7(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_8(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 1 -cct 1 --fe_tb -tnt --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_9(self):
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 5 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_10(self):
        args = "--json compliance/tests/rv_i/rv32i_control_transfer_conditional.json --max_instrs 5000 --rpt_cnt 5 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_11(self):
        args = "--json compliance/tests/rv_i/rv32i_no_branches.json --max_instrs 5000 --rpt_cnt 1 -cct 1 --wysiwyg  -tnt --user_config ./compliance/tests/configs/machine_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_12(self):
        args = "--json compliance/tests/rv_i/rv32i_compute_register_immediate.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_13(self):
        args = "--json compliance/tests/rv_i/rv32i_load_store.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_14(self):
        args = "--json compliance/tests/rv_i/rv32i_control_transfer_unconditional.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_15(self):
        args = "--json compliance/tests/rv_i/rv32i_compute_register_register.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
