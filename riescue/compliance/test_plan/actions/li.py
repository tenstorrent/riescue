# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Union, Optional, TYPE_CHECKING

from coretp import Instruction
from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext
from coretp.step import LoadImmediateStep, Memory, TestStep
from coretp import StepIR


class LiAction(Action):
    """
    Generic Load Immediate Action

    For now returns li instruction for integer registers
    """

    register_fields = ["value"]

    def __init__(
        self,
        step_id: str,
        immediate: Union[int, str],
        bits: Optional[int] = None,
    ):
        super().__init__(step_id=step_id)
        self.value = immediate
        self.bits = bits

    def repr_info(self) -> str:
        if isinstance(self.value, str):
            return self.value
        elif isinstance(self.value, int):
            return f"0x{self.value:x}"
        else:
            raise ValueError(f"Invalid value for li: {self.value}")

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction("li")

        if self.bits is not None:
            self.value = ctx.random_n_width_number(self.bits)
        elif self.value is None:
            self.value = ctx.random_n_width_number()

        if len(selected_instruction.source) != 1:
            raise ValueError(f"Expected li to have 1 source operand, but " f"{selected_instruction.name} has " f"{selected_instruction.source}")

        selected_instruction.source[0].val = self.value
        return selected_instruction

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, LoadImmediateStep)

        imm_raw = step.step.imm
        bits = step.step.bits

        if isinstance(imm_raw, Memory):
            # imm is a Memory step — load its base address via the resolved memory label
            mem_label = next((i for i in step.inputs if isinstance(i, str)), None)
            if mem_label is None:
                raise ValueError(f"LoadImmediateStep {step_id} references a Memory but no memory label found in inputs")
            return cls(step_id=step_id, immediate=mem_label, bits=bits)
        elif isinstance(imm_raw, TestStep):
            raise ValueError(f"LoadImmediateStep {step_id} has unsupported TestStep type as imm: {type(imm_raw).__name__}")

        imm = imm_raw or 0
        return cls(step_id=step_id, immediate=imm, bits=bits)
