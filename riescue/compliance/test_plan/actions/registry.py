# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Union

from coretp import TestStep, StepIR

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.memory import MemoryRegistry
from riescue.lib.rand import RandNum


class ActionRegistry:
    """
    Registry for all default and user test Actions. Used to map TestStep object to Action objects.

    Mapping is set of tuples of (TestStep, Action) externally defined. To use default:

    .. code-block:: python

        from riescue.compliance.test_plan.actions.registry import DefaultActionRegistry

        registry = DefaultActionRegistry()
        registry.register(Load, CustomLoadAction)

    """

    def __init__(self, mapping: list[tuple[type[TestStep], type[Action]]]):
        self._map = dict(mapping)

        self._id = 0  # Tracks unique IDs for each generated Action

    def get_action(self, step: StepIR) -> Action:
        """Retrieve"""
        step_type = type(step)
        # FIXME: Remove this when StepIR is fully implemented and Step->Action is removed
        if isinstance(step, StepIR):
            if step.step is not None:
                step_type = type(step.step)
        if step_type not in self._map:
            raise RuntimeError(f"StepIR {step} has no step associated with it. Please register the action with the ActionRegistry.")

        # handle nested code actions, not all code actions supprot code
        kwargs = {}
        code_actions = [self.get_action(s) for s in step.code]  # Handle nested code actions
        if code_actions:
            kwargs = {"code": code_actions}  # Only include when there are code actions. Not all Actions support code

        try:
            action = self._map[step_type].from_step(step_id=step.id, step=step, **kwargs)
        except TypeError as e:
            if "unexpected keyword argument 'code'" in str(e):
                raise ValueError(f"Error initializing TestStep - {step_type.__name__} does not support 'code' attribute") from e
            else:
                raise ValueError(f"Error initializing step {step_type.__name__} ({step_type}): {e}") from e

        self._id += 1
        return action

    def register(self, step: type[TestStep], action_cls: type[Action]):
        "generated method, will revist when bringing up strong test flow"
        self._map[step] = action_cls
