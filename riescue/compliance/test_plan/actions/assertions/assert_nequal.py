# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Optional, TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step import AssertNotEqual
from coretp.isa import Label

from riescue.compliance.test_plan.actions import Action
from .assert_equal import AssertEqualAction
from riescue.compliance.test_plan.context import LoweringContext


import logging

logger = logging.getLogger(__name__)


class AssertNotEqualAction(AssertEqualAction):
    """
    AssertNotEqual action.
    """

    register_fields = ["val1", "val2"]

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssertNotEqual)
        if not len(step.inputs) == 2:
            raise ValueError(f"AssertNotEqual action {step} has {len(step.inputs)} inputs. Only supports two inputs, no immediates")
        if isinstance(step.inputs[0], str) and isinstance(step.inputs[1], str):
            val1 = step.inputs[0]
            val2 = step.inputs[1]
        else:
            raise ValueError(f"AssertNotEqual action {step} has {step.inputs[0]} as first input. Only supports string inputs, no immediates")
        return cls(step_id=step_id, val1=val1, val2=val2, **kwargs)

    def repr_info(self) -> str:
        return f"{self.val1} != {self.val2}"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        bne = ctx.instruction_catalog.get_instruction("bne")
        self.rewire_assert_instruction(bne)
        return bne
