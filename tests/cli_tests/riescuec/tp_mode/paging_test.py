# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class PagingTest(BaseRiescueCTest):
    "Runs paging test plan"

    def test_cli(self):
        self.run_tp_mode(plan="paging", cli_args=["--test_paging_mode", "sv39", "--test_priv_mode", "super"])
        self.run_tp_mode(plan="paging", cli_args=["--test_paging_mode", "sv39", "--test_priv_mode", "user"])
        self.run_tp_mode(plan="paging", cli_args=["--test_paging_mode", "sv48", "--test_priv_mode", "super"])
        self.run_tp_mode(plan="paging", cli_args=["--test_paging_mode", "sv48", "--test_priv_mode", "user"])
        self.run_tp_mode(plan="paging", cli_args=["--test_paging_mode", "sv57", "--test_priv_mode", "super"])
        self.run_tp_mode(plan="paging", cli_args=["--test_paging_mode", "sv57", "--test_priv_mode", "user"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
