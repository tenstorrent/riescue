# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class HypervisorPagingCsrAdDBitTest(BaseRiescueCTest):
    "Runs hypervisor_paging_d_bit test plan (D-bit update scenarios)"

    def test_cli_two_stage(self):
        for paging_mode in ("sv39", "sv48", "sv57"):
            for g_paging_mode in ("sv39", "sv48", "sv57"):
                for priv_mode in ("super", "user"):
                    self.run_tp_mode(
                        plan="hypervisor_paging_d_bit",
                        cli_args=["--test_paging_mode", paging_mode, "--test_paging_g_mode", g_paging_mode, "--test_priv_mode", priv_mode, "--test_env", "virtualized", "--repeat_times", "1"],
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
