# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class Litmus_ExampleTests(BaseRiescuedTest):
    """
    Combined tests for litmus_example
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/litmus_example.s"
        super().setUp()

    def test_litmus_super(self):
        args = [
            "--test_env",
            "virtualized",
            "--test_priv_mode",
            "super",
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

    def test_litmus(self):
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
