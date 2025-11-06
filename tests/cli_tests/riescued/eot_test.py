# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest
from riescue.lib.toolchain.exceptions import ToolFailureType


class EotTests(BaseRiescuedTest):
    """
    Tests that check End Of Test (EOT) mechanism
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test.s"
        super().setUp()

    def test_cli_pass_value(self):
        "Tests that pass value is being ovewritten. This will appear as a failure to whisper - check that fail_code is the passed code"
        fail_code = 0xACED
        args = ["--run_iss", "--eot_pass_value", str(fail_code)]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=self.iterations):
            self.assertEqual(failure.fail_code, fail_code)

    def test_cli_pass_value_unchanged(self):
        "Test that uses standard EOT pass value with eot_pass overwritten. Check that test writes 0x1 on success"
        args = ["--run_iss", "--eot_pass_value", "0x1"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_cli(self):
        args = ["--run_iss", "--tohost", "0x60000000"]
        testname = "dtest_framework/tests/test_alt_htif.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


class FailTests(BaseRiescuedTest):
    "Tests that target fail_test.s"

    def setUp(self):
        self.testname = "dtest_framework/tests/fail_test.s"
        super().setUp()

    def test_cli_fail(self):
        "Test that fails because fail_test.s is designed to fail"
        args = ["--run_iss"]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=self.iterations):
            self.assertEqual(failure.fail_code, 0x3)

    def test_cli_fail_value_unchanged(self):
        "Test that uses standard EOT fail value with eot_fail overritten. FIXME: Check that test writes 0x3 on failure"
        args = ["--run_iss", "--eot_fail_value", "0x3"]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=self.iterations):
            self.assertEqual(failure.fail_code, 0x3)

    def test_cli_fail_value(self):
        "Test that uses non-standard EOT fail value. FIXME: Check that test writes 0xACED on failure"
        fail_code = 0xACED
        args = ["--run_iss", "--eot_fail_value", str(fail_code)]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=self.iterations):
            self.assertEqual(failure.fail_code, fail_code)

    def test_cli_pass(self):
        "Test that uses non-standard EOT pass value. Will appear as a pass FIXME: Check that test writes 0x1 on pass"
        args = ["--run_iss", "--eot_fail_value", "0x1"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_cli_pass_mp_parallel(self):
        "Test that uses non-standard EOT pass value. Will appear as a pass"
        default_args = ["--run_iss", "--eot_fail_value", "0x1", "--num_cpus", "2", "--mp_mode", "parallel"]
        for deleg in ["machine", "super"]:
            for test_priv in ["machine", "super", "user"]:
                args = default_args + ["--test_priv_mode", test_priv, "--deleg_excp_to", deleg]
                self.run_riescued(testname=self.testname, cli_args=args, iterations=1)

    def test_cli_fail_dead(self):
        "Test that uses non-standard EOT fail value with eot_fail overwritten. FIXME: Check that test writes 0xDEAD on failure"
        fail_code = 0xDEAD
        args = ["--run_iss", "--eot_fail_value", str(fail_code)]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=self.iterations):
            self.assertEqual(failure.fail_code, fail_code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
