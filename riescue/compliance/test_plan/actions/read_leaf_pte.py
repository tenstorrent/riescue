# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step.memory import ReadLeafPTE
from coretp.rv_enums import Category, OperandType, Extension, Xlen, PagingMode
from coretp.isa.operands import Operand
from coretp.isa import get_register

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class ReadLeafPteAction(Action):
    """
    Action that generates a ;#read_leaf_pte directive to read a leaf PTE entry.
    Returns the PTE value in t2 register after page table walk in machine mode.
    """

    register_fields = ["memory"]  # Memory address is the input

    def __init__(self, memory: str, **kwargs):
        super().__init__(**kwargs)
        self.memory = memory
        self.constraints = {}  # Will be manually picking a custom instruction

    def repr_info(self) -> str:
        return f"[memory={self.memory}]"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ReadLeafPteAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ReadLeafPTE)

        if step.step.memory is None:
            raise ValueError("ReadLeafPteAction requires a memory")

        # Get the memory name from inputs
        memory = None
        for src in step.inputs:
            if isinstance(src, str):
                memory = src
                break

        if memory is None:
            raise ValueError("ReadLeafPteAction requires a memory input")

        return cls(step_id=step_id, memory=memory, **kwargs)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        # Get paging mode from context environment
        paging_mode_enum = ctx.env.paging_mode

        # Convert PagingMode enum to string for API (sv39, sv48, sv57)
        if paging_mode_enum == PagingMode.SV39:
            paging_mode_str = "sv39"
        elif paging_mode_enum == PagingMode.SV48:
            paging_mode_str = "sv48"
        elif paging_mode_enum == PagingMode.SV57:
            paging_mode_str = "sv57"
        else:
            # Default to sv39 if not specified or unsupported
            paging_mode_str = "sv39"

        return ReadLeafPteApiInstruction(memory_name=self.memory, paging_mode=paging_mode_str)


class ReadLeafPteApiInstruction(Instruction):
    """
    Custom instruction that generates the ;#read_leaf_pte(lin_name, paging_mode) directive.

    This instruction:
    - Takes a memory address (lin_name) as input
    - Walks the page tables based on the paging mode (sv39/sv48/sv57)
    - Returns the leaf PTE value in t2 register
    - Clobbers t1, t2, and x31 registers
    """

    def __init__(self, memory_name: str, paging_mode: str):
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")

        super().__init__(
            name="read_leaf_pte",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=Operand(type=OperandType.GPR, name="rd", val=t2_reg),
            source=[],  # No explicit sources, memory name is in the directive
            clobbers=[t1_reg.name, t2_reg.name, "x31"],
        )
        self.memory_name = memory_name
        self.paging_mode = paging_mode

    def format(self):
        """Generate the ;#read_leaf_pte directive."""
        return f";#read_leaf_pte({self.memory_name}, {self.paging_mode})"
