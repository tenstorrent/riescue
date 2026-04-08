# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional

from coretp import Instruction, StepIR
from coretp.step import AssertFetchException
from coretp.rv_enums import Extension, Category, OperandType, Xlen, ExceptionCause, ExceptionHandlerMode
from coretp.isa import Operand, get_register

from riescue.compliance.test_plan.actions import Action, LabelAction, LiAction
from riescue.compliance.test_plan.context import LoweringContext
from .assertion_base import AssertionBase, AssertionJumpToFail


class _SdAction(AssertionBase):
    """Store doubleword: sd rs2, 0(rs1)"""

    register_fields = ["rs1", "rs2"]

    def __init__(self, rs1: str, rs2: str, **kwargs):
        super().__init__(**kwargs)
        self.rs1 = rs1
        self.rs2 = rs2

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction("sd")
        rs1 = selected_instruction.get_source("rs1")
        if rs1 is None:
            raise ValueError("sd instruction has no rs1")
        rs1.val = self.rs1

        rs2 = selected_instruction.get_source("rs2")
        if rs2 is None:
            raise ValueError("sd instruction has no rs2")
        rs2.val = self.rs2

        offset = selected_instruction.immediate_operand()
        if offset is not None:
            offset.val = 0

        return selected_instruction


class _FetchJalrAction(AssertionBase):
    """jalr ra, 0(rs1) — jump to target; fetch fault occurs at target"""

    register_fields = ["rs1"]

    def __init__(self, rs1: str, **kwargs):
        super().__init__(**kwargs)
        self.rs1 = rs1

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        selected_instruction = ctx.instruction_catalog.get_instruction("jalr_ra")
        rs1 = selected_instruction.rs1()
        if rs1 is None:
            raise ValueError("jalr_ra instruction has no rs1")
        rs1.val = self.rs1
        return selected_instruction


class AssertFetchExceptionAction(AssertionBase):
    """
    Assertion Action that checks for an instruction-fetch exception.

    Emits OS_SETUP_CHECK_EXCP with excp_return_label for both excp and return labels,
    overrides check_excp_expected_pc with the target address, then jumps to the target.
    The fetch fault at the target triggers the exception handler which returns to excp_return_label.
    """

    register_fields = ["target", "tval", "htval"]

    def __init__(self, cause: ExceptionCause, target, cause_value: int = 0, tval=0, htval=0, gva_check: bool = False, expected_mode: ExceptionHandlerMode = ExceptionHandlerMode.ANY, **kwargs):
        super().__init__(**kwargs)
        self.cause = cause
        self.target = target  # step_id of any step producing a jump target address
        self.cause_value = cause_value
        self.tval: int = tval
        self.htval: int = htval
        self.gva_check: bool = gva_check
        self.expected_mode: ExceptionHandlerMode = expected_mode
        self.expanded = False
        self.excp_return_label: Optional[str] = None

    def repr_info(self) -> str:
        return f"{self.cause}, target={self.target}"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "AssertFetchExceptionAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssertFetchException)
        if step.step.cause is None:
            raise ValueError("AssertFetchException has no cause. Cause must be specified.")
        cause = step.step.cause
        if isinstance(cause.value, tuple):
            cause_value = int(cause.value[0])
        else:
            cause_value = int(cause.value)
        # inputs[0] = cause, inputs[1] = target, inputs[2] = tval (if not None), next = htval (if not None)
        if len(step.inputs) < 2:
            raise ValueError(f"AssertFetchException expects at least two inputs (cause, target), got {len(step.inputs)}")
        raw_target = step.inputs[1]
        target_id = raw_target if isinstance(raw_target, str) else str(raw_target)
        input_idx = 2
        tval: int = 0
        htval: int = 0
        if step.step.tval is not None:
            raw_tval = step.inputs[input_idx]
            tval = raw_tval if isinstance(raw_tval, int) else 0
            input_idx += 1
        if step.step.htval is not None:
            raw_htval = step.inputs[input_idx]
            htval = raw_htval if isinstance(raw_htval, int) else 0
        return cls(
            step_id=step_id, cause=cause, target=target_id, cause_value=cause_value, tval=tval, htval=htval, gva_check=step.step.gva_check, expected_mode=step.step.expected_handler_mode, **kwargs
        )

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True

        self.excp_return_label = ctx.unique_label("fetch_excp_return_label")

        # li check_excp_expected_pc address
        li_expected_pc = LiAction(step_id=ctx.new_value_id(), immediate="check_excp_expected_pc")

        # If the target is a memory label, load its address via li.
        # Otherwise it's already a register value — use it directly.
        if ctx.mem_reg.is_memory_label(self.target):
            li_target = LiAction(step_id=ctx.new_value_id(), immediate=self.target)
            target_reg = li_target.step_id
            load_address_actions = [li_target]
        else:
            target_reg = self.target
            load_address_actions = []

        # sd target_addr, 0(check_excp_expected_pc_addr)
        sd_action = _SdAction(step_id=ctx.new_value_id(), rs1=li_expected_pc.step_id, rs2=target_reg)
        # jalr ra, target_addr
        call_action = _FetchJalrAction(step_id=ctx.new_value_id(), rs1=target_reg)

        jump_to_fail = AssertionJumpToFail(step_id=ctx.new_value_id())
        exception_return_label = LabelAction(step_id=ctx.new_label(), name=self.excp_return_label)

        return [self, *load_address_actions, li_expected_pc, sd_action, call_action, jump_to_fail, exception_return_label]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        if self.excp_return_label is None:
            raise RuntimeError("excp_return_label not set. Ensure expand() is called.")

        macro = Instruction(
            name="OS_SETUP_CHECK_EXCP",
            extension=Extension.I,
            xlen=Xlen.XLEN32,
            category=Category.PSEUDO,
            destination=None,
            source=[
                Operand(
                    type=OperandType.IMM,
                    name="cause",
                    val=self.cause_value,
                ),
                Operand(
                    type=OperandType.SYMBOL,
                    name="excp_label",
                    val=self.excp_return_label,
                ),
                Operand(
                    type=OperandType.SYMBOL,
                    name="excp_ret_label",
                    val=self.excp_return_label,
                ),
                Operand(
                    type=OperandType.SYMBOL if isinstance(self.tval, (str, tuple)) else OperandType.IMM,
                    name="tval",
                    val=f"({self.tval[0]} + {self.tval[1]})" if isinstance(self.tval, tuple) else self.tval,
                ),
                Operand(
                    type=OperandType.SYMBOL if isinstance(self.htval, (str, tuple)) else OperandType.IMM,
                    name="htval",
                    val=f"(({self.htval[0]}_phys + {self.htval[1]}) >> 2)" if isinstance(self.htval, tuple) else (f"({self.htval}_phys >> 2)" if isinstance(self.htval, str) else self.htval),
                ),
                Operand(
                    type=OperandType.IMM,
                    name="gva_check",
                    val=int(self.gva_check),
                ),
                Operand(
                    type=OperandType.IMM,
                    name="expected_mode",
                    val=self.expected_mode.value,
                ),
            ],
            formatter="OS_SETUP_CHECK_EXCP {cause}, {excp_label}, {excp_ret_label}, {tval}, {htval}, 0, 0, 0, {gva_check}, {expected_mode}",
            clobbers=[get_register("t0").name, get_register("t1").name, get_register("t2").name, get_register("t3").name, "x31"],
        )
        return macro
