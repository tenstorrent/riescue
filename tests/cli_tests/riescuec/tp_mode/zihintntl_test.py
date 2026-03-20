# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class ZihintntlTest(BaseRiescueCTest):
    "Runs ZIHINTNTL test plan"

    def test_cli(self):
        self.run_tp_mode(plan="zihintntl", cli_args=["--repeat_times", "1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
