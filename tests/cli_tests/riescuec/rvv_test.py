# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rvv(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rvv/rvv_5.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json  -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rvv/rvv_16.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000"
        args += " --user_config ./compliance/tests/configs/quals_rv64_IMFV.json  -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_2(self):
        args = "--json compliance/tests/rvv/rvv_15.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_3(self):
        args = "--json compliance/tests/rvv/rvv_1.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_4(self):
        args = "--json compliance/tests/rvv/rvv_13.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_5(self):
        args = "--json compliance/tests/rvv/rvv_6.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000"
        args += " --user_config ./compliance/tests/configs/quals_rv64_IMFV.json"
        args += " -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_6(self):
        args = "--json compliance/tests/rvv/rvv_3.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json  -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_7(self):
        args = "--json compliance/tests/rvv/rvv_0.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
