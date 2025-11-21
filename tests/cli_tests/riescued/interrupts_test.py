# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest
from riescue.lib.toolchain.exceptions import ToolFailureType


class Interrupt_EnabledTests(BaseRiescuedTest):
    """
    Combined tests for interrupt_enabled
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/interrupt_enabled_test.s"
        super().setUp()

    def test_cli(self):
        args = ["--run_iss", "--user_interrupt_table"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_cli_1(self):
        # Test fails because --interrupts_disabled set and test expects interrupts to be enabeld
        args = ["--run_iss", "--user_interrupt_table", "--interrupts_disabled"]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=self.iterations):
            self.assertEqual(failure.fail_code, 0x3)

    def test_cli_2(self):
        testname = "dtest_framework/tests/non_instr_tests/interrupts_M.s"
        args = ["--run_iss"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_cli_3(self):
        testname = "dtest_framework/tests/non_instr_tests/interrupts_S_delegate_S.s"
        args = ["--run_iss", "--disable_wfi_wait"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_user_interrupt_table(self):
        args = ["--run_iss", "--user_interrupt_table"]
        testname = "dtest_framework/tests/user_interrupt_table.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_vectored_interrupt(self):
        args = ["--run_iss"]
        testname = "dtest_framework/tests/test_interrupts.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
