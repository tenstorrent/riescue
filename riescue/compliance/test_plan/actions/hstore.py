# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional

from coretp import Instruction, StepIR
from coretp.step.load_store.hstore import HStore
from coretp.rv_enums import Category, PageSize, PageFlags, Extension

from riescue.compliance.test_plan.actions import Action, LiAction
from riescue.compliance.test_plan.actions.memory import MemoryAction
from riescue.compliance.test_plan.context import LoweringContext


class HStoreAction(Action):
    """
    Handles hypervisor store (hsv.*) instructions.
    Like StoreAction but without offset support.
    """

    register_fields = ["value", "memory"]

    def __init__(
        self,
        memory: Optional[str] = None,
        value: Optional[str] = None,
        access_size: Optional[int] = None,
        op: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.memory = memory
        self.value = value
        self.expanded = False
        self.access_size = access_size
        if self.access_size is not None and self.access_size not in [1, 2, 4, 8]:
            raise ValueError(f"Invalid access size: {self.access_size}")
        self.op = op

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "HStoreAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, HStore)
        memory = None
        value = None

        for src in step.inputs:
            if isinstance(src, str) and src.startswith("m"):
                memory = src
            elif isinstance(src, str) and not src.startswith("m"):
                value = src

        return cls(
            step_id=step_id,
            memory=memory,
            value=value,
            access_size=step.step.access_size,
            op=step.step.op,
            **kwargs,
        )

    def repr_info(self) -> str:
        return f"({self.memory})"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True

        new_actions = []

        if self.memory is None:
            random_size = ctx.random_n_width_number(32, 13) & 0xFFFFF000
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

        if new_actions:
            new_actions.append(self)
            return new_actions

        return None

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        if self.op is not None:
            selected_instruction = ctx.instruction_catalog.get_instruction(self.op)
        else:
            instruction_choices = ctx.instruction_catalog.filter(
                category=Category.HYPERVISOR | Category.CONTROL,
                extension=Extension.H,
            )
            instruction_choices = [i for i in instruction_choices if i.name.startswith("hsv.")]

            if self.access_size is not None:
                size_map = {1: "b", 2: "h", 4: "w", 8: "d"}
                size_char = size_map.get(self.access_size)
                if size_char is not None:
                    instruction_choices = [i for i in instruction_choices if i.name.split(".")[-1][0] == size_char]

            selected_instruction = ctx.rng.choice(instruction_choices)

        rs1 = selected_instruction.get_source("rs1")
        if rs1 is None:
            raise ValueError(f"Expected a rs1 operand not available for HStore action, with instruction {selected_instruction}")
        if self.memory is None:
            raise NotImplementedError("No memory provided for HStoreAction, need to implement memory allocation")
        rs1.val = self.memory

        rs2 = selected_instruction.get_source("rs2")
        if rs2 is None:
            raise ValueError(f"Expected a rs2 operand not available for HStore action, with instruction {selected_instruction}")
        if self.value is None:
            self.value = ctx.new_value_id()
        rs2.val = self.value

        return selected_instruction
