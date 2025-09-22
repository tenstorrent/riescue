# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from riescue.riescuec import RiescueC


class Rv64A(unittest.TestCase):

    def test_0(self):
        args = "--json compliance/tests/rv_a/rv64a.json --max_instrs 5000 --rpt_cnt 5 --seed 0"
        RiescueC.run_cli(args=args.split())

    def test_1(self):
        args = "--json compliance/tests/rv_a/rv64a.json --max_instrs 5000 --rpt_cnt 5 -cct 1 --seed 0"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
