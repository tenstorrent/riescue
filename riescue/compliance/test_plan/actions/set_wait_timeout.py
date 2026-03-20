# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step import SetWaitTimeout

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class SetWaitTimeoutAction(Action):
    """
    Action for SetWaitTimeout step.

    This is a simulator-only directive that sets the timeout value for wait instructions
    (WRS.STO, WFI). In generated code, it just emits a NOP since the timeout configuration
    is handled by the simulator.
    """

    register_fields = []

    def __init__(self, step_id: str, cycles: int, **kwargs):
        super().__init__(step_id=step_id)
        self.cycles = cycles

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "SetWaitTimeoutAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, SetWaitTimeout)

        return cls(step_id=step_id, cycles=step.step.cycles, **kwargs)

    def repr_info(self) -> str:
        return f"cycles={self.cycles}"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return ctx.instruction_catalog.get_instruction("nop")
