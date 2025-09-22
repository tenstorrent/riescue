# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional

from coretp import TestScenario

from riescue.lib.rand import RandNum
from riescue.compliance.test_plan.actions import DefaultActionRegistry, ActionRegistry
from riescue.compliance.test_plan.types import DiscreteTest


class TestPlanFactory:
    """
    Factory for turning ``TestScenario`` objects into ``DiscreteTest`` objects.

    Concerned with gathering correct ``TestStep`` objects and driving ``DiscreteTest`` generation.
    Doesn't select environment, randomize, or solve


    :meth:`TestPlanFactory.build()`: Generate a list of ``DiscreteTest`` objects from the ``TestScenario``.
    :param rng: :class:`RandNum` object to use for randomization.
    :param action_registry: :class:`ActionRegistry` object to use for generating actions. If not provided, the default action registry will be used.
    """

    def __init__(
        self,
        action_registry: Optional[ActionRegistry] = None,
    ):

        if action_registry is None:
            self.action_registry = DefaultActionRegistry()
        else:
            self.action_registry = action_registry

    def build(self, scenario: TestScenario) -> DiscreteTest:
        """
        Create a list of ``DiscreteTest`` object(s) from the ``TestScenario``.

        :param scenario: :class:`TestScenario` object to build :class:`DiscreteTest` objects from.
        """
        return DiscreteTest(
            name=scenario.name,
            actions=[self.action_registry.get_action(step) for step in scenario.steps],
            env=scenario.env,
        )
