# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class Test_SteeTests(BaseRiescuedTest):
    """
    Combined tests for test_stee
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_stee.s"
        super().setUp()

    def test_stee_secure_config(self):
        args = [
            "--run_iss",
            "--cpuconfig=riescue/dtest_framework/lib/config_secure_0.json",
            "--whisper_config_json=dtest_framework/lib/whisper_secure_config.json",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_stee_secure_config_4kb_pages(self):
        args = [
            "--run_iss",
            "--all_4kb_pages",
            "--cpuconfig=riescue/dtest_framework/lib/config_secure_0.json",
            "--whisper_config_json=dtest_framework/lib/whisper_secure_config.json",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
