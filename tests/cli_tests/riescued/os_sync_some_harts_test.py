# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class OsSyncSomeHartsTests(BaseRiescuedTest):
    """
    Directed tests for the OS_SYNC_SOME_HARTS macro.

    The test programs 8 harts (simultaneous MP) but only harts with mhartid 0 and 1
    participate in the subset barrier (num_harts=2 < num_cpus=8). Hart 0 enters the
    barrier directly, hart 1 enters after a short wait loop, and harts 2..7 run a long
    wait loop and never call the macro. The run passes only if the 2-hart subset barrier
    rendezvouses correctly (a broken subset barrier would time out into os_failed).
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/os_sync_some_harts.s"
        super().setUp()

    def test_os_sync_some_harts_spike(self):
        args = [
            "--run_iss",
            "--iss",
            "spike",
            "--deleg_excp_to",
            "machine",
            "--num_cpus",
            "8",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_os_sync_some_harts_whisper(self):
        args = [
            "--run_iss",
            "--iss",
            "whisper",
            "--deleg_excp_to",
            "machine",
            "--num_cpus",
            "8",
            "--pbmt_ncio_randomization",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
