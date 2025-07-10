# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class Mp_Par_5PTests(BaseRiescuedTest):
    """
    Combined tests for mp_par_5p
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/mp_par_5p.s"
        super().setUp()

    def test_mp_par5_default_mode(self):
        args = ["--run_iss", "--repeat_times", "2", "--test_priv_mode", "user", "--num_cpus", "5", "--pbmt_ncio_randomization", "0"]
        testname = "dtest_framework/tests/mp_par_5p_mode_default_test.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_mp_par5_basic(self):
        args = [
            "--run_iss",
            "--repeat_times",
            "2",
            "--num_cpus",
            "2",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp_par5_user_priv_2cpu(self):
        args = [
            "--run_iss",
            "--repeat_times",
            "2",
            "--test_priv_mode",
            "user",
            "--num_cpus",
            "2",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp_par5_user_priv_5cpu(self):
        args = ["--run_iss", "--repeat_times", "2", "--test_priv_mode", "user", "--num_cpus", "5", "--pbmt_ncio_randomization", "0"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp_par5_round_robin(self):
        args = [
            "--run_iss",
            "--repeat_times",
            "2",
            "--num_cpus",
            "2",
            "--parallel_scheduling_mode",
            "round_robin",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class Mp5pSemaphoreTests(BaseRiescuedTest):
    """
    Combined tests for mp_5p_semaphore
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/mp_5p_semaphore.s"
        super().setUp()

    def test_mp5_semaphore(self):
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--repeat_times",
            "2",
            "--pbmt_ncio_randomization",
            "0",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp5_lr_sc(self):
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--repeat_times",
            "2",
            "--pbmt_ncio_randomization",
            "0",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
