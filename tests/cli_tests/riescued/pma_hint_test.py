# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class PmaHintTest(BaseRiescuedTest):
    """
    Tests for PMA hint functionality
    """

    def setUp(self):
        self.testname = "riescue/dtest_framework/tests/test_pma_hint.s"
        super().setUp()

    def test_pma_hint_basic(self):
        "Test PMA hint directive functionality with basic configuration"
        cli_args = ["--run_iss", "--needs_pma"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_pma_hint_with_cpuconfig(self):
        "Test PMA hint with custom CPU config"
        cli_args = ["--run_iss", "--needs_pma", "--cpuconfig", "riescue/dtest_framework/tests/cpu_config_pma.json"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_pma_hint_multiple_seeds(self):
        "Test PMA hint with multiple seeds to verify randomization"
        cli_args = ["--run_iss", "--needs_pma"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
