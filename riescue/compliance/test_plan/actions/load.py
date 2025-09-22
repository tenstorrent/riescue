# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Any, Optional

from coretp import Instruction, StepIR
from coretp.step import Load
from coretp.rv_enums import Category, PageSize, PageFlags

from riescue.compliance.test_plan.actions import Action, ArithmeticAction, LiAction
from riescue.compliance.test_plan.actions.memory import MemoryAction
from riescue.compliance.test_plan.context import LoweringContext


class LoadAction(Action):
    """
    Handles Load instructions. Gets random memory if none is provided

    """

    register_fields = ["memory", "rs1"]

    def __init__(self, offset: int = 0, memory: Optional[str] = None, op: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.offset = offset
        self.memory = memory  # Should only ever hold label to memory block
        self.rs1 = memory  # can be mem or a register
        self.op = op
        self.aligned = True  # TODO: Add support for unaligned loads
        self.constraints = {"category": Category.LOAD}
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "LoadAction":
        """
        Create a LoadAction from a StepIR object.
        """
        if TYPE_CHECKING:
            assert isinstance(step.step, Load)
        memory = None
        for src in step.inputs:
            if isinstance(src, str):
                memory = src
                break
        return cls(step_id=step_id, offset=step.step.offset, memory=memory, **kwargs)

    def repr_info(self) -> str:
        return f"{self.offset}('{self.memory}'))"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        """Expands to add MemoryAction if no Memory action provided"""
        if self.expanded:
            return None
        self.expanded = True

        new_actions = []
        if self.memory is None:
            random_size = ctx.random_n_width_number(32, 12) & 0xFFFFF000
            mem = MemoryAction(step_id=ctx.new_memory_id(), size=random_size, page_size=PageSize.SIZE_4K, flags=PageFlags.READ)
            print("no memory, allocating memory", mem.step_id)
            self.memory = mem.step_id
            ctx.mem_reg.allocate_data(mem.step_id, mem)
            memory_li = LiAction(step_id=ctx.new_value_id(), immediate=mem.step_id)
            self.rs1 = memory_li.step_id
            new_actions.append(memory_li)
            return new_actions
        elif ctx.mem_reg.is_memory_label(self.memory):
            # if memory is a label to a page, need to load the page base address
            # otherwise if it's a register, leave it alone
            print("memory is a label to a page, loading page base address")
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

        if new_actions:
            new_actions.append(self)
            return new_actions
        return None

    def _recurse_pick_instruction(self, ctx: LoweringContext, constraints: dict[str, Any]) -> Instruction:
        if self.op is not None:
            return ctx.instruction_catalog.get_instruction(self.op)
        instruction_choices = ctx.instruction_catalog.filter(**constraints)
        selected_instruction = ctx.rng.choice(instruction_choices)
        if "sp" in selected_instruction.name:
            # Can't have stack pointer-relative access. Try again
            selected_instruction = self._recurse_pick_instruction(ctx, constraints)
        return selected_instruction

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        "Recurisvely calls to avoid picking stack pointer-relative instructions"

        constraints: dict[str, Any] = {"category": Category.LOAD}
        if self.offset:
            constraints["has_immediate"] = True
        selected_instruction = self._recurse_pick_instruction(ctx, constraints)

        # Pick offset
        offset_operand = selected_instruction.immediate_operand()
        if offset_operand is None and self.offset:
            raise ValueError(
                f"unable to find an offset operand but offset requested? {selected_instruction.name} has source operands {selected_instruction.source} but wanted to set offset 0x{self.offset:x}"
            )
        if offset_operand is not None:
            offset_operand.val = self.offset

        rs1 = selected_instruction.get_source("rs1")
        if rs1 is None:
            raise ValueError(f"Expected a rs1 operand not available for Load action, with instruction {selected_instruction}")
        rs1.val = self.rs1

        return selected_instruction
