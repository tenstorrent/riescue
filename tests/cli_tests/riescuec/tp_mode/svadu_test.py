# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class SvaduTest(BaseRiescueCTest):
    "Runs SVADU test plan"

    def test_cli(self):
        self.run_tp_mode(plan="svadu", cli_args=["--test_paging_mode", "sv39", "--deleg_excp_to", "machine", "--test_priv_mode", "super", "--repeat_times", "1"])
        self.run_tp_mode(plan="svadu", cli_args=["--test_paging_mode", "sv48", "--deleg_excp_to", "machine", "--test_priv_mode", "super", "--repeat_times", "1"])
        self.run_tp_mode(plan="svadu", cli_args=["--test_paging_mode", "sv57", "--deleg_excp_to", "machine", "--test_priv_mode", "super", "--repeat_times", "1"])
        self.run_tp_mode(plan="svadu", cli_args=["--test_paging_mode", "sv39", "--deleg_excp_to", "machine", "--test_priv_mode", "user", "--repeat_times", "1"])
        self.run_tp_mode(plan="svadu", cli_args=["--test_paging_mode", "sv48", "--deleg_excp_to", "machine", "--test_priv_mode", "user", "--repeat_times", "1"])
        self.run_tp_mode(plan="svadu", cli_args=["--test_paging_mode", "sv57", "--deleg_excp_to", "machine", "--test_priv_mode", "user", "--repeat_times", "1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
