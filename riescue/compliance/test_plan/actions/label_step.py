# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Action that lowers the coretp ``Label`` TestStep into a label assembly
instruction at the current position in the generated test.

Distinct from the existing ``LabelAction`` which is an internal helper used
by Actions like ``AssertExceptionAction``; this one is the public-facing
TestStep→Action binding registered in ``mappings.py``.
"""

from typing import TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.isa import Label as LabelInstruction
from coretp.step import Label

from riescue.compliance.test_plan.actions.action import Action
from riescue.compliance.test_plan.context import LoweringContext


class LabelTestStepAction(Action):
    """Emit a named assembly label at this point in the test."""

    register_fields: list[str] = []

    def __init__(self, step_id: str, name: str, **kwargs):
        super().__init__(step_id=step_id)
        self.name = name

    def repr_info(self) -> str:
        return f"'{self.name}'"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "LabelTestStepAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, Label)
        return cls(step_id=step_id, name=step.step.name)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        # instruction_pointer=False: this label is a plain address marker, not a
        # fault label. The legalizer defers `instruction_pointer=True` labels
        # until the next non-label instruction (see legalizer.insert_casts),
        # which causes back-to-back labels to overwrite each other.
        return LabelInstruction(self.name, instruction_pointer=False)
