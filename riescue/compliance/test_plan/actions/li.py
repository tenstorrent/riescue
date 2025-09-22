# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Union

from coretp import Instruction
from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class LiAction(Action):
    """
    Generic Load Immediate Action

    For now returns li instruction for integer registers
    """

    register_fields = []

    def __init__(self, step_id: str, immediate: Union[int, str]):
        super().__init__(step_id=step_id)
        self.value = immediate

    def repr_info(self) -> str:
        if isinstance(self.value, str):
            return self.value
        elif isinstance(self.value, int):
            return f"0x{self.value:x}"
        else:
            raise ValueError(f"Invalid value for li: {self.value}")

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction("li")

        if self.value is None:
            self.value = ctx.random_n_width_number()
        if len(selected_instruction.source) != 1:
            raise ValueError(f"Expected li to have 1 source operand, but {selected_instruction.name} has {selected_instruction.source}")
        selected_instruction.source[0].val = self.value
        return selected_instruction
