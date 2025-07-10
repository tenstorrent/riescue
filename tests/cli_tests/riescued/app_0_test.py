# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class App0Test(BaseRiescuedTest):
    def test_app_linux_mode(self):
        args = ["--linux_mode", "--run_iss", "--iss", "spike", "-rt", "-1", "-smi", "30000"]
        self.testname = "dtest_framework/tests/app_test.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
