# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing4(unittest.TestCase):

    def test_65(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_indexed_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_66(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_fp_widening_reductions --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_67(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_compare --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_68(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_fma_s --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_69(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_move --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_71(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fixed_point_scaling_shift --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_72(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_whole_reg --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_73(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_int_quad_widening_4d_dot_prod --first_pass_iss whisper --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_74(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_unit_stride_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_76(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_vec_int_extension --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
