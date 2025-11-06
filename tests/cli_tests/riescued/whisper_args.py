# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest
from riescue.lib.toolchain.whisper import ToolFailureType


class DefaultTest(BaseRiescuedTest):
    """
    Default tests
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test.s"
        super().setUp()

    def test_cli_whisper(self):
        "Default test with spike"
        cli_args = ["--run_iss", "--seed", "0", "--whisper_dumpmem=file.hex:@code:@code+0x2000"]
        riescued_results = self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)
        for rd in riescued_results:
            self.assertTrue((rd.run_dir / "file.hex").exists(), "Whisper dump file not created")

    def test_whisper_max_instr(self):
        "Test that Whisper raises an error if the instruction limit is reached"
        cli_args = ["--run_iss", "--seed", "0", "--whisper_max_instr=100"]
        fail_code = 0
        for failure in self.expect_toolchain_failure_generator(
            testname=self.testname,
            cli_args=cli_args,
            failure_kind=ToolFailureType.MAX_INSTRUCTION_LIMIT,
            iterations=self.iterations,
        ):
            self.assertEqual(failure.fail_code, fail_code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
