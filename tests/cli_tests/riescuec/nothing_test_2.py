# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing2(unittest.TestCase):

    def test_17(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvv_3_a_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_19(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_fault_only_first --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_21(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_convert --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_24(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvv_macc --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_26(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_unit_stride_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_28(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_strided_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_30(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_v_fp_minmax --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_32(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvx_1_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_33(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_strided_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_34(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_unit_stride_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
