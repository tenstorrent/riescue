# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
import unittest

from coretp import TestEnvCfg
from coretp.step import TestStep, Memory, Arithmetic, Load, AssertEqual, AssertNotEqual, AssertException
from coretp.rv_enums import ExceptionCause, PrivilegeMode, PagingMode, PageSize, PageFlags
from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase


class AssertionTest(BasicTestBase):
    """
    Basic Assertion tests. Ensures that Assertion instruction generation works
    """

    def test_assertion_dependency(self):
        """
        Create steps for Assertion test
        """
        steps = []
        op0 = Arithmetic()
        op1 = Arithmetic(arithmetic_type="add", op="addi", src1=1, src2=op0)
        op2 = Arithmetic(arithmetic_type="add", op="addi", src1=1, src2=op0)

        # Edge case 1: Multiple nodes with same dependencies
        op3 = Arithmetic(arithmetic_type="add", op="add")
        op4 = Arithmetic(arithmetic_type="add", op="add", src1=op1, src2=op2)
        op5 = Arithmetic(arithmetic_type="add", op="add", src1=op3)

        # Edge case 2: Chain of dependencies that could be reordered
        op5 = Arithmetic(arithmetic_type="add", op="sub", src1=op3)
        op6 = Arithmetic(arithmetic_type="add", op="sub", src1=op4)
        op7 = Arithmetic(arithmetic_type="add", op="sub", src1=op5, src2=op6)

        # Edge case 3: Independent nodes mixed with dependent ones
        op8 = Arithmetic(arithmetic_type="add", op="addi", src1=5, src2=op0)  # no deps
        op9 = Arithmetic(arithmetic_type="add", op="add", src1=op7)
        op10 = Arithmetic(arithmetic_type="add", op="addi", src1=6, src2=op0)  # no deps
        op11 = AssertNotEqual(src1=op9, src2=op10)

        # Edge case 4: Complex dependency pattern
        op12 = Arithmetic(arithmetic_type="add", op="add", src1=op8, src2=op9)
        op13 = Arithmetic(arithmetic_type="add", op="add", src1=op10)

        # Edge case 5: Multiple independent nodes at the end
        op14 = Arithmetic(arithmetic_type="add", op="addi", src1=7, src2=op0)  # no deps
        op15 = Arithmetic(arithmetic_type="add", op="addi", src1=8, src2=op0)  # no deps
        op16 = Arithmetic(arithmetic_type="add", op="addi", src1=9, src2=op0)  # no deps
        steps.append(op0)
        steps.append(op1)
        steps.append(op2)
        steps.append(op3)
        steps.append(op4)
        steps.append(op5)
        steps.append(op6)
        steps.append(op7)
        steps.append(op8)
        steps.append(op9)
        steps.append(op10)
        steps.append(op11)
        steps.append(op12)
        steps.append(op13)
        steps.append(op14)
        steps.append(op15)
        steps.append(op16)
        self.run_test("test_assertion_dependency", steps, env=TestEnvCfg(priv_modes=[PrivilegeMode.U], paging_modes=[PagingMode.SV39], reg_widths=[32]))

    def test_assert_equal(self):
        """
        Checking that AssertEqual generates correctly
        """
        # Steps
        steps = []
        op1 = Arithmetic(op="li", src1=100)
        op2 = Arithmetic(op="li", src1=100)
        op3 = AssertEqual(src1=op1, src2=op2)
        steps.append(op1)
        steps.append(op2)
        steps.append(op3)

        # Generate Test text
        test_name = "test_assert_equal"
        text = self.generator_from_steps(steps, "rv32i", test_scenario_name=test_name, env=TestEnvCfg(priv_modes=[PrivilegeMode.U], paging_modes=[PagingMode.SV39], reg_widths=[32]))

        # Check test text
        routines = self.text_to_instr_list(text)
        routine = routines.get(test_name)
        self.assertIsNotNone(routine)
        assert routine is not None, "typehint"
        self.assert_valid_test_routines(routine)

        # Check that branch to pass label is generated
        beqs = routine.matching_instr("beq")
        self.assertEqual(len(beqs), 1, "expected a single beq instruction to the pass label")
        branch_label = beqs[0].operands[-1]
        self.assertIn(branch_label, [r.name for r in routines.values()], f"Exected the branch label '{branch_label}' to be a test label but not found.")

        self.run_test(test_name, steps, "rv32i", env=TestEnvCfg(priv_modes=[PrivilegeMode.U], paging_modes=[PagingMode.SV39], reg_widths=[32]))

    def test_assert_not_equal(self):
        """
        Checking that AssertNotEqual generates correctly
        """
        steps = []
        op1 = Arithmetic(op="li", src1=1)
        op2 = Arithmetic(op="li", src1=2)
        op3 = AssertNotEqual(src1=op1, src2=op2)
        steps.append(op1)
        steps.append(op2)
        steps.append(op3)

        test_name = "test_assert_not_equal"
        text = self.generator_from_steps(steps, "rv32i", test_scenario_name=test_name, env=TestEnvCfg(priv_modes=[PrivilegeMode.U], paging_modes=[PagingMode.SV39], reg_widths=[32]))

        routines = self.text_to_instr_list(text)
        routine = routines.get(test_name)
        self.assertIsNotNone(routine)
        assert routine is not None, "typehint"
        self.assert_valid_test_routines(routine)

        # Check that branch to pass label is generated
        bnes = routine.matching_instr("bne")
        self.assertEqual(len(bnes), 1, "expected a single beq instruction to the pass label")
        branch_label = bnes[0].operands[-1]
        self.assertIn(branch_label, [r.name for r in routines.values()], f"Exected the branch label '{branch_label}' to be a test label but not found.")

        self.run_test(test_name, steps, "rv32i", env=TestEnvCfg(priv_modes=[PrivilegeMode.U], paging_modes=[PagingMode.SV39], reg_widths=[32]))

    def test_multiple_assertions(self):
        """
        Checking that multiple assertions generate correctly
        """
        steps = []
        op1 = Arithmetic(op="li", src1=1)
        op2 = Arithmetic(op="li", src1=2)
        op3 = Arithmetic(op="li", src1=2)
        op4 = AssertEqual(src1=op2, src2=op3)
        op5 = AssertNotEqual(src1=op1, src2=op3)
        steps.append(op1)
        steps.append(op2)
        steps.append(op3)
        steps.append(op4)
        steps.append(op5)

        test_name = "test_multiple_assertions"
        text = self.generator_from_steps(steps, "rv32i", test_scenario_name=test_name, env=TestEnvCfg(priv_modes=[PrivilegeMode.U], paging_modes=[PagingMode.SV39], reg_widths=[32]))
        routines = self.text_to_instr_list(text)
        routine = routines.get(test_name)
        self.assertIsNotNone(routine)
        assert routine is not None, "typehint"

        self.run_test(test_name, steps, "rv64i")

    def test_basic_exception(self):
        """
        Create steps for Exception test
        """

        steps = []
        mem = Memory(page_size=PageSize.SIZE_4K, size=0x1000, flags=PageFlags.DIRTY)
        op0 = Arithmetic()
        op1 = Arithmetic(arithmetic_type="add", op="addi", src1=1, src2=op0)
        op2 = AssertException(
            cause=ExceptionCause.LOAD_PAGE_FAULT,
            code=[
                op1,
                Load(memory=mem, offset=0x40),
            ],
        )
        steps = [
            mem,
            op0,
            op1,
            op2,
        ]
        self.run_test("test_basic_exception", steps, env=TestEnvCfg(priv_modes=[PrivilegeMode.U], paging_modes=[PagingMode.SV39], reg_widths=[32]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
