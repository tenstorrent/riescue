# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Optional

from coretp import Instruction, TestEnv, InstructionCatalog
from coretp.isa import Label, Register, RISCV_REGISTERS

from riescue.compliance.test_plan.types import DiscreteTest, TextSegment, DataSegment, GlobalFunction, DataPage, TestCase
from riescue.lib.rand import RandNum
from .expander import Expander
from .elaborator import Elaborator
from .canonicalizer import Canonicalizer
from .legalizer import Legalizer
from .allocator import Allocator
from .test_harness import TestHarness
from riescue.compliance.test_plan.context import LoweringContext
from riescue.compliance.test_plan.memory import MemoryRegistry
from riescue.compliance.test_plan.actions import Action, StackPageAction

logger = logging.getLogger(__name__)


class Transformer:
    """
    Orchestrator for transformation process; transforms ``TestStep`` IR into ``Action`` IR, then ``Instruction`` Final IR

    :param rng: :class:`RandNum` object to use for randomization.
    """

    def __init__(self, rng: RandNum, mem_reg: MemoryRegistry, isa: str = "rv32i"):
        self.rng = rng
        self.mem_reg = mem_reg  # Memory Registry exists for life of Transformer

        self.expander = Expander()  # Expands Actions into a flat list of Actions
        self.elaborator = Elaborator()  # Elaborates Actions into Instructions
        self.canonicalizer = Canonicalizer()  # Canonicalizes Actions
        self.legalizer = Legalizer()  # Legalizes Instructions
        self.allocator = Allocator()  # Allocates Instructions into Subroutines
        self.test_harness = TestHarness()  # Adds test harness code
        self.catalog = InstructionCatalog(isa)

    def transform_tests(self, tests: list[DiscreteTest], env: TestEnv) -> tuple[TextSegment, DataSegment]:
        """
        Transform a list of ``DiscreteTest`` IR objects into a list of lists of ``Instruction`` IR objects.
        """
        ctx = LoweringContext(
            rng=self.rng,
            mem_reg=self.mem_reg,
            env=env,
            instruction_catalog=self.catalog,
        )
        canonicalized_tests, code_page_actions, memory = self.canonicalizer.canonicalize(tests, ctx)
        global_functions = []
        # create global functions
        for code_page_action in code_page_actions:
            code_page_id = code_page_action.step_id
            function_instructions = self._transform([code_page_action], ctx)
            code_page = ctx.mem_reg.get_code_page(code_page_id)
            global_functions.append(GlobalFunction.from_instructions(code_page, function_instructions))

            ctx.global_function_clobbers[code_page_id] = self._clobbered_registers(function_instructions)
        data_segment = DataSegment(blocks=global_functions)  # Global functions and data

        test_blocks = []
        for test in canonicalized_tests:
            # add stack pages for each test before transforming
            stack_name = f"{test.name}_stack"
            stack_page = StackPageAction(name=stack_name)
            ctx.mem_reg.allocate_data(stack_name, stack_page)

            # transform DiscreteTest into TestCase
            test_instructions = self._transform(test.actions, ctx)
            test_cast_instructions = [Label(test.name)] + self.initialize_stack(test.name, ctx) + test_instructions
            test_block = TestCase.from_instructions(test_cast_instructions, header=f";#discrete_test(test={test.name})")
            test_block.blocks.append(self.test_harness.test_passed(test_name=test.name))
            test_blocks.append(test_block)
        text_segment = TextSegment(blocks=test_blocks)  # Test code

        # add test harness code
        text_segment = self.test_harness.add_test_harness(text_segment)

        # allocate all memory (global functions / test code may have allocated more)
        for mem in ctx.mem_reg.data:
            data_segment.blocks.append(mem)
        return text_segment, data_segment

    def _transform(self, actions: list[Action], ctx: LoweringContext) -> list[Instruction]:
        """
        Transform a ``ActionBlock`` of ``Action`` IR objects into a list of ``Instruction`` IR objects.

        Private to indicate that transform_tests should be used instead. This is becasue the canonicalizer needs to be called first.
        Otherwise actions that expand and source operands might need new IDs and canonicalizer needs to be setup correctly
        """

        expanded_actions = self.expander.expand(actions, ctx)
        elaborated_instructions = self.elaborator.elaborate(expanded_actions, ctx)
        legalized_instructions = self.legalizer.legalize(elaborated_instructions, ctx)
        allocated_subroutines = self.allocator.allocate(legalized_instructions, ctx)
        return allocated_subroutines

    def _split_instructions(self, instructions: list[Instruction]) -> list[list[Instruction]]:
        """
        Split instructions into label separated instructions
        """
        current_block: list[Instruction] = []
        all_instructions: list[list[Instruction]] = []
        for instruction in instructions:
            if isinstance(instruction, Label):
                all_instructions.append(current_block)
                current_block = [instruction]
            else:
                current_block.append(instruction)
        all_instructions.append(current_block)
        return all_instructions

    def initialize_stack(self, test_name: str, ctx: LoweringContext) -> list[Instruction]:
        """
        Initialize stack for a test

        Note: Tests assume stack pointer is initialized. Might need a better way to determine
        if stack is needed at all, and a better way to link the stack memory besides the test name
        """

        stack_page_symbol = f"{test_name}_stack"
        stack_page = ctx.mem_reg.get_data_page(stack_page_symbol)

        sp = RISCV_REGISTERS[2]
        t0 = RISCV_REGISTERS[5]
        if sp.name != "sp" or t0.name != "t0":
            raise Exception(f"Incorrect sp and/or t0 in {sp=} {t0=}. Need better way to get registers from RISCV_REGISTERS")

        # li sp, stack_page_symbol
        li = ctx.instruction_catalog.get_instruction("li")
        li.instruction_id = ctx.new_value_id()
        li_imm = li.immediate_operand()
        li_rd = li.destination
        if li_imm is None or li_rd is None:
            raise Exception(f"li instruction {li.name} has no immediate operand /destination operand")
        li_imm.val = stack_page_symbol
        li_rd.val = sp

        # li t0, stack_page_size
        li_t0 = ctx.instruction_catalog.get_instruction("li")
        li_t0.instruction_id = ctx.new_value_id()
        li_t0_imm = li_t0.immediate_operand()
        li_t0_rd = li_t0.destination
        if li_t0_imm is None or li_t0_rd is None:
            raise Exception(f"li instruction {li_t0.name} has no immediate operand /destination operand")
        li_t0_imm.val = stack_page.size
        li_t0_rd.val = t0

        # add sp, sp, t0
        add = ctx.instruction_catalog.get_instruction("add")
        add.instruction_id = ctx.new_value_id()
        add_rs1 = add.rs1()
        add_rs2 = add.rs2()
        add_rd = add.destination
        if add_rs1 is None or add_rs2 is None or add_rd is None:
            raise Exception(f"add instruction {add.name} has no source operand /source operand /destination operand")
        add_rs1.val = sp
        add_rs2.val = t0
        add_rd.val = sp

        # andi sp, sp, -16
        andi = ctx.instruction_catalog.get_instruction("andi")
        andi.instruction_id = ctx.new_value_id()
        andi_imm = andi.immediate_operand()
        andi_rs1 = andi.rs1()
        andi_rd = andi.destination
        if andi_imm is None or andi_rs1 is None or andi_rd is None:
            raise Exception(f"andi instruction {andi.name} has no immediate operand /source operand /destination operand")

        andi_imm.val = -16
        andi_rs1.val = sp
        andi_rd.val = sp

        # Add stack size to stack page
        return [li, li_t0, add, andi]

    def _clobbered_registers(self, instructions: list[Instruction]) -> list[Register]:
        """
        Get the list of registers that are clobbered by the instructions
        """
        return [instr.destination.val for instr in instructions if instr.destination is not None and isinstance(instr.destination.val, Register)]
