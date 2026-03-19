# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional, Any

from coretp import Instruction, StepIR
from coretp.step import LoadPhysicalAddress, Memory
from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext


class LoadPhysicalAddressAction(Action):
    """
    Load Physical Address Action

    Loads the physical address of a Memory step into a register.
    This is used for dynamically constructing pmacfg CSR values that
    need to reference actual physical addresses allocated by the framework.

    The action generates an 'li' instruction with a memory label reference
    that will be resolved to the actual physical address during assembly generation.
    The value is automatically shifted right by 12 bits (PA>>12) to get the PPN
    which is needed for pmacfg CSR fields.
    """

    register_fields = ["memory_step_id"]

    def __init__(self, step_id: str, memory_step_id: str):
        super().__init__(step_id=step_id)
        self.memory_step_id = memory_step_id

    def repr_info(self) -> str:
        return f"memory={self.memory_step_id}"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs: Any) -> "LoadPhysicalAddressAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, LoadPhysicalAddress)

        # Get the memory step ID from inputs
        # The Memory step should be passed as an input during lowering
        if len(step.inputs) != 1:
            raise ValueError(f"LoadPhysicalAddress expects exactly 1 input (Memory step), got {len(step.inputs)}")

        memory_step_id = step.inputs[0]
        if not isinstance(memory_step_id, str):
            raise ValueError(f"LoadPhysicalAddress input must be a step ID string, got {type(memory_step_id)}")

        return cls(step_id=step_id, memory_step_id=memory_step_id)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        """
        Generate an 'li' instruction to load the physical address.

        The memory label reference will be resolved to the actual PA
        during assembly generation, similar to how LiAction handles memory labels.
        """
        # Get the li instruction
        selected_instruction = ctx.instruction_catalog.get_instruction("li")

        if len(selected_instruction.source) != 1:
            raise ValueError(f"Expected li to have 1 source operand, but {selected_instruction.name} has {selected_instruction.source}")

        # Set the immediate value to the memory step ID with "_phys" suffix
        # Memory pages define both "memX" (virtual/linear address) and "memX_phys" (physical address)
        # LoadPhysicalAddress must reference the physical address variant
        # This will be resolved to the actual PA during assembly generation
        selected_instruction.source[0].val = f"{self.memory_step_id}_phys"

        return selected_instruction
