# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
"""
Unit tests for the Exceptions test plan.

Covers RISC-V exception handling: illegal instructions, misaligned addresses,
environment calls, nested exceptions, xRET instructions, and negative cases.
"""
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class ExceptionsTest(BaseRiescueCTest):
    """
    Runs Exceptions test plan.

    Tests exception handling scenarios including:
    - Illegal instruction exceptions
    - Misaligned address exceptions
    - Environment calls
    - Nested exceptions
    - xRET instructions
    """

    def test_exceptions_tp(self):
        """Run the full exceptions test plan."""
        self.run_tp_mode(plan="exceptions", cli_args=["--repeat_times", "1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
