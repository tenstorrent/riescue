# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

import riescue.compliance
from coretp import TestEnvCfg, TestScenario, TestPlan
from coretp.step import TestStep, CodePage, Arithmetic, Call, Memory, Load, Store
from coretp.rv_enums import PageFlags, PrivilegeMode
from riescue.compliance.test_plan.actions import ArithmeticAction

from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase, Routine


class FunctionTest(BasicTestBase):
    """
    Basic Code Page tests. Ensures that CodePage instruction generation works
    """

    def test_code_page_with_instruction(self):
        """
        Test that CodePage with some instructions gets generated correctly, instructions should be in the code page
        """

        test_name = "test_code_page_with_instruction"
        scenario = self.code_page_scenario(name=test_name)
        text = self.generator_from_scenario(isa="rv64imafd_zicsr", test_scenario=scenario)
        # manual checking
        self.assertNotIn("None", text, f"None found in generated test, {text}")
        self.assertIn("jalr", text, "No jalr instruction found in generated test")
        self.assertIn("code_mem0", text, "No code page found in generated test")
        self.assertIn("ret", text, "No ret instruction found in generated test")

        routines = self.text_to_instr_list(text)
        routine = routines.get(test_name)
        self.assertIsNotNone(routine, f"Routine {test_name} not found in generated routines {routines.keys()}")
        assert routine is not None, "typehint"
        self.assert_registers_in_order(routine)
        # Check that branch to local_test_failed is generated
        self.assertIn("local_test_failed", text, "expected a jump to local_test_failed")

        self.run_test_from_scenario(scenario, isa="rv64imafd_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]))

    def test_code_page_with_instruction_check_fail(self):
        """
        Test that CodePage with some instructions gets generated correctly, instructions should be in the code page
        """

        test_name = "test_code_page_with_instruction_check_fail"
        scenario = self.code_page_scenario_check_fail(name=test_name)
        text = self.generator_from_scenario(isa="rv64imafd_zicsr", test_scenario=scenario)

        # print(text)
        # manual checking
        self.assertNotIn("None", text, f"None found in generated test, {text}")
        self.assertIn("jalr", text, "No jalr instruction found in generated test")
        self.assertIn("code_mem0", text, "No code page found in generated test")
        self.assertIn("ret", text, "No ret instruction found in generated test")

        routines = self.text_to_instr_list(text)
        routine = routines.get(test_name)
        self.assertIsNotNone(routine, f"Routine {test_name} not found in generated routines {routines.keys()}")
        assert routine is not None, "typehint"
        self.assert_registers_in_order(routine)
        # Check that branch to local_test_failed is generated
        self.assertIn("local_test_failed", text, "expected a jump to local_test_failed")

        self.run_test_from_scenario(scenario, isa="rv64imafd_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]), should_fail=True)

    def test_cross_code_page_call(self):
        """
        Test that CodePage with some instructions gets generated correctly, instructions should be in the code page
        """

        test_name = "test_cross_code_page_call"
        scenario = self.cross_code_page_call(name=test_name)
        text = self.generator_from_scenario(isa="rv64imafd_zicsr", test_scenario=scenario)

        routines = self.text_to_instr_list(text)
        routine = routines.get(test_name)
        self.assertIsNotNone(routine, f"Routine {test_name} not found in generated routines {routines.keys()}")
        assert routine is not None, "typehint"
        self.assert_registers_in_order(routine)
        # Check that branch to local_test_failed is generated
        self.assertIn("local_test_failed", text, "expected a jump to local_test_failed")

        self.run_test_from_scenario(scenario, isa="rv64imafd_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]))

    def test_code_page_complex_mem(self):
        """
        Test that CodePage with some instructions gets generated correctly, instructions should be in the code page
        """

        mem = Memory()
        ld = Load(memory=mem)
        op = Arithmetic(src1=ld)
        func = CodePage(
            code=[
                ld,
                op,
                Store(memory=mem, value=op),
            ]
        )
        call = Call(target=func)

        steps: list[TestStep] = [mem, func, call]
        text = self.generator_from_steps(steps, "rv64imafd_zicsr")

        self.assertNotIn("None", text)
        self.assertIn("jalr", text, "No jalr instruction found in generated test")
        self.assertIn("ret", text, "No ret instruction found in generated test")
        self.run_test("test_code_page_complex_mem", steps, "rv64imafd_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]))

    def test_function_call_doesnt_clobber_registers(self):
        """
        Checking that function calls don't clobber registers inside of a function.

        i.e. <code> <call> <more_code> shouldn't have registers clobbered from <code>
        Testing with 32 arithmetic steps before and after function call
        """

        arith_steps = self.n_register_arithmetic_steps(max=16)
        function = self.function_with_steps(steps=self.n_register_arithmetic_steps(max=26))
        mid = len(arith_steps) // 2
        arith_steps.insert(mid, Call(target=function))  # place call in middle of function
        arith_steps.insert(mid, function)  # place def in middle of function
        steps = arith_steps

        test_name = "test_function_call"
        text = self.generator_from_steps(steps, "rv64imafd_zicsr", test_scenario_name=test_name)
        routines = self.text_to_instr_list(text)

        self.assertIn("code_mem0", routines, "No function called code_mem0 found in generated routines")
        function_code = routines["code_mem0"]
        self.assertIn(test_name, routines.keys(), f"Test function {test_name} not found in generated routines {routines.keys()}")
        test_code = routines[test_name]

        # split between function call and post function call
        post = False
        pre_test_code = []
        post_test_code = []
        for t in test_code.instructions:
            if post:
                post_test_code.append(t)
            else:
                if t.name == "jalr":
                    post = True
                    continue
                pre_test_code.append(t)
        pre_test_routine = Routine(test_name, pre_test_code)
        post_test_routine = Routine(test_name, post_test_code)
        self.assertFalse(pre_test_routine.external_dependencies(), f"External dependencies found in pre test routine {test_name}. This shouldn't have happened")
        self.assertFalse(function_code.external_dependencies(), f"External dependencies found in function code {test_name}. This shouldn't have happened")

        for external_dependency in post_test_routine.external_dependencies():
            clobbered_registers = function_code.destination_registers()
            self.assertNotIn(external_dependency, clobbered_registers, f"Register {external_dependency} is used in function and clobbered in post test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
