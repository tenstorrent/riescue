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
        self.testname = "dtest_framework/tests/test_interrupts.s"
        super().setUp()

    def test_cli_2(self):
        testname = "dtest_framework/tests/non_instr_tests/interrupts_M.s"
        args = ["--run_iss", "--deleg_excp_to=machine"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_cli_3(self):
        testname = "dtest_framework/tests/non_instr_tests/interrupts_S_delegate_S.s"
        args = ["--run_iss", "--deleg_excp_to=super"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_vectored_interrupt(self):
        args = ["--run_iss", "--deleg_excp_to=machine"]
        testname = "dtest_framework/tests/test_interrupts.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_custom_handler_dispatch(self):
        """Verify per-segment custom handler dispatch: two discrete_tests on the same vector
        each install a different handler via PROLOGUE/EPILOGUE and confirm the pointer switch
        isolates each handler to its own segment."""
        testname = "dtest_framework/tests/non_instr_tests/custom_handler_dispatch.s"
        args = ["--run_iss"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
