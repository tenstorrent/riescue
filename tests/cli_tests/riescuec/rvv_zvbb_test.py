# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC


class RvvZvbb10Test(unittest.TestCase):
    def test1(self):
        args = " ".join(
            [
                "--json",
                "compliance/tests/rvv/rvv_zvbb_1.json",
                "--first_pass_iss",
                "whisper",
                "--rpt_cnt",
                "1",
                "--max_instrs",
                "10000",
                "--user_config",
                "./compliance/tests/configs/user_rvv_fp_quals.json",
                "--rv_zvbb_experimental",
                "--seed",
                "0",
            ]
        )
        RiescueC.run_cli(args=args.split())

    def test2(self):
        args = " ".join(
            [
                "--json",
                "compliance/tests/rvv/rvv_zvbb_2.json",
                "--first_pass_iss",
                "whisper",
                "--rpt_cnt",
                "1",
                "--max_instrs",
                "10000",
                "--user_config",
                "./compliance/tests/configs/user_rvv_fp_quals.json",
                "--rv_zvbb_experimental",
                "--seed",
                "0",
            ]
        )

        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
