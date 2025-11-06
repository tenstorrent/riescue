# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Any, Optional

from coretp import Instruction, StepIR
from coretp.isa import Label
from coretp.step import ModifyPte
from riescue.compliance.test_plan.actions import Action, LiAction, StoreAction
from riescue.compliance.test_plan.context import LoweringContext


class ModifyPteAction(Action):
    """
    Modify PTE Action.
    """

    register_fields: list[str] = ["memory"]

    def __init__(self, memory: str, level: int, make_recursive: bool, **kwargs):
        super().__init__(**kwargs)
        self.memory = memory
        self.level = level
        self.make_recursive = make_recursive
        self.expanded = False

    def repr_info(self) -> str:
        return f"[memory={self.memory}, level={self.level}, make_recursive={self.make_recursive}]"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ModifyPteAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ModifyPte)
        if step.step.memory is None:
            raise ValueError("ModifyPteAction requires a memory")
        if step.step.level is None:
            raise ValueError("ModifyPteAction requires a level")
        memory = None
        for src in step.inputs:
            if isinstance(src, str):
                memory = src
                break
        if memory is None:
            raise ValueError("ModifyPteAction requires a memory")
        return cls(
            step_id=step_id,
            memory=memory,
            level=step.step.level,
            make_recursive=step.step.make_recursive,
            **kwargs,
        )

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        """
        Expands code page to a list of instructions, followed by return instruction
        """

        if self.expanded:
            return None
        self.expanded = True

        # write_address = pt_levelN + (entry_index * 8)
        # pte_value = ((target_pt__phys >> 12) << 10) | 0x1
        # store pte_value to write_address

        # make level N-1 point to N

        pte_non_leaf = f"{self.memory}__pt_level{self.level}"
        target_pa = f"(({self.memory}__pt_level{self.level+1}__phys  >> 12) << 10) | 0x1"
        li_action = LiAction(step_id=ctx.new_value_id(), immediate=target_pa)
        store_action = StoreAction(step_id=ctx.new_value_id(), memory=pte_non_leaf, value=li_action.step_id, op="sd")
        return [li_action, store_action]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        raise Exception("No instruction to pick")
