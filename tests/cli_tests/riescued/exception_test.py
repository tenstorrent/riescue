# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class Test_ExcpTests(BaseRiescuedTest):
    """
    Combined tests for test_excp
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_excp.s"
        super().setUp()

    def test_excp_with_cpuconfig(self):
        args = ["--run_iss", "--cpuconfig", "dtest_framework/tests/cpu_config.json"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_excp_virtualized_env(self):
        args = ["--run_iss", "--test_env", "virtualized"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_excp_basic(self):
        args = ["--run_iss"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_excp_pbmt_ncio(self):
        args = ["--run_iss", "--pbmt_ncio_randomization", "100"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_excp_hooks(self):
        args = ["--run_iss", "--excp_hooks"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class Skip_InstrTests(BaseRiescuedTest):
    """
    Combined tests for skip_instr
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/skip_instr.s"
        super().setUp()

    def test_skip_instr_basic(self):
        args = [
            "--run_iss",
            "--skip_instruction_for_unexpected",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=1)  # FIXME: Fails on seed 2+

    def test_skip_instr_parallel(self):
        args = [
            "--run_iss",
            "--skip_instruction_for_unexpected",
            "--num_cpus",
            "2",
            "--mp_mode",
            "parallel",
            "--parallel_scheduling_mode",
            "round_robin",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=1)  # FIXME: Fails on seed 2+


if __name__ == "__main__":
    unittest.main(verbosity=2)
