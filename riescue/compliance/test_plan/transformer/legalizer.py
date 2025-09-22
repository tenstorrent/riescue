# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from coretp import Instruction
from coretp.isa import Operand
from coretp.rv_enums import Category

from riescue.compliance.test_plan.actions import Action, MemoryAction
from riescue.compliance.test_plan.context import LoweringContext
from coretp import Label


class Legalizer:
    """
    Ensures all instructions are legalized.
    Missing load immediate values are added. Inserts casting operations as needed
    """

    def legalize(self, instructions: list[Instruction], ctx: LoweringContext) -> list[Instruction]:
        """
        Legalize a list of instructions. Adds missing dependencies between instructions and fills in missing registers.

        Does so in two passes:

        1. Resolve all immediates.
        2. Insert cast instructions as needed

        e.g.

        .. code-block:: asm

            subw t0, t1, t2


        becomes

        .. code-block:: asm

            li t1, <rand value>
            li t2, <rand value>
            subw t0, t1, t2

        """
        first_pass_instructions = self.resolve_li_immediates(instructions, ctx)
        second_pass_instructions = self.insert_casts(first_pass_instructions, ctx)
        return second_pass_instructions

    def resolve_li_immediates(self, instructions: list[Instruction], ctx: LoweringContext) -> list[Instruction]:
        resolved_ids = set()  # All instructions that have been resolved. All IDs should be set before they appear as a source register

        instructions_with_li: list[Instruction] = []
        for i, instruction in enumerate(instructions):
            if instruction.instruction_id in resolved_ids:
                raise ValueError(f"Duplicate instruction ID {instruction.instruction_id} in {instruction=}")
            resolved_ids.add(instruction.instruction_id)
            li_instructions = self._resolve_li_immediates(instruction, ctx, resolved_ids)

            # Check if we need to handle a preceding label
            if i > 0 and isinstance(instructions[i - 1], Label) and len(li_instructions) > 1:

                # The previous instruction (label) is the last item in our output
                preceding_label = instructions_with_li[-1]  # Get (don't remove yet)

                # Remove the label and reorganize: li_instructions -> label -> target_instruction
                instructions_with_li.pop()  # Remove the label
                li_only = li_instructions[:-1]  # All li instructions
                target_instruction = li_instructions[-1]  # The original instruction

                # Add in correct order: li first, then label, then target
                instructions_with_li.extend(li_only)
                instructions_with_li.append(preceding_label)
                instructions_with_li.append(target_instruction)
            else:
                instructions_with_li.extend(li_instructions)

        return instructions_with_li

    def _resolve_li_immediates(self, instruction: Instruction, ctx: LoweringContext, resolved_ids: set[str]) -> list[Instruction]:
        """
        Checks if load instructions are needed for a given instruction.
        Returns list of instructions with load immediates inserted before given instruction
        """
        new_instructions: list[Instruction] = []
        for src in instruction.source:
            if src.is_register() and isinstance(src.val, str) and src.val not in resolved_ids:
                li_instruction = self._load_immediate(ctx)
                li_dest = li_instruction.destination
                if li_dest is None:
                    raise ValueError(f"li instruction {li_instruction} has no destination")
                li_dest.val = src.val
                li_instruction.instruction_id = src.val

                new_instructions.append(li_instruction)
        new_instructions.append(instruction)
        return new_instructions

    def _load_immediate(self, ctx: LoweringContext) -> Instruction:
        """
        Generate a li instruction.
        """
        selected_instruction = ctx.instruction_catalog.get_instruction("li")

        # FIXME: Should probably check if this is a 64-bit or 32-bit register somehow? Should this info be in ctx?
        # And also check if it's floating point or not, and generate a more interesting li instruction later

        random_load_value = ctx.random_n_width_number(32)

        imm = selected_instruction.get_source("imm")
        if imm is None:
            raise ValueError(f"li instruction {selected_instruction} has no imm field")
        imm.val = random_load_value
        return selected_instruction

    def insert_casts(self, instructions: list[Instruction], ctx: LoweringContext) -> list[Instruction]:
        """
        Insert cast instructions as needed.
        """
        casted_instructions = []

        dest_registers: dict[str, Instruction] = {instr.instruction_id: instr for instr in instructions}

        for instruction in instructions:
            new_instructions = self._resolve_casts(instruction, dest_registers, ctx)
            casted_instructions.extend(new_instructions)

        return casted_instructions

    def _resolve_casts(self, instruction: Instruction, dest_registers: dict[str, Instruction], ctx: LoweringContext) -> list[Instruction]:
        """
        Resolves casts for a given instruction. Prepends cast right before instruction if needed.

        If no cast needed returns original instruction. If cast needed returns [cast, instr]
        """

        new_instructions: list[Instruction] = []
        for src in instruction.source:
            if src.is_register() and isinstance(src.val, str):
                if src.val in dest_registers:
                    producer_instr = dest_registers[src.val]
                    producer_dest = producer_instr.destination
                    if producer_dest is None:
                        raise ValueError(f"Destination {producer_instr} has no destination")
                    if producer_dest.type == src.type:
                        continue  # if cast is not needed just continue
                    cast_instr_choices = ctx.instruction_catalog.filter(
                        destination_type=src.type,
                        source_type=producer_dest.type,
                        category=Category.CAST,
                    )
                    if len(cast_instr_choices) == 0:
                        raise ValueError(f"No cast instructions found for {producer_instr.name} {producer_dest.type} -> {src.type}, \n{src.val} in {instruction=}")

                    # currently just going to pick one. This should probably be a bit more intelligent later (getting word vs double etc)
                    cast_instr = ctx.rng.choice(cast_instr_choices)

                    self._rewire_cast_instruction(cast_instr, producer_instr, src, ctx, "rs1")
                    new_instructions.append(cast_instr)

        new_instructions.append(instruction)
        return new_instructions

    def _rewire_cast_instruction(
        self,
        cast_instr: Instruction,
        in_instr: Instruction,
        out_operand: Operand,
        ctx: LoweringContext,
        src_operand_name: str = "rs1",
    ):
        """
        Rewires a src_instr -> dest_instr into a src_instr -> cast_instr -> dest_instr.

        Modifies instructions in place.

        :param cast_instr: The cast instruction to rewire
        :param in_instr: The source or initial instruction
        :param out_operand: The operand that needs to be updated to use casted value
        :param src_operand_name: The name of the cast instruction's source operand to rewire, e.g. ``rs1``
        """

        # set new instruction id for cast, temp register ID for cast destination
        cast_instr.instruction_id = ctx.new_value_id()
        cast_destination_operand = cast_instr.destination
        if cast_destination_operand is None:
            raise ValueError(f"Cast instruction {cast_instr} has no destination operand")
        cast_destination_operand.val = cast_instr.instruction_id

        # wire up input to cast
        in_dest_operand = in_instr.destination
        if in_dest_operand is None:
            raise ValueError(f"Source instruction {in_instr} has no destination operand")
        cast_source_operand = cast_instr.get_source(src_operand_name)
        if cast_source_operand is None:
            raise ValueError(f"Cast instruction {cast_instr} has no source operand for {src_operand_name}")
        cast_source_operand.val = in_dest_operand.val

        # Wire cast to output
        out_operand.val = cast_destination_operand.val
