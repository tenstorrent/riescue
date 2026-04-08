# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class HypervisorExceptionsTest(BaseRiescueCTest):
    """Runs hypervisor_exceptions test plan.

    Tests hypervisor extension virtual instruction exceptions and trap behavior.
    """

    # =========================================================================
    # Virtualized mode tests (V=1)
    # =========================================================================

    def test_cli_virtualized_vs_mode(self):
        """Test virtualized VS-mode (super) scenarios."""
        self.run_tp_mode(
            plan="hypervisor_exceptions",
            cli_args=[
                "--test_priv_mode",
                "super",
                "--test_env",
                "virtualized",
                "--repeat_times",
                "1",
            ],
        )

    def test_cli_virtualized_vu_mode(self):
        """Test virtualized VU-mode (user) scenarios."""
        self.run_tp_mode(
            plan="hypervisor_exceptions",
            cli_args=[
                "--test_priv_mode",
                "user",
                "--test_env",
                "virtualized",
                "--repeat_times",
                "1",
            ],
        )

    # =========================================================================
    # Non-virtualized mode tests (V=0)
    # =========================================================================

    def test_cli_bare_metal_hu_mode(self):
        """Test non-virtualized HU-mode (user) scenarios."""
        self.run_tp_mode(
            plan="hypervisor_exceptions",
            cli_args=[
                "--test_priv_mode",
                "user",
                "--test_env",
                "bare_metal",
                "--repeat_times",
                "1",
            ],
        )

    def test_cli_bare_metal_hs_mode(self):
        """Test non-virtualized HS-mode (super) scenarios."""
        self.run_tp_mode(
            plan="hypervisor_exceptions",
            cli_args=[
                "--test_priv_mode",
                "super",
                "--test_env",
                "bare_metal",
                "--repeat_times",
                "1",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
