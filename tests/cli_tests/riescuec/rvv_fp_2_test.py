# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class RvvFp(unittest.TestCase):

    def test_13(self):
        args = "--json compliance/tests/rvv/rvv_fp_minmax.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_14(self):
        args = "--json compliance/tests/rvv/rvv_fp_move.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_15(self):
        args = "--json compliance/tests/rvv/rvv_fp_signinj.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_16(self):
        args = "--json compliance/tests/rvv/rvv_fp_slides.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_17(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_widening_add_sub --rpt_cnt 1 --disable_pass --max_instrs 10000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_18(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_sqrt --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_19(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_widening_mul --rpt_cnt 1 --disable_pass --max_instrs 10000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_20(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_widening_mac --rpt_cnt 1 --disable_pass --max_instrs 10000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
