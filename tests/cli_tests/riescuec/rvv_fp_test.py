# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class RvvFp(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rvv/rvv_fp_cvt_w.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rvv/rvv_fp_cvt_s.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rvv/rvv_fp_fma_s.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rvv/rvv_fp_widening_mul.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rvv/rvv_fp_rec_sqrt.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = (
            "--json compliance/tests/rvv/rvv_fp_widening_add_sub.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        )
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/rvv/rvv_fp_addsub_s.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_7(self):
        args = "--json compliance/tests/rvv/rvv_fp_muldiv_s.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
