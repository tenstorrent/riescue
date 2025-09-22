# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional

from coretp import InstructionCatalog, Instruction
from coretp.isa import Label

from riescue.compliance.test_plan.actions import Action, LiAction, LoadAction
from riescue.compliance.test_plan.context import LoweringContext


class AssertionBase(Action):
    """
    Base class for all assertion actions.

    Basic assertions consist of some branch comparison, where failures should jump to local_test_failed.
    Passes should continue

    .. code-block:: asm
        branch rs1, rs2, pass_label
        j local_test_failed
        pass_label:

    """

    register_fields = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.constraints = {}

    def repr_info(self) -> str:
        return ""

    def branch_to_label(self, op: str, label: Label, instruction_catalog: InstructionCatalog) -> Instruction:
        """
        Branch to a label.

        :param op: The operation to use.
        :param label: The label to branch to.
        :param instruction_catalog: The instruction catalog to use.
        """
        selected_instruction = instruction_catalog.get_instruction(op)
        # Assign offset to pass label. Assumes label within 12-bits of current PC.
        # There's probably a better way to do this
        # But the actual instruction uses an immediate operand, and the assembler replaces the symbol with the immediate val
        # Instructions don't support operands having multiple types, so using offset name to identify the operand
        for source in selected_instruction.source:
            if source.name == "offset":
                source_branch = source
                break
        else:
            raise ValueError("offset not found in beq instruction")
        source_branch.val = label.label_name()
        return selected_instruction


class AssertionJumpToFail(AssertionBase):
    """Jump to failure"""

    register_fields = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.expanded = False

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True
        # if in a code page cant j local_test_failed, need to do li t0, local_test_failed, jalr t0
        # safest way is just to always do the li, jalr
        self.jump_to_fail = LiAction(step_id=ctx.new_value_id(), immediate="failed_addr")
        op = "ld" if ctx.env.reg_width == 64 else "lw"
        self.load_failed_addr = LoadAction(step_id=ctx.new_value_id(), memory=self.jump_to_fail.step_id, op=op)
        return [self.jump_to_fail, self.load_failed_addr, self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction("jr")
        rs1 = selected_instruction.rs1()
        if rs1 is None:
            raise ValueError("jalr instruction has no destination")
        rs1.val = self.load_failed_addr.step_id
        return selected_instruction
