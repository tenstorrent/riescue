# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Any

from coretp.isa import Instruction, Label

from .base import AssemblyBase
from .page import Page


class Block(AssemblyBase):
    """
    Base block of a generated assembly test. Can contain instructions, data, etc.
    """

    pass


class InstructionBlock(Block):
    """
    Contains labels, instruction, and an optional header
    """

    def __init__(self, label: str, instructions: list[Instruction], header: str = ""):
        self.label = label
        self.instructions = instructions
        self.header = header

    @classmethod
    def from_instructions(cls, instructions: list[Instruction], header: str = "") -> "InstructionBlock":
        """
        Create an InstructionBlock from a list of instructions. Assumes that the first instruction has a label.
        """
        if len(instructions) == 0:
            raise ValueError("No instructions to create instruction block")
        if not isinstance(instructions[0], Label):
            raise ValueError(f"First instruction must be a label but got {instructions[0]}, {type(instructions[0])}")
        return cls(instructions[0].label_name(), instructions[1:], header)

    def emit(self) -> str:
        if self.header:
            return self.header + "\n" + self.label + ":\n\t" + "\n\t".join([instruction.format() for instruction in self.instructions])
        elif self.instructions:
            return self.label + ":\n\t" + "\n\t".join([instruction.format() for instruction in self.instructions])
        else:
            return self.label + ":"


class TextBlock(Block):
    """
    Contains a list of strings, used for hard-coded text and comments.

    If label, adds tabs to each line in text, otherwise just joins text with newlines
    """

    def __init__(self, text: list[str], label: str = ""):
        self.text = text
        self.label = label

    def emit(self) -> str:
        if self.label:
            return self.label + ":\n\t" + "\n\t".join(self.text)
        else:
            return "\n".join(self.text)


class FunctionBlock(Block):
    """
    Contains a paage, and list of instructions.

    Since RiescueD handles label, this just contains page and instructions
    """

    def __init__(self, page: Page, instruction: list[Instruction]):
        self.page = page
        self.label = page.name
        self.instruction = instruction

    def emit(self) -> str:
        return self.page.emit() + "\n\t" + "\n\t".join([instruction.format() for instruction in self.instruction])
