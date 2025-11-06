# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class EndTestTests(BaseRiescuedTest):
    """
    Checks that end_test.s runs correctly. Runs on 2-hart by default
    Runs on all privileges and delegations
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/end_test.s"
        self.default_args = ["--run_iss", "--whisper_max_instr", "10000"]
        super().setUp()

    def test_single_hart_all_priv(self):
        for deleg in ["machine", "super"]:
            for test_priv in ["machine", "super", "user"]:
                args = self.default_args + ["--test_priv_mode", test_priv, "--deleg_excp_to", deleg, "--num_cpus", "1"]
                self.run_riescued(testname=self.testname, cli_args=args, iterations=1)

    def test_mp_parallel_all_priv(self):
        for deleg in ["machine", "super"]:
            for test_priv in ["machine", "super", "user"]:
                args = self.default_args + ["--test_priv_mode", test_priv, "--deleg_excp_to", deleg, "--num_cpus", "2", "--mp_mode", "parallel"]
                self.run_riescued(testname=self.testname, cli_args=args, iterations=1)

    def test_mp_simultaneous_all_priv(self):
        for deleg in ["machine", "super"]:
            for test_priv in ["machine", "super", "user"]:
                args = self.default_args + ["--test_priv_mode", test_priv, "--deleg_excp_to", deleg, "--num_cpus", "2", "--mp_mode", "simultaneous"]
                self.run_riescued(testname=self.testname, cli_args=args, iterations=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
