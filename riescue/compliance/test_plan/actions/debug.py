# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Debug / Sdtrig Actions for RiescueC test plan compilation.

One Action per coretp.step.debug TestStep. Tier 1 actions emit
``;#trigger_config(...)`` / ``;#trigger_enable(...)`` / ``;#trigger_disable(...)``
directives. Tier 2 actions emit ``;#csr_rw(<csr>, ...)`` directives (reusing
the same RiescueD path that CsrWrite / CsrRead use).
"""

from typing import Optional, Sequence, TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.step.debug import (
    ConfigureExecuteTrigger,
    ConfigureLoadTrigger,
    ConfigureStoreTrigger,
    ConfigureLoadStoreTrigger,
    ConfigureIcountTrigger,
    ConfigureItrigger,
    ConfigureEtrigger,
    EnableTrigger,
    DisableTrigger,
    SelectTrigger,
    WriteTriggerCsr,
    ReadTriggerCsr,
    TriggerAction,
    TriggerMatch,
    TriggerPrivMode,
)

from riescue.compliance.test_plan.actions.action import Action
from riescue.compliance.test_plan.actions.directive import DirectiveInstruction
from riescue.compliance.test_plan.actions.csr import CsrApiInstruction, LiT2Action
from riescue.compliance.test_plan.context import LoweringContext


# When the scenario leaves priv_mode unset, the TestStep default is ("env",).
# The translate-path default (what `;#trigger_config` assumes when the directive
# omits `priv_mode=`) is the SdtrigGeneratorMixin default: ("m","s","u").
_DIRECTIVE_DEFAULT_PRIV_MODE = ("m", "s", "u")


def _resolve_env_priv(ctx: LoweringContext) -> str:
    """Resolve the ``"env"`` sentinel to the concrete priv-mode token for this
    test's runtime mode, accounting for the virtualized flag."""
    priv_char = ctx.env.priv.name[0].lower()  # "m", "s", or "u"
    if getattr(ctx.env, "virtualized", False):
        if priv_char == "s":
            return "vs"
        if priv_char == "u":
            return "vu"
    return priv_char


def _priv_mode_fragment(priv_mode: Sequence, ctx: LoweringContext) -> str:
    """Return directive fragment ``, priv_mode=[...]`` — resolves the ``"env"``
    sentinel against ``ctx.env.priv`` and omits the fragment when the resolved
    tuple matches the directive-path default (``("m","s","u")``)."""
    tokens = []
    for p in priv_mode:
        t = p.value if isinstance(p, TriggerPrivMode) else p
        if t == "env":
            tokens.append(_resolve_env_priv(ctx))
        else:
            tokens.append(t)
    tokens = tuple(tokens)
    if tokens == _DIRECTIVE_DEFAULT_PRIV_MODE:
        return ""
    return f", priv_mode=[{','.join(tokens)}]"


def _action_str(action) -> str:
    if isinstance(action, TriggerAction):
        return action.directive_str
    return str(action)


def _match_fragment(match) -> str:
    m_str = match.directive_str if isinstance(match, TriggerMatch) else str(match)
    return f", match={m_str}" if m_str != "equal" else ""


# ---------------------------------------------------------------------------
# Tier 1 — typed trigger configuration Actions
# ---------------------------------------------------------------------------


class _TriggerDirectiveAction(Action):
    """Base helper for trigger-directive-emitting actions.

    Subclasses store the coretp TestStep itself (``self.spec``) and implement
    ``_build_directive(ctx)`` — the directive is produced at ``pick_instruction``
    time so ``priv_mode="env"`` sentinels resolve against the current
    ``LoweringContext``'s env.priv.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, spec, **kwargs):
        super().__init__(step_id=step_id)
        self.spec = spec  # original frozen-dataclass TestStep

    def repr_info(self) -> str:
        return f"{self.spec.__class__.__name__}(index={getattr(self.spec, 'index', '?')})"

    def _build_directive(self, ctx: LoweringContext) -> str:
        raise NotImplementedError

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return DirectiveInstruction(directive=self._build_directive(ctx))


class ConfigureExecuteTriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureExecuteTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureExecuteTrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        s: ConfigureExecuteTrigger = self.spec
        priv = _priv_mode_fragment(s.priv_mode, ctx)
        match_str = _match_fragment(s.match)
        return f";#trigger_config(index={s.index}, type=execute, addr={s.addr}, action={_action_str(s.action)}{priv}{match_str})\n"


class ConfigureLoadTriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureLoadTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureLoadTrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        s: ConfigureLoadTrigger = self.spec
        size_str = f", size={s.size}" if s.size != 4 else ""
        priv = _priv_mode_fragment(s.priv_mode, ctx)
        match_str = _match_fragment(s.match)
        return f";#trigger_config(index={s.index}, type=load, addr={s.addr}, action={_action_str(s.action)}{size_str}{priv}{match_str})\n"


class ConfigureStoreTriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureStoreTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureStoreTrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        s: ConfigureStoreTrigger = self.spec
        size_str = f", size={s.size}" if s.size != 4 else ""
        priv = _priv_mode_fragment(s.priv_mode, ctx)
        match_str = _match_fragment(s.match)
        return f";#trigger_config(index={s.index}, type=store, addr={s.addr}, action={_action_str(s.action)}{size_str}{priv}{match_str})\n"


class ConfigureLoadStoreTriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureLoadStoreTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureLoadStoreTrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        s: ConfigureLoadStoreTrigger = self.spec
        priv = _priv_mode_fragment(s.priv_mode, ctx)
        match_str = _match_fragment(s.match)
        return f";#trigger_config(index={s.index}, type=load_store, addr={s.addr}, action={_action_str(s.action)}{priv}{match_str})\n"


class ConfigureIcountTriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureIcountTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureIcountTrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        s: ConfigureIcountTrigger = self.spec
        pending_str = f", pending={s.pending}" if s.pending != 0 else ""
        # whisper's per-slot WARL allows icount-shaped tdata1 writes only on
        # trigger slot 8 (whisper_config.json triggers[8].mask = 0xf800000007ffffc7).
        # Slots 0-7 have an mcontrol6-shaped mask that snaps any type=3 write to
        # type=15 DISABLED. Force index=8 here so icount triggers actually arm.
        # voyager2's sdtrig_stress plugin already uses index=8 for the same reason.
        #
        # Priv-mode resolution:
        #   - If user used the default ("env",) sentinel, target the test's
        #     runtime priv mode directly (M, S, or U) — no special-case
        #     exclusion for M. With A2 (cfg lives inside AssertException.code)
        #     plus skip_pc_check=True the syscall-return fire is tolerated, so
        #     M-mode tests can legitimately have the trigger arm in M.
        #   - If the user supplied an explicit priv_mode tuple, use it as-is.
        if tuple(s.priv_mode) == ("env",):
            forced_modes: tuple[str, ...] = (_resolve_env_priv(ctx),)
        else:
            forced_modes = tuple(s.priv_mode)
        priv = _priv_mode_fragment(forced_modes, ctx)
        return f";#trigger_config(index=8, type=icount, count={s.count}, action={_action_str(s.action)}{priv}{pending_str})\n"


class ConfigureItriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureItriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureItrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        s: ConfigureItrigger = self.spec
        priv = _priv_mode_fragment(s.priv_mode, ctx)
        return f";#trigger_config(index={s.index}, type=itrigger, addr={hex(s.interrupt_mask)}, action={_action_str(s.action)}{priv})\n"


class ConfigureEtriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConfigureEtriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConfigureEtrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        s: ConfigureEtrigger = self.spec
        priv = _priv_mode_fragment(s.priv_mode, ctx)
        return f";#trigger_config(index={s.index}, type=etrigger, addr={hex(s.exception_mask)}, action={_action_str(s.action)}{priv})\n"


class EnableTriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "EnableTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, EnableTrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        return f";#trigger_enable(index={self.spec.index})\n"


class DisableTriggerAction(_TriggerDirectiveAction):
    register_fields: list[str] = []

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "DisableTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, DisableTrigger)
        return cls(step_id=step_id, spec=step.step)

    def _build_directive(self, ctx: LoweringContext) -> str:
        return f";#trigger_disable(index={self.spec.index})\n"


# ---------------------------------------------------------------------------
# Tier 2 — raw trigger-CSR access Actions
# ---------------------------------------------------------------------------


class SelectTriggerAction(Action):
    """Emit ``li t2, index`` + ``;#csr_rw(tselect, write, false, false)``."""

    register_fields: list[str] = []

    def __init__(self, step_id: str, index: int, **kwargs):
        super().__init__(step_id=step_id)
        self.index = index
        self.expanded = False

    def repr_info(self) -> str:
        return f"index={self.index}"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "SelectTriggerAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, SelectTrigger)
        s: SelectTrigger = step.step
        return cls(step_id=step_id, index=s.index)

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True
        li = LiT2Action(step_id=ctx.new_value_id(), immediate=self.index)
        return [li, self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return CsrApiInstruction(
            csr_name="tselect",
            src=None,
            direct_read_write=False,
            # tselect is M-only; SelectTrigger has no direct/indirect knob, so
            # always route through the M-mode helper. Without this, the
            # generated csrrw to tselect raises ILLEGAL_INSTRUCTION in S/U
            # tests.
            force_machine_rw=True,
            name="csrw",
            api_call="write",
            instruction_id=self.step_id,
        )


class WriteTriggerCsrAction(Action):
    """Write a raw value to any trigger-related CSR.

    Emits ``li t2, <value>`` + ``;#csr_rw(<csr_name>, write, <direct_write>, false)``.
    Accepts either an immediate int ``value`` or a step-dependency ``src`` id
    (resolved from ``step.inputs`` like ``CsrWriteAction``).
    """

    register_fields: list[str] = ["src"]

    def __init__(
        self,
        step_id: str,
        csr_name: str,
        value: Optional[int] = None,
        src: Optional[str] = None,
        direct_write: bool = False,
        **kwargs,
    ):
        super().__init__(step_id=step_id)
        self.csr_name = csr_name
        self.write_value = value
        self.src = src
        self.direct_write = direct_write
        self.expanded = False

    def repr_info(self) -> str:
        if self.write_value is not None:
            return f"'{self.csr_name}', value={self.write_value}"
        return f"'{self.csr_name}', src={self.src}"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "WriteTriggerCsrAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, WriteTriggerCsr)
        s: WriteTriggerCsr = step.step
        value = s.value if isinstance(s.value, int) else None
        src = None
        if value is None and len(step.inputs) >= 1:
            src = str(step.inputs[0])
        return cls(
            step_id=step_id,
            csr_name=s.csr_name,
            value=value,
            src=src,
            direct_write=s.direct_write,
        )

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        if self.expanded:
            return None
        self.expanded = True
        if self.src is not None:
            # Value comes from a prior step's GPR — LI-into-t2 is emitted by the
            # translate step (`_add_write_trigger_csr_step` mirrors CsrWrite).
            return [self]
        if self.write_value is None:
            self.write_value = 0
        li_id = ctx.new_value_id()
        load_immediate = LiT2Action(step_id=li_id, immediate=self.write_value)
        self.src = li_id
        return [load_immediate, self]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return CsrApiInstruction(
            csr_name=self.csr_name,
            src=self.src,
            direct_read_write=self.direct_write,
            # When the scenario asks for an indirect write (direct_write=False),
            # force the M-mode helper path. Trigger CSRs (tdata1/tdata2/tselect/
            # tinfo) are M-only; without this flag, RiescueD leaves the literal
            # csrw in place and the write raises ILLEGAL_INSTRUCTION when the
            # test runs in S/U.
            force_machine_rw=not self.direct_write,
            name="csrw",
            api_call="write",
            instruction_id=self.step_id,
        )


class ReadTriggerCsrAction(Action):
    """Read any trigger-related CSR.

    Emits ``;#csr_rw(<csr_name>, read, <direct_read>, false)`` — RiescueD
    expands this to ``csrr t2, <csr>``. Downstream translate step copies t2
    into a fresh reserved register when the step's id is consumed.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, csr_name: str, direct_read: bool = False, **kwargs):
        super().__init__(step_id=step_id)
        self.csr_name = csr_name
        self.direct_read = direct_read

    def repr_info(self) -> str:
        return f"'{self.csr_name}'"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ReadTriggerCsrAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ReadTriggerCsr)
        s: ReadTriggerCsr = step.step
        return cls(step_id=step_id, csr_name=s.csr_name, direct_read=s.direct_read)

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return CsrApiInstruction(
            csr_name=self.csr_name,
            src=None,
            direct_read_write=self.direct_read,
            # When the scenario asks for an indirect read (direct_read=False),
            # force the M-mode helper path. tdata1/tdata2/tselect/tinfo are
            # M-only CSRs; without this flag, RiescueD's `;#csr_rw` directive
            # falls through to a literal csrr and the read raises
            # ILLEGAL_INSTRUCTION when the test runs in S/U.
            force_machine_rw=not self.direct_read,
            name="csrr",
            api_call="read",
            instruction_id=self.step_id,
        )
