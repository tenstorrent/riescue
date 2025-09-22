# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC

# class Rvv11Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_11.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv11Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_11.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json  -cct 1 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv11Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_11.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv11Test(unittest.TestCase):
#        args = " ".join([
#    "--json",
#    "compliance/tests/rvv/rvv_11.json",
#    "--first_pass_iss",
#    "whisper",
#    "--rpt_cnt",
#    "1",
#    "--disable_pass",
#    "--max_instrs",
#    "10000",
#    "--user_config",
#    "./compliance/tests/configs/quals_rv64_IMFV.json",
#    "-cct",
#    "1",
#    "--seed",
#    "0",
# ])
#        RiescueC.run_cli(args=args.split())
#
# class Rv64ATest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_a/rv64a.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv32ATest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_a/rv32a.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv32DTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_d/rv32d.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv32DTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_d/rv32d.json --num_cpus 2 --mp_mode simultaneous --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv64DTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_d/rv64d.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv64DTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_d/rv64d.json --num_cpus 2 --mp_mode simultaneous --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv18Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_18.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv18Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_18.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json  -cct 1 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv18Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_18.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv18Test(unittest.TestCase):
#        args = " ".join([
#    "--json",
#    "compliance/tests/rvv/rvv_18.json",
#    "--first_pass_iss",
#    "whisper",
#    "--rpt_cnt",
#    "1",
#    "--disable_pass",
#    "--max_instrs",
#    "10000",
#    "--user_config",
#    "./compliance/tests/configs/quals_rv64_IMFV.json",
#    "-cct",
#    "1",
#    "--seed",
#    "0",
# ])
#        RiescueC.run_cli(args=args.split())
#
# class Rvv19Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_19.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv19Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_19.json --first_pass_iss whisper --rpt_cnt 1 --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json  -cct 1 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv19Test(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rvv/rvv_19.json --first_pass_iss whisper --rpt_cnt 1 --disable_pass --max_instrs 10000 --user_config ./compliance/tests/configs/quals_rv64_IMFV.json --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rvv19Test(unittest.TestCase):
#        args = " ".join([
#    "--json",
#    "compliance/tests/rvv/rvv_19.json",
#    "--first_pass_iss",
#    "whisper",
#    "--rpt_cnt",
#    "1",
#    "--disable_pass",
#    "--max_instrs",
#    "10000",
#    "--user_config",
#    "./compliance/tests/configs/quals_rv64_IMFV.json",
#    "-cct",
#    "1",
#    "--seed",
#    "0",
# ])
#        RiescueC.run_cli(args=args.split())
#
# class Rv32ZfhTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_zfh/rv32zfh.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv32ZfhTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_zfh/rv32zfh.json --num_cpus 2 --mp_mode simultaneous --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv64ZfhTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_zfh/rv64zfh.json --num_cpus 2 --mp_mode parallel --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
# class Rv64ZfhTest(unittest.TestCase):
#    def test_cli(self):
#        args = "--json compliance/tests/rv_zfh/rv64zfh.json --num_cpus 2 --mp_mode simultaneous --max_instrs 5000 --rpt_cnt 5 --seed 0"
#        RiescueC.run_cli(args=args.split())
#
if __name__ == "__main__":
    unittest.main(verbosity=2)
