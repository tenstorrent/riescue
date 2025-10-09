# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
import unittest

from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase


class ArithmeticExtTest(BasicTestBase):
    """
    Basic TestPlan test. Checks that RV32IMF / RV64IMF is supported. Checks casting
    """

    def test_basic_32im(self):
        """Simple test plan for RV32IM"""
        steps = self.simple_steps()
        self.run_test("test_basic_32im", steps, "rv32im")

    def test_complex_32im(self):
        """Complex test plan for RV32IM"""
        steps = self.complex_steps()
        self.run_test("test_complex_32im", steps, "rv32im")

    def test_basic_32ifd(self):
        """Simple test plan for RV32IF"""
        steps = self.complex_steps()
        self.run_test("test_basic_32if", steps, "rv32if")

    def test_complex_32ifd(self):
        """Simple test plan for RV32IF"""
        steps = self.complex_steps()
        self.run_test("test_complex_32if", steps, "rv32if")

    # FIXME: Compressed is not working, since the register allocator will pick registers that aren't available in compressed mode
    # Need some changes in the register allocator to fix this
    def test_basic_rv64ifdamc(self):
        """Simple test plan for RV32IF"""
        steps = self.complex_steps()
        text = self.generator_from_steps(steps, "rv64ifdam")
        self.assertNotIn("None", text)

    def test_complex_rv64ifdamc(self):
        """Simple test plan for RV32IF"""
        steps = self.complex_steps()
        text = self.generator_from_steps(steps, "rv64ifdam")
        self.assertNotIn("None", text, f"None found in text, \n{text}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
