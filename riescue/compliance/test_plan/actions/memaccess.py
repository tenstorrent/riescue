# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional, Union

from coretp import Instruction, StepIR, TestStep
from coretp.isa import get_register
from coretp.step import MemAccess
from coretp.rv_enums import Category, PageSize, PageFlags, Extension
from riescue.compliance.test_plan.actions import Action, ArithmeticAction, LiAction
from riescue.compliance.test_plan.actions.memory import MemoryAction
from riescue.compliance.test_plan.context import LoweringContext


class MemAccessAction(Action):
    """
    Handles MemAccess instructions. Gets random memory if none is provided

    """

    register_fields = ["memory", "rs1"]

    def __init__(self, offset: int = 0, memory: Optional[str] = None, src2: Optional[Union[str, int]] = None, op: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.offset = offset
        self.memory = memory  # Should only ever hold label to memory block
        self.rs1 = memory  # can be mem or a register
        self.rs2 = src2
        self.op = op
        self.expanded = False

        # not sure on this, please advise
        self.value = None

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "MemAccessAction":
        """
        Create a MemAccessAction from a StepIR object.
        """
        if TYPE_CHECKING:
            assert isinstance(step.step, MemAccess)
        memory = None
        src2 = None
        for index, src in enumerate(step.inputs):
            if isinstance(src, str) and src.startswith("m"):
                memory = src
            else:
                if index == 0:
                    continue  # skip the first input, it's the mandatory offset
                src2 = src

        return cls(step_id=step_id, offset=step.step.offset, memory=memory, src2=src2, op=step.step.op, **kwargs)

    def repr_info(self) -> str:
        return f"{self.offset}('{self.memory}'))"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        """Expands to add MemoryAction if no Memory action provided"""
        if self.expanded:
            return None
        self.expanded = True

        new_actions = []
        if self.memory is None:
            random_size = ctx.random_n_width_number(32, 13) & 0xFFFFF000
            mem = MemoryAction(step_id=ctx.new_memory_id(), size=random_size, page_size=PageSize.SIZE_4K, flags=PageFlags.READ)
            self.memory = mem.step_id
            ctx.mem_reg.allocate_data(mem.step_id, mem)
            memory_li = LiAction(step_id=ctx.new_value_id(), immediate=mem.step_id)
            self.rs1 = memory_li.step_id
            new_actions.append(memory_li)
            return new_actions
        elif ctx.mem_reg.is_memory_label(self.memory):
            # if memory is a label to a page, need to load the page base address
            # otherwise if it's a register, leave it alone
            memory_li = LiAction(step_id=ctx.new_value_id(), immediate=self.memory)
            self.rs1 = memory_li.step_id
            new_actions.append(memory_li)

        if not (-2048 <= self.offset <= 2047):
            li = LiAction(step_id=ctx.new_value_id(), immediate=self.offset)
            add = ArithmeticAction(step_id=ctx.new_value_id(), op="add", src1=self.rs1, src2=li.step_id)  # Should be able to select addi x1, (num>2^12) and it shoudl handle the li
            self.rs1 = add.step_id  # rewire
            self.offset = 0  # replace offset with 0
            new_actions.append(li)
            new_actions.append(add)

        if self.rs2 is not None:
            if isinstance(self.rs2, int):
                if self.rs2 == 0:
                    self.rs2 = get_register("zero")
                else:
                    li = LiAction(step_id=ctx.new_value_id(), immediate=self.rs2)
                    self.rs2 = li.step_id
                    new_actions.append(li)

        if new_actions:
            new_actions.append(self)
            return new_actions
        return None

    def _recurse_pick_instruction(self, ctx: LoweringContext) -> Instruction:
        if self.op is not None:
            return ctx.instruction_catalog.get_instruction(self.op)
        else:
            instruction_choices = ctx.instruction_catalog.filter(category=Category.LOAD | Category.STORE, exclude_extensions=Extension.SVINVAL)
            return ctx.rng.choice(instruction_choices)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        "Recurisvely calls to avoid picking stack pointer-relative instructions"

        selected_instruction = self._recurse_pick_instruction(ctx)

        rs1 = selected_instruction.get_source("rs1")
        if rs1 is None:
            raise ValueError(f"Expected a rs1 operand not available for Load action, with instruction {selected_instruction}")
        rs1.val = self.rs1

        imm = selected_instruction.immediate_operand()
        if imm is not None:
            self.offset = 0
            imm.val = 0  # we have already added the value to the offset

        rs2 = selected_instruction.get_source("rs2")
        if rs2 is not None:
            if self.rs2 is None:
                self.rs2 = ctx.new_value_id()  # Use random value if no store value provided
            rs2.val = self.rs2

        return selected_instruction
