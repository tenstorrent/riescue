# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest
from riescue.lib.toolchain.exceptions import ToolFailureType


# htif_rvcp_fail_test.s calls ;#test_failed() unconditionally — the generated binary always
# writes tohost=3. Run with --run_iss to verify the TOHOST_FAIL path; run without --run_iss
# (elaborate-only) to inspect the generated assembly without executing the test.
_FAIL_TEST = "dtest_framework/tests/htif_rvcp_fail_test.s"
_PASS_TEST = "dtest_framework/tests/test.s"

# Patterns emitted by assembly_writer.py when HTIF RVCP injection is active.
_HTIF_CALL_PATTERN = "jal ra, htif_rvcp_print"
_HTIF_SUBROUTINE_PATTERN = "htif_rvcp_print:"
_RVCP_STRING_PATTERN = "RVCP: Test File"


def _read_generated_assembly(result) -> str:
    """Return the full text of the generated .S file for a RiescueD result."""
    asm_path = result.generated_files.assembly
    with open(asm_path, "r") as f:
        return f.read()


def _read_generated_eot_inc(result) -> str:
    """Return the text of the generated *_eot.inc file for a RiescueD result."""
    asm_path = result.generated_files.assembly
    eot_inc = asm_path.parent / f"{asm_path.stem}_eot.inc"
    with open(eot_inc, "r") as f:
        return f.read()


class HtifRvcpAssemblyInjectionTests(BaseRiescuedTest):
    """Verify that --eot_print_htif_console / --print_rvcp_passes inject HTIF call sites and
    the htif_rvcp_print subroutine into the generated assembly, without running the ISS."""

    def setUp(self):
        super().setUp()

    def _compile_only(self, testname: str, extra_args: list) -> list:
        """Generate + compile without running the ISS (elaborate_only=False skipped via no --run_iss)."""
        return self.run_riescued(testname=testname, cli_args=extra_args, iterations=1)

    def test_eot_print_htif_console_injects_call_site(self):
        """--eot_print_htif_console must inject 'jal ra, htif_rvcp_print' at ;#test_failed() site."""
        results = self._compile_only(_FAIL_TEST, ["--eot_print_htif_console"])
        asm = _read_generated_assembly(results[0])
        self.assertIn(_HTIF_CALL_PATTERN, asm, "Expected 'jal ra, htif_rvcp_print' in generated assembly")

    def test_eot_print_htif_console_emits_subroutine(self):
        """--eot_print_htif_console must emit the htif_rvcp_print subroutine in *_eot.inc."""
        results = self._compile_only(_FAIL_TEST, ["--eot_print_htif_console"])
        eot = _read_generated_eot_inc(results[0])
        self.assertIn(_HTIF_SUBROUTINE_PATTERN, eot, "Expected htif_rvcp_print label in *_eot.inc")

    def test_eot_print_htif_console_embeds_rvcp_string(self):
        """--eot_print_htif_console must embed an 'RVCP: Test File' .asciz string in the assembly."""
        results = self._compile_only(_FAIL_TEST, ["--eot_print_htif_console"])
        asm = _read_generated_assembly(results[0])
        self.assertIn(_RVCP_STRING_PATTERN, asm, "Expected RVCP string literal in generated assembly")

    def test_eot_print_htif_console_includes_discrete_name(self):
        """The injected RVCP string must contain the discrete test name 'htif_rvcp_fail'."""
        results = self._compile_only(_FAIL_TEST, ["--eot_print_htif_console"])
        asm = _read_generated_assembly(results[0])
        self.assertIn("htif_rvcp_fail", asm, "Expected discrete test name 'htif_rvcp_fail' in RVCP string")

    def test_print_rvcp_passes_injects_call_site(self):
        """--print_rvcp_passes must inject 'jal ra, htif_rvcp_print' at ;#test_passed() sites."""
        results = self._compile_only(_PASS_TEST, ["--print_rvcp_passes"])
        asm = _read_generated_assembly(results[0])
        self.assertIn(_HTIF_CALL_PATTERN, asm, "Expected 'jal ra, htif_rvcp_print' in generated assembly")

    def test_print_rvcp_passes_all_passed_string_in_eot(self):
        """--print_rvcp_passes must embed 'ALL PASSED' string in *_eot.inc."""
        results = self._compile_only(_PASS_TEST, ["--print_rvcp_passes"])
        eot = _read_generated_eot_inc(results[0])
        self.assertIn("ALL PASSED", eot, "Expected 'ALL PASSED' .asciz string in *_eot.inc")

    def test_no_flags_no_htif_injection(self):
        """Without HTIF flags, no htif_rvcp_print call sites should appear in the assembly."""
        results = self._compile_only(_PASS_TEST, [])
        asm = _read_generated_assembly(results[0])
        self.assertNotIn(_HTIF_CALL_PATTERN, asm, "htif_rvcp_print call site should not appear without flags")
        eot = _read_generated_eot_inc(results[0])
        self.assertNotIn(_HTIF_SUBROUTINE_PATTERN, eot, "htif_rvcp_print subroutine should not appear without flags")


class HtifRvcpFailTestIssTests(BaseRiescuedTest):
    """Run htif_rvcp_fail_test.s through the ISS. The test always calls ;#test_failed() so we
    expect TOHOST_FAIL — but we verify the binary compiles and the ISS runs to the eot path."""

    def setUp(self):
        self.testname = _FAIL_TEST
        super().setUp()

    def test_eot_print_htif_console_compiles_and_fails(self):
        """Binary must link and the ISS must reach the tohost-fail path (code 3)."""
        args = ["--run_iss", "--eot_print_htif_console"]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=1):
            self.assertEqual(failure.fail_code, 0x3, "Expected tohost fail code 0x3")

    def test_both_flags_compile_and_fail(self):
        """Both flags together: binary must link and ISS must reach tohost-fail."""
        args = ["--run_iss", "--eot_print_htif_console", "--print_rvcp_passes"]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=1):
            self.assertEqual(failure.fail_code, 0x3, "Expected tohost fail code 0x3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
