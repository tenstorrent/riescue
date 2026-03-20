# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step import Directive
from coretp.rv_enums import Category, Extension, Xlen

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class DirectiveAction(Action):
    """
    Action that prints a directive string directly to the .S file.
    """

    register_fields = []

    def __init__(self, step_id: str, directive: str, **kwargs):
        super().__init__(step_id=step_id)
        self.directive = directive

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "DirectiveAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, Directive)

        return cls(step_id=step_id, directive=step.step.directive, **kwargs)

    def repr_info(self) -> str:
        return f"directive='{self.directive}'"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return DirectiveInstruction(directive=self.directive)


class DirectiveInstruction(Instruction):
    """
    Custom instruction that outputs a directive string directly.
    """

    def __init__(self, directive: str):
        super().__init__(
            name="directive",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,
            source=[],
            clobbers=[],
        )
        self.directive = directive

    def format(self):
        return self.directive
