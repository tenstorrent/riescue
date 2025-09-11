# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class HypervisorTests(BaseRiescuedTest):

    def test_virtualized_env(self):
        "Default test with Virtualized environment"
        self.testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--test_env", "virtualized"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_vs_basic(self):
        args = [
            "--run_iss",
            "--test_env",
            "virtualized",
        ]
        self.testname = "dtest_framework/tests/test_vs.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    # def test_vs_gstage_super(self):
    # RVTOOLS-4732
    #     args = [
    #         "--run_iss",
    #         "--test_env",
    #         "virtualized",
    #         "--deleg_excp_to",
    #         "super",
    #     ]
    #     testname = "dtest_framework/tests/test_vs_gstage.s"
    #     self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    # def test_vs_gstage_super_no_svadu(self):
    # RVTOOLS-4732
    #     args = [
    #         "--run_iss",
    #         "--test_env",
    #         "virtualized",
    #         "--deleg_excp_to",
    #         "super",
    #         "--a_d_bit_randomization",
    #         "0",
    #     ]
    #     testname = "dtest_framework/tests/test_vs_gstage.s"
    #     self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)  # FIXME: Fails on seed 2, 3

    # def test_vs_gstage_super_with_svadu(self):
    # RVTOOLS-4732
    #     args = [
    #         "--run_iss",
    #         "--test_env",
    #         "virtualized",
    #         "--deleg_excp_to",
    #         "super",
    #         "--a_d_bit_randomization",
    #         "100",
    #     ]
    #     testname = "dtest_framework/tests/test_vs_gstage.s"
    #     self.run_riescued(testname=testname, cli_args=args, iterations=1)  # FIXME: Fails on seed 2, 3


if __name__ == "__main__":
    unittest.main(verbosity=2)
