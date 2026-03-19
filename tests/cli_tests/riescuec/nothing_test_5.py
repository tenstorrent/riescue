# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing5(unittest.TestCase):

    def test_77(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_unit_stride --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_78(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_addsub_s --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_79(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvv_2_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_80(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_unit_stride --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_81(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_whole_vec_reg_move --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_84(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opivi --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_85(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_aes_block_cipher --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_86(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_rec_sqrt_est --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_90(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_vec_single_width_integer_fma --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_91(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_signinj --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
