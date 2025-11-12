# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from coretp import Instruction
from coretp.isa.registers import Register

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class Elaborator:
    """
    Responsible for expanding flat list of ``Action`` IR into a flat list of ``Instruction`` IR objects.

    Selected instructions should have temporary registers / symbols and immediates all filled in.

    Unfilled operands will be legalized later.
    """

    def elaborate(self, actions: list[Action], ctx: LoweringContext) -> list[Instruction]:
        """
        Elaborate a list of actions into a list of instructions.

        Assigns destination register and instruction ID to each instruction.
        """
        instructions = []
        for action in actions:
            instr = action.pick_instruction(ctx)

            # Assign destination register and instruction ID
            instr.instruction_id = action.step_id
            if instr.destination is not None and instr.destination.is_register():
                # If hard-coded register, don't want to overwrite it
                if not isinstance(instr.destination.val, Register):
                    instr.destination.val = action.step_id
            instr.instruction_id = action.step_id
            instructions.append(instr)
        return instructions
