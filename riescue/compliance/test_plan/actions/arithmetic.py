# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
# pyright: reportUnnecessaryIsInstance=false
# pyright: reportMissingTypeStubs=false

import logging

from typing import Any, Optional, TYPE_CHECKING

from coretp import StepIR, Instruction
from coretp.step import Arithmetic
from coretp.rv_enums import Category
from riescue.compliance.test_plan.actions import Action
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

        src1 = None
        src2 = None
        imm = None
        inputs = step.inputs
        if len(inputs) > 2:
            raise ValueError(f"Arithmetic action {step} has more than 2 inputs, which isn't supported")
        # Naively assume that the first input is the src1 and the second is the src2
        elif len(inputs) == 2:
            if isinstance(inputs[0], int) and isinstance(inputs[1], int):
                raise ValueError(f"Arithmetic action {step} has two immediate operands {inputs}")
            elif isinstance(inputs[0], int):
                imm = inputs[0]
                if not isinstance(inputs[1], str):
                    raise ValueError(f"Arithmetic action {step} has an immediate operand and a non-register source operand {type(inputs[1])=}")
                src1 = inputs[1]
                # src2 = inputs[1]
            elif isinstance(inputs[1], int):
                imm = inputs[1]
                if not isinstance(inputs[0], str):
                    raise ValueError(f"Arithmetic action {step} has an immediate operand and a non-register source operand {type(inputs[1])=}")
                src1 = inputs[0]
            else:
                src1 = inputs[0]
                src2 = inputs[1]
        # Assume that the first input is the src1 and the second is the imm
        elif len(inputs) == 1:
            if isinstance(inputs[0], int):
                imm = inputs[0]
            elif isinstance(inputs[0], str):
                src1 = inputs[0]

        variables = {"src1": src1, "src2": src2}
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
                rs1.val = ctx.new_value_id()
            else:
                rs1.val = self.src1
        if rs2 is not None:
            if self.src2 is None:
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
