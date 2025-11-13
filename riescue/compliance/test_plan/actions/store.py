# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Any, Optional

from coretp import TestEnv, InstructionCatalog, Instruction, StepIR
from coretp.step import Store, Memory
from coretp.rv_enums import Category, PageSize, PageFlags, Extension

from riescue.compliance.test_plan.actions import Action, ArithmeticAction, LiAction
from riescue.compliance.test_plan.actions.memory import MemoryAction
from riescue.compliance.test_plan.context import LoweringContext


class StoreAction(Action):
    """
    Action generating some store instruction.

    """

    register_fields = ["value", "memory"]

    def __init__(
        self,
        offset: int = 0,
        memory: Optional[str] = None,
        value: Optional[str] = None,
        access_size: Optional[int] = None,
        op: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.offset = offset
        self.memory = memory
        self.value = value

        self.aligned = True  # TODO: Add support for unaligned loads
        self.expanded = False
        self.access_size = access_size
        if self.access_size is not None and self.access_size not in [1, 2, 4, 8]:
            raise ValueError(f"Invalid access size: {self.access_size}")
        self.op = op

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "StoreAction":
        """
        Create a LoadAction from a StepIR object.
        """
        if TYPE_CHECKING:
            assert isinstance(step.step, Store)
        memory = None
        value = None

        # Really need a better way to do this
        for src in step.inputs:
            if isinstance(src, str) and src.startswith("m"):
                memory = src
            elif isinstance(src, str) and not src.startswith("m"):
                value = src

        return cls(step_id=step_id, offset=step.step.offset, memory=memory, value=value, **kwargs)

    def repr_info(self) -> str:
        return f"{self.offset}('{self.memory}')"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        """Expands to add MemoryAction if no Memory action provided"""
        if self.expanded:
            return None
        self.expanded = True

        new_actions = []

        if self.memory is None:
            random_size = ctx.random_n_width_number(32, 12) & 0xFFFFF000
            # Allocate memory page, place in memory registry
            mem = MemoryAction(step_id=ctx.new_memory_id(), size=random_size, page_size=PageSize.SIZE_4K, flags=PageFlags.READ)
            ctx.mem_reg.allocate_data(mem.step_id, mem)
            memory_li = LiAction(step_id=ctx.new_value_id(), immediate=mem.step_id)
            self.memory = memory_li.step_id
            new_actions.append(memory_li)
            return new_actions
        else:
            memory_li = LiAction(step_id=ctx.new_value_id(), immediate=self.memory)
            self.memory = memory_li.step_id
            new_actions.append(memory_li)

        if not (-2048 <= self.offset <= 2047):
            li = LiAction(step_id=ctx.new_value_id(), immediate=self.offset)
            add = ArithmeticAction(step_id=ctx.new_value_id(), op="add", src1=self.memory, src2=li.step_id)  # Should be able to select addi x1, (num>2^12) and it shoudl handle the li
            self.memory = add.step_id  # rewire
            self.offset = 0  # replace offset with 0
            new_actions.append(li)
            new_actions.append(add)

        if new_actions:
            new_actions.append(self)
            return new_actions

        return None

    def _recurse_pick_instruction(self, ctx: LoweringContext, constraints: dict[str, Any], access_size_instr: Optional[str]) -> Instruction:
        instruction_choices = ctx.instruction_catalog.filter(**constraints)
        selected_instruction = ctx.rng.choice(instruction_choices)
        if access_size_instr is not None:
            instruction_choices = [i for i in instruction_choices if i.name.endswith(access_size_instr)]
        if self.op is not None:
            instruction_choices = [i for i in instruction_choices if i.name == self.op]
            if len(instruction_choices) == 0:
                raise ValueError(f"No instruction found for op: {self.op}")
            return instruction_choices[0]
        if "sp" in selected_instruction.name or "sc" in selected_instruction.name:
            # Can't have stack pointer-relative access. Try again
            selected_instruction = self._recurse_pick_instruction(ctx, constraints, access_size_instr)
        return selected_instruction

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        "Recurisvely calls to avoid picking stack pointer-relative instructions"
        constraints: dict[str, Any] = {"category": Category.STORE, "exclude_extensions": Extension.A | Extension.SVINVAL}
        if self.offset:
            constraints["has_immediate"] = True
        access_size_instr = None
        if self.access_size == 1:
            access_size_instr = "b"
        elif self.access_size == 2:
            access_size_instr = "h"
        elif self.access_size == 4:
            access_size_instr = "w"
        elif self.access_size == 8:
            access_size_instr = "d"
        selected_instruction = self._recurse_pick_instruction(ctx, constraints, access_size_instr)

        # Pick offset
        offset_operand = selected_instruction.immediate_operand()
        if offset_operand is None and self.offset:
            raise ValueError(f"unable to find an offset operand but offset requested? {selected_instruction.name} has source operands {selected_instruction.source}")
        if offset_operand is not None:
            offset_operand.val = self.offset

        rs1 = selected_instruction.get_source("rs1")  # Addr
        if rs1 is None:
            raise ValueError(f"Expected a rs1 operand not available for Store action, with instruction {selected_instruction}")

        if self.memory is None:
            raise NotImplementedError("No memory provided for StoreAction, need to implement memory allocation")
        rs1.val = self.memory

        rs2 = selected_instruction.get_source("rs2")  # Value
        if rs2 is None:
            raise ValueError(f"Expected an addr rs2 operand but not available for Store action, with instruction {selected_instruction}")
        if self.value is None:
            self.value = ctx.new_value_id()  # Use random value if no store value provided
        rs2.val = self.value

        # Pick value
        return selected_instruction
