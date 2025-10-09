# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import re
from typing import Any, Optional, NamedTuple, TYPE_CHECKING
from pathlib import Path


from riescue.compliance.test_plan import TestPlanGenerator
from riescue.lib.rand import RandNum
from riescue.lib.toolchain.exceptions import ToolFailureType

from coretp import TestEnvCfg, TestScenario, TestPlan
from coretp.step import TestStep, Arithmetic
from tests.compliance.test_plan.base_test_plan import BaseTestPlan


class Label(NamedTuple):
    "Label with name"

    name: str

    def __str__(self) -> str:
        return f"{self.name}:"


class Instr(NamedTuple):
    "Instruction with name and operands"

    name: str
    operands: list[str]

    def __str__(self) -> str:
        return f"{self.name} {', '.join(self.operands)}"


class Routine(NamedTuple):
    "Routine with name and instructions"

    name: str
    instructions: list[Instr]

    def __str__(self) -> str:
        return f"{self.name}: {', '.join(str(instr) for instr in self.instructions)}"

    def matching_instr(self, name: str) -> list[Instr]:
        "Helper method to get all Instr objects that match the name"
        return [i for i in self.instructions if i.name == name]

    def instr_index(self, instr: Instr) -> int:
        "Return the index of the first Instr object that matches the name. Raise ValueError if not found"
        for i, instruction in enumerate(self.instructions):
            if instruction == instr:
                return i

        raise ValueError(f"Instr {instr} not found in routine {self.name}")

    def destination_registers(self) -> set[str]:
        "Return a set of destination registers"
        return set(i.operands[0] for i in self.instructions if i.operands and len(i.operands) > 0)

    def source_registers(self) -> set[str]:
        "Return a set of source registers"
        source_registers = set()
        for x in self.instructions:
            if len(x.operands) > 1:
                operands = x.operands[1:]
                for o in operands:
                    if not o.startswith("0x"):
                        source_registers.add(o)
        return source_registers

    def external_dependencies(self) -> set[str]:
        """
        Returns a set of registers that are read (sourced) but never written (destination)
        in the given list of instructions.

        :param instructions: List of Instr objects representing the routine.
        :return: Set of register names that are external dependencies.
        """
        written = set()
        read = set()
        for instr in self.instructions:
            if instr.operands:
                written.add(instr.operands[0])
                if len(instr.operands) > 1:
                    for op in instr.operands[1:]:
                        # ignore immediate numbers, symbols
                        if not op.startswith("0x") and not op.startswith("-") and len(op) < 5:
                            read.add(op)
        return read - written


class BasicTestBase(BaseTestPlan):
    """
    Base class for basic test plans. test_plan/basic the current home for the bringup tests.
    This provides some of the basic tests to include

    Use routine_from_steps() to get a Routine objects from a list of TestSteps and ISA string.
    Use assert_valid_test_routines() to assert that the generated routines are valid.

    """

    def simple_steps(self) -> list[Any]:
        """Return simple arithmetic tree"""
        steps = []
        op1 = Arithmetic()
        op2 = Arithmetic()
        op3 = Arithmetic(src1=op1, src2=op2)
        steps.append(op1)
        steps.append(op2)
        steps.append(op3)
        return steps

    def complex_steps(self) -> list[Any]:
        """Return complex arithmetic tree"""
        steps = []
        op1 = Arithmetic()
        op2 = Arithmetic()
        op3 = Arithmetic(src1=op1, src2=op2)
        op4 = Arithmetic(src1=op1, src2=op3)
        op5 = Arithmetic(src1=op2, src2=op3)
        op6 = Arithmetic(src1=op1, src2=op2)
        op7 = Arithmetic(src1=op1, src2=op3)
        op8 = Arithmetic(src1=op2, src2=op3)
        op9 = Arithmetic(src1=op2, src2=op4)
        op10 = Arithmetic(src1=op1, src2=op7)
        op11 = Arithmetic(src1=op7, src2=op8)
        op12 = Arithmetic(src1=op5, src2=op9)
        op13 = Arithmetic(src1=op6, src2=op10)
        op14 = Arithmetic(src1=op4, src2=op11)
        op15 = Arithmetic(src1=op2, src2=op12)
        op16 = Arithmetic(src1=op3, src2=op13)
        op17 = Arithmetic(src1=op1, src2=op15)
        steps.extend([op1, op2, op3, op4, op5, op6, op7, op8, op9, op10, op11, op12, op13, op14, op15, op16, op17])
        return steps

    def create_generator(self, isa: str, rng: Optional[RandNum] = None):
        rng = rng or RandNum(seed=10)
        generator = TestPlanGenerator(isa, rng)
        return generator

    def generator_from_steps(
        self,
        steps: list[TestStep],
        isa: str,
        env: Optional[TestEnvCfg] = None,
        test_scenario_name: str = "test_scenario",
    ) -> str:
        """
        Create a TestPlanGenerator from a list of TestSteps and ISA string.
        Creates test_plan, runs build, solves, generate() methods, returns text
        Always uses seed=0
        """

        if env is None:
            env = TestEnvCfg()
        test_scenario = TestScenario.from_steps(name=test_scenario_name, description="Test scenario", env=env, steps=steps)
        return self.generator_from_scenario(isa=isa, test_scenario=test_scenario)

    def generator_from_scenario(
        self,
        isa: str,
        test_scenario: TestScenario,
        rng: Optional[RandNum] = None,
    ) -> str:
        """
        Create a TestPlanGenerator from a TestScenario and ISA string.
        Creates test_plan, runs build, solves, generate() methods, returns text
        Always uses seed=0
        """

        generator = self.create_generator(isa=isa, rng=rng)
        test_plan = TestPlan(name="other_test_plan", scenarios=[test_scenario])
        tests = generator.build(test_plan)
        solved_env = generator.solve(tests)
        return generator.generate(tests, solved_env)

    def routine_from_steps(self, steps: list[TestStep], isa: str, test_name: str, **kwargs) -> Routine:
        """
        Create a Routine from a list of TestSteps and ISA string.
        """
        text = self.generator_from_steps(steps, isa, test_scenario_name=test_name, **kwargs)
        routines = self.text_to_instr_list(text)
        routine = routines.get(test_name)
        self.assertIsNotNone(routine, f"Routine {test_name} not found in generated test text. Should have been generated")
        if TYPE_CHECKING:
            assert routine is not None, "typehint"
        return routine

    def text_to_instr_list(self, text: str) -> dict[str, Routine]:
        routines = []
        prev_instr = []
        prev_label = None

        text, data_text = text.split(".section .data")

        for line in (t.strip() for t in text.split("\n") if t.strip()):

            if ":" in line:
                label_name = line.split(":")[0]
                if prev_label is None:
                    routines.append(Routine(label_name, prev_instr))
                    prev_instr = []
                    prev_label = label_name
                else:
                    routines.append(Routine(prev_label, prev_instr))
                    prev_instr = []
                    prev_label = label_name
            else:
                name = line.split(" ")[0]
                operands = [o.replace(",", "") for o in line.split(" ")[1:]]
                prev_instr.append(Instr(name, operands))
        if prev_label is not None:
            routines.append(Routine(prev_label, prev_instr))

        prev_data_lable = None
        data_section_start = ";#init_memory @"
        for line in (t.strip() for t in data_text.split("\n") if t.strip()):
            if line.startswith(data_section_start):
                label_name = line.replace(data_section_start, "")
                if prev_data_lable is None:
                    prev_data_lable = label_name
                else:
                    routines.append(Routine(prev_data_lable, prev_instr))
                    prev_data_lable = label_name
                    prev_instr = []
        if prev_data_lable is not None:
            routines.append(Routine(prev_data_lable, prev_instr))
        return {routine.name: routine for routine in routines}

    # A set of common instructions that do not write to a destination register.
    _INSTR_NO_DEST = {"sb", "sh", "sw", "sd", "fsw", "fsd", "beq", "bne", "blt", "bge", "bltu", "bgeu", "j", "ecall", "ebreak", "mret", "sret", "uret", "wfi", "fence", "fence.i", "jalr"}

    def _get_instr_operands(self, instr: Instr) -> tuple[Optional[str], list[str]]:
        """
        Parses an instruction into its destination and source registers.
        Assumes that if an instruction has a destination, it is the first operand.

        :returns: A tuple of (destination_register, list_of_source_registers).
        """
        if instr.name.startswith(".") or not instr.operands:
            return None, []

        source_ops = instr.operands
        dest_op = None

        if instr.name not in self._INSTR_NO_DEST:
            dest_op = instr.operands[0]
            source_ops = instr.operands[1:]

        dest_reg = self._get_regs_from_operand(dest_op)[0] if dest_op and self._get_regs_from_operand(dest_op) else None

        source_regs = []
        for op in source_ops:
            source_regs.extend(self._get_regs_from_operand(op))

        return dest_reg, source_regs

    def _get_regs_from_operand(self, op_str: str) -> list[str]:
        """Extracts register names (e.g., x5, f12, s0) from an operand string."""
        return re.findall(r"\b(x\d{1,2}|f\d{1,2}|zero|ra|sp|gp|tp|t[0-6]|s[0-1]?\d|a[0-7])\b", op_str)

    def assert_registers_in_order(self, routine: Routine):
        """
        Checks that source registers are defined before use within a routine.
        """
        defined_registers = {"x0", "zero"}  # x0 is always defined

        for instr in routine.instructions:
            if instr.name.startswith("."):
                continue

            dest_reg, source_regs = self._get_instr_operands(instr)

            for src in source_regs:
                self.assertIn(
                    src,
                    defined_registers,
                    f"In routine '{routine.name}', instruction '{instr}' uses register '{src}' before it is defined.",
                )

            if dest_reg:
                defined_registers.add(dest_reg)

    def assert_no_unused_li(self, routine: Routine):
        """
        Checks that every 'li' has its destination register used before being redefined or the routine ends.
        """
        for i, instr in enumerate(routine.instructions):
            if instr.name != "li":
                continue

            li_dest_reg, _ = self._get_instr_operands(instr)
            if not li_dest_reg or li_dest_reg in ["x0", "zero"]:
                continue

            for next_instr in routine.instructions[i + 1 :]:
                next_dest, next_sources = self._get_instr_operands(next_instr)
                if li_dest_reg in next_sources:
                    break  # Found a use, this li is valid.
                if next_dest == li_dest_reg:
                    self.fail(f"Unused 'li' in routine '{routine.name}': '{instr}'. " f"Register '{li_dest_reg}' is loaded but overwritten before being used.")
            else:
                # The 'for' loop completed without 'break', so the register was never used.
                self.fail(f"Unused 'li' in routine '{routine.name}': '{instr}'. " f"Register '{li_dest_reg}' is loaded but never used.")

    def assert_valid_test_routines(self, routine: Routine):
        """
        Assert that all routines are valid
        """
        self.assert_registers_in_order(routine)
        self.assert_no_unused_li(routine)

    def run_test(self, test_name: str, steps: list[Any], isa: str = "rv32i", env: Optional[TestEnvCfg] = None):
        """
        Compiles and runs test on ISS. Currently uses defualt TestEnvCfg. calls RiescueD directly, doesn't use RiescueC
        """
        self.disable_logging()
        env = env or TestEnvCfg()
        test_scenario = TestScenario.from_steps(name=test_name, description="Test scenario", env=env, steps=steps)
        return self.run_test_from_scenario(test_scenario, isa=isa, env=env)

    def run_test_from_scenario(self, test_scenario: TestScenario, isa: str = "rv32i", env: Optional[TestEnvCfg] = None, should_fail: bool = False):
        self.disable_logging()

        for i in range(self.iterations):
            rng = RandNum(seed=i)
            text = self.generator_from_scenario(isa, test_scenario, rng)
            self.assertNotIn("None", text, f"Generated text contains None: {text}")

            # pass test to RiescueD
            test_file = self.test_dir / "assertion_test.s"
            with open(test_file, "w") as f:
                f.write(text)

            if should_fail:
                for failure in self.expect_toolchain_failure_generator(testname=str(test_file), cli_args=["--run_iss"], failure_kind=ToolFailureType.TOHOST_FAIL, iterations=1, starting_seed=i):
                    self.assertEqual(failure.fail_code, 0x03, "Expected TOHOST_FAIL failure 0x3")
            else:
                self.run_riescued(
                    str(test_file),
                    cli_args=["--run_iss"],
                    iterations=1,
                    starting_seed=i,
                )
