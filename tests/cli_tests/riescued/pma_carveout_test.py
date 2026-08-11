# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class PmaCarveoutTest(BaseRiescuedTest):
    """
    Tests for memory-map region tags, pma_randomization, and ;#test.user_programmable_pmacfg.

    Uses the worked example from the PMA user guide, so a break here means the documented
    example stopped working.
    """

    CPUCONFIG = "riescue/dtest_framework/tests/cpu_config_pma_carveout.json"

    def setUp(self):
        self.testname = "riescue/dtest_framework/tests/pma_carveout.s"
        super().setUp()

    def test_fixed_windows_with_randomization(self):
        "Fixed windows survive decoy randomization: equates resolve and the reserved entries stay free"
        cli_args = ["--run_iss", "--cpuconfig", self.CPUCONFIG, "--enable_pma_randomization"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_fixed_windows_without_randomization(self):
        "Same test with randomization off: fixed windows are emitted through the legacy path"
        cli_args = ["--run_iss", "--cpuconfig", self.CPUCONFIG, "--needs_pma"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_fixed_windows_with_mask_stress(self):
        "Carve-out mask stress at 100% must still leave the fixed windows unmasked"
        cli_args = ["--run_iss", "--cpuconfig", self.CPUCONFIG, "--enable_pma_randomization", "--pma_carveout_mask_pct", "100"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
