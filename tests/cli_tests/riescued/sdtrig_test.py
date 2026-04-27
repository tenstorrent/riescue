# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class SdtrigTests(BaseRiescuedTest):
    """
    Gate the ``riescue/dtest_framework/tests/sdtrig/*.s`` test suite in the
    pre-merge pytest/bazel check.

    These files used to only be exercised by the nightly qual regressions
    (``quals/quals_list_block_7.txt``). Moving them into CLI unittests
    catches sdtrig regressions at MR time instead of the morning after.

    Every test runs through Whisper (the only ISS in-tree that implements
    sdtrig) with ``ext_sdtrig.enable`` coming from each test's
    ``;#test.features`` header, so no extra CLI features flag is needed.
    """

    iss_args = ["--run_iss", "--iss", "whisper"]

    def test_sdtrig_basic(self):
        testname = "dtest_framework/tests/sdtrig/sdtrig_basic.s"
        self.run_riescued(testname=testname, cli_args=self.iss_args, iterations=self.iterations)

    def test_sdtrig_load_store(self):
        testname = "dtest_framework/tests/sdtrig/sdtrig_load_store.s"
        self.run_riescued(testname=testname, cli_args=self.iss_args, iterations=self.iterations)

    def test_sdtrig_edge_cases(self):
        testname = "dtest_framework/tests/sdtrig/sdtrig_edge_cases.s"
        self.run_riescued(testname=testname, cli_args=self.iss_args, iterations=self.iterations)

    def test_sdtrig_privilege(self):
        testname = "dtest_framework/tests/sdtrig/sdtrig_privilege.s"
        self.run_riescued(testname=testname, cli_args=self.iss_args, iterations=self.iterations)

    def test_sdtrig_reexecute(self):
        testname = "dtest_framework/tests/sdtrig/sdtrig_reexecute.s"
        self.run_riescued(testname=testname, cli_args=self.iss_args, iterations=self.iterations)

    def test_sdtrig_reexecute_hooked(self):
        testname = "dtest_framework/tests/sdtrig/sdtrig_reexecute_hooked.s"
        args = self.iss_args + ["--excp_hooks"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
