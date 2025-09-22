# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from coretp.isa import Instruction, Label

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class LabelAction(Action):
    """
    Label action, returns a label instruction.

    Basic ``Action`` that doesn't need a ``step_id`` or ``inputs``.
    """

    register_fields: list[str] = []

    def __init__(self, name: str, **kwargs):
        super().__init__(**kwargs)
        self.name = name

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return Label(self.name)

    def repr_info(self) -> str:
        return f"'{self.name}'"
