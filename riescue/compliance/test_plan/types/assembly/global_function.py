# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from coretp.isa import Instruction, Label, Register

from .block import Block, InstructionBlock, FunctionBlock
from .page import Page


class GlobalFunction(Block):
    """
    Represents a single global function, can be emitted to assembly.

    global functions start with the data header (from page) and have a list of InstructionBlocks


    RiescueD already defines the section with a symbol and defines the address.
    So adding a label to first instruction is optional; Other labels are not optional

    :param page: Page that this function is part of
    :param subroutine: initial instruction block, no label attached
    :param extra_blocks: list of instruction blocks with labels
    """

    def __init__(self, subroutine: FunctionBlock, extra_blocks: list[InstructionBlock], clobbered_registers: set[Register]):
        self.subroutine = subroutine
        self.extra_blocks = extra_blocks
        self.clobbered_registers = clobbered_registers

    @staticmethod
    def split_instructions(instructions: list[Instruction]) -> list[list[Instruction]]:
        """
        Split instructions into label separated lists of Instructions.
        """
        current_block: list[Instruction] = []
        all_instructions: list[list[Instruction]] = []
        for instruction in instructions:
            if isinstance(instruction, Label):
                all_instructions.append(current_block)
                current_block = [instruction]
            else:
                current_block.append(instruction)
        all_instructions.append(current_block)
        return all_instructions

    @classmethod
    def from_instructions(cls, page: Page, instructions: list[Instruction]) -> "GlobalFunction":
        """
        Create a GlobalFunction from a list of instructions
        """
        instruction_blocks = cls.split_instructions(instructions)
        if len(instruction_blocks) == 0:
            raise ValueError("No instructions to create global function")
        subroutine = FunctionBlock(page, instruction_blocks[0])
        extra_blocks = [InstructionBlock.from_instructions(instruction_list) for instruction_list in instruction_blocks[1:]]
        clobbered_registers = cls.find_clobbered_registers(instructions)
        return cls(subroutine, extra_blocks, clobbered_registers)

    def emit(self) -> str:
        subroutine = self.subroutine.emit()
        extra_blocks = "\n".join([block.emit() for block in self.extra_blocks])
        return subroutine + "\n" + extra_blocks

    @staticmethod
    def find_clobbered_registers(instructions: list[Instruction]) -> set[Register]:
        """
        Compute the set of registers that are clobbered by the function.

        Clobbering means the register's value is changed by the function
        """
        clobbered_registers: set[Register] = set()
        # print("computing clobbered registers")
        for instruction in instructions:
            dest = instruction.destination
            if dest is not None and dest.is_register():
                if isinstance(dest.val, Register):
                    clobbered_registers.add(dest.val)

        return clobbered_registers
