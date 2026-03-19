# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue import RiescueD

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class Test_ExcpTests(BaseRiescuedTest):
    """
    Combined tests for test_excp
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_excp.s"
        super().setUp()

    def excp_checks(self, runs: list[RiescueD]):
        "Additional checks for excp tests. Check that tval was checked with expected value"
        for run in runs:
            all_subroutines = self.get_all_executed_subrountines(run)
            tval_subroutines = [subroutine for subroutine in all_subroutines if "nonzero_tval_check" in subroutine]
            self.assertGreater(len(tval_subroutines), 0, f"Expected at least 1 tval check subroutine, got {len(tval_subroutines)} routines that ended with 'nonzero_tval_check'")

    def htval_checks(self, runs: list[RiescueD]):
        "Check that htval was checked with expected value"
        for run in runs:
            all_subroutines = self.get_all_executed_subrountines(run)
            htval_subroutines = [subroutine for subroutine in all_subroutines if "nonzero_htval_check" in subroutine]
            self.assertGreater(len(htval_subroutines), 0, f"Expected at least 1 htval check subroutine, got {len(htval_subroutines)} routines that ended with 'nonzero_htval_check'")

    def test_excp_with_cpuconfig(self):
        args = ["--run_iss", "--cpuconfig", "dtest_framework/tests/cpu_config.json"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)

    def test_excp_virtualized_env_hs_deleg(self):
        args = ["--run_iss", "--test_env", "virtualized", "--hedeleg=0x0"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)
        self.htval_checks(runs)

    def test_excp_virtualized_env(self):
        args = ["--run_iss", "--test_env", "virtualized"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)

    def test_excp_basic(self):
        args = ["--run_iss"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)

    def test_excp_pbmt_ncio(self):
        args = ["--run_iss", "--pbmt_ncio_randomization", "100"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_excp_hooks(self):
        args = ["--run_iss", "--excp_hooks"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_aplic_intr(self):
        args = [
            "--run_iss",
            "--iss",
            "whisper",
            "--c_used",
            "--cpuconfig",
            "dtest_framework/tests/cpu_config_aplic_imsic.json",
            "--whisper_config_json",
            "dtest_framework/lib/whisper_aplic_config.json",
        ]
        self.testname = "dtest_framework/tests/test_aplic_intr.s"
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
