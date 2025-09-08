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
            "dtest_framework/lib/basic_config.json",
            "--whisper_config",
            "dtest_framework/lib/whisper_basic_config.json",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
