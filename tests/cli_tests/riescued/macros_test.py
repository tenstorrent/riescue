# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class MacrosTests(BaseRiescuedTest):
    """
    Checks that test_macros.s runs correctly.
    Runs on all privileges and delegations
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_macros.s"
        super().setUp()

    def test_machine_test_machine_deleg(self):
        args = ["--run_iss", "--test_priv_mode", "machine", "--deleg_excp_to", "machine"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_machine_test_super_deleg(self):
        args = ["--run_iss", "--test_priv_mode", "machine", "--deleg_excp_to", "super"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_super_test_machine_deleg(self):
        args = ["--run_iss", "--test_priv_mode", "super", "--deleg_excp_to", "machine"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_super_test_super_deleg(self):
        args = ["--run_iss", "--test_priv_mode", "super", "--deleg_excp_to", "super"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_user_test_machine_deleg(self):
        args = ["--run_iss", "--test_priv_mode", "user", "--deleg_excp_to", "machine"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_user_test_super_deleg(self):
        args = ["--run_iss", "--test_priv_mode", "user", "--deleg_excp_to", "super"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
