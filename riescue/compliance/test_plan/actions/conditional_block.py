# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Optional

from coretp import Instruction, StepIR
from coretp.step import ConditionalBlock
from coretp.rv_enums import Extension
from riescue.compliance.test_plan.actions import Action, LiAction, CodeMixin
from riescue.compliance.test_plan.context import LoweringContext


class ConditionalBlockAction(Action, CodeMixin):
    """
    Evaluate a condditional block at generation time.
    Conditional blocks should not have code depending on them, as evaluating to false doesn't execute the code


    """

    register_fields = ["memory"]

    def __init__(
        self,
        code: list[Action],
        features: list[Extension],
        requested_mem: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._code = code

        # enabled features should probably be a flag rather than list
        self.features: Extension = Extension.I  # default to I extension since it's always enabled
        for feature in features:
            self.features |= feature

        self.requested_mem = requested_mem
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "ConditionalBlockAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, ConditionalBlock)
        features = step.step.enabled_features
        memory = None
        for src in step.inputs:
            if isinstance(src, str):
                memory = src
                break

        if not features and memory is None:
            raise ValueError("ConditionalBlock must have either features or requested_mem")
        return cls(step_id=step_id, features=features, requested_mem=memory, **kwargs)

    # CodeMixin methods
    @property
    def code(self) -> list[Action]:
        return self._code

    def update_code(self, code: list[Action]):
        self._code = code

    def repr_info(self) -> str:
        return f"ConditionalBlock(features={self.features}, code={self.code})"

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:

        if self.expanded:
            return None
        self.expanded = True

        actions: list[Action] = []

        if self.features:
            if ctx.instruction_catalog.supports_extensions(self.features):
                actions = [*self.code]
            else:
                actions = []
        elif self.requested_mem:
            if ctx.mem_reg.request_successful(self.requested_mem):
                actions = [*self.code]
            else:
                actions = []
        else:
            raise ValueError("ConditionalBlock must have either features or requested_mem")

        return actions

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        raise RuntimeError("ConditionalBlockAction should not be picked directly")
