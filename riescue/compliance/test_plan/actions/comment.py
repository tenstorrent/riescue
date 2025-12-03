# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step import Comment
from coretp.rv_enums import Category, Extension, Xlen

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class CommentAction(Action):
    """
    Action that generates a comment in the test output.
    """

    register_fields = []

    def __init__(self, step_id: str, comment: str, **kwargs):
        super().__init__(step_id=step_id)
        self.comment = comment

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "CommentAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, Comment)

        return cls(step_id=step_id, comment=step.step.comment, **kwargs)

    def repr_info(self) -> str:
        return f"comment='{self.comment}'"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return TPCommentInstruction(comment=self.comment)


class TPCommentInstruction(Instruction):
    """
    Custom instruction that generates a comment directive in the test output.
    """

    def __init__(self, comment: str):
        super().__init__(
            name="comment",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,
            source=[],
            clobbers=[],
        )
        self.comment = comment

    def format(self):
        """Generate the comment directive."""
        return f"# {self.comment}"
