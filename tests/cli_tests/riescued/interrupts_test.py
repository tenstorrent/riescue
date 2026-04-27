# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest
from riescue.lib.toolchain.exceptions import ToolFailureType

DEFAULT_HANDLER_OVERRIDE_CONF = Path(__file__).resolve().parents[3] / "riescue/dtest_framework/tests/non_instr_tests/default_handler_override_conf.py"


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

    def test_default_handler_override(self):
        """Verify that Conf.add_hooks() / FeatMgr.register_default_handler() replaces the
        test-wide default handler for a given vector.  The conf file registers a custom
        SSI handler that writes 0xCAFE to test_marker; the test fires SSIP and checks the
        marker to confirm the override ran (M-mode path: vec 1 non-delegated, mip/mret).
        Uses vectored mtvec mode (MODE=1) — hardware dispatches to the vector table."""
        testname = "dtest_framework/tests/non_instr_tests/default_handler_override.s"
        args = ["--run_iss", f"--conf={DEFAULT_HANDLER_OVERRIDE_CONF}"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_default_handler_override_direct_mode(self):
        """Verify register_default_handler() dispatch in direct mtvec mode (MODE=0).

        Same test and conf as test_default_handler_override, but passes
        -teq USE_DIRECT_MODE=1 so the test keeps mtvec in direct mode.
        The trap handler's software mcause-based dispatch must route
        the SSI (cause 1) to the registered handler via the vector table,
        identically to hardware vectored mode."""
        testname = "dtest_framework/tests/non_instr_tests/default_handler_override.s"
        args = ["--run_iss", f"--conf={DEFAULT_HANDLER_OVERRIDE_CONF}", "-teq", "USE_DIRECT_MODE=1"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_default_handler_override_s(self):
        """Verify register_default_handler() with S-mode delegation (TrapContext path).

        The same conf handler (my_ssi_handler) is registered for vec 1, but the test
        uses ;#vector_delegation(1, supervisor) so the framework routes it to the S-mode
        TrapHandler and calls assembly_fn(SUPERVISOR_CTX).  The handler body therefore
        uses sip/sret.  Fires SSIP via csrsi sip, 2 and checks that 0xCAFE was written
        to test_marker to confirm the S-mode override ran."""
        testname = "dtest_framework/tests/non_instr_tests/default_handler_override_s.s"
        args = ["--run_iss", "--deleg_excp_to=super", f"--conf={DEFAULT_HANDLER_OVERRIDE_CONF}"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
