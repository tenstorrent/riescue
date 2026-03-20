# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing1(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_sha2_secure_hash --rpt_cnt 1 --max_instrs 1000 --rv_zvknhb_experimental --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_muldiv --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_unit_stride_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups zvkg --rpt_cnt 1 --max_instrs 1000 --rv_zvkg_experimental --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups zvbb --rpt_cnt 1 --max_instrs 1000  --rv_zvbb_experimental --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_8(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_indexed_ordered --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_9(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --instrs vadd.vv --rpt_cnt 1 --max_instrs 5000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_13(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvv_3_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_14(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups zvbc --rpt_cnt 1 --max_instrs 1000 --rv_zvbc_experimental --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_16(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_fixed_point_averaging_add_sub --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
