# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue import RiescueD

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest

DIRECTED = "riscv-coretp/coretp/plans/hypervisor_exceptions/directed_tests"
LANDINGS = f"{DIRECTED}/sret_v0_sets_v_to_spv.s"
STATUS = f"{DIRECTED}/sret_v0_priv_and_status_update.s"


class HypervisorSretTests(BaseRiescuedTest):
    """
    CLI tests for the two hand-written H-extension SRET dtests.

    They cover two rules from the hypervisor_exceptions test plan that its generated
    scenarios do not reach, because both are stated for V=0 and a generated scenario runs
    inside the guest.

    Both fixtures run under ``--test_env virtualized`` and leave the guest for HS with
    syscall ``0xf0001002``, which is the only way a RiescueD test reaches V=0 while still
    having a guest to return to. Every check is inside the fixture -- a wrong landing mode
    raises the wrong exception, or none -- so a passing run is already the result. These
    tests additionally assert that each landing pad *executed*, since a fixture whose
    excursion silently did not happen would pass by checking nothing.
    """

    def assert_reached(self, runs: list[RiescueD], labels: tuple, forbidden: tuple = ()):
        """Assert every label in ``labels`` executed and none in ``forbidden`` did."""
        for run in runs:
            executed = set(self.get_all_executed_subrountines(run))
            self.assertEqual([label for label in labels if label not in executed], [], f"fixture did not reach: {sorted(set(labels) - executed)}")
            self.assertEqual([label for label in forbidden if label in executed], [], "fixture reached a failure path")

    # ; every landing pad, plus the label the exception check resumes at
    LANDING_LABELS = ("hv_pad_vs", "hv_vs_probe", "hv_vs_done", "hv_pad_vu", "hv_vu_done", "hv_pad_hs", "hv_pad_u", "hv_u_done")

    def run_landings(self, cli_args: list) -> list[RiescueD]:
        args = ["--run_iss", "--disassemble_test", "--test_env", "virtualized"] + cli_args
        runs = self.run_riescued(testname=LANDINGS, cli_args=args, iterations=self.iterations)
        self.assert_reached(runs, self.LANDING_LABELS)
        return runs

    def run_status(self, cli_args: list) -> list[RiescueD]:
        args = ["--run_iss", "--disassemble_test", "--test_env", "virtualized"] + cli_args
        runs = self.run_riescued(testname=STATUS, cli_args=args, iterations=self.iterations)
        self.assert_reached(runs, ("hv_status_spie1", "hv_status_spie0"), forbidden=("hv_fail",))
        return runs

    def test_sret_landings(self):
        "VS-mode test body; both paging stages come from the seed."
        self.run_landings(["--test_priv_mode", "super"])

    def test_sret_landings_vu_body(self):
        "VU-mode test body, so the excursion starts and ends at V=1 user privilege."
        self.run_landings(["--test_priv_mode", "user"])

    def test_sret_landings_no_paging(self):
        "Both stages Bare: the landing pads are addressed by PA and must still be reachable."
        self.run_landings(["--test_priv_mode", "super", "--test_paging_mode", "disable", "--test_paging_g_mode", "disable"])

    def test_sret_landings_mixed_stages(self):
        "A VS stage wider than the G stage, so the two walks cannot be confused for one."
        self.run_landings(["--test_priv_mode", "super", "--test_paging_mode", "sv57", "--test_paging_g_mode", "sv39"])

    def test_sret_status_update(self):
        "SPV=0, SPP=0, SIE=SPIE, SPIE=1, read back at HS right after the SRET."
        self.run_status(["--test_priv_mode", "super"])

    def test_sret_status_update_vu_body(self):
        self.run_status(["--test_priv_mode", "user"])

    def test_sret_status_update_no_paging(self):
        self.run_status(["--test_priv_mode", "super", "--test_paging_mode", "disable", "--test_paging_g_mode", "disable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
