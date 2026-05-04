# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Any, Optional, Sequence

from coretp import TestEnv, InstructionCatalog, StepIR
from coretp.step import AssertException
from coretp.rv_enums import Extension, Category, OperandType, Xlen, ExceptionCause, ExceptionHandlerMode
from coretp.isa import Instruction, Label, Operand, get_register

from riescue.compliance.test_plan.actions import Action, LabelAction
from riescue.compliance.test_plan.actions.directive import DirectiveAction
from riescue.compliance.test_plan.actions.label_step import LabelTestStepAction
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
        self,
        cause: ExceptionCause,
        code: list[Action],
        cause_value: int = 0,
        tval=0,
        htval=0,
        gva_check: bool = False,
        expected_mode: ExceptionHandlerMode = ExceptionHandlerMode.ANY,
        re_execute: Optional[bool] = None,
        skip_pc_check: bool = False,
        disable_triggers_after: bool = False,
        **kwargs,
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
        self.skip_pc_check: bool = bool(skip_pc_check)
        # Resolve re_execute: scenario-supplied value wins; otherwise use the
        # cause-driven default (BREAKPOINT → re-execute the faulting PC so the
        # trigger-clearing logic in the trap handler can resume cleanly; every
        # other cause advances xepc as usual).
        if re_execute is None:
            re_execute = cause == ExceptionCause.BREAKPOINT
        self.re_execute: bool = bool(re_execute)
        self.disable_triggers_after: bool = bool(disable_triggers_after)

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
        return cls(
            step_id=step_id,
            cause=step.step.cause,
            cause_value=cause_value,
            tval=tval,
            htval=htval,
            gva_check=step.step.gva_check,
            expected_mode=step.step.expected_handler_mode,
            re_execute=step.step.re_execute,
            skip_pc_check=step.step.skip_pc_check,
            disable_triggers_after=step.step.disable_triggers_after,
            **kwargs,
        )

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

        If ``self.code`` contains a ``LabelTestStepAction`` (i.e. the scenario
        author put a ``Label`` TestStep in the code list), that label's name is
        used as ``self.fault_label`` so ``OS_SETUP_CHECK_EXCP`` points at the
        user-provided symbol. In that case, no synthetic fault label is
        injected — the user's Label serves the same role.
        """
        if self.expanded:
            return None
        self.expanded = True

        # Validate code list shape. Supported forms:
        #   [code_step]                     — framework synthesizes a fault label before code_step
        #   [Label, code_step]              — user Label serves as the fault label
        #   [setup..., code_step]           — setup actions emit BEFORE the fault label;
        #                                     synthesized fault label sits before code_step.
        #                                     Used by sdtrig icount scenarios so that
        #                                     ;#trigger_config emits AFTER OS_SETUP_CHECK_EXCP
        #                                     completes writing expected_cause/skip_pc_check,
        #                                     before the count=1 trigger is armed.
        #   [Label, setup..., code_step]    — user label + setup actions before code_step.
        # The last item is always the faulting code step; non-last items are emitted
        # before the fault label.
        if not self.code:
            raise ValueError("AssertException requires at least one code step")
        if isinstance(self.code[-1], LabelTestStepAction):
            raise ValueError("AssertException.code's last item must be the faulting code step, not a Label.")
        # Identify a leading user-supplied Label (optional). Any other non-Label
        # actions before the last are treated as "setup" emitted before the
        # fault label.
        user_label_action: Optional[LabelTestStepAction] = None
        setup_start_idx = 0
        if len(self.code) >= 2 and isinstance(self.code[0], LabelTestStepAction):
            user_label_action = self.code[0]
            setup_start_idx = 1
        # Any further LabelTestStepAction inside the code list is a mistake.
        for c in self.code[setup_start_idx:-1]:
            if isinstance(c, LabelTestStepAction):
                raise ValueError("AssertException.code may contain at most one leading Label.")

        if user_label_action is not None:
            self.fault_label = user_label_action.name
            synthetic_exception_label = None
        else:
            self.fault_label = ctx.unique_label("fault_label")
            synthetic_exception_label = LabelAction(
                step_id=ctx.new_label(),
                name=self.fault_label,
                instruction_pointer=True,
            )

        # For BREAKPOINT, we want the trap handler to resume at the faulting PC
        # so the instruction that triggered the breakpoint actually executes
        # (the scenario is responsible for disabling the trigger before re-entry
        # to avoid re-fire loops). That is signaled to the macro/handler via
        # ``__re_execute=1`` in pick_instruction(); the return label below is
        # still a fresh symbol because the macro consumes it unconditionally.
        self.excp_return_label = ctx.unique_label("excp_return_label")
        exception_return_label = LabelAction(step_id=ctx.new_label(), name=self.excp_return_label)
        jum_to_fail = AssertionJumpToFail(step_id=ctx.new_value_id())

        # Expanding in place because label needs to be directly before the failing instruction.
        # All actions except the last are expanded normally; the last (faulting) action's
        # expansion is spliced so the label lands right before the faulting action, as
        # indicated by its ``fault_expansion_index`` (the default ``-1`` keeps the old
        # "label before last" behavior for actions whose expansion prepends preparation).
        expanded_code: list[Action] = []
        for code in self.code[:-1]:
            new_code = code.expand(ctx)
            if new_code is not None:
                expanded_code.extend(new_code)
            else:
                expanded_code.append(code)

        last_action = self.code[-1]
        last_expansion = last_action.expand(ctx)
        if last_expansion is None:
            last_expansion = [last_action]

        fault_pos = last_action.fault_expansion_index
        if fault_pos < 0:
            fault_pos = len(last_expansion) + fault_pos
        expanded_code.extend(last_expansion[:fault_pos])
        # Only inject the synthetic fault label when the user didn't supply a
        # Label themselves — otherwise emitting both would either duplicate the
        # symbol or position it wrong relative to the trigger's target PC. In
        # the user-supplied case, the LabelTestStepAction was expanded as part
        # of the self.code[:-1] loop above and already sits at the position
        # the scenario author chose.
        if synthetic_exception_label is not None:
            expanded_code.append(synthetic_exception_label)
        expanded_code.extend(last_expansion[fault_pos:])

        # When __re_execute=1 the trap handler returns to the faulting PC. With
        # the plan-wide ``excp_handler_post`` body installed (see TestPlan.excp_handler_post,
        # invoked when ``--excp_hooks`` is set), the trigger is cleared in the
        # handler, so the re-fetched faulting instruction now completes
        # cleanly — that *is* the success path. Without this skip jump the clean
        # re-execution would fall straight into the AssertionJumpToFail
        # trampoline below (li failed_addr; ld; jr → test_failed) and fail the
        # test. Emit ``j <excp_return_label>`` right after the faulting code to
        # branch over jum_to_fail. The non-re-execute path is unaffected because
        # the handler writes xepc=excp_return_label and xret skips this code
        # entirely.
        if self.re_execute:
            expanded_code.append(
                DirectiveAction(
                    step_id=ctx.new_value_id(),
                    directive=f"j {self.excp_return_label}",
                )
            )

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
                Operand(
                    type=OperandType.IMM,
                    name="re_execute",
                    # Pass as str so coretp's Instruction.format() doesn't run
                    # int values through format_offset (which hex-formats positives
                    # as "0x1"). The macro takes a plain integer flag.
                    val="1" if self.re_execute else "0",
                ),
                Operand(
                    type=OperandType.IMM,
                    name="skip_pc_check",
                    val="1" if self.skip_pc_check else "0",
                ),
                Operand(
                    type=OperandType.IMM,
                    name="disable_triggers_after",
                    val="1" if self.disable_triggers_after else "0",
                ),
            ],
            formatter="OS_SETUP_CHECK_EXCP {cause}, {excp_label}, {excp_ret_label}, {tval}, {htval}, {skip_pc_check}, 0, 0, {gva_check}, {expected_mode}, {re_execute}, {disable_triggers_after}",
            clobbers=[get_register("t0").name, get_register("t1").name, get_register("t2").name, get_register("t3").name, "x31"],
        )
        return macro
