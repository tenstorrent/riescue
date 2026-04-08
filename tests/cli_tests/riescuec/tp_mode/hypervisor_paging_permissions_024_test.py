# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class HypervisorPagingPermissions024Test(BaseRiescueCTest):
    "Runs hypervisor_paging_permissions_024 test plan"

    def test_gstage_only(self):
        for g_paging_mode in ("sv39", "sv48", "sv57"):
            for priv_mode in ("super", "user"):
                self.run_tp_mode(
                    plan="hypervisor_paging_permissions_024",
                    cli_args=["--test_paging_mode", "disable", "--test_paging_g_mode", g_paging_mode, "--test_priv_mode", priv_mode, "--test_env", "virtualized", "--repeat_times", "1"],
                )

    def test_vstage_only(self):
        for paging_mode in ("sv39", "sv48", "sv57"):
            for priv_mode in ("super", "user"):
                self.run_tp_mode(
                    plan="hypervisor_paging_permissions_024",
                    cli_args=["--test_paging_mode", paging_mode, "--test_paging_g_mode", "disable", "--test_priv_mode", priv_mode, "--test_env", "virtualized", "--repeat_times", "1"],
                )

    def test_two_stage(self):
        # We don't test all combinations of paging modes to save time

        for paging_mode in ("sv39", "sv48", "sv57"):
            for priv_mode in ("super", "user"):
                self.run_tp_mode(
                    plan="hypervisor_paging_permissions_024",
                    cli_args=["--test_paging_mode", paging_mode, "--test_paging_g_mode", "sv39", "--test_priv_mode", priv_mode, "--test_env", "virtualized", "--repeat_times", "1"],
                )

        for g_paging_mode in ("sv39", "sv48", "sv57"):
            for priv_mode in ("super", "user"):
                self.run_tp_mode(
                    plan="hypervisor_paging_permissions_024",
                    cli_args=["--test_paging_mode", "sv39", "--test_paging_g_mode", g_paging_mode, "--test_priv_mode", priv_mode, "--test_env", "virtualized", "--repeat_times", "1"],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
