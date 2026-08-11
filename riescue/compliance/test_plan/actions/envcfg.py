# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, TYPE_CHECKING

from coretp import StepIR, Instruction
from coretp.step import EnableEnvCfg

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.actions.csr import CsrWriteAction, CsrOperation
from riescue.compliance.test_plan.context import LoweringContext


class EnableEnvCfgAction(Action):
    """
    Enable ``mask`` bits in the ``*envcfg`` CSR(s) required for the privilege mode
    the test is being elaborated in.

    Expands into one :class:`CsrWriteAction` (SET) per register returned by
    :meth:`EnableEnvCfg.registers_for` for the current execution mode, reusing the
    existing per-register privilege routing (direct ``csrrs`` vs the M-mode
    ``;#csr_rw`` API path). In M-mode no register is required, so the action
    expands to nothing.
    """

    register_fields: list[str] = []

    def __init__(self, mask: int, **kwargs):
        super().__init__(**kwargs)
        self.mask = mask
        self.constraints = {}
        self.expanded = False

    def repr_info(self) -> str:
        return f"0x{self.mask:x}"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        if TYPE_CHECKING:
            assert isinstance(step.step, EnableEnvCfg)
        return cls(step_id=step_id, mask=step.step.mask, **kwargs)

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        if self.expanded:
            return None
        self.expanded = True

        # Derive the execution-mode token (m/s/u/vs/vu) the same way voyager2's
        # _current_env_priv_token does: V=1 promotes S->vs and U->vu.
        base = ctx.env.priv.name[0].lower()
        if ctx.env.virtualized and base in ("s", "u"):
            token = "v" + base
        else:
            token = base

        return [
            CsrWriteAction(
                step_id=ctx.new_value_id(),
                csr_name=reg,
                operation=CsrOperation.SET,
                value=self.mask,
            )
            for reg in EnableEnvCfg.registers_for(token)
        ]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        raise RuntimeError("EnableEnvCfgAction should have been expanded into CsrWriteActions")
