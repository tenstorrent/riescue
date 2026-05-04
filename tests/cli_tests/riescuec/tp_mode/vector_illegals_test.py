# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
"""
Unit tests for the vector_illegals test plan.

NOTE: These tests are skipped because the vector_illegals scenarios target
RTL functional coverage — the spec-illegal vtype/EMUL combinations (FP at
SEW=e8, widening EEW>ELEN, etc.) are not modelled as ILLEGAL_INSTRUCTION
traps by whisper (the ISS). The OS_SETUP_CHECK_EXCP handler expects a trap
that whisper never raises, causing test_failed(). The scenarios are only
meaningful when run against RTL via the daily arch-coverage regression.
"""
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


@unittest.skip("vector_illegals scenarios require RTL; whisper does not model spec-illegal vtype traps")
class VectorIllegalsTest(BaseRiescueCTest):
    """Runs vector_illegals test plan.

    Tests spec-illegal vector instruction patterns in both bare-metal and
    virtualized modes. Each scenario emits instructions that trap as
    ILLEGAL_INSTRUCTION due to architectural vtype/EMUL constraints, wrapped
    with OS_SETUP_CHECK_EXCP so the test passes.
    """

    # =========================================================================
    # Bare-metal tests (V=0) — covers vector_instruction_coverage_all_vtype_combinations
    # =========================================================================

    def test_cli_bare_metal_machine(self):
        """Spec-illegal vector ops in M-mode (bare metal)."""
        self.run_tp_mode(
            plan="vector_illegals",
            cli_args=[
                "--test_priv_mode",
                "machine",
                "--test_env",
                "bare_metal",
                "--repeat_times",
                "1",
            ],
        )

    def test_cli_bare_metal_supervisor(self):
        """Spec-illegal vector ops in S-mode (bare metal)."""
        self.run_tp_mode(
            plan="vector_illegals",
            cli_args=[
                "--test_priv_mode",
                "super",
                "--test_env",
                "bare_metal",
                "--repeat_times",
                "1",
            ],
        )

    def test_cli_bare_metal_user(self):
        """Spec-illegal vector ops in U-mode (bare metal)."""
        self.run_tp_mode(
            plan="vector_illegals",
            cli_args=[
                "--test_priv_mode",
                "user",
                "--test_env",
                "bare_metal",
                "--repeat_times",
                "1",
            ],
        )

    # =========================================================================
    # Virtualized tests (V=1) — covers vec_x_hyp__cr_vtype_eff_priv_vec_fp/loads/stores
    # =========================================================================

    def test_cli_virtualized_vs_mode(self):
        """Spec-illegal vector ops in VS-mode — covers vector_x_hypervisor FP bins."""
        self.run_tp_mode(
            plan="vector_illegals",
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
        """Spec-illegal vector ops in VU-mode — covers vector_x_hypervisor load/store bins."""
        self.run_tp_mode(
            plan="vector_illegals",
            cli_args=[
                "--test_priv_mode",
                "user",
                "--test_env",
                "virtualized",
                "--repeat_times",
                "1",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
