# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional

from coretp import Instruction, StepIR
from coretp.step import SetWaitTimeout
from coretp.rv_enums import Category, Extension, Xlen, OperandType
from coretp.isa.operands import Operand
from coretp.isa import get_register

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class ImplementationSetWaitTimeoutAction(Action):
    """
    This is a dummy action for implementation-specific SetWaitTimeout step.
    """

    register_fields = []

    def __init__(self, step_id: str, cycles: int, **kwargs):
        super().__init__(step_id=step_id)
        self.cycles = cycles
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ImplementationSetWaitTimeoutAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, SetWaitTimeout)

        return cls(step_id=step_id, cycles=step.step.cycles, **kwargs)

    def repr_info(self) -> str:
        return f"cycles={self.cycles}"

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        return [self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        """
        Generate csr_rw directive for c_wfitimer.
        """
        instruction_id = ctx.new_value_id()
        return ImplementationSetWaitTimeoutInstruction()


class ImplementationSetWaitTimeoutInstruction(Instruction):
    """
    Dummy Instruction for implementation-specific SetWaitTimeout step.
    """

    def __init__(self, instruction_id: str = ""):

        super().__init__(
            name="nop",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,
            source=[],
            clobbers=[],
            instruction_id=instruction_id,
        )

    def format(self):
        return "nop"
