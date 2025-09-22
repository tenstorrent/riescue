# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional

from coretp import Instruction, StepIR
from coretp.step import Call
from coretp.isa import RISCV_REGISTERS
from riescue.compliance.test_plan.actions import Action, LiAction
from riescue.compliance.test_plan.context import LoweringContext


class CallAction(Action):
    """
    Jump to remote code page. Defines the j / jalr / call instructions


    """

    register_fields = ["target"]

    def __init__(self, target: str, **kwargs):
        super().__init__(**kwargs)
        self.target = target
        self.expanded = False
        self.rs1 = Optional[str]

    def repr_info(self) -> str:
        return f"ra, 0({self.target})"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "CallAction":
        if TYPE_CHECKING:
            assert isinstance(step, Call)
        if not len(step.inputs) == 1:
            raise ValueError(f"Expected a single memory reference as input, got {len(step.inputs)}, {step.inputs}")
        target_id = step.inputs[0]
        if not isinstance(target_id, str):
            raise ValueError(f"Expected a memory label as input, got {target_id}")
        return cls(step_id=step_id, target=target_id, **kwargs)

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        """
        Expand call to a `li reg, addr` + `jalr rd, 0(reg)`
        """

        if self.expanded:
            return None
        self.expanded = True
        li_id = ctx.new_value_id()
        # create a li for target address
        li_action = LiAction(step_id=li_id, immediate=self.target)
        # re-wire rs1 to li_action's register
        self.rs1 = li_id
        return [li_action, self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction("jalr_ra")
        # lazy workaround to make sure ra isn't getting overwritten
        rs1 = selected_instruction.rs1()
        if rs1 is None:
            raise ValueError("No rs1 for jalr but need register to set")
        if self.rs1 is None:
            raise ValueError("rs1 was never set")
        if TYPE_CHECKING:
            assert isinstance(self.rs1, str), "typehint"
        rs1.val = self.rs1
        return selected_instruction
