# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase


class ArithmeticGeneratorTest(BasicTestBase):
    """
    Basic TestPlan test.

    Currently tests imports, TestScenario types, etc. Eventually will want to move coretp tests somewhere else.
    """

    def test_basic_arithmetic32(self):
        """Test plan with basic triangle dependency"""
        steps = self.simple_steps()
        self.run_test("test_basic_arithmetic32", steps, "rv32i")

    def test_basic_arithmetic64(self):
        steps = self.simple_steps()
        self.run_test("test_basic_arithmetic64", steps, "rv64i")

    def test_complex_arithmetic32i(self):
        """Test plan with a more copmlex depency tree"""
        steps = self.complex_steps()
        self.run_test("test_complex_arithmetic32i", steps, "rv32i")

    def test_complex_arithmetic64i(self):
        """Test plan with a more copmlex depency tree"""
        steps = self.complex_steps()
        self.run_test("test_complex_arithmetic64i", steps, "rv64i")


if __name__ == "__main__":
    unittest.main(verbosity=2)
