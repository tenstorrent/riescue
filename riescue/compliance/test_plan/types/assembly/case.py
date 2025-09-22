# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Union

from coretp.isa import Instruction, Label

from .block import Block, InstructionBlock, TextBlock


class TestCase(Block):
    """
    Top-level block that builds up Segments.
    Contains a sequence of InstructionBlocks and TextBlocks.
    Can be built from instructions
    """

    def __init__(self, blocks: list[Union[InstructionBlock, TextBlock]]):
        self.blocks = blocks

    @staticmethod
    def split_instructions(instructions: list[Instruction]) -> list[list[Instruction]]:
        """
        Split instructions into label separated lists of Instructions.
        """
        current_block: list[Instruction] = []
        all_instructions: list[list[Instruction]] = []
        for instruction in instructions:
            if isinstance(instruction, Label):
                if current_block:
                    all_instructions.append(current_block)
                current_block = [instruction]
            else:
                current_block.append(instruction)
        all_instructions.append(current_block)
        return all_instructions

    @classmethod
    def from_instructions(cls, instructions: list[Instruction], header: str = "") -> "TestCase":
        """
        Create a series of InstructionBlocks from a list of instructions.
        Assigns header to first InstructionBlock
        """
        instruction_lists = cls.split_instructions(instructions)
        if len(instruction_lists) == 0:
            raise ValueError("No instructions to create global function")

        instruction_blocks: list[Union[InstructionBlock, TextBlock]] = [InstructionBlock.from_instructions(instruction_list) for instruction_list in instruction_lists]
        if isinstance(instruction_blocks[0], InstructionBlock):
            instruction_blocks[0].header = header
        return cls(instruction_blocks)

    @classmethod
    def from_text(cls, text: list[str]) -> "TestCase":
        return cls([TextBlock(text=text)])

    def emit(self) -> str:
        return "\n" + "\n".join([block.emit() for block in self.blocks])
