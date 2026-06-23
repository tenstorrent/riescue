# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step import AssertNotEqual

from riescue.compliance.test_plan.actions import Action
from .assert_equal import AssertEqualAction
from riescue.compliance.test_plan.context import LoweringContext


import logging

logger = logging.getLogger(__name__)


class AssertNotEqualAction(AssertEqualAction):
    """
    AssertNotEqual action.

    Inherits immediate-lowering behavior from :class:`AssertEqualAction`; differs
    only in selecting ``bne`` instead of ``beq``.
    """

    register_fields = ["val1", "val2"]

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssertNotEqual)
        if len(step.inputs) != 2:
            raise ValueError(f"{cls.__name__} {step} has {len(step.inputs)} inputs; expected exactly two")
        input0, input1 = step.inputs[0], step.inputs[1]
        if not isinstance(input0, (str, int)) or not isinstance(input1, (str, int)):
            raise ValueError(f"{cls.__name__} {step} has unsupported input types: {type(input0).__name__}, {type(input1).__name__}")
        if isinstance(input0, int) and isinstance(input1, int):
            raise ValueError(f"{cls.__name__} {step} has two immediate inputs; at least one must be a register/step reference")
        return cls(step_id=step_id, val1=input0, val2=input1, **kwargs)

    def repr_info(self) -> str:
        return f"{self.val1} != {self.val2}"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        bne = ctx.instruction_catalog.get_instruction("bne")
        self.rewire_assert_instruction(bne)
        return bne
