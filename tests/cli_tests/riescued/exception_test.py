# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue import RiescueD

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest

DEFAULT_EXCP_HANDLER_OVERRIDE_CONF = Path(__file__).resolve().parents[3] / "riescue/dtest_framework/tests/non_instr_tests/default_excp_handler_override_conf.py"
DEFAULT_COMBINED_HANDLER_OVERRIDE_CONF = Path(__file__).resolve().parents[3] / "riescue/dtest_framework/tests/non_instr_tests/default_combined_handler_override_conf.py"


class Test_ExcpTests(BaseRiescuedTest):
    """
    Combined tests for test_excp
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_excp.s"
        super().setUp()

    def excp_checks(self, runs: list[RiescueD]):
        "Additional checks for excp tests. Check that tval was checked with expected value"
        for run in runs:
            all_subroutines = self.get_all_executed_subrountines(run)
            tval_subroutines = [subroutine for subroutine in all_subroutines if "nonzero_tval_check" in subroutine]
            self.assertGreater(len(tval_subroutines), 0, f"Expected at least 1 tval check subroutine, got {len(tval_subroutines)} routines that ended with 'nonzero_tval_check'")

    def htval_checks(self, runs: list[RiescueD]):
        "Check that htval was checked with expected value"
        for run in runs:
            all_subroutines = self.get_all_executed_subrountines(run)
            htval_subroutines = [subroutine for subroutine in all_subroutines if "nonzero_htval_check" in subroutine]
            self.assertGreater(len(htval_subroutines), 0, f"Expected at least 1 htval check subroutine, got {len(htval_subroutines)} routines that ended with 'nonzero_htval_check'")

    def test_excp_with_cpuconfig(self):
        args = ["--run_iss", "--cpuconfig", "dtest_framework/tests/cpu_config.json"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)

    def test_excp_virtualized_env_hs_deleg(self):
        args = ["--run_iss", "--test_env", "virtualized", "--hedeleg=0x0"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)
        self.htval_checks(runs)

    def test_excp_virtualized_env(self):
        args = ["--run_iss", "--test_env", "virtualized"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)

    def test_excp_basic(self):
        args = ["--run_iss"]
        self.testname = "dtest_framework/tests/test_excp.s"
        runs = self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
        self.excp_checks(runs)

    def test_excp_pbmt_ncio(self):
        args = ["--run_iss", "--pbmt_ncio_randomization", "100"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_excp_hooks(self):
        args = ["--run_iss", "--excp_hooks"]
        self.testname = "dtest_framework/tests/test_excp.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_aplic_intr(self):
        args = [
            "--run_iss",
            "--iss",
            "whisper",
            "--c_used",
            "--cpuconfig",
            "dtest_framework/tests/cpu_config_aplic_imsic.json",
            "--whisper_config_json",
            "dtest_framework/lib/whisper_aplic_config.json",
        ]
        self.testname = "dtest_framework/tests/test_aplic_intr.s"
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class DefaultExceptionHandlerOverrideTests(BaseRiescuedTest):
    """
    CLI tests for ``FeatMgr.register_default_exception_handler()`` overrides.

    Covers the test-wide synchronous exception handler override API: M-mode and
    S-mode delegation paths, dispatch under vectored ``mtvec`` mode, and
    composition with ``register_default_handler()`` (interrupt overrides) in
    the same :class:`~riescue.dtest_framework.config.conf.Conf`.
    """

    def test_default_excp_handler_override(self):
        """Verify register_default_exception_handler() routes ILLEGAL_INSTRUCTION (cause 2)
        to a conf-registered handler in M-mode (medeleg bit 2 clear).

        The conf file registers my_illegal_handler, which writes 0xCAFE to test_marker
        and advances mepc by 4 so execution resumes after the faulting .word 0x0.
        The test fires the illegal instruction and checks the marker."""
        testname = "dtest_framework/tests/non_instr_tests/default_excp_handler_override.s"
        args = ["--run_iss", "--deleg_excp_to=machine", f"--conf={DEFAULT_EXCP_HANDLER_OVERRIDE_CONF}"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_default_excp_handler_override_s(self):
        """Verify register_default_exception_handler() with S-mode delegation path.

        Same conf (my_illegal_handler) registered for cause 2, but run with
        --deleg_excp_to=super so medeleg bit 2 is set and the handler is emitted in the
        S-mode TrapHandler with SUPERVISOR_CTX (sepc/sret).  Confirms the single
        TrapContext-aware callable works unchanged across delegation modes."""
        testname = "dtest_framework/tests/non_instr_tests/default_excp_handler_override_s.s"
        args = ["--run_iss", "--deleg_excp_to=super", f"--conf={DEFAULT_EXCP_HANDLER_OVERRIDE_CONF}"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_default_excp_handler_override_vectored_mode(self):
        """Verify register_default_exception_handler() works when mtvec is in vectored MODE.

        RISC-V spec: exceptions always dispatch to mtvec BASE regardless of MODE.
        Only interrupts use BASE + 4*cause in vectored mode.  This test sets
        SET_VECTORED_INTERRUPTS before triggering the illegal instruction to prove
        the dispatch at exception_path runs identically in both modes."""
        testname = "dtest_framework/tests/non_instr_tests/default_excp_handler_override.s"
        args = ["--run_iss", "--deleg_excp_to=machine", f"--conf={DEFAULT_EXCP_HANDLER_OVERRIDE_CONF}", "-teq", "USE_VECTORED_MODE=1"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_default_combined_handler_override(self):
        """Verify register_default_handler() and register_default_exception_handler() compose.

        Single conf file registers both a SSI interrupt handler (vec 1) and an
        ILLEGAL_INSTRUCTION exception handler (cause 2) in one add_hooks() call.
        The directed test triggers both and checks that each handler wrote its
        own marker, proving the two override mechanisms coexist without
        interfering."""
        testname = "dtest_framework/tests/non_instr_tests/default_combined_handler_override.s"
        args = ["--run_iss", "--deleg_excp_to=machine", f"--conf={DEFAULT_COMBINED_HANDLER_OVERRIDE_CONF}"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


class Skip_InstrTests(BaseRiescuedTest):
    """
    Combined tests for skip_instr
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/skip_instr.s"
        super().setUp()

    def test_skip_instr_basic(self):
        args = [
            "--run_iss",
            "--skip_instruction_for_unexpected",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=1)  # FIXME: Fails on seed 2+

    def test_skip_instr_parallel(self):
        args = [
            "--run_iss",
            "--skip_instruction_for_unexpected",
            "--num_cpus",
            "2",
            "--mp_mode",
            "parallel",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=1)  # FIXME: Fails on seed 2+


if __name__ == "__main__":
    unittest.main(verbosity=2)
