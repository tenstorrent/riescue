# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Optional, TYPE_CHECKING, Union
from enum import Enum, auto

from coretp import TestStep, TestEnv, InstructionCatalog, Instruction, StepIR
from coretp.step import CsrRead, CsrWrite
from coretp.rv_enums import Category, OperandType

from riescue.compliance.test_plan.actions import Action, LiAction
from riescue.compliance.test_plan.context import LoweringContext
from riescue.lib.rand import RandNum


class CsrReadAction(Action):
    """
    Casts register value to a different type

    For now returns li instruction for integer registers
    """

    register_fields = []

    def __init__(self, csr_name: str, **kwargs):
        super().__init__(**kwargs)
        self.csr_name = csr_name
        self.constraints = {}  # Will be manually picking a CSR instruction

    def repr_info(self) -> str:
        return f"'{self.csr_name}'"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, CsrRead)
        return cls(
            step_id=step_id,
            csr_name=step.step.csr_name,
        )

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction("csrr")

        csr_operand = selected_instruction.csr_operand()
        if csr_operand is None:
            raise ValueError(f"No CSR operand available for CSR Read action, with instruction {selected_instruction}")
        csr_operand.val = self.csr_name
        return selected_instruction


class CsrOperation(Enum):
    "Small enum to make CsrWriteAction easier to handle"

    SET = auto()
    CLEAR = auto()
    WRITE = auto()


class CsrWriteAction(Action):
    """
    Writes a value to a CSR. Complex action if values aren't immediates

    If no value is passed through, generates a random load immediate. Doesn't care about valid values. Defaults to CSR Write

    """

    register_fields = ["src"]

    def __init__(
        self,
        csr_name: str,
        operation: CsrOperation = CsrOperation.WRITE,
        value: Optional[int] = None,
        src: Optional[str] = None,
        **kwargs,
    ):
        """
        Set state for instruction picking
        """
        super().__init__(**kwargs)
        self.csr_name = csr_name
        self.operation = operation
        self.write_value = value
        self.src = src
        self.constraints = {}  # Will be manually picking a CSR instruction
        self.expanded = False

    def repr_info(self) -> str:
        if self.write_value is not None:
            return f"'{self.csr_name}', {self.operation}, {self.write_value}"
        else:
            return f"'{self.csr_name}', {self.operation}, {self.src}"

    @staticmethod
    def write_value_and_operation(step: CsrWrite, inputs: list[Union[str, int]]) -> tuple[Optional[int], CsrOperation, Optional[str]]:
        """
        Helper function to get the write value and operation from a CsrWrite step

        :param step: CsrWrite step
        :return: tuple of write value and operation
        """
        write_value = None
        src = None
        if step.set_mask is not None:
            operation = CsrOperation.SET
            if isinstance(step.set_mask, int):
                write_value = step.set_mask
        elif step.clear_mask is not None:
            operation = CsrOperation.CLEAR
            if isinstance(step.clear_mask, int):
                write_value = step.clear_mask
        else:
            operation = CsrOperation.WRITE
            if isinstance(step.value, int):
                write_value = step.value
            # elif step.value is not None:
        if write_value is None:
            # check if the only input is a register?
            if len(inputs) == 1:
                src = str(inputs[0])

        return write_value, operation, src

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, CsrWrite)
        write_value, operation, src = cls.write_value_and_operation(step.step, step.inputs)
        return cls(
            step_id=step_id,
            csr_name=step.step.csr_name,
            operation=operation,
            value=write_value,
            src=src,
            **kwargs,
        )

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        """
        If values aren't immediate this will need to return a list of actions to expand to.

        This will return itself as an action.
        """
        if self.expanded:
            return None
        else:
            self.expanded = True

            # if src input, don't need to expand
            if self.src is not None:
                return [self]

            if self.write_value is None:
                self.write_value = ctx.random_n_width_number()  # random number if none passed in
            # is a li needed for the write value?
            if isinstance(self.write_value, int):
                self.use_imm = self.write_value < 32
                need_imm_register = self.write_value > 32  # zimm5 supports only 0..31
            else:
                need_imm_register = False  # no write value to load
            if need_imm_register:
                li_id = ctx.new_value_id()
                load_immediate = LiAction(step_id=li_id, immediate=self.write_value)
                self.src = li_id
                return [load_immediate, self]
            return [self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        """
        Selects instruction based on passed oepration. If set_op, clear_op, write_op, will pick a different instruction.
        If set, pick a CSRRS

        if value < 32, use immediate, otherwise need to use a register. If using immediate, set input operand as x0

        generate() responsible for setting correct operand / immediate if needed.
        """

        use_imm = False
        if self.write_value is not None and self.src is None:
            # if immediate is less than 64 use an immediate. Otherwise, use a register
            # expand() should handle this if needed
            use_imm = self.write_value < 64

        if self.operation == CsrOperation.WRITE:
            if use_imm:
                selected_instruction = ctx.instruction_catalog.get_instruction("csrrwi")
            else:
                selected_instruction = ctx.instruction_catalog.get_instruction("csrrw")
        elif self.operation == CsrOperation.SET:
            if use_imm:
                selected_instruction = ctx.instruction_catalog.get_instruction("csrrsi")
            else:
                selected_instruction = ctx.instruction_catalog.get_instruction("csrrs")
        elif self.operation == CsrOperation.CLEAR:
            if use_imm:
                selected_instruction = ctx.instruction_catalog.get_instruction("csrrci")
            else:
                selected_instruction = ctx.instruction_catalog.get_instruction("csrrc")
        else:
            raise ValueError(f"Invalid operation: {self.operation}")

        # If this instruction needs to load immediate, there's a couple options.

        # 1. reuse the register that we were going to be using as the destination for the operation easy
        # 2. Have a mechanism for adding additional actions after picking instructions.
        #    Currently there is none. There's ComplexAction, but that doesn't help if this is done at pick time. Especially if some external constratins are going to come in.

        csr_operand = selected_instruction.csr_operand()
        if csr_operand is None:
            raise ValueError(f"Expected a CSR operand available for CSR Write action, with instruction {selected_instruction}")
        csr_operand.val = self.csr_name

        if use_imm:
            immediate_operand = selected_instruction.immediate_operand()
            if immediate_operand is not None:
                immediate_operand.val = self.write_value
        else:
            rs1 = selected_instruction.get_source("rs1")
            if rs1 is None:
                raise ValueError(f"Expected a rs1 operand not available for CSR Write action, with instruction {selected_instruction}")
            rs1.val = self.src
        return selected_instruction
