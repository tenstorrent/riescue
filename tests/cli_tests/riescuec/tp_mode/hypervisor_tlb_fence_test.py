# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class HypervisorTlbFenceTest(BaseRiescueCTest):
    """Runs hypervisor_tlb_fence test plan.

    Tests hypervisor TLB fence behavior: TLB entry hits with page faults and
    guest page faults after permission changes.
    """

    # =========================================================================
    # Virtualized mode tests (V=1)
    #
    # All hypervisor_tlb_fence scenarios require two-stage translation
    # (VS-stage and G-stage paging both enabled) in virtualized VS-mode, so
    # explicit paging modes must be supplied for the env solver to match.
    # =========================================================================

    def test_cli_virtualized_vs_mode(self):
        """Test virtualized VS-mode (super) scenarios across two-stage paging modes."""
        for paging_mode in ("sv39", "sv48", "sv57"):
            for g_paging_mode in ("sv39", "sv48", "sv57"):
                self.run_tp_mode(
                    plan="hypervisor_tlb_fence",
                    cli_args=[
                        "--test_paging_mode",
                        paging_mode,
                        "--test_paging_g_mode",
                        g_paging_mode,
                        "--test_priv_mode",
                        "super",
                        "--test_env",
                        "virtualized",
                        "--repeat_times",
                        "1",
                    ],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
