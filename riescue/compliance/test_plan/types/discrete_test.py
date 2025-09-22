# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass
from typing import TYPE_CHECKING

from coretp import TestEnvCfg

if TYPE_CHECKING:
    from riescue.compliance.test_plan.actions import Action


@dataclass
class DiscreteTest:
    """
    Container for a single generated test.

    :param name: Name of the test.
    :param actions: List of :class:`Action` IR objects for generating test.
    :param env: :class:`TestEnvCfg` object for the test environment.
    """

    name: str
    actions: list["Action"]
    env: TestEnvCfg
