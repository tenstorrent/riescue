# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


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
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)
        self.assertTrue(Path(self.test_dir / "file.hex").exists(), "Whisper dump file not created")


if __name__ == "__main__":
    unittest.main(verbosity=2)
