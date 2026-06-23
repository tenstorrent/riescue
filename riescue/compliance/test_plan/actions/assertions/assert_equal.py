# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import TYPE_CHECKING, Optional, Union

from coretp import Instruction, StepIR
from coretp.step import AssertEqual

from riescue.compliance.test_plan.actions import Action, LabelAction, LiAction
from riescue.compliance.test_plan.context import LoweringContext
from riescue.lib.rand import RandNum
from .assertion_base import AssertionBase, AssertionJumpToFail

logger = logging.getLogger(__name__)


class AssertEqualAction(AssertionBase):
    """
    AssertEqual action.

    Accepts either register references (``str`` step IDs) or integer immediates
    for ``val1`` / ``val2``. Immediate operands are materialized into temporary
    registers via :class:`LiAction` during :meth:`expand` because the underlying
    branch instruction (``beq``) is register-register only.
    """

    register_fields = ["val1", "val2"]

    def repr_info(self) -> str:
        return f"({self.val1} == {self.val2})"

    def __init__(self, val1: Union[str, int], val2: Union[str, int], **kwargs):
        super().__init__(**kwargs)
        self.val1 = val1
        self.val2 = val2
        self.expanded = False
        logger.debug(f"{type(self).__name__} {self}")

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssertEqual)
        if len(step.inputs) != 2:
            raise ValueError(f"{cls.__name__} {step} has {len(step.inputs)} inputs; expected exactly two")
        input0, input1 = step.inputs[0], step.inputs[1]
        if not isinstance(input0, (str, int)) or not isinstance(input1, (str, int)):
            raise ValueError(f"{cls.__name__} {step} has unsupported input types: {type(input0).__name__}, {type(input1).__name__}")
        if isinstance(input0, int) and isinstance(input1, int):
            raise ValueError(f"{cls.__name__} {step} has two immediate inputs; at least one must be a register/step reference")
        return cls(step_id=step_id, val1=input0, val2=input1, **kwargs)

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        if self.expanded:
            return None
        self.expanded = True

        # beq/bne are register-register; materialize any immediate via li first.
        new_actions: list[Action] = []
        if isinstance(self.val1, int):
            li1 = LiAction(step_id=ctx.new_value_id(), immediate=self.val1)
            self.val1 = li1.step_id
            new_actions.append(li1)
        if isinstance(self.val2, int):
            li2 = LiAction(step_id=ctx.new_value_id(), immediate=self.val2)
            self.val2 = li2.step_id
            new_actions.append(li2)

        self.pass_label = ctx.unique_label("pass_label")  # label for passing
        pass_label = LabelAction(step_id=ctx.new_label(), name=self.pass_label)
        jum_to_fail = AssertionJumpToFail(step_id=ctx.new_value_id())
        return [*new_actions, self, jum_to_fail, pass_label]

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
