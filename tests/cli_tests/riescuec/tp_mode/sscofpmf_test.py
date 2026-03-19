# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class SscofpmfTest(BaseRiescueCTest):
    "Runs SSCOFPMFtest plan"

    def test_cli(self):
        self.run_tp_mode(plan="sscofpmf", cli_args=["--test_paging_mode", "sv39", "--repeat_times", "1"])
        self.run_tp_mode(plan="sscofpmf", cli_args=["--test_paging_mode", "sv48", "--repeat_times", "1"])
        self.run_tp_mode(plan="sscofpmf", cli_args=["--test_paging_mode", "sv57", "--repeat_times", "1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
