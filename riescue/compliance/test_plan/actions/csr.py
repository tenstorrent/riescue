# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING, Union
from enum import Enum, auto

from coretp import TestStep, TestEnv, InstructionCatalog, Instruction, StepIR
from coretp.step import CsrRead, CsrWrite, CsrDirectAccess
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
        priv_modes = ["m", "s", "vs", "u"]
        csr_priv_mode = self.csr_name[0]
        effective_csr_name = self.csr_name
        if csr_priv_mode == "h":
            csr_priv_mode = "s"
        if csr_priv_mode == "v":
            csr_priv_mode = "vs"
            if ctx.env.virtualized:
                effective_csr_name = self.csr_name[1:]
        which_priv_mode = 2  # assume U mode acess
        if csr_priv_mode in priv_modes:
            which_priv_mode = priv_modes.index(csr_priv_mode)

        api_access = True
        ctx_priv_str = ctx.env.priv.name[0].lower()
        if ctx_priv_str == "s" and ctx.env.virtualized:
            ctx_priv_str = "vs"
        ctx_priv_mode = priv_modes.index(ctx_priv_str)
        if ctx_priv_mode <= which_priv_mode:
            api_access = False

        if not api_access:
            selected_instruction = ctx.instruction_catalog.get_instruction("csrr")

            csr_operand = selected_instruction.csr_operand()
            if csr_operand is None:
                raise ValueError(f"No CSR operand available for CSR Read action, with instruction {selected_instruction}")
            csr_operand.val = effective_csr_name
            return selected_instruction
        else:
            instruction_id = ctx.new_value_id()
            print(f"csrr_instruction_id: {instruction_id}")
            selected_instruction = CsrApiInstruction(csr_name=self.csr_name, src=None, direct_read_write=self.direct_read, name="csrr", api_call="read", instruction_id=instruction_id)
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
    def write_value_and_operation(step: CsrWrite, inputs: "list[Union[str, int, tuple[Any, ...]]]") -> tuple[Optional[int], CsrOperation, Optional[str]]:
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

        priv_modes = ["m", "s", "vs", "u"]
        csr_priv_mode = self.csr_name[0]
        if csr_priv_mode == "h":
            csr_priv_mode = "s"
        if csr_priv_mode == "v":
            if ctx.env.virtualized:
                csr_priv_mode = self.csr_name[1]
            else:
                csr_priv_mode = "vs"
        which_priv_mode = 2  # assume U mode acess
        if csr_priv_mode in priv_modes:
            which_priv_mode = priv_modes.index(csr_priv_mode)

        api_access = True
        ctx_priv_str = ctx.env.priv.name[0].lower()
        if ctx_priv_str == "s" and ctx.env.virtualized:
            ctx_priv_str = "vs"
        ctx_priv_mode = priv_modes.index(ctx_priv_str)
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

        priv_modes = ["m", "s", "vs", "u"]
        csr_priv_mode = self.csr_name[0]
        effective_csr_name = self.csr_name
        if csr_priv_mode == "h":
            csr_priv_mode = "s"
        if csr_priv_mode == "v":
            csr_priv_mode = "vs"
            if ctx.env.virtualized:
                effective_csr_name = self.csr_name[1:]
        which_priv_mode = 2  # assume U mode acess
        if csr_priv_mode in priv_modes:
            which_priv_mode = priv_modes.index(csr_priv_mode)

        api_access = True
        ctx_priv_str = ctx.env.priv.name[0].lower()
        if ctx_priv_str == "s" and ctx.env.virtualized:
            ctx_priv_str = "vs"
        ctx_priv_mode = priv_modes.index(ctx_priv_str)
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
                instruction_id = ctx.new_value_id()
                selected_instruction = CsrApiInstruction(csr_name=self.csr_name, src=self.src, direct_read_write=self.direct_write, name="csrw", api_call="write", instruction_id=instruction_id)
        elif self.operation == CsrOperation.SET:
            if not api_access:
                if use_imm:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrsi")
                else:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrs")
            else:
                instruction_id = ctx.new_value_id()
                selected_instruction = CsrApiInstruction(csr_name=self.csr_name, src=self.src, direct_read_write=self.direct_write, name="csrs", api_call="set", instruction_id=instruction_id)
                selected_instruction.instruction_id = ctx.new_value_id()
        elif self.operation == CsrOperation.CLEAR:
            if not api_access:
                if use_imm:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrci")
                else:
                    selected_instruction = ctx.instruction_catalog.get_instruction("csrrc")
            else:
                instruction_id = ctx.new_value_id()
                selected_instruction = CsrApiInstruction(csr_name=self.csr_name, src=self.src, direct_read_write=self.direct_write, name="csrc", api_call="clear", instruction_id=instruction_id)
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
        csr_operand.val = effective_csr_name

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
    def __init__(
        self,
        csr_name: str,
        src: Optional[str] = None,
        direct_read_write: bool = False,
        name: str = "csrw",
        api_call: str = "write",
        force_machine_rw: bool = False,
        instruction_id: str = "",
    ):
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
            instruction_id=instruction_id,
        )
        self.csr_name = csr_name
        self.direct_read_write = direct_read_write
        self.api_call = api_call
        self.force_machine_rw = force_machine_rw

    def format(self):
        direct_read_write_str = "true" if self.direct_read_write else "false"
        force_machine_rw_str = "true" if self.force_machine_rw else "false"
        return f";#csr_rw({self.csr_name}, {self.api_call}, {direct_read_write_str}, {force_machine_rw_str})"


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


class CsrDirectAccessAction(Action):
    """
    Handles CsrDirectAccess step for direct CSR instruction access.

    Supports both immediate and register-based CSR operations:
    - csrrw, csrrs, csrrc (register-based)
    - csrrwi, csrrsi, csrrci (immediate-based)

    When csr_name is None, a CSR will be randomly selected based on the
    current privilege mode and operation type.
    """

    register_fields = ["src"]

    def __init__(
        self,
        op: str,
        csr_name: Optional[str] = None,
        src_value: Optional[int] = None,
        src: Optional[str] = None,
        target_is_x0: bool = False,
        **kwargs,
    ):
        """
        Initialize CsrDirectAccessAction.

        :param op: CSR operation name (csrrw, csrrs, csrrc, csrrwi, csrrsi, csrrci)
        :param csr_name: CSR name (None = randomize)
        :param src_value: Integer value for src1 (used for immediate or LI)
        :param src: Step ID dependency for src1
        :param target_is_x0: Destination is x0 (discard result)
        """
        super().__init__(**kwargs)
        self.op = op
        self.csr_name = csr_name
        self.src_value = src_value
        self.src = src
        self.target_is_x0 = target_is_x0
        self.constraints = {}
        self.expanded = False

    def repr_info(self) -> str:
        return f"'{self.op}', csr='{self.csr_name}'"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, CsrDirectAccess)

        src_value = None
        src = None

        # Handle src1: can be int or step dependency
        if isinstance(step.step.src1, int):
            src_value = step.step.src1
        elif step.step.src1 is not None:
            # It's a step dependency - resolve from inputs
            if len(step.inputs) >= 1:
                src = str(step.inputs[0])
        else:
            src = "zero"

        return cls(
            step_id=step_id,
            op=step.step.op,
            csr_name=step.step.csr_name,
            src_value=src_value,
            src=src,
            target_is_x0=step.step.target_is_x0,
            **kwargs,
        )

    def _is_immediate_op(self) -> bool:
        """Check if this is an immediate CSR operation."""
        return self.op in ["csrrwi", "csrrsi", "csrrci"]

    def _randomize_csr(self, ctx: LoweringContext) -> str:
        """Select a random CSR valid for current privilege mode and operation."""
        priv = ctx.env.priv.name.lower()

        FILTERED_CSRS = ["mip", "mie", "sip", "sie", "satp"]  # Do not create new interrupts

        # Map privilege to Accessibility filter
        accessibility_map = {
            "m": "Machine",
            "s": "Supervisor",
            "u": "User",
        }
        accessibility = accessibility_map.get(priv, "Machine")

        is_read_only_op = self.op in ["csrrs", "csrrc", "csrrsi", "csrrci"] and (self.src is None or self.src == "zero") and (self.src_value == 0 or self.src_value is None)

        names_to_csrs = []

        if accessibility == "User":
            csr_configs_exclude_machine = ctx.get_csr_manager().lookup_csrs(
                match={"software-write": "W", "ISS_Support": "Yes"},
                exclude={"Accessibility": "Machine"},
            )

            non_machine_csrs = list(csr_configs_exclude_machine.keys())

            csr_configs_exclude_super = ctx.get_csr_manager().lookup_csrs(
                match={"software-write": "W", "ISS_Support": "Yes"},
                exclude={"Accessibility": "Supervisor"},
            )

            non_super_csrs = list(csr_configs_exclude_super.keys())

            names_to_csrs += [csr for csr in non_machine_csrs if csr in non_super_csrs]

        else:
            csr_configs = ctx.get_csr_manager().lookup_csrs(
                match={"Accessibility": accessibility, "software-write": "W", "ISS_Support": "Yes"},
            )
            if not csr_configs:
                # Fallback: try without ISS_Support filter
                csr_configs = ctx.get_csr_manager().lookup_csrs(
                    match={"Accessibility": accessibility, "software-write": "W"},
                )
            names_to_csrs += list(csr_configs.keys())

        if is_read_only_op:
            if accessibility == "User":
                csr_configs_exclude_machine = ctx.get_csr_manager().lookup_csrs(
                    match={"software-read": "R", "ISS_Support": "Yes"},
                    exclude={"Accessibility": "Machine"},
                )

                non_machine_csrs = list(csr_configs_exclude_machine.keys())

                csr_configs_exclude_super = ctx.get_csr_manager().lookup_csrs(
                    match={"software-read": "R", "ISS_Support": "Yes"},
                    exclude={"Accessibility": "Supervisor"},
                )

                non_super_csrs = list(csr_configs_exclude_super.keys())

                names_to_csrs += [csr for csr in non_machine_csrs if csr in non_super_csrs]

            else:
                csr_configs = ctx.get_csr_manager().lookup_csrs(
                    match={"Accessibility": accessibility, "software-read": "R", "ISS_Support": "Yes"},
                )
                if not csr_configs:
                    # Fallback: try without ISS_Support filter
                    csr_configs = ctx.get_csr_manager().lookup_csrs(
                        match={"Accessibility": accessibility, "software-read": "R"},
                    )
                names_to_csrs += list(csr_configs.keys())
        names_to_csrs = [name for name in names_to_csrs if name not in FILTERED_CSRS]
        csr_name = ctx.rng.choice(names_to_csrs)
        return csr_name

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        """
        Expand action if needed:
        - Randomize CSR if csr_name is None
        - Create LI action if src_value is provided and op is not immediate
        """
        if self.expanded:
            return None

        self.expanded = True

        # Randomize CSR if not specified
        if self.csr_name is None:
            self.csr_name = self._randomize_csr(ctx)

        # If we have an integer value and it's not an immediate operation,
        # we need to load it into a register first
        if self.src_value is not None and not self._is_immediate_op():
            li_id = ctx.new_value_id()
            load_immediate = LiAction(step_id=li_id, immediate=self.src_value)
            self.src = li_id
            self.src_value = None  # Clear since we're using src now
            return [load_immediate, self]

        return [self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        """
        Select appropriate CSR instruction based on operation type.
        """
        is_imm_op = self._is_immediate_op()

        # Map op to instruction name
        # For immediate ops, the op name is already the instruction name
        # For register ops, the op name is already the instruction name
        selected_instruction = ctx.instruction_catalog.get_instruction(self.op)

        # Set CSR operand
        csr_operand = selected_instruction.csr_operand()
        if csr_operand is None:
            raise ValueError(f"No CSR operand available for CsrDirectAccess action, " f"with instruction {selected_instruction}")
        csr_operand.val = self.csr_name

        # Set source operand (rs1 for register ops, immediate for immediate ops)
        if is_imm_op:
            immediate_operand = selected_instruction.immediate_operand()
            if immediate_operand is not None:
                # Use src_value for immediate, default to 0
                immediate_operand.val = self.src_value if self.src_value is not None else 0
        else:
            rs1 = selected_instruction.get_source("rs1")
            if rs1 is None:
                raise ValueError(f"Expected rs1 operand for CSR instruction {selected_instruction}")
            # If we have a src (step dependency), use it
            # Otherwise use x0 (src_value=0 or src_value=None means x0)
            if self.src is not None:
                rs1.val = self.src
            else:
                rs1.val = "zero"

        # Set destination operand if target_is_x0
        if self.target_is_x0:
            rd = selected_instruction.destination
            if rd is not None:
                rd.val = "zero"

        return selected_instruction
