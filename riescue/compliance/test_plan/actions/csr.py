# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING, Union
from enum import Enum, auto

from coretp import TestStep, TestEnv, InstructionCatalog, Instruction, StepIR
from coretp.step import CsrRead, CsrWrite
from coretp.rv_enums import Category, OperandType, Extension, Xlen
from coretp.isa.operands import Operand
from coretp.isa import get_register

from riescue.compliance.test_plan.actions import Action, LiAction, ArithmeticAction
from riescue.compliance.test_plan.context import LoweringContext
from riescue.lib.rand import RandNum


class CsrReadAction(Action):
    """
    Casts register value to a different type

    For now returns li instruction for integer registers
    """

    register_fields = []

    def __init__(self, csr_name: str, direct_read: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.csr_name = csr_name
        self.direct_read = direct_read
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
            direct_read=step.step.direct_read,
        )

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        priv_modes = ["m", "s", "u"]
        csr_priv_mode = self.csr_name[0]
        if csr_priv_mode == "h":
            csr_priv_mode = "s"
        if csr_priv_mode == "v":
            csr_priv_mode = self.csr_name[1]
        which_priv_mode = 2  # assume U mode acess
        if csr_priv_mode in priv_modes:
            which_priv_mode = priv_modes.index(csr_priv_mode)

        api_access = True
        ctx_priv_mode = priv_modes.index(ctx.env.priv.name[0].lower())
        if ctx_priv_mode <= which_priv_mode:
            api_access = False

        if not api_access:
            selected_instruction = ctx.instruction_catalog.get_instruction("csrr")

            csr_operand = selected_instruction.csr_operand()
            if csr_operand is None:
                raise ValueError(f"No CSR operand available for CSR Read action, with instruction {selected_instruction}")
            csr_operand.val = self.csr_name
            return selected_instruction
        else:
            return CsrApiInstruction(csr_name=self.csr_name, src=None, direct_read_write=self.direct_read, name="csrr", api_call="read")


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
        direct_write: bool = False,
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
        self.direct_write = direct_write
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
            direct_write=step.step.direct_write,
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

        priv_modes = ["m", "s", "u"]
        csr_priv_mode = self.csr_name[0]
        if csr_priv_mode == "h":
            csr_priv_mode = "s"
        if csr_priv_mode == "v":
            csr_priv_mode = self.csr_name[1]
        which_priv_mode = 2  # assume U mode acess
        if csr_priv_mode in priv_modes:
            which_priv_mode = priv_modes.index(csr_priv_mode)

        api_access = True
        ctx_priv_mode = priv_modes.index(ctx.env.priv.name[0].lower())
        if ctx_priv_mode <= which_priv_mode:
            api_access = False

        if self.expanded:
            return None
        else:
            self.expanded = True

            # if src input, don't need to expand
            if self.src is not None:
                if not api_access:
                    return [self]
                else:
                    # need to expand to self.src to t2
                    t2_id = ctx.new_value_id()
                    new_arithmetic_action = MvT2Action(step_id=t2_id, src1=self.src)
                    self.src = t2_id
                    return [new_arithmetic_action, self]

            if self.write_value is None:
                self.write_value = ctx.random_n_width_number()  # random number if none passed in
            # is a li needed for the write value?
            if isinstance(self.write_value, int):
                self.use_imm = self.write_value < 32 and not api_access
                need_imm_register = self.write_value >= 32 or api_access  # zimm5 supports only 0..31
            else:
                need_imm_register = False  # no write value to load
            if need_imm_register:
                li_id = ctx.new_value_id()
                if not api_access:
                    load_immediate = LiAction(step_id=li_id, immediate=self.write_value)
                else:
                    load_immediate = LiT2Action(step_id=li_id, immediate=self.write_value)
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

        priv_modes = ["m", "s", "u"]
        csr_priv_mode = self.csr_name[0]
        if csr_priv_mode == "h":
            csr_priv_mode = "s"
        if csr_priv_mode == "v":
            csr_priv_mode = self.csr_name[1]
        which_priv_mode = 2  # assume U mode acess
        if csr_priv_mode in priv_modes:
            which_priv_mode = priv_modes.index(csr_priv_mode)

        api_access = True
        ctx_priv_mode = priv_modes.index(ctx.env.priv.name[0].lower())
        if ctx_priv_mode <= which_priv_mode:
            api_access = False

        use_imm = False
        if self.write_value is not None and self.src is None:
            # if immediate is less than 32 use an immediate. Otherwise, use a register
            # expand() should handle this if needed
            use_imm = self.write_value < 32

        # selective instruction picking for csr access, dependent on dependent
        selected_instruction = None
        if self.operation == CsrOperation.WRITE:
            if not api_access:
                if use_imm:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrwi")
                else:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrw")
            else:
                selected_instruction = CsrApiInstruction(csr_name=self.csr_name, src=self.src, direct_read_write=self.direct_write, name="csrw", api_call="write")
        elif self.operation == CsrOperation.SET:
            if not api_access:
                if use_imm:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrsi")
                else:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrs")
            else:
                selected_instruction = CsrApiInstruction(csr_name=self.csr_name, src=self.src, direct_read_write=self.direct_write, name="csrs", api_call="set")
        elif self.operation == CsrOperation.CLEAR:
            if not api_access:
                if use_imm:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrci")
                else:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrc")
            else:
                selected_instruction = CsrApiInstruction(csr_name=self.csr_name, src=self.src, direct_read_write=self.direct_write, name="csrc", api_call="clear")
        else:
            raise ValueError(f"Invalid operation: {self.operation}")

        if api_access:
            return selected_instruction

        # If this instruction needs to load immediate, there's a couple options.

        # 1. reuse the register that we were going to be using as the destination for the operation easy
        # 2. Have a mechanism for adding additional actions after picking instructions.
        #    Currently there is none. There's ComplexAction, but that doesn't help if this is done at pick time. Especially if some external constratins are going to come in.

        # on dependent csr accesses, require proper operand handling
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


class CsrApiInstruction(Instruction):
    def __init__(self, csr_name: str, src: Optional[str] = None, direct_read_write: bool = False, name: str = "csrw", api_call: str = "write"):
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")

        if src is None:
            src_reg = get_register("zero")
        else:
            src_reg = src

        super().__init__(
            name=name,
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=Operand(type=OperandType.GPR, name="rd", val=t2_reg),
            source=[
                Operand(type=OperandType.CSR, name="csr", val=csr_name),
                Operand(type=OperandType.GPR, name="rs1", val=src_reg),
            ],
            clobbers=[t1_reg.name, t2_reg.name, "x31"],
        )
        self.csr_name = csr_name
        self.direct_read_write = direct_read_write
        self.api_call = api_call

    def format(self):
        direct_read_write_str = "true" if self.direct_read_write else "false"
        return f";#csr_rw({self.csr_name}, {self.api_call}, {direct_read_write_str})"


class MvT2Action(ArithmeticAction):
    # new_arithmetic_action = ArithmeticAction(step_id=t2_id, op="mv", src1=self.src, src2=t2_id)
    register_fields = ["src1"]

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return MvT2Instruction(src1=self.src1)


class LiT2Action(LiAction):
    register_fields = []

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return LiT2Instruction(value=self.value)


class LiT2Instruction(Instruction):
    def __init__(self, value: Union[int, str] = 0):
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")

        super().__init__(
            name="li",
            extension=Extension.I,
            xlen=Xlen.XLEN32,
            category=Category.PSEUDO,
            destination=Operand(type=OperandType.GPR, name="rd", val=t2_reg),
            source=[
                Operand(
                    type=OperandType.IMM,
                    name="imm",
                    val=value,
                ),
            ],
            clobbers=[t1_reg.name, t2_reg.name, "x31"],
        )
        self.value = value

    def format(self):
        return f"li t2, {self.value}"


class MvT2Instruction(Instruction):

    def __init__(self, src1: Optional[str] = None):
        t1_reg = get_register("t1")
        t2_reg = get_register("t2")

        super().__init__(
            name="mv",
            extension=Extension.I,
            xlen=Xlen.XLEN32,
            category=Category.PSEUDO,
            destination=Operand(type=OperandType.GPR, name="rd", val=t2_reg),
            source=[
                Operand(
                    type=OperandType.GPR,
                    name="rs1",
                    val=src1,
                ),
            ],
            formatter="mv t2, {rs1}",
            clobbers=[t1_reg.name, t2_reg.name, "x31"],
        )
