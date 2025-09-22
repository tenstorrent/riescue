# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging


from coretp import TestEnv

from riescue.compliance.test_plan.actions import Action, CodePageAction
from riescue.compliance.test_plan.context import LoweringContext

# from riescue.compliance.test_plan.types import DiscreteCodePage
from riescue.lib.rand import RandNum

logger = logging.getLogger(__name__)


class Expander:
    """
    Responsible for expanding ``Action`` IR into a flat list of ``Action`` IR objects.

    Supports actions that expand()
    """

    def expand(self, actions: list[Action], ctx: LoweringContext) -> list[Action]:
        """
        Expand a list of ``Action`` IR objects into a flat list of ``Action`` IR objects.
        Calls ``expand()`` on each :class:`Action`. If undefined, returns Action, otherwise returns expanded list.

        Used to flatten list of actions. Separating code pages from test code, allocates code pages

        """
        expanded_test_actions = self._recursive_expand(actions, ctx)
        return expanded_test_actions

    def _recursive_expand(self, actions: list[Action], ctx: LoweringContext) -> list[Action]:
        """
        Recursively expand actions. If no expansion possible, returns original action.
        """

        expanded_actions: list[Action] = []
        for action in actions:

            # Include action in error for debug
            try:
                expanded_action = action.expand(ctx)
            except Exception as e:
                raise RuntimeError(f"Error expanding action {action}") from e

            if expanded_action is None:
                expanded_actions.append(action)
            else:
                expanded_actions.extend(self._recursive_expand(expanded_action, ctx))
        return expanded_actions
