# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional, Union
from abc import abstractmethod

from coretp import Instruction, StepIR, TestStep
from coretp.step.memory import ReadLeafPTE, WritePTE, WriteLeafPTE, ReadPTE
from coretp.rv_enums import Category, OperandType, Extension, Xlen, PagingMode
from coretp.isa.operands import Operand
from coretp.isa import get_register

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext
from riescue.compliance.test_plan.actions.csr import MvT2Action


def get_leaf_level_for_paging_mode(paging_mode: PagingMode) -> int:
    """Get the last (leaf) level index for a given paging mode."""
    if paging_mode == PagingMode.SV39:
        return 2  # sv39 has levels 0, 1, 2
    elif paging_mode == PagingMode.SV48:
        return 3  # sv48 has levels 0, 1, 2, 3
    elif paging_mode == PagingMode.SV57:
        return 4  # sv57 has levels 0, 1, 2, 3, 4
    else:
        # Default to sv39
        return 2


def paging_mode_to_string(paging_mode: PagingMode) -> str:
    """Convert PagingMode enum to string for API (sv39, sv48, sv57)."""
    if paging_mode == PagingMode.SV39:
        return "sv39"
    elif paging_mode == PagingMode.SV48:
        return "sv48"
    elif paging_mode == PagingMode.SV57:
        return "sv57"
    else:
        # Default to sv39 if not specified or unsupported
        return "sv39"


class PteAction(Action):
    """
    Abstract base class for PTE (Page Table Entry) actions.
    Provides common functionality for reading and writing PTEs at specific levels.

    Takes a memory address and a level index for page table operations.
    """

    register_fields = ["memory", "level"]  # Memory address is the input

    def __init__(self, memory: str, level: int, **kwargs):
        super().__init__(**kwargs)
        self.memory = memory
        self.level = level
        self.constraints = {}  # Will be manually picking a custom instruction

    def repr_info(self) -> str:
        return f"[memory={self.memory}, level={self.level}]"

    @abstractmethod
    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        """Subclasses must implement this to return the appropriate instruction."""
        pass


class ReadPteAction(PteAction):
    """
    Action that generates a ;#read_pte directive to read a PTE entry at a specific level.
    Returns the PTE value in t2 register after page table walk in machine mode.
    """

    register_fields = ["memory", "level"]

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, level: int = -1, **kwargs) -> "ReadPteAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ReadPTE)

        if step.step.memory is None:
            raise ValueError("ReadPteAction requires a memory")

        # Get the memory name from inputs
        memory = None
        for src in step.inputs:
            if isinstance(src, str):
                memory = src
                break

        if memory is None:
            raise ValueError("ReadPteAction requires a memory input")

        return cls(step_id=step_id, memory=memory, level=level, **kwargs)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        # Get paging mode from context environment
        paging_mode_enum = ctx.env.paging_mode
        paging_mode_str = paging_mode_to_string(paging_mode_enum)

        return ReadPteApiInstruction(memory_name=self.memory, paging_mode=paging_mode_str, pte_level=self.level)


class WritePteAction(PteAction):
    """
    Action that generates a ;#write_pte directive to write a PTE entry at a specific level.
    Writes the value from t2 register to the PTE after page table walk in machine mode.
    """

    register_fields = ["memory", "level", "src"]

    def __init__(self, memory: str, level: int, src: Optional[Union[TestStep, str, int]] = None, **kwargs):
        super().__init__(memory=memory, level=level, **kwargs)
        self.expanded = False
        self.src = src

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, level: int = -1, **kwargs) -> "WritePteAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, WritePTE)

        if step.step.memory is None:
            raise ValueError("WritePteAction requires a memory")

        # Get the memory name and src from inputs
        memory = None
        src_value = None

        # Parse inputs to find memory and src
        # Memory inputs typically start with 'm', src values don't
        for input_val in step.inputs:
            if isinstance(input_val, str):
                if input_val.startswith("m") and memory is None:
                    memory = input_val
                elif not input_val.startswith("m") and src_value is None:
                    src_value = input_val

        if memory is None:
            raise ValueError("WritePteAction requires a memory input")

        # Use src from step.step if not found in inputs, otherwise use from inputs
        final_src = src_value if src_value is not None else (step.step.src if hasattr(step.step, "src") else None)

        return cls(step_id=step_id, memory=memory, level=level, src=final_src, **kwargs)

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        """
        If values aren't immediate this will need to return a list of actions to expand to.

        This will return itself as an action.
        """
        if self.expanded:
            return None
        else:
            self.expanded = True
            t2_id = ctx.new_value_id()
            new_arithmetic_action = MvT2Action(step_id=t2_id, src1=self.src)
            self.src = t2_id
            return [new_arithmetic_action, self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        # Get paging mode from context environment
        paging_mode_enum = ctx.env.paging_mode
        paging_mode_str = paging_mode_to_string(paging_mode_enum)

        return WritePteApiInstruction(memory_name=self.memory, paging_mode=paging_mode_str, pte_level=self.level)


class ReadLeafPteAction(ReadPteAction):
    """
    Action that reads a leaf PTE (last level PTE based on paging mode).
    This is a convenience class that automatically determines the leaf level.
    """

    register_fields = ["memory"]

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, level: int = -1, **kwargs) -> "ReadLeafPteAction":
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

        # Level will be determined at pick_instruction time based on paging mode
        return cls(step_id=step_id, memory=memory, level=-1, **kwargs)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        # Get paging mode from context environment and determine leaf level
        paging_mode_enum = ctx.env.paging_mode
        paging_mode_str = paging_mode_to_string(paging_mode_enum)
        leaf_level = get_leaf_level_for_paging_mode(paging_mode_enum)

        return ReadPteApiInstruction(memory_name=self.memory, paging_mode=paging_mode_str, pte_level=leaf_level)


class WriteLeafPteAction(WritePteAction):
    """
    Action that writes a leaf PTE (last level PTE based on paging mode).
    This is a convenience class that automatically determines the leaf level.
    """

    register_fields = ["memory", "src"]

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, level: int = -1, **kwargs) -> "WriteLeafPteAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, WriteLeafPTE)

        if step.step.memory is None:
            raise ValueError("WriteLeafPteAction requires a memory")

        # Get the memory name and src from inputs
        # The memory should be derived from step.step.memory if it's a Memory object
        # Otherwise, look for it in inputs (it will be a string reference)
        memory = None
        src_value = None

        # Parse inputs to find memory and src
        # Memory inputs typically start with 'm', src values don't
        for input_val in step.inputs:
            if isinstance(input_val, str):
                if input_val.startswith("m") and memory is None:
                    memory = input_val
                elif not input_val.startswith("m") and src_value is None:
                    src_value = input_val

        if memory is None:
            raise ValueError("WriteLeafPteAction requires a memory input")

        # Use src from step.step if not found in inputs, otherwise use from inputs
        final_src = src_value if src_value is not None else (step.step.src if hasattr(step.step, "src") else None)

        # Level will be determined at pick_instruction time based on paging mode
        return cls(step_id=step_id, memory=memory, level=-1, src=final_src, **kwargs)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        # Get paging mode from context environment and determine leaf level
        paging_mode_enum = ctx.env.paging_mode
        paging_mode_str = paging_mode_to_string(paging_mode_enum)
        leaf_level = get_leaf_level_for_paging_mode(paging_mode_enum)

        return WritePteApiInstruction(memory_name=self.memory, paging_mode=paging_mode_str, pte_level=leaf_level)


class ReadPteApiInstruction(Instruction):
    """
    Custom instruction that generates the ;#read_pte(lin_name, paging_mode, level) directive.

    This instruction:
    - Takes a memory address (lin_name) as input
    - Walks the page tables based on the paging mode (sv39/sv48/sv57)
    - Returns the PTE value at the specified level in t2 register
    - Clobbers t1, t2, and x31 registers
    """

    def __init__(self, memory_name: str, paging_mode: str, pte_level: int):
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")

        super().__init__(
            name="read_pte",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=Operand(type=OperandType.GPR, name="rd", val=t2_reg),
            source=[],  # No explicit sources, memory name is in the directive
            clobbers=[t1_reg.name, t2_reg.name, "x31"],
        )
        self.memory_name = memory_name
        self.paging_mode = paging_mode
        self.pte_level = pte_level

    def format(self):
        """Generate the ;#read_pte directive."""
        return f";#read_pte({self.memory_name}, {self.paging_mode}, {self.pte_level})"


class WritePteApiInstruction(Instruction):
    """
    Custom instruction that generates the ;#write_pte(lin_name, paging_mode, level) directive.

    This instruction:
    - Takes a memory address (lin_name) as input
    - Takes the value to write from t2 register (rs1)
    - Walks the page tables based on the paging mode (sv39/sv48/sv57)
    - Writes the value from t2 to the PTE at the specified level
    - Clobbers t1, t2, and x31 registers
    """

    def __init__(self, memory_name: str, paging_mode: str, pte_level: int):
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")

        super().__init__(
            name="write_pte",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,  # No destination, this is a write operation
            source=[Operand(type=OperandType.GPR, name="rs1", val=t2_reg)],  # Value to write from t2
            clobbers=[t1_reg.name, t2_reg.name, "x31"],
        )
        self.memory_name = memory_name
        self.paging_mode = paging_mode
        self.pte_level = pte_level

    def format(self):
        """Generate the ;#write_pte directive."""
        return f";#write_pte({self.memory_name}, {self.paging_mode}, {self.pte_level})"
