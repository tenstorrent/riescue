# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import tempfile
import json

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class CpuconfigTests(BaseRiescuedTest):
    """
    Tests for cpuconfig
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test.s"
        super().setUp()

    def test_alt_memmap(self):
        args = [
            "--run_iss",
            "--cpuconfig",
        ]
        ram_zero_memamp = {"mmap": {"ram": {"start": "0x0000000", "size": "0x400000"}, "io": {"start": "0x80000", "size": "0x1FFFFFF80000"}}}
        with tempfile.NamedTemporaryFile() as f:
            with open(f.name, "w") as f:
                json.dump(ram_zero_memamp, f)
            args.append(f.name)
            self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
