# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Nothing9(unittest.TestCase):

    def test_162(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups vector_opmvv_vid --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_167(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_vec_integer_merge --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_168(self):
        args = "--json compliance/tests/special/nothing.json --first_pass_iss whisper --groups rvv_vec_single_width_shift --rpt_cnt 1 --max_instrs 1000 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
