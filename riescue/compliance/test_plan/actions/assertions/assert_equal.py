# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import TYPE_CHECKING, Optional

from coretp import Instruction, StepIR
from coretp.step import AssertEqual

from riescue.compliance.test_plan.actions import Action, LabelAction
from riescue.compliance.test_plan.context import LoweringContext
from riescue.lib.rand import RandNum
from .assertion_base import AssertionBase, AssertionJumpToFail

logger = logging.getLogger(__name__)


class AssertEqualAction(AssertionBase):
    """
    AssertEqual action.
    """

    register_fields = ["val1", "val2"]

    def repr_info(self) -> str:
        return f"({self.val1} == {self.val2})"

    def __init__(self, val1: str, val2: str, **kwargs):
        super().__init__(**kwargs)
        self.val1 = val1
        self.val2 = val2
        self.expanded = False
        logger.debug(f"AssertNotEqualAction {self}")

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssertEqual)
        if not len(step.inputs) == 2:
            raise ValueError(f"AssertNotEqual action {step} has {len(step.inputs)} inputs. Only supports two inputs, no immediates")
        if isinstance(step.inputs[0], str) and isinstance(step.inputs[1], str):
            val1 = step.inputs[0]
            val2 = step.inputs[1]
        else:
            raise ValueError(f"AssertNotEqual action {step} has {step.inputs[0]} as first input. Only supports string inputs, no immediates")
        return cls(step_id=step_id, val1=val1, val2=val2, **kwargs)

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        if self.expanded:
            return None
        self.expanded = True

        self.pass_label = ctx.unique_label("pass_label")  # label for passing
        pass_label = LabelAction(step_id=ctx.new_label(), name=self.pass_label)
        jum_to_fail = AssertionJumpToFail(step_id=ctx.new_value_id())
        return [self, jum_to_fail, pass_label]

    def rewire_assert_instruction(self, instr: Instruction):
        "Wires offset, rs1, and rs2 in branch instruction"
        # Set offset operand
        offset_operand = instr.get_source("offset")
        if offset_operand is None:
            raise ValueError("bne instruction has no offset operand")
        offset_operand.val = self.pass_label

        rs1 = instr.get_source("rs1")
        if rs1 is None:
            raise ValueError("bne instruction has no rs1 operand")
        rs1.val = self.val1

        rs2 = instr.get_source("rs2")
        if rs2 is None:
            raise ValueError("bne instruction has no rs2 operand")
        rs2.val = self.val2

        logger.debug(f"Picked instruction {instr}")
        return instr

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        beq = ctx.instruction_catalog.get_instruction("beq")
        self.rewire_assert_instruction(beq)
        return beq
