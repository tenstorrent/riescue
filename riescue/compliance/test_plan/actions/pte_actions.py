# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional, Union
from abc import abstractmethod

from coretp import Instruction, StepIR, TestStep
from coretp.step.memory import WritePTE, ReadPTE
from coretp.rv_enums import Category, OperandType, Extension, Xlen, PteLevel
from coretp.isa.operands import Operand
from coretp.isa import get_register

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext
from riescue.compliance.test_plan.actions.csr import MvT2Action


class PteAction(Action):
    """
    Abstract base class for PTE (Page Table Entry) actions.
    Provides common functionality for reading and writing PTEs at specific levels.

    Takes a memory address and a level index for page table operations.
    """

    register_fields = ["memory", "level"]  # Memory address is the input

    def __init__(self, memory: str, level: Union[int, PteLevel], g_level: Optional[Union[int, PteLevel]] = None, **kwargs):
        super().__init__(**kwargs)
        self.memory = memory
        self.level = level
        self.g_level = g_level
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
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ReadPteAction":
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

        level = step.step.level if step.step.level is not None else -1
        g_level = step.step.g_level if step.step.g_level is not None else None

        return cls(step_id=step_id, memory=memory, level=level, g_level=g_level, **kwargs)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return ReadPteApiInstruction(memory_name=self.memory, pte_level=self.level, g_level=self.g_level)


class WritePteAction(PteAction):
    """
    Action that generates a ;#write_pte directive to write a PTE entry at a specific level.
    Writes the value from t2 register to the PTE after page table walk in machine mode.
    """

    register_fields = ["memory", "level", "src"]

    def __init__(self, memory: str, level: Union[int, PteLevel], g_level: Optional[Union[int, PteLevel]] = None, src: Optional[Union[TestStep, str, int]] = None, **kwargs):
        super().__init__(memory=memory, level=level, g_level=g_level, **kwargs)
        self.expanded = False
        self.src = src

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "WritePteAction":
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

        level = step.step.level if step.step.level is not None else -1
        g_level = step.step.g_level if step.step.g_level is not None else None

        return cls(step_id=step_id, memory=memory, level=level, g_level=g_level, src=final_src, **kwargs)

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
        return WritePteApiInstruction(memory_name=self.memory, pte_level=self.level, g_level=self.g_level)


def _format_pte_level(level: Union[int, PteLevel]) -> str:
    """Format a level value as a string for the directive."""
    if isinstance(level, PteLevel):
        return level.value
    return str(level)


class ReadPteApiInstruction(Instruction):
    """
    Custom instruction that generates the ;#read_pte(lin_name, level[, g_level]) directive.

    This instruction:
    - Takes a memory address (lin_name) as input
    - Walks the page tables (paging mode determined at runtime from satp)
    - Returns the PTE value at the specified level in t2 register
    - Clobbers t0-t6 and x31 registers
    """

    def __init__(self, memory_name: str, pte_level: Union[int, PteLevel], g_level: Optional[Union[int, PteLevel]] = None):
        t0_reg = get_register("t0")
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")
        t3_reg = get_register("t3")
        t4_reg = get_register("t4")
        t5_reg = get_register("t5")
        t6_reg = get_register("t6")

        super().__init__(
            name="read_pte",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=Operand(type=OperandType.GPR, name="rd", val=t2_reg),
            source=[],  # No explicit sources, memory name is in the directive
            clobbers=[t0_reg.name, t1_reg.name, t2_reg.name, t3_reg.name, t4_reg.name, t5_reg.name, t6_reg.name],
        )
        self.memory_name = memory_name
        self.pte_level = pte_level
        self.g_level = g_level

    def format(self):
        """Generate the ;#read_pte directive."""
        level_str = _format_pte_level(self.pte_level)
        if self.g_level is not None:
            g_level_str = _format_pte_level(self.g_level)
            return f";#read_pte({self.memory_name}, {level_str}, {g_level_str})"
        return f";#read_pte({self.memory_name}, {level_str})"


class WritePteApiInstruction(Instruction):
    """
    Custom instruction that generates the ;#write_pte(lin_name, level[, g_level]) directive.

    This instruction:
    - Takes a memory address (lin_name) as input
    - Takes the value to write from t2 register (rs1)
    - Walks the page tables (paging mode determined at runtime from satp)
    - Writes the value from t2 to the PTE at the specified level
    - Clobbers t0-t6 and x31 registers
    """

    def __init__(self, memory_name: str, pte_level: Union[int, PteLevel], g_level: Optional[Union[int, PteLevel]] = None):
        t0_reg = get_register("t0")
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")
        t3_reg = get_register("t3")
        t4_reg = get_register("t4")
        t5_reg = get_register("t5")
        t6_reg = get_register("t6")

        super().__init__(
            name="write_pte",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,  # No destination, this is a write operation
            source=[Operand(type=OperandType.GPR, name="rs1", val=t2_reg)],  # Value to write from t2
            clobbers=[t0_reg.name, t1_reg.name, t2_reg.name, t3_reg.name, t4_reg.name, t5_reg.name, t6_reg.name],
        )
        self.memory_name = memory_name
        self.pte_level = pte_level
        self.g_level = g_level

    def format(self):
        """Generate the ;#write_pte directive."""
        level_str = _format_pte_level(self.pte_level)
        if self.g_level is not None:
            g_level_str = _format_pte_level(self.g_level)
            return f";#write_pte({self.memory_name}, {level_str}, {g_level_str})"
        return f";#write_pte({self.memory_name}, {level_str})"
