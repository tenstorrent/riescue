# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing6(unittest.TestCase):

    def test_97(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_widening_mac --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_101(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fixed_point_narrowing_clip --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_102(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvx_macc --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_103(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_fp_scalar_move --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_106(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_indexed_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_107(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fixed_point_saturation_add_sub --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_109(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opivv_2_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_110(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvx_macc --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_111(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opivx_2_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_112(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_int_arithmetic --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
