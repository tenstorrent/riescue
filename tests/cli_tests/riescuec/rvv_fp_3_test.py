# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class RvvFp(unittest.TestCase):

    def test_8(self):
        args = "--json compliance/tests/rvv/rvv_fp_widening_mac.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_9(self):
        args = "--json compliance/tests/rvv/rvv_fp_cvt_n.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_10(self):
        args = "--json compliance/tests/rvv/rvv_fp_class.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_11(self):
        args = "--json compliance/tests/rvv/rvv_fp_compare.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_12(self):
        args = "--json compliance/tests/rvv/rvv_fp_merge.json --first_pass_iss whisper --rpt_cnt 2 --max_instrs 10000 --user_config ./compliance/tests/configs/user_rvv_fp_quals.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_21(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_widening_mul --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_22(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_widening_add_sub --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_23(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_rec_est --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_49(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fp_slides --rpt_cnt 1 --disable_pass --max_instrs 10000 --seed 0"
        RiescueC.run_cli(args=args.split())
