# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rvv(unittest.TestCase):

    def test_8(self):
        args = "--json compliance/tests/rvv/rvv_4.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000"
        args += " --user_config ./compliance/tests/configs/quals_rv64_IMFV.json"
        args += " -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_9(self):
        args = "--json compliance/tests/rvv/rvv_2.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json  -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_10(self):
        args = "--json compliance/tests/rvv/rvv_17.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000"
        args += " --user_config ./compliance/tests/configs/quals_rv64_IMFV.json"
        args += " -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_11(self):
        args = "--json compliance/tests/rvv/rvv_9.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000"
        args += " --user_config ./compliance/tests/configs/quals_rv64_IMFV.json"
        args += " -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_12(self):
        args = "--json compliance/tests/rvv/rvv_14.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_13(self):
        args = "--json compliance/tests/rvv/rvv_8.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_14(self):
        args = "--json compliance/tests/rvv/rvv_10.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_15(self):
        args = "--json compliance/tests/rvv/rvv_7.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000"
        args += " --user_config ./compliance/tests/configs/quals_rv64_IMFV.json"
        args += " -cct 1 --more_os_pages --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_16(self):
        args = "--json compliance/tests/rvv/rvv_12.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000"
        args += " --user_config ./compliance/tests/configs/quals_rv64_IMFV.json"
        args += " -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())
