# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class OnDemand_CSR_RW_Tests(BaseRiescuedTest):
    """
    Combined tests for ondemand_csr_rw
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_on_demand_csr.s"
        super().setUp()

    def test_ondemand_csr_rw_super(self):
        args = [
            "--test_priv_mode",
            "super",
            "--deleg_excp_to",
            "machine",
            "--run_iss",
            "--repeat_times",
            "2",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_ondemand_csr_rw_machine(self):
        args = [
            "--test_priv_mode",
            "machine",
            "--deleg_excp_to",
            "machine",
            "--run_iss",
            "--repeat_times",
            "2",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_ondemand_csr_rw_user(self):
        args = [
            "--test_priv_mode",
            "user",
            "--deleg_excp_to",
            "machine",
            "--run_iss",
            "--repeat_times",
            "2",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
