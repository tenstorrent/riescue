# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
# pyright: reportUnnecessaryIsInstance=false
# pyright: reportMissingTypeStubs=false

import logging

from typing import Any, Optional, TYPE_CHECKING, Union

from coretp import StepIR, Instruction
from coretp.step import Arithmetic
from coretp.rv_enums import Category
from riescue.compliance.test_plan.actions import Action, LiAction
from riescue.compliance.test_plan.context import LoweringContext

log = logging.getLogger(__name__)


class ArithmeticAction(Action):
    """
    Generic Arithmetic Action.

    .. note::

        ``fmadd.s`` and other floating point instructions have three source operands, but for most use cases,
        two source operands are enough.
    """

    register_fields = ["src1", "src2"]

    def __init__(
        self,
        src1: Optional[str] = None,
        src2: Optional[str] = None,
        imm: Optional[int] = None,
        op: Optional[str] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.op = op
        self.src1 = src1
        self.src2 = src2
        self.imm = imm
        if self.src2 is not None and self.imm is not None and self.src1 is not None:
            if self.src1 != "zero" and self.src2 != "zero":
                raise ValueError("Arithmetic action cannot have both two source register and an immediate operand")

    def repr_info(self) -> str:
        op = ""
        if self.op is not None:
            op = "op=" + self.op
        if isinstance(self.imm, int):
            imm = f"0x{self.imm:x}"
        else:
            imm = self.imm
        return f"'{self.src1}', '{self.src2}', {imm}, {op}"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs: Any) -> "ArithmeticAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, Arithmetic)

        src1: Optional[str] = None
        src2: Optional[str] = None
        imm: Optional[int] = None
        # Filter inputs to only str and int (tuples are not supported for arithmetic)
        # step.inputs comes from untyped coretp module - it's list[str | int | tuple[str, int]]
        inputs: list[Union[str, int]] = [i for i in step.inputs if isinstance(i, (str, int))]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
        if len(inputs) > 2:
            raise ValueError(f"Arithmetic action {step} has more than 2 inputs, which isn't supported")
        # Naively assume that the first input is the src1 and the second is the src2
        elif len(inputs) == 2:
            input0 = inputs[0]
            input1 = inputs[1]
            if isinstance(input0, int) and isinstance(input1, int):
                raise ValueError(f"Arithmetic action {step} has two immediate operands {inputs}")
            elif isinstance(input0, int):
                if input0 == 0:
                    src1 = "zero"
                    src2 = input1 if isinstance(input1, str) else None
                    imm = 0
                else:
                    imm = input0
                    if not isinstance(input1, str):
                        raise ValueError(f"Arithmetic action {step} has an immediate operand and a non-register source operand {type(input1)=}")
                    src1 = input1
                    # src2 = input1
            elif isinstance(input1, int):
                if input1 == 0:
                    src1 = input0 if isinstance(input0, str) else None
                    src2 = "zero"
                    imm = 0
                else:
                    imm = input1
                    if not isinstance(input0, str):
                        raise ValueError(f"Arithmetic action {step} has an immediate operand and a non-register source operand {type(input1)=}")
                    src1 = input0
            else:
                src1 = input0
                src2 = input1
        # Assume that the first input is the src1 and the second is the imm
        elif len(inputs) == 1:
            input0 = inputs[0]
            if isinstance(input0, int):
                imm = input0
            elif isinstance(input0, str):
                src1 = input0

        variables: dict[str, Optional[str]] = {"src1": src1, "src2": src2}
        for field, value in variables.items():
            if value is not None and not isinstance(value, str):
                raise TypeError(f"Expected {field} to be a string or None but got {type(value)}\n{inputs=}")
        return cls(
            step_id=step_id,
            src1=src1,
            src2=src2,
            imm=imm,
            op=step.step.op,
        )

    def expand(self, ctx: LoweringContext) -> "Optional[list[Action]]":
        new_actions: list[Action] = []

        if self.src1 is not None and ctx.mem_reg.is_memory_label(self.src1):
            src1_li = LiAction(step_id=ctx.new_value_id(), immediate=self.src1)
            self.src1 = src1_li.step_id
            new_actions.append(src1_li)

        if self.src2 is not None and ctx.mem_reg.is_memory_label(self.src2):
            src2_li = LiAction(step_id=ctx.new_value_id(), immediate=self.src2)
            self.src2 = src2_li.step_id
            new_actions.append(src2_li)

        if new_actions:
            new_actions.append(self)
            return new_actions
        return None

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        constraints: dict[str, Any] = {"category": Category.ARITHMETIC}

        # if there are two input ops, pick an instruction with two source operands
        if self.src1 is not None and self.src2 is not None:
            constraints["source_reg_count"] = 2
        elif self.src1 is not None:
            constraints["source_reg_count"] = 1
        if self.imm is not None:
            constraints["has_immediate"] = True  # if an immediate, don't want to pick an isntruction that has an immediate operand

        # check if input op is present
        instruction = None
        if self.op:
            try:
                instruction = ctx.instruction_catalog.get_instruction(self.op)
            except IndexError:
                # Should this instead raise an error to indicate that the instruction is not supported?
                log.error(f"Warning: Instruction '{self.op}' not found, falling back to constraint-based selection")

        # if no op is provided, pick a random instruction
        if not instruction:
            instruction_choices = ctx.instruction_catalog.filter(**constraints)
            instruction = ctx.rng.choice(instruction_choices)

        # Set input registers with temps; set immediate

        rs1 = instruction.rs1()
        rs2 = instruction.rs2()
        imm = instruction.immediate_operand()
        if rs1 is not None:
            if self.src1 is None:
                if self.op == "sfence.vma":
                    rs1.val = "zero"
                    self.src1 = "zero"
                else:
                    rs1.val = ctx.new_value_id()
            else:
                rs1.val = self.src1
        if rs2 is not None:
            if self.src2 is None:
                if self.op == "sfence.vma":
                    rs2.val = "zero"
                    self.src2 = "zero"
                else:
                    rs2.val = ctx.new_value_id()
            else:
                rs2.val = self.src2
        if imm is not None:
            if self.imm is None:
                if (
                    instruction.name == "slli"
                    or instruction.name == "srli"
                    or instruction.name == "srai"
                    or instruction.name == "slliw"
                    or instruction.name == "sraiw"
                    or instruction.name == "srliw"
                    or instruction.name == "rori"
                    or instruction.name == "roriw"
                ):
                    # FIXME: size limit of immediate should be handled in instruction catalog
                    imm.val = ctx.random_n_width_number(5)
                else:
                    # 11 bits because this is interpreted as number w/o counting sign bit in the generated instruction
                    imm.val = ctx.random_n_width_number(11)
            else:
                imm.val = self.imm

        # Floating point instructions have three source operands
        rs3 = instruction.get_source("rs3")
        if rs3 is not None:
            rs3.val = ctx.new_value_id()

        return instruction
