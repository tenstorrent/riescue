# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class Mp5CoreTests(BaseRiescuedTest):
    """
    Combined tests for mp_5p
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/mp_5p.s"
        super().setUp()

    def test_mp5_spike_machine(self):
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--deleg_excp_to",
            "machine",
            "--repeat_times",
            "2",
            "--num_cpus",
            "10",
            "--pbmt_ncio_randomization",
            "0",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp5_spike_machine_priv(self):
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

    def test_mp5_spike_user_priv(self):
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
            "--pbmt_ncio_randomization",
            "0",
            "--disable_wfi_wait",  # RVTOOLS-4204
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_mp5_whisper_super_priv(self):
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

    def test_mp5_whisper_machine_priv(self):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
