# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step import AssignRandomEventToCounter

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class AssignRandomEventToCounterAction(Action):
    """
    Default action for AssignRandomEventToCounter.

    Emits a NOP. Real behavior is provided by a conf-file overload
    """

    register_fields = []

    def __init__(self, step_id: str, csr_name: str, **kwargs):
        super().__init__(step_id=step_id)
        self.csr_name = csr_name

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "AssignRandomEventToCounterAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssignRandomEventToCounter)

        return cls(step_id=step_id, csr_name=step.step.csr_name, **kwargs)

    def repr_info(self) -> str:
        return f"csr_name={self.csr_name}"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return ctx.instruction_catalog.get_instruction("nop")
