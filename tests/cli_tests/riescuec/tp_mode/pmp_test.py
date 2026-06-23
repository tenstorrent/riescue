# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class PmpTest(BaseRiescueCTest):
    """Runs PMP (Physical Memory Protection) test plan.

    Tests PMP CSR access, WARL checks, memory access permissions, address matching,
    region crossing, AMO operations, misaligned access, and hypervisor scenarios.

    The testplan contains scenarios for multiple privilege modes:
    - M-mode scenarios: Test CSR access, WARL checks, locked bit behavior
    - S-mode scenarios: Test PMP enforcement for supervisor-level access
    - Virtualized scenarios: Test PMP behavior with two-stage translation

    Each test method runs with a specific privilege mode, and only scenarios
    with compatible TestEnvCfg will be selected for that run.
    """

    def test_cli_m_mode(self):
        """Run PMP scenarios that require M-mode."""
        self.run_tp_mode(
            plan="pmp",
            cli_args=[
                "--test_priv_mode",
                "machine",
                "--whisper_config_json",
                "dtest_framework/lib/whisper_pmp_config.json",
            ],
        )

    def test_cli_s_mode(self):
        """Run PMP scenarios that require S-mode (bare metal)."""
        self.run_tp_mode(
            plan="pmp",
            cli_args=[
                "--test_priv_mode",
                "super",
                "--test_paging_mode",
                "disable",
                "--setup_pmp",
                "--whisper_config_json",
                "dtest_framework/lib/whisper_pmp_config.json",
            ],
        )

    def test_cli_s_virtualized(self):
        """Run PMP scenarios that require virtualized S-mode (hypervisor)."""
        self.run_tp_mode(
            plan="pmp",
            cli_args=[
                "--test_priv_mode",
                "super",
                "--test_env",
                "virtualized",
                "--test_paging_mode",
                "disable",
                "--test_paging_g_mode",
                "disable",
                "--setup_pmp",
                "--whisper_config_json",
                "dtest_framework/lib/whisper_pmp_config.json",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
