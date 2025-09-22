# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass
from typing import TYPE_CHECKING

from coretp import TestEnvCfg

if TYPE_CHECKING:
    from riescue.compliance.test_plan.actions import Action


@dataclass
class ActionBlock:
    """
    Cotainer for a series of ``Action`` objects. Logically groups actions together

    :param name: Name of the test.
    :param actions: List of :class:`Action` IR objects for generating test.
    """

    name: str
    actions: list["Action"]
