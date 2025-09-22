# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescuec import RiescueC


class ExcludeInstrTest(unittest.TestCase):
    def test_cli(self):
        args = "--json compliance/tests/rv_i/rv64i.json --max_instrs 5000 --rpt_cnt 5 --seed 0 --exclude_instrs ld --exclude_instrs addi --exclude_instrs ld"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
