# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field
from typing import Optional

from coretp.isa import Instruction, Label, Register
from coretp.rv_enums import RegisterClass

from riescue.compliance.test_plan.context import LoweringContext
from .register_pool import RegisterPool
from .linear_scan import LinearScan
from .types import BasicBlock

logger = logging.getLogger(__name__)


"""
LLVM pseudo code:

for each function in module:
    build CFG from basic blocks
    perform liveness analysis to determine live ranges of variables
    build interference graph where nodes are variables, edges mean overlapping live ranges
    color the interference graph (assign registers so no two connected nodes share a register)
    for each variable:
        if assigned a register:
            map variable to register
        else:
            spill variable to stack (insert load/store as needed)
    rewrite instructions to use assigned registers or stack slots


Simpler approach, build CFG from basic blocks

"""


class Allocator:
    """
    Allocate registers for instructions
    """

    def __init__(self):
        pass

    def allocate(
        self,
        instructions: list[Instruction],
        ctx: LoweringContext,
    ) -> list[Instruction]:
        """
        Allocate memory, symbols and registers

        :param test_name: Name of the test
        :param instructions: List of instructions to allocate
        :param ctx: Lowering context
        :return: List of subroutines
        """

        logger.debug("Allocating registers")
        linear_scan = LinearScan(instructions, ctx)
        return linear_scan.allocate()

    def liveness_analysis(self, basic_blocks: list[BasicBlock]) -> list[BasicBlock]:
        """
        Perform liveness analysis on the basic blocks.
        This can be used to build up an interference graph, but currently unused.
        """
        for block in reversed(basic_blocks):
            for instr in block.instructions:
                for src in instr.source:
                    if src.is_register() and isinstance(src.val, str):
                        block.live_in.add(src.val)
                if instr.destination is not None and instr.destination.is_register() and isinstance(instr.destination.val, str):
                    block.live_out.add(instr.destination.val)
        return basic_blocks

    def split_into_basic_blocks(self, instructions: list[Instruction]) -> list[BasicBlock]:
        """
        Split instructions into basic blocks.
        BasicBlock is just a list of instructions separated by labels or jumps.
        Naively assuming jalr and Label are only things to worry about.

        Assuming that other symbols are resolved before this pass? I.e calls to other functions have been resolved.
        The correct way to do this is to force calling convention, but that's a little out of the scope
        """
        basic_blocks: list[BasicBlock] = []
        current_block: list[Instruction] = []
        block_name = None
        for instruction in instructions:
            if isinstance(instruction, Label):
                basic_blocks.append(BasicBlock(instructions=current_block, label=block_name))
                current_block = [instruction]
                block_name = instruction.name
            elif isinstance(instruction, Instruction):
                current_block.append(instruction)
        basic_blocks.append(BasicBlock(instructions=current_block, label=block_name))
        return basic_blocks

    def register_allocator(self, instructions: list[Instruction], ctx: LoweringContext) -> list[Instruction]:
        """
        Allocate registers for a list of instructions. Modifies in place.
        Should be last pass since other steps might add new instructions
        """

        last_uses = self._compute_last_uses(instructions)
        reg_pool = RegisterPool(ignore_registers_filters=(RegisterClass.special,), ctx=ctx)

        for i in instructions:
            dest_reg = i.destination
            if dest_reg is not None and dest_reg.is_register():
                if isinstance(dest_reg.val, str):
                    # allocate a register for the destination
                    dest_reg.val = reg_pool.allocate(dest_reg.type, i.instruction_id)

            # if source operands, they should have already been allocated
            for src in i.source:
                if src.is_register() and isinstance(src.val, str):
                    temp_reg_name = src.val
                    src.val = reg_pool.get_reg_from_temp_name(temp_reg_name)

                    last_use = last_uses.get(temp_reg_name, None)
                    if last_use is None:
                        raise ValueError(f"No last use found for {temp_reg_name}")
                    if i.instruction_id == last_use:
                        reg_pool.free(temp_reg_name)
        return instructions

    def _compute_last_uses(self, instructions: list[Instruction]) -> dict[str, str]:
        """
        Calculates the last node that consumes each register. Creates a map of {temp_reg_name: last_use_id}.
        """
        last_uses: dict[str, str] = {}
        for instr in instructions:
            for src in instr.source:
                if src.is_register() and isinstance(src.val, str):
                    last_uses[src.val] = instr.instruction_id

        return last_uses
