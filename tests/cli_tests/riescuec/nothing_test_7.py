# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing7(unittest.TestCase):

    def test_113(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rv_zvfbfwma --rpt_cnt 1 --max_instrs 1000 --rv_zvfbfwma_experimental --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_114(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_int_single_width_reductions --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_120(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvx_1_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_127(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opivi_2_data_processing --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_128(self):
        args = "--json compliance/tests/special/nothing.json --groups rv_zvfbfmin -rvf --rpt_cnt 1 --max_instrs 5000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_129(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_store_indexed_unordered --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_131(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_vec_int_extension --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_132(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_whole_reg --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_135(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vec_int_widening_multiply_add --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_136(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_load_fault_only_first_segmented --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
