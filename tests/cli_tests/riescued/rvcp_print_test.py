# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue.lib.toolchain.exceptions import ToolFailureType

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class RvcpPrintTest(BaseRiescuedTest):
    """Verify RVCP pass/fail messages are printed via HTIF console.

    Tests the --print_rvcp_passed and --print_rvcp_failed flags.
    Format: "RVCP: Test File {testname} {discrete_test_name} PASSED/FAILED"

    Note: test_setup and test_cleanup are skipped - only discrete tests are printed.
    """

    def setUp(self):
        super().setUp()

    def _read_console_log(self, console_log: Path) -> str:
        """Read raw console log content (stdout+stderr merged)."""
        self.assertTrue(console_log.exists(), f"Console log not found at {console_log}")
        with open(console_log, "r") as f:
            return f.read()

    def _get_console_log_content(self, result) -> str:
        """Read the console log content from a RiescueD result, filtering to RVCP lines only."""
        elf = result.generated_files.elf
        console_log = elf.parent / (elf.name + "_whisper_stdout.log")
        raw = self._read_console_log(console_log)
        return self._filter_rvcp_lines(raw)

    def _get_console_log_for_failing_test(self, testname: str) -> str:
        """Read the console log for a failing test, filtering to RVCP lines only.

        For failing tests, expect_toolchain_failure uses test_dir directly (no seed subdirectory).
        The ELF name is derived from the test file's basename without extension.
        """
        elf_stem = Path(testname).stem
        console_log = self.test_dir / f"{elf_stem}_whisper_stdout.log"
        raw = self._read_console_log(console_log)
        return self._filter_rvcp_lines(raw)

    @staticmethod
    def _filter_rvcp_lines(content: str) -> str:
        """Filter console output to only RVCP lines (ignore Whisper stderr noise)."""
        lines = [line for line in content.splitlines() if line.startswith("RVCP:")]
        return "\n".join(lines) + "\n" if lines else ""

    # ==================== --print_rvcp_passed only ====================

    def test_print_rvcp_passed_only(self):
        """Test --print_rvcp_passed flag alone prints PASSED messages with correct format."""
        testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--print_rvcp_passed", "--seed", "0"]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        # Check for correct format: "RVCP: Test File test {test_name} PASSED"
        self.assertIn("RVCP: Test File test", log_content, "Missing RVCP prefix with test file name")
        self.assertIn("PASSED", log_content, "No PASSED status found")
        # Verify full message format for at least one discrete test
        self.assertRegex(log_content, r"RVCP: Test File test test\d+ PASSED", "Message format should be 'RVCP: Test File test <testname> PASSED'")
        # Should NOT contain FAILED since test passes
        self.assertNotIn("FAILED", log_content, "Should not have FAILED messages when test passes")
        # Should NOT contain test_setup or test_cleanup (they are skipped)
        self.assertNotIn("test_setup", log_content, "Should not print test_setup")
        self.assertNotIn("test_cleanup", log_content, "Should not print test_cleanup")

    def test_print_rvcp_passed_machine_mode(self):
        """Verify --print_rvcp_passed works in machine mode with correct format."""
        testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--print_rvcp_passed", "--test_priv_mode", "machine", "--seed", "0"]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        self.assertIn("RVCP: Test File test", log_content)
        self.assertIn("PASSED", log_content)
        self.assertRegex(log_content, r"RVCP: Test File test test\d+ PASSED")

    def test_print_rvcp_passed_has_all_discrete_tests(self):
        """Verify all discrete test names appear in RVCP output."""
        testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--print_rvcp_passed", "--seed", "0"]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        # Check that actual discrete test names appear
        self.assertNotIn("UNKNOWN", log_content, "Test name should not be UNKNOWN")
        # test.s has test01, test02, test03, test04 - check at least some appear
        found_tests = [f"test0{i}" for i in range(1, 5) if f"test0{i}" in log_content]
        self.assertGreater(len(found_tests), 0, f"Expected discrete test names in output, got: {log_content}")

    # ==================== --print_rvcp_failed only ====================

    def test_print_rvcp_failed_failing_test(self):
        """Test --print_rvcp_failed prints FAILED message with correct format."""
        testname = "dtest_framework/tests/htif_rvcp_fail_test.s"
        cli_args = ["--run_iss", "--print_rvcp_failed", "--seed", "0"]

        # This test intentionally fails
        failures = self.expect_toolchain_failure(testname=testname, cli_args=cli_args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=1)
        self.assertTrue(len(failures) > 0, "Expected at least one failure")

        # Verify FAILED message format in console log
        log_content = self._get_console_log_for_failing_test(testname)
        self.assertIn("RVCP: Test File htif_rvcp_fail_test", log_content, "Missing RVCP prefix with test file name")
        self.assertIn("FAILED", log_content, "No FAILED status found")
        # Verify full message format: "RVCP: Test File htif_rvcp_fail_test test01 FAILED"
        self.assertRegex(log_content, r"RVCP: Test File htif_rvcp_fail_test test01 FAILED", "Message format should be 'RVCP: Test File htif_rvcp_fail_test test01 FAILED'")
        # Should NOT contain PASSED since only --print_rvcp_failed is set
        self.assertNotIn("PASSED", log_content, "Should not have PASSED messages with only --print_rvcp_failed")
        # Should NOT contain test_setup or test_cleanup
        self.assertNotIn("test_setup", log_content, "Should not print test_setup")
        self.assertNotIn("test_cleanup", log_content, "Should not print test_cleanup")

    def test_print_rvcp_failed_no_pass_messages(self):
        """Verify --print_rvcp_failed alone does NOT print PASSED messages."""
        testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--print_rvcp_failed", "--seed", "0"]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        # Should NOT have any RVCP messages since test passes and only --print_rvcp_failed is set
        # (print_rvcp_failed only prints on FAIL)
        self.assertEqual(log_content, "", "Should have no output when test passes with only --print_rvcp_failed")

    # ==================== Both flags ====================

    def test_both_flags_passing_test(self):
        """Test both flags with a passing test - should see PASSED messages only."""
        testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--print_rvcp_passed", "--print_rvcp_failed", "--seed", "0"]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        self.assertIn("RVCP: Test File test", log_content)
        self.assertIn("PASSED", log_content)
        self.assertRegex(log_content, r"RVCP: Test File test test\d+ PASSED")
        # Should NOT contain FAILED since test passes
        self.assertNotIn("FAILED", log_content, "Should not have FAILED when test passes")

    def test_both_flags_failing_test(self):
        """Test both flags with a failing test - should see FAILED message."""
        testname = "dtest_framework/tests/htif_rvcp_fail_test.s"
        cli_args = ["--run_iss", "--print_rvcp_passed", "--print_rvcp_failed", "--seed", "0"]

        # This test has test_setup (passes, but skipped for RVCP) and test01 (fails)
        failures = self.expect_toolchain_failure(testname=testname, cli_args=cli_args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=1)
        self.assertTrue(len(failures) > 0, "Expected at least one failure")

        # Verify messages in console log
        log_content = self._get_console_log_for_failing_test(testname)
        self.assertIn("RVCP: Test File htif_rvcp_fail_test", log_content)
        # Should have FAILED for test01
        self.assertIn("FAILED", log_content, "Should have FAILED message for test01")
        self.assertRegex(log_content, r"RVCP: Test File htif_rvcp_fail_test test01 FAILED")
        # Should NOT contain test_setup or test_cleanup (they are skipped)
        self.assertNotIn("test_setup", log_content, "Should not print test_setup")
        self.assertNotIn("test_cleanup", log_content, "Should not print test_cleanup")

    # ==================== Neither flag ====================

    def test_no_flags_no_rvcp_output(self):
        """Verify no RVCP output when neither flag is set."""
        testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--seed", "0"]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        # Should have no RVCP output at all
        self.assertEqual(log_content, "", "Should have no RVCP output when no flags set")

    # ==================== Message format verification ====================

    def test_message_format_complete_passed(self):
        """Verify the complete PASSED message format is correct."""
        testname = "dtest_framework/tests/test.s"
        cli_args = ["--run_iss", "--print_rvcp_passed", "--seed", "0"]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        # Split into lines and verify format of each line
        lines = [line for line in log_content.strip().split("\n") if line]
        self.assertGreater(len(lines), 0, "Expected at least one RVCP message")

        for line in lines:
            # Each line should match: "RVCP: Test File test <testname> PASSED"
            self.assertRegex(line, r"^RVCP: Test File test \S+ PASSED$", f"Line does not match expected format: {line}")

    # ==================== --rvmodel_macros (UART) ====================

    def test_rvmodel_macros_uart_passed(self):
        """Verify --rvmodel_macros with UART macros prints PASSED messages."""
        testname = "dtest_framework/tests/test.s"
        cli_args = [
            "--run_iss",
            "--print_rvcp_passed",
            "--rvmodel_macros",
            "dtest_framework/lib/rvmodel_macros_uart.h",
            "--seed",
            "0",
        ]

        results = self.run_riescued(testname=testname, cli_args=cli_args, iterations=1)
        log_content = self._get_console_log_content(results[0])

        self.assertIn("RVCP: Test File test", log_content, "Missing RVCP prefix with test file name")
        self.assertRegex(log_content, r"RVCP: Test File test test\d+ PASSED")
        self.assertNotIn("FAILED", log_content, "Should not have FAILED messages when test passes")

    def test_message_format_complete_failed(self):
        """Verify the complete FAILED message format is correct."""
        testname = "dtest_framework/tests/htif_rvcp_fail_test.s"
        cli_args = ["--run_iss", "--print_rvcp_failed", "--seed", "0"]

        # This test intentionally fails
        self.expect_toolchain_failure(testname=testname, cli_args=cli_args, failure_kind=ToolFailureType.TOHOST_FAIL, iterations=1)

        log_content = self._get_console_log_for_failing_test(testname)

        # Split into lines and verify format of each line
        lines = [line for line in log_content.strip().split("\n") if line]
        self.assertGreater(len(lines), 0, "Expected at least one RVCP message")

        for line in lines:
            # Each line should match: "RVCP: Test File htif_rvcp_fail_test <testname> FAILED"
            self.assertRegex(line, r"^RVCP: Test File htif_rvcp_fail_test \S+ FAILED$", f"Line does not match expected format: {line}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
