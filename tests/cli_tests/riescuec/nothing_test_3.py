# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing3(unittest.TestCase):

    def test_36(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_vec_single_width_shift --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_37(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_vec_single_width_integer_fma --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_42(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_strided_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_44(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_class --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_46(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_strided_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_47(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_int_widening_reductions --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_55(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_strided --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_57(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvv_4_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_60(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_scalar_move --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_64(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fixed_point_multiply_saturating_rounding --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
