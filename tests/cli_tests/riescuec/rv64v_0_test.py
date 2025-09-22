# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC


class Rv64V0Test(unittest.TestCase):
    def test_1(self):
        args = "--json compliance/tests/rvv/rv64v.json --first_pass_iss whisper --rpt_cnt 2  --seed 3702527970 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rvv/rvv_vrgather.json --first_pass_iss whisper --rpt_cnt 5 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rvv/rvv_integer_merge.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rvv/rvv_integer_extension.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
