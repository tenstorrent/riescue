# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing8(unittest.TestCase):

    def test_137(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_merge --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_149(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_whole_vec_reg_move --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_150(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups zvbc,zvkg --rpt_cnt 1 --max_instrs 1000 --rv_zvbc_experimental --rv_zvkg_experimental --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_151(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_int_narrowing_right_shift --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_152(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_strided --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_153(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_indexed_unordered --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_154(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_indexed_ordered --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_156(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opivv --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_159(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_int_narrowing_right_shift --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_160(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_fp_single_width_reductions --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
