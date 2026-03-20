# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue.lib.toolchain import ToolchainError, ToolFailureType

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class ConfTest(BaseRiescueCTest):
    "Checks that conf gets passed through correctly"

    def test_cli(self):
        loop_conf = Path(__file__).parents[2] / "riescued/data/loop_conf.py"
        args = "--json compliance/tests/rv_i/rv32i.json --max_instrs 5000 --rpt_cnt 5 --whisper_max_instr 2500 --conf " + str(loop_conf)
        toolchain_errors = self.expect_toolchain_failure_tp(cli_args=args.split(), failure_kind=ToolFailureType.MAX_INSTRUCTION_LIMIT)

        for error in toolchain_errors:
            self.assertEqual(error.kind, ToolFailureType.MAX_INSTRUCTION_LIMIT, f"Expected max instruction limit exception, got {error.kind}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
