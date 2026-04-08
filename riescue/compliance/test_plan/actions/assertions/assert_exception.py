# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Any, Optional, Sequence

from coretp import TestEnv, InstructionCatalog, StepIR
from coretp.step import AssertException
from coretp.rv_enums import Extension, Category, OperandType, Xlen, ExceptionCause, ExceptionHandlerMode
from coretp.isa import Instruction, Label, Operand, get_register

from riescue.compliance.test_plan.actions import Action, LabelAction
from riescue.compliance.test_plan.context import LoweringContext
from .assertion_base import AssertionBase, AssertionJumpToFail


class AssertExceptionMarkerInstruction(Instruction):
    """
    Marker instruction for AssertException block boundaries.

    This instruction doesn't emit any actual assembly code - it's used
    internally to track where AssertException block code starts and ends during
    transformation. This allows the CSR save/restore logic to skip CSRs that
    are only accessed within AssertException blocks (since those accesses are
    expected to cause exceptions).
    """

    def __init__(self, marker_type: str, instruction_id: str):
        super().__init__(
            name=f"assert_exception_{marker_type}",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,
            source=[],
            clobbers=[],
        )
        self.marker_type = marker_type  # "start" or "end"
        self.instruction_id = instruction_id

    def format(self) -> str:
        """Marker instructions don't emit assembly."""
        return f"# assert_exception block {self.marker_type}"


class AssertExceptionMarkerAction(Action):
    """Marker action for AssertException block boundaries (start or end)."""

    register_fields: list[str] = []

    def __init__(self, step_id: str, marker_type: str):
        super().__init__(step_id=step_id)
        self.marker_type = marker_type  # "start" or "end"

    def repr_info(self) -> str:
        return f"marker_type={self.marker_type}"

    def pick_instruction(self, ctx: "LoweringContext") -> Instruction:
        return AssertExceptionMarkerInstruction(
            marker_type=self.marker_type,
            instruction_id=self.step_id,
        )


class AssertExceptionAction(AssertionBase):
    """
    Assertion Action that checks for an exception.

    Assumes that failing action is the **last** action in the code list
    """

    register_fields = ["tval", "htval"]

    def __init__(
        self, cause: ExceptionCause, code: list[Action], cause_value: int = 0, tval=0, htval=0, gva_check: bool = False, expected_mode: ExceptionHandlerMode = ExceptionHandlerMode.ANY, **kwargs
    ):
        super().__init__(**kwargs)
        self.cause = cause
        self.expanded = False
        self.code = code

        self.fault_label = None
        self.excp_return_label = None
        self.cause_value: int = cause_value
        self.tval: int = tval
        self.htval: int = htval
        self.gva_check: bool = gva_check
        self.expected_mode: ExceptionHandlerMode = expected_mode

    def repr_info(self) -> str:
        return f"{self.cause}, code=[{', '.join(repr(act) for act in self.code)}]"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "AssertExceptionAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssertException)
        if step.step.cause is None:
            raise ValueError("AssertException has no cause. Cause must be specified.")
        cause = step.step.cause
        if isinstance(cause.value, tuple):
            cause_value = int(cause.value[0])
        else:
            cause_value = int(cause.value)
        # inputs[0] = cause, inputs[1] = tval (if not None), next = htval (if not None)
        input_idx = 1
        tval: int = 0
        htval: int = 0
        if step.step.tval is not None:
            raw_tval = step.inputs[input_idx]
            tval = raw_tval if isinstance(raw_tval, int) else 0
            input_idx += 1
        if step.step.htval is not None:
            raw_htval = step.inputs[input_idx]
            htval = raw_htval if isinstance(raw_htval, int) else 0
        return cls(step_id=step_id, cause=step.step.cause, cause_value=cause_value, tval=tval, htval=htval, gva_check=step.step.gva_check, expected_mode=step.step.expected_handler_mode, **kwargs)

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        """
        Expands to:
        assert_setup -> dependent -> assert_fail -> done

        which should be

        .. code-block:: asm

            MACRO
            exception_label:
                fault
                j local_test_failed
            exception_return_label:

        Removes code from action aftering expanding, since this method returns all ``*self.code``
        """
        if self.expanded:
            return None
        self.expanded = True

        self.fault_label = ctx.unique_label("fault_label")  # label for faulting action(s)
        self.excp_return_label = ctx.unique_label("excp_return_label")  # Label macro returns to after exception

        exception_label = LabelAction(step_id=ctx.new_label(), name=self.fault_label, instruction_pointer=True)
        exception_return_label = LabelAction(step_id=ctx.new_label(), name=self.excp_return_label)
        jum_to_fail = AssertionJumpToFail(step_id=ctx.new_value_id())

        # Expandin place because label needs to be directly before the failing instruction
        expanded_code = []
        for code in self.code:
            new_code = code.expand(ctx)
            if new_code is not None:
                expanded_code.extend(new_code)
            else:
                expanded_code.append(code)
        # Assuming last action is the failing action, placing label before it
        expanded_code.insert(-1, exception_label)

        # Add markers around AssertException code so CSR save/restore logic
        # can skip CSRs that are only accessed within AssertException blocks
        start_marker = AssertExceptionMarkerAction(step_id=ctx.new_value_id(), marker_type="start")
        end_marker = AssertExceptionMarkerAction(step_id=ctx.new_value_id(), marker_type="end")
        actions = [self, start_marker, *expanded_code, jum_to_fail, end_marker, exception_return_label]
        self.code = []
        return actions

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:  # Createing custom macro here.
        if self.fault_label is None or self.excp_return_label is None:
            raise RuntimeError("Labels not set. Ensure expand() is called.")

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
                    val=self.fault_label,
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
