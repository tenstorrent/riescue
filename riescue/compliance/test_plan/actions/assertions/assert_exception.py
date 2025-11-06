# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Any, Optional, Sequence

from coretp import TestEnv, InstructionCatalog, StepIR
from coretp.step import AssertException
from coretp.rv_enums import Extension, Category, OperandType, Xlen, ExceptionCause
from coretp.isa import Instruction, Label, Operand

from riescue.compliance.test_plan.actions import Action, LabelAction
from riescue.compliance.test_plan.context import LoweringContext
from .assertion_base import AssertionBase, AssertionJumpToFail


class AssertExceptionAction(AssertionBase):
    """
    Assertion Action that checks for an exception.

    Assumes that failing action is the **last** action in the code list
    """

    register_fields = []

    def __init__(self, cause: ExceptionCause, code: list[Action], cause_value: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.cause = cause
        self.expanded = False
        self.code = code

        self.fault_label = None
        self.excp_return_label = None
        self.cause_value: int = cause_value

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
        return cls(step_id=step_id, cause=step.step.cause, cause_value=cause_value, **kwargs)

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
        actions = [self, *expanded_code, jum_to_fail, exception_return_label]
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
            ],
            formatter="OS_SETUP_CHECK_EXCP {cause}, {excp_label}, {excp_ret_label}",
        )
        return macro
