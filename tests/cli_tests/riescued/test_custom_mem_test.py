# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class TestCustomMem(BaseRiescuedTest):
    """Runs test_custom_mem.s with the dedicated cpuconfig (mmap.custom probe regions)."""

    def setUp(self):
        self.testname = "dtest_framework/tests/test_custom_mem.s"
        super().setUp()

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            "dtest_framework/lib/test_custom_mem_cpuconfig.json",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
