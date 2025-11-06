# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class MpTests(BaseRiescuedTest):
    """
    Tests that use MP mode
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test.s"
        super().setUp()

    def test_mp_simultaneous_spike(self):
        "Default test with MP mode with spike"
        # RVTOOLS-4204
        cli_args = ["--run_iss", "--iss", "spike", "--mp", "on", "--mp_mode", "simultaneous", "--num_cpus", "3", "--disable_wfi_wait"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_mp_simultaneous_whisper(self):
        "Default test with MP mode with whisper"
        cli_args = ["--run_iss", "--iss", "whisper", "--mp", "on", "--mp_mode", "simultaneous", "--num_cpus", "3"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)


class Mp_2PTests(BaseRiescuedTest):
    """
    Combined tests for mp_2p
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        super().setUp()

    def test_mp2_whisper_machine_priv(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "whisper",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--test_priv_mode",
            "machine",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp2_spike_super_priv(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--test_priv_mode",
            "super",
            "--pbmt_ncio_randomization",
            "0",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp2_spike_user_priv(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--test_priv_mode",
            "user",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp2_spike_basic(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp2_whisper_user_priv(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "whisper",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--test_priv_mode",
            "user",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp2_whisper_basic(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "whisper",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp2_whisper_super_priv(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "whisper",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--test_priv_mode",
            "super",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp2_spike_machine_priv(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--test_priv_mode",
            "machine",
            "--pbmt_ncio_randomization",
            "0",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    # Whisper prevented max instructions from causing error,
    # Needs to be fixed RVTOOLS-4829
    # def test_mp2_with_c_whisper_private_maps(self):
    #     self.testname = "dtest_framework/tests/mp_with_c.s"
    #     args = [
    #         "--cpuconfig",
    #         "dtest_framework/tests/cpu_config_mp_with_c.json",
    #         "--private_maps",
    #         "--run_iss",
    #         "--iss",
    #         "whisper",
    #         "--deleg_excp_to",
    #         "machine",
    #         "--cfile",
    #         "dtest_framework/tests/c_func.c",
    #         "--c_used",
    #     ]
    #     self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
