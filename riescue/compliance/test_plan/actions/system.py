# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Union, Optional, TYPE_CHECKING

from coretp import Instruction
from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext
from coretp.step import System
from coretp import StepIR


class SystemAction(Action):
    """
    System Instruction Action. Used for simpler system instructions that dont require any operands.
    """

    register_fields = []

    def __init__(self, step_id: str, instruction: str):
        super().__init__(step_id=step_id)
        self.instruction = instruction

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, System)
        instruction = step.step.instruction
        if instruction == "":
            raise ValueError(f"System instruction is empty for step {step_id}")
        return cls(step_id=step_id, instruction=instruction)

    def repr_info(self) -> str:
        return self.instruction

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction(self.instruction)
        return selected_instruction
