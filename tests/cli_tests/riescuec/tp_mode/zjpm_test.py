# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class ZjpmTest(BaseRiescueCTest):
    "Runs ZJPM test plan"

    def test_cli(self):
        """Test ZJPM in non-virtualized environment with different paging modes."""
        for paging_mode in ("sv39", "sv48", "sv57"):
            self.run_tp_mode(plan="zjpm", cli_args=["--test_paging_mode", paging_mode, "--repeat_times", "1"])

    def test_cli_virtualized(self):
        """Test ZJPM in virtualized environment with different paging mode combinations."""
        for paging_mode in ("sv39", "sv48", "sv57"):
            for g_paging_mode in ("sv39", "sv48", "sv57"):
                self.run_tp_mode(
                    plan="zjpm",
                    cli_args=[
                        "--test_env",
                        "virtualized",
                        "--test_paging_mode",
                        paging_mode,
                        "--test_paging_g_mode",
                        g_paging_mode,
                        "--repeat_times",
                        "1",
                    ],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
