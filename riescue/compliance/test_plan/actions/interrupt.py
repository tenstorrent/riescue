# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional

from coretp import StepIR, Instruction
from coretp.step import (
    EnableInterrupts,
    DisableInterrupts,
    ConfigureInterruptMode,
    DelegateInterrupt,
    TriggerInterrupt,
    ClearInterrupt,
    AssertInterrupt,
    RegisterInterruptHandler,
)
from coretp.rv_enums import (
    Category,
    Extension,
    Xlen,
    OperandType,
    InterruptCause,
    InterruptMode,
    ExceptionHandlerMode,
    PrivilegeMode,
)
from coretp.isa import Operand, get_register

from riescue.compliance.test_plan.actions import Action, LabelAction
from riescue.compliance.test_plan.actions.directive import DirectiveAction, DirectiveInstruction
from riescue.compliance.test_plan.actions.privilege_mode import SupervisorCodeAction
from riescue.compliance.test_plan.actions.assertions.assertion_base import AssertionBase, AssertionJumpToFail
from riescue.compliance.test_plan.context import LoweringContext


# ---------------------------------------------------------------------------
# Helper: compute bitmask from a tuple of InterruptCause
# ---------------------------------------------------------------------------


def _cause_bitmask(causes: tuple[InterruptCause, ...]) -> int:
    """OR together the bit masks for each interrupt cause."""
    mask = 0
    for c in causes:
        mask |= 1 << c.value
    return mask


# Hideleg bit positions for VS-mode delegation.  Two naming conventions exist:
# HS-level cause codes (SSI=1, STI=5, SEI=9) map to VS hideleg positions (2,6,10);
# VS-specific cause codes (VSSI=2, VSTI=6, VSEI=10) already sit at their hideleg
# positions.  Both conventions are present in test scenarios so we handle both.
_HIDELEG_VS_BIT_MAP = {
    InterruptCause.SSI: 2,
    InterruptCause.STI: 6,
    InterruptCause.SEI: 10,
    InterruptCause.VSSI: 2,
    InterruptCause.VSTI: 6,
    InterruptCause.VSEI: 10,
}


def _hideleg_bitmask(causes: tuple[InterruptCause, ...]) -> int:
    """Compute hideleg bitmask using VS-mode bit positions (SSI->2, STI->6, SEI->10)."""
    mask = 0
    for c in causes:
        bit = _HIDELEG_VS_BIT_MAP.get(c)
        if bit is not None:
            mask |= 1 << bit
    return mask


def _supported(ctx: "LoweringContext", cause: InterruptCause) -> bool:
    """Lookup cpuconfig.interrupts_supported for ``cause``. Causes outside the
    six-bit MSI/MEI/MTI/SSI/SEI/STI set (e.g. COI, PLATFORM) pass through."""
    return ctx.featmgr.cpu_config.interrupts_supported.is_cause_supported(cause.name)


def _filter_supported(ctx: "LoweringContext", causes: tuple[InterruptCause, ...]) -> tuple[InterruptCause, ...]:
    return tuple(c for c in causes if _supported(ctx, c))


def _in_vu_mode(ctx: "LoweringContext") -> bool:
    """True when the test body executes in VU mode (priv=U, V=1)."""
    return ctx.env.priv == PrivilegeMode.U and ctx.env.virtualized


def _wrap_in_supervisor(ctx: "LoweringContext", directive: str) -> list[Action]:
    """Wrap a privileged interrupt set/clear directive in a SupervisorCode block.

    Used when the test body runs in VU mode and the set/clear lowering touches a
    CSR that VU cannot reach directly (``stopei``/``vstopei``/``vstimecmp`` etc.).
    A SupervisorCode block always lands in HS-mode (V=0), where those CSRs are
    accessible and where the VS-level interrupt being manipulated stays delegated
    to VS (so it cannot fire in M or HS while we touch it). The caller must pass
    the HS-context (V=0) variant of the directive.
    """
    directive_action = DirectiveAction(step_id=ctx.new_value_id(), directive=directive)
    return [SupervisorCodeAction(step_id=ctx.new_value_id(), code=[directive_action], block_index=SupervisorCodeAction.next_block_index())]


# ---------------------------------------------------------------------------
# EnableInterruptsAction
# ---------------------------------------------------------------------------


class EnableInterruptsAction(Action):
    """
    Enables per-cause bits in mie/sie and optionally the global MIE/SIE bit.
    Emits: li t1, <bitmask> ; csrs mie/sie, t1 ; ENABLE_MIE / ENABLE_SIE
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, causes: tuple[InterruptCause, ...], handler_mode: ExceptionHandlerMode, global_enable: bool, **kwargs):
        super().__init__(step_id=step_id)
        self.causes = causes
        self.handler_mode = handler_mode
        self.global_enable = global_enable
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "EnableInterruptsAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, EnableInterrupts)
        return cls(
            step_id=step_id,
            causes=step.step.causes,
            handler_mode=step.step.handler_mode,
            global_enable=step.step.global_enable,
            **kwargs,
        )

    def repr_info(self) -> str:
        cause_names = ", ".join(c.name for c in self.causes)
        return f"causes=({cause_names}), handler_mode={self.handler_mode.name}, global={self.global_enable}"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True

        actions: list[Action] = []

        # Drop causes the cpuconfig declares as unsupported so we don't OR
        # their bits into mie/sie or kick off IMSIC bring-up for a line the
        # target can't deliver.
        causes = _filter_supported(ctx, self.causes)

        # Per-cause enable: csrs mie/sie/vsie, <bitmask>
        # We always enable at every privilege level at or above handler_mode so
        # the interrupt can propagate through the delegation chain.  In
        # particular, VS-specific interrupt bits (e.g. VSTIP at mie[6]) are
        # read-only zero in sie, so the only reliable way to set them is to
        # write mie directly from M mode.
        if causes:
            if self.handler_mode == ExceptionHandlerMode.VS:
                # In VS mode use sie (the VS-mode alias for vsie, CSR 0x104).
                # In HS mode use vsie directly (CSR 0x204, accessible from HS).
                # In VU mode the sie alias is unreachable and the csr_rw escalation
                # lands in HS where sie != vsie, so target vsie directly via the
                # M-mode table (force_machine=true; vsie is accessible from M).
                if _in_vu_mode(ctx):
                    xie, force = "vsie", "true"
                else:
                    xie, force = ("sie" if ctx.env.virtualized else "vsie"), "false"
                bitmask = _cause_bitmask(causes)
                # Write mie directly (force_machine_rw=true) so VS-specific bits
                # (e.g. VSTIP at mie[6]) that are read-only zero in sie get set.
                # effectiveVsie is derived from mie, not vsie/sie.
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mie, set, false, true)"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f";#csr_rw({xie}, set, false, {force})"))
            else:
                bitmask = _cause_bitmask(causes)
                # For HS mode: write mie first (M level), then sie (HS level).
                # VS-specific bits (e.g. VSTIP at mie[6]) are read-only zero in
                # sie, so mie must be written directly.
                if self.handler_mode == ExceptionHandlerMode.HS:
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mie, set, false, true)"))
                xie = "mie" if self.handler_mode == ExceptionHandlerMode.MACHINE else "sie"
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f";#csr_rw({xie}, set, false, true)"))

            # MEI/SEI go through the per-hart IMSIC interrupt file. Setting
            # mie.MEIE / sie.SEIE alone is not enough — without eidelivery=1
            # and an eie bit set for the test interrupt ID, the eventual
            # RVMODEL_SET_MEXT_INT / RVMODEL_SET_SEXT_INT write to seteipnum_le
            # only marks an IMSIC pending bit that nobody is delivering, so
            # mip.MEIP / mip.SEIP never asserts. Emit ;#enable_ext_intr_id so
            # the dtest_framework parser flips pool.init_aplic_interrupts=True
            # and runtime/loader.py drops in the full IMSIC+APLIC bring-up at
            # boot (eidelivery, eithreshold, eie, plus APLIC domain/source
            # config). MEI_TEST_ID / SEI_TEST_ID = 1 from rvmodel_macros.h,
            # matching what RVMODEL_SET_M/SEXT_INT later writes to seteipnum_le.
            # parser.py:120 matches ";#enable_ext_intr_id" via line.startswith(),
            # so the directive MUST land at column 0. The downstream emitter
            # tab-indents every Action's output, so we prepend a newline to
            # force the `;#` onto its own column-0 line.
            #
            # VSEI/SGEI go through the IMSIC guest interrupt files. mode=v in the
            # directive sets pool.init_guest_imsic=True so loader.py also
            # initialises the guest files (eidelivery, eithreshold, eie,
            # hstatus.VGEIN=1, hgeie[2]) — VSEI is delivered via guest file 1
            # (VGEIN-selected) and SGEI via guest file 2 (hgeie[2]=1). One
            # directive is enough to bring up the guest IMSIC for either cause.
            if self.handler_mode != ExceptionHandlerMode.VS:
                for cause in causes:
                    if cause == InterruptCause.MEI and self.handler_mode == ExceptionHandlerMode.MACHINE:
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="\n;#enable_ext_intr_id(intr=1, source_mode=edge1, hart=0, state=enabled)"))
                    elif cause == InterruptCause.SEI and self.handler_mode == ExceptionHandlerMode.HS:
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="\n;#enable_ext_intr_id(intr=1, source_mode=edge1, hart=0, state=enabled)"))
            if any(c in (InterruptCause.VSEI, InterruptCause.SGEI) for c in causes):
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="\n;#enable_ext_intr_id(intr=1, source_mode=edge1, hart=0, state=enabled, mode=v)"))

        # Global enable: ENABLE_MIE / ENABLE_SIE / ENABLE_VSIE
        # ENABLE_VSIE writes vsstatus (runs from HS mode regardless of virtualized).
        # Enable at every privilege level at or above handler_mode so higher-mode
        # global enables don't silently mask the interrupt.
        if self.global_enable:
            if self.handler_mode == ExceptionHandlerMode.VS:
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="ENABLE_MIE"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="ENABLE_SIE"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="ENABLE_VSIE"))
            elif self.handler_mode == ExceptionHandlerMode.HS:
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="ENABLE_MIE"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="ENABLE_SIE"))
            else:
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="ENABLE_MIE"))

        return actions if actions else None

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        # Should not be reached after expand, but provide a fallback
        return DirectiveInstruction(directive="# EnableInterrupts (expanded)")


# ---------------------------------------------------------------------------
# DisableInterruptsAction
# ---------------------------------------------------------------------------


class DisableInterruptsAction(Action):
    """
    Disables per-cause bits in mie/sie and optionally the global MIE/SIE bit.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, causes: tuple[InterruptCause, ...], handler_mode: ExceptionHandlerMode, global_disable: bool, **kwargs):
        super().__init__(step_id=step_id)
        self.causes = causes
        self.handler_mode = handler_mode
        self.global_disable = global_disable
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "DisableInterruptsAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, DisableInterrupts)
        return cls(
            step_id=step_id,
            causes=step.step.causes,
            handler_mode=step.step.handler_mode,
            global_disable=step.step.global_disable,
            **kwargs,
        )

    def repr_info(self) -> str:
        cause_names = ", ".join(c.name for c in self.causes)
        return f"causes=({cause_names}), handler_mode={self.handler_mode.name}, global={self.global_disable}"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True

        actions: list[Action] = []

        # Global disable first (before clearing per-cause bits)
        # DISABLE_VSIE writes vsstatus (runs from HS mode regardless of virtualized).
        # Disable at every privilege level at or above handler_mode (mirrors EnableInterrupts).
        if self.global_disable:
            if self.handler_mode == ExceptionHandlerMode.VS:
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="DISABLE_MIE"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="DISABLE_SIE"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="DISABLE_VSIE"))
            elif self.handler_mode == ExceptionHandlerMode.HS:
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="DISABLE_MIE"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="DISABLE_SIE"))
            else:
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="DISABLE_MIE"))

        # Drop unsupported causes so their bits stay out of the xie clear
        # bitmask and we don't emit RVMODEL_CLR_* for a line the target lacks.
        causes = _filter_supported(ctx, self.causes)

        # Per-cause disable: csrc mie/sie/vsie, <bitmask>
        # Mirror the EnableInterrupts logic: clear at every privilege level at or
        # above handler_mode so VS-specific bits (e.g. VSTIP at mie[6]) that are
        # read-only zero in sie/vsie are actually cleared.
        if causes:
            if self.handler_mode == ExceptionHandlerMode.VS:
                # In VS mode use sie (alias for vsie); in HS mode use vsie directly.
                # In VU mode write vsie directly via the M-mode table (the sie alias
                # is unreachable and the csr_rw escalation lands in HS where sie != vsie).
                if _in_vu_mode(ctx):
                    xie, force = "vsie", "true"
                else:
                    xie, force = ("sie" if ctx.env.virtualized else "vsie"), "false"
                bitmask = _cause_bitmask(causes)
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mie, clear, false, true)"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f";#csr_rw({xie}, clear, false, {force})"))

                # VSTIP is cleared explicitly; VSSIP is cleared via hvip by the scenario.
                # VSEI is now delivered via IMSIC guest interrupt file 1 — clear via vstopei.
                for cause in causes:
                    if cause == InterruptCause.VSEI and not ctx.env.virtualized:
                        # Claim guest file 1 (the VGEIN home) via vstopei; t2 holds
                        # the guest interrupt file index. Runs in HS, where the HGEI
                        # macro's hstatus.VGEIN manipulation is legal.
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="li t2, 1"))
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_HGEI_INT(t0, t1, t2)"))
                    elif cause == InterruptCause.STI:
                        if ctx.env.virtualized:
                            # Use force_machine_rw=true so vstimecmp is written via ecall from
                            # HS/M mode — direct csrw stimecmp from VS mode is blocked by VTI=1
                            actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="li t2, -1"))
                            actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(vstimecmp, write, false, true)"))
                        else:
                            actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_VSTIMER_INT(t0, t1)"))
            else:
                bitmask = _cause_bitmask(causes)
                # For HS mode: clear mie first (M level), then sie (HS level).
                if self.handler_mode == ExceptionHandlerMode.HS:
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mie, clear, false, true)"))
                xie = "mie" if self.handler_mode == ExceptionHandlerMode.MACHINE else "sie"
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f";#csr_rw({xie}, clear, false, true)"))

                # clear all bits via rvmodel_macros.h macros
                for cause in causes:
                    if cause == InterruptCause.MEI and self.handler_mode == ExceptionHandlerMode.MACHINE:
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_MEXT_INT(t0, t1)"))
                    elif cause == InterruptCause.SEI and self.handler_mode == ExceptionHandlerMode.HS and not ctx.env.virtualized:
                        # stopei (used by RVMODEL_CLR_SEXT_INT) is HS-mode only; skip in VS mode
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_SEXT_INT(t0, t1)"))
                    elif cause == InterruptCause.SSI:
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_SSW_INT(t0, t1)"))
                    elif cause == InterruptCause.MSI:
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_MSW_INT(t0, t1)"))
                    elif cause == InterruptCause.STI:
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_STIMER_INT(t0, t1)"))
                    elif cause == InterruptCause.MTI:
                        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="RVMODEL_CLR_MTIMER_INT(t0, t1)"))

        return actions if actions else None

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return DirectiveInstruction(directive="# DisableInterrupts (expanded)")


# ---------------------------------------------------------------------------
# ConfigureInterruptModeAction
# ---------------------------------------------------------------------------


class ConfigureInterruptModeAction(Action):
    """
    Sets mtvec/stvec mode to DIRECT or VECTORED.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, mode: InterruptMode, handler_mode: ExceptionHandlerMode, **kwargs):
        super().__init__(step_id=step_id)
        self.mode = mode
        self.handler_mode = handler_mode

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureInterruptModeAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureInterruptMode)
        return cls(
            step_id=step_id,
            mode=step.step.mode,
            handler_mode=step.step.handler_mode,
            **kwargs,
        )

    def repr_info(self) -> str:
        return f"mode={self.mode.name}, handler_mode={self.handler_mode.name}"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        if self.handler_mode == ExceptionHandlerMode.VS:
            macro = "SET_VECTORED_INTERRUPTS_VS" if self.mode == InterruptMode.VECTORED else "SET_DIRECT_INTERRUPTS_VS"
        elif self.mode == InterruptMode.VECTORED:
            macro = "SET_VECTORED_INTERRUPTS" if self.handler_mode == ExceptionHandlerMode.MACHINE else "SET_VECTORED_INTERRUPTS_S"
        else:
            macro = "SET_DIRECT_INTERRUPTS" if self.handler_mode == ExceptionHandlerMode.MACHINE else "SET_DIRECT_INTERRUPTS_S"
        return DirectiveInstruction(directive=macro)


# ---------------------------------------------------------------------------
# DelegateInterruptAction
# ---------------------------------------------------------------------------


class DelegateInterruptAction(Action):
    """
    Delegates / undelegates interrupt causes via mideleg.

    - ``delegate_to=S``: set bits in mideleg (delegate to S-mode)
    - ``delegate_to=M``: clear bits in mideleg (undelegate, trap to M-mode)

    The transformer's CSR save/restore prologue/epilogue automatically saves
    and restores ``mideleg`` whenever this action appears in a test, so no
    explicit per-call snapshot is emitted here.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, causes: tuple[InterruptCause, ...], handler_mode: ExceptionHandlerMode, **kwargs):
        super().__init__(step_id=step_id)
        self.causes = causes
        self.handler_mode = handler_mode
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "DelegateInterruptAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, DelegateInterrupt)
        return cls(
            step_id=step_id,
            causes=step.step.causes,
            handler_mode=step.step.handler_mode,
            **kwargs,
        )

    def repr_info(self) -> str:
        cause_names = ", ".join(c.name for c in self.causes)
        return f"causes=({cause_names}), handler_mode={self.handler_mode.name}"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True

        if self.handler_mode == ExceptionHandlerMode.ANY:
            raise ValueError(f"DelegateInterrupt only supports handler_mode=HS, VS, or MACHINE, got {self.handler_mode.name}")

        actions: list[Action] = []

        if self.handler_mode == ExceptionHandlerMode.VS:
            # Route causes to VS mode: set mideleg first (M→HS), then set hideleg (HS→VS).
            original_empty = not self.causes
            causes = _filter_supported(ctx, self.causes)
            mideleg_bitmask = _cause_bitmask(causes)
            hideleg_bitmask = _hideleg_bitmask(causes)
            if hideleg_bitmask != 0:
                if mideleg_bitmask != 0:
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(mideleg_bitmask)}"))
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mideleg, set, false, true)"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(hideleg_bitmask)}"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(hideleg, set, false, true)"))
            elif original_empty:
                # Empty causes with handler_mode=VS means "undelegate to VS":
                # wipe hideleg (csrw hideleg, zero), leaving mideleg untouched.
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="mv t2, zero"))
                actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(hideleg, write, false, true)"))
        else:
            # Distinguish "caller asked to wipe mideleg" (empty causes) from
            # "caller asked to delegate causes, all of which got filtered". The
            # zero-write path below is only valid for the former.
            original_empty = not self.causes
            causes = _filter_supported(ctx, self.causes)
            bitmask = _cause_bitmask(causes)
            hideleg_bitmask = _hideleg_bitmask(causes)

            if self.handler_mode == ExceptionHandlerMode.HS:
                if bitmask == 0 and original_empty:
                    # Empty causes with handler_mode=HS means "undelegate to HS":
                    # wipe mideleg (csrw mideleg, zero). WARL-hardwired VS bits
                    # (mideleg[10,6,2]) stay 1; writable delegation bits clear.
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive="mv t2, zero"))
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mideleg, write)"))
                elif bitmask != 0:
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mideleg, set)"))
                # Clear the corresponding hideleg bits so the interrupt routes to HS,
                # not VS.  hideleg[6]=1 (set by initial setup) would otherwise
                # redirect VSTIP past HS into VS mode.
                if hideleg_bitmask != 0:
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(hideleg_bitmask)}"))
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(hideleg, clear, false, true)"))
            else:  # ExceptionHandlerMode.MACHINE -> clear delegated bits (undelegate)
                if bitmask != 0:
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"li t2, {hex(bitmask)}"))
                    actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=";#csr_rw(mideleg, clear)"))

        return actions

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return DirectiveInstruction(directive="# DelegateInterrupt (expanded)")


# ---------------------------------------------------------------------------
# TriggerInterruptAction
# ---------------------------------------------------------------------------


class TriggerInterruptAction(Action):
    """
    Triggers a specific interrupt by writing to the appropriate CSR or MMIO.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, cause: InterruptCause, **kwargs):
        super().__init__(step_id=step_id)
        self.cause = cause
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "TriggerInterruptAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, TriggerInterrupt)
        return cls(step_id=step_id, cause=step.step.cause, **kwargs)

    def repr_info(self) -> str:
        return f"cause={self.cause.name}"

    def _directive(self, ctx: LoweringContext, virtualized: bool) -> tuple[str, bool]:
        """Return ``(directive, needs_priv_wrap)`` for setting this interrupt.

        ``needs_priv_wrap`` is True when the directive pokes a CSR that VU mode
        cannot reach (e.g. ``vstimecmp``); such directives must be wrapped in a
        SupervisorCode block when the body runs in VU. MMIO seteipnum pokes and
        ``;#csr_rw`` (auto-escalating) directives work directly from VU and are
        flagged False. ``virtualized`` selects the V=1 vs V=0 macro variant —
        pass False when computing the directive for the HS-mode wrapper.
        """
        if not _supported(ctx, self.cause):
            return f"nop  # TriggerInterrupt {self.cause.name}: unsupported by cpuconfig", False
        if self.cause == InterruptCause.SSI:
            return "RVMODEL_SET_SSW_INT(t0, t1)", False
        elif self.cause == InterruptCause.MSI:
            return "RVMODEL_SET_MSW_INT(t0, t1)", False
        elif self.cause == InterruptCause.STI:
            return "RVMODEL_SET_STIMER_INT(t0, t1)", True
        elif self.cause == InterruptCause.MTI:
            return "RVMODEL_SET_MTIMER_INT(t0, t1)", False
        elif self.cause == InterruptCause.SEI:
            # Supervisor external interrupt — must go through the S-mode IMSIC
            # (RVMODEL_SET_SEXT_INT writes SEI_TEST_ID to seteipnum_le of the
            # current hart's S-IMSIC file). Pure MMIO poke, works from VU.
            return "RVMODEL_SET_SEXT_INT(t0, t1)", False
        elif self.cause == InterruptCause.MEI:
            # Machine external interrupt via M-mode IMSIC (RVMODEL_SET_MEXT_INT
            # writes MEI_TEST_ID to seteipnum_le of the current hart's M-IMSIC
            # file). Requires EnableInterrupts(MEI) to have run first so the
            # IMSIC eidelivery/eie config is in place.
            return "RVMODEL_SET_MEXT_INT(t0, t1)", False
        elif self.cause == InterruptCause.VSSI:
            # hvip.VSSIP (bit 2) — use csr_rw so the access goes through the OS
            # ecall path and works regardless of current privilege mode (VS/HS/VU).
            return "li t2, 0x4\n;#csr_rw(hvip, set, false, true)", False
        elif self.cause == InterruptCause.VSTI:
            # Arms vstimecmp (aliased by stimecmp when V=1). A CSR write that VU
            # cannot perform, so it needs the SupervisorCode wrapper from VU.
            return f"RVMODEL_SET_{'' if virtualized else 'V'}STIMER_INT(t0, t1)", True
        elif self.cause == InterruptCause.VSEI:
            # IMSIC guest interrupt file 1 delivery (analogous to SEI via S-IMSIC).
            # t2 holds the guest interrupt file index (1, the VGEIN home); the HGEI
            # macro pokes that file's seteipnum. Pure MMIO, works from VU. Requires
            # EnableInterrupts(VSEI) to have run first with mode=v so the guest file
            # eidelivery/eie and hstatus.VGEIN=1 are in place.
            return "li t2, 1\nRVMODEL_SET_HGEI_INT(t0, t1, t2)", False
        elif self.cause == InterruptCause.SGEI:
            # Supervisor guest external interrupt via IMSIC guest file 2. t2 holds
            # the guest interrupt file index (2); the HGEI macro pokes its seteipnum.
            # hgeie[2]=1 (set during init) makes hgeip[2] raise hip[12]=SGEIP.
            # Requires EnableInterrupts(SGEI or VSEI) to have run first with mode=v
            # so the guest files are brought up.
            return "li t2, 2\nRVMODEL_SET_HGEI_INT(t0, t1, t2)", False
        elif self.cause == InterruptCause.COI:
            # hvip[13] (virtual LCOFI) — csr_rw auto-escalates.
            return "li t2, 0x2000\n;#csr_rw(hvip, set, false, true)", False
        else:
            # Platform-defined causes
            return f"li t0, (1 << {self.cause.value})\ncsrs mip, t0", False

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True
        _, needs_wrap = self._directive(ctx, ctx.env.virtualized)
        if needs_wrap and _in_vu_mode(ctx):
            # SupervisorCode lands in HS (V=0); emit the V=0 directive variant.
            hs_directive, _ = self._directive(ctx, virtualized=False)
            return _wrap_in_supervisor(ctx, hs_directive)
        return None

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        directive, _ = self._directive(ctx, ctx.env.virtualized)
        return DirectiveInstruction(directive=directive)


# ---------------------------------------------------------------------------
# ClearInterruptAction
# ---------------------------------------------------------------------------


class ClearInterruptAction(Action):
    """
    Clears a previously-triggered interrupt by reversing the MMIO/CSR write
    that TriggerInterruptAction emitted for the same cause.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, cause: InterruptCause, **kwargs):
        super().__init__(step_id=step_id)
        self.cause = cause
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ClearInterruptAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ClearInterrupt)
        return cls(step_id=step_id, cause=step.step.cause, **kwargs)

    def repr_info(self) -> str:
        return f"cause={self.cause.name}"

    def _directive(self, ctx: LoweringContext, virtualized: bool) -> tuple[str, bool]:
        """Return ``(directive, needs_priv_wrap)`` for clearing this interrupt.

        ``needs_priv_wrap`` is True when the clear claims/writes a CSR that VU
        mode cannot reach (``stopei``/``vstopei``/``vstimecmp``); those must be
        wrapped in a SupervisorCode block when the body runs in VU. ``;#csr_rw``
        directives auto-escalate and are flagged False. ``virtualized`` selects
        the V=1 vs V=0 macro variant — pass False for the HS-mode wrapper.
        """
        if not _supported(ctx, self.cause):
            return f"nop  # ClearInterrupt {self.cause.name}: unsupported by cpuconfig", False
        if self.cause == InterruptCause.SSI:
            return "RVMODEL_CLR_SSW_INT(t0, t1)", False
        elif self.cause == InterruptCause.MSI:
            return "RVMODEL_CLR_MSW_INT(t0, t1)", False
        elif self.cause == InterruptCause.STI:
            return "li t0, -1        # push stimecmp deadline to max\ncsrw stimecmp, t0", True
        elif self.cause == InterruptCause.MTI:
            asm = "\n".join(
                [
                    "li t0, 0x02004000  # CLINT mtimecmp",
                    "li t1, -1          # push mtimecmp deadline to max",
                    "sd t1, 0(t0)",
                ]
            )
            return asm, False
        elif self.cause == InterruptCause.SEI:
            # S-IMSIC clear via stopei. mip is M-mode-only so the `csrc mip`
            # fallback is dropped. stopei needs S/HS — wrap from VU.
            return "RVMODEL_CLR_SEXT_INT(t0, t1)", True
        elif self.cause == InterruptCause.MEI:
            return "RVMODEL_CLR_MEXT_INT(t0, t1)", False
        elif self.cause == InterruptCause.VSEI:
            # Claim the top external interrupt from the IMSIC guest file. In VS
            # mode use `stopei` (V=1 routes it to the VGEIN-selected guest file);
            # in HS mode use `vstopei` (VGEIN=1). Both need supervisor privilege,
            # so from VU this is wrapped in a SupervisorCode (HS) block.
            if virtualized:
                return "RVMODEL_CLR_SEXT_INT(t0, t1)", True
            # HS clear: t2 holds the guest interrupt file index (1, the VGEIN home).
            return "li t2, 1\nRVMODEL_CLR_HGEI_INT(t0, t1, t2)", True
        elif self.cause == InterruptCause.SGEI:
            # Claim/clear the pending bit in IMSIC guest file 2 (HS-mode CSR ops).
            # When the body runs in VS the source was already claimed by the HS
            # handler, so a VS-mode re-clear is a no-op. From VU we wrap the real
            # HS clear (RVMODEL_CLR_HGEI_INT, guest file index 2) in a SupervisorCode
            # block.
            if virtualized:
                return "# ClearInterrupt SGEI: already claimed by HS handler (no VS-mode access)", True
            return "li t2, 2\nRVMODEL_CLR_HGEI_INT(t0, t1, t2)", True
        elif self.cause == InterruptCause.VSSI:
            # hvip[2] (VSSIP) is the source — csr_rw auto-escalates from any mode.
            return "li t2, 0x4\n;#csr_rw(hvip, clear, false, true)", False
        elif self.cause == InterruptCause.VSTI:
            # Push the timer compare to max, de-asserting the VS timer interrupt.
            # vstimecmp (aliased by stimecmp when V=1) is unreachable from VU, so
            # this is wrapped in a SupervisorCode (HS) block.
            return f"RVMODEL_CLR_{'' if virtualized else 'V'}STIMER_INT(t0, t1)", True
        elif self.cause == InterruptCause.COI:
            # hvip[13] (virtual LCOFI) — csr_rw auto-escalates.
            return "li t2, 0x2000\n;#csr_rw(hvip, clear, false, true)", False
        else:
            return f"li t0, (1 << {self.cause.value})\ncsrc mip, t0", False

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True
        _, needs_wrap = self._directive(ctx, ctx.env.virtualized)
        if needs_wrap and _in_vu_mode(ctx):
            # SupervisorCode lands in HS (V=0); emit the V=0 directive variant.
            hs_directive, _ = self._directive(ctx, virtualized=False)
            return _wrap_in_supervisor(ctx, hs_directive)
        return None

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        directive, _ = self._directive(ctx, ctx.env.virtualized)
        return DirectiveInstruction(directive=directive)


# ---------------------------------------------------------------------------
# AssertInterruptAction
# ---------------------------------------------------------------------------


class AssertInterruptAction(AssertionBase):
    """
    Assertion that a specific interrupt fires and is handled.
    Follows the AssertExceptionAction pattern but accounts for interrupt async latency.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, cause: InterruptCause, code: list[Action], expected_mode: ExceptionHandlerMode = ExceptionHandlerMode.ANY, **kwargs):
        super().__init__(step_id=step_id)
        self.cause = cause
        self.code = code
        self.expanded = False
        self.trigger_label = None
        self.intr_return_label = None
        self.expected_mode = expected_mode

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "AssertInterruptAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, AssertInterrupt)
        if step.step.cause is None:
            raise ValueError("AssertInterrupt has no cause. Cause must be specified.")
        return cls(
            step_id=step_id,
            cause=step.step.cause,
            expected_mode=step.step.expected_handler_mode,
            **kwargs,
        )

    def repr_info(self) -> str:
        return f"{self.cause.name}, code=[{', '.join(repr(act) for act in self.code)}]"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True

        # If the cpuconfig says this cause isn't supported, the trigger nested
        # inside would also be NOP'd and the OS_SETUP_CHECK_INTR setup would
        # never fire — collapse the whole assertion to a single nop so the
        # slot still produces one line of assembly.
        if not _supported(ctx, self.cause):
            self.code = []
            return [DirectiveAction(step_id=ctx.new_value_id(), directive=f"nop  # AssertInterrupt {self.cause.name}: unsupported by cpuconfig")]

        self.trigger_label = ctx.unique_label("intr_trigger_label")
        self.intr_return_label = ctx.unique_label("intr_return_label")

        trigger_label_action = LabelAction(step_id=ctx.new_label(), name=self.trigger_label, instruction_pointer=True)
        return_label_action = LabelAction(step_id=ctx.new_label(), name=self.intr_return_label)
        jump_to_fail = AssertionJumpToFail(step_id=ctx.new_value_id())

        # Expand nested code actions
        expanded_code = []
        for code_action in self.code:
            new_code = code_action.expand(ctx)
            if new_code is not None:
                expanded_code.extend(new_code)
            else:
                expanded_code.append(code_action)

        # Insert NOP sled after code for interrupt latency (RVMODEL_INTERRUPT_LATENCY = 10 cycles)
        nop_sled = DirectiveAction(step_id=ctx.new_value_id(), directive="\n".join(["nop"] * 10 + ["# end interrupt latency window"]))

        # Sequence: setup_macro, trigger_label, code..., nop_sled, jump_to_fail, return_label
        actions = [self, trigger_label_action, *expanded_code, nop_sled, jump_to_fail, return_label_action]
        self.code = []
        return actions

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        if self.trigger_label is None or self.intr_return_label is None:
            raise RuntimeError("Labels not set. Ensure expand() is called.")

        macro = Instruction(
            name="OS_SETUP_CHECK_INTR",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.PSEUDO,
            destination=None,
            source=[
                Operand(type=OperandType.IMM, name="cause", val=self.cause.value),
                Operand(type=OperandType.SYMBOL, name="intr_label", val=self.trigger_label),
                Operand(type=OperandType.SYMBOL, name="intr_ret_label", val=self.intr_return_label),
                Operand(type=OperandType.IMM, name="expected_mode", val=self.expected_mode.value),
            ],
            formatter="OS_SETUP_CHECK_INTR {cause}, {intr_label}, {intr_ret_label}, {expected_mode}",
            clobbers=[get_register("t0").name, get_register("t1").name, get_register("t2").name],
        )
        return macro


# ---------------------------------------------------------------------------
# RegisterInterruptHandlerAction
# ---------------------------------------------------------------------------


class RegisterInterruptHandlerAction(Action):
    """
    Registers a custom interrupt handler for a vector.
    Emits the CUSTOM_HANDLER_PROLOGUE macro and handler body.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, cause: InterruptCause, handler_asm: str, **kwargs):
        super().__init__(step_id=step_id)
        self.cause = cause
        self.handler_asm = handler_asm
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "RegisterInterruptHandlerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, RegisterInterruptHandler)
        return cls(
            step_id=step_id,
            cause=step.step.cause,
            handler_asm=step.step.handler_asm,
            **kwargs,
        )

    def repr_info(self) -> str:
        return f"cause={self.cause.name}, handler_asm='{self.handler_asm[:40]}...'"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True

        handler_label = ctx.unique_label(f"custom_isr_{self.cause.name}")
        vec_num = self.cause.value

        actions: list[Action] = []

        # Install the custom handler via CUSTOM_HANDLER_PROLOGUE_V
        actions.append(DirectiveAction(step_id=ctx.new_value_id(), directive=f"CUSTOM_HANDLER_PROLOGUE_{vec_num} {handler_label}"))

        return actions

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return DirectiveInstruction(directive="# RegisterInterruptHandler (expanded)")
