# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from coretp.step import SetWaitTimeout

from riescue.dtest_framework.config import Conf
from riescue.compliance.test_plan.implementation_specific.custom_actions import ImplementationSetWaitTimeoutAction


class ImplementationSpecificMapping(Conf):
    """
    Provides implementation-specific mappings from TestStep to Action.
    """

    def __init__(self):
        pass

    def get_mapping(self) -> list[tuple[type, type]]:
        """
        Returns a list of tuples mapping TestStep types to Action types.

        :return: List of (TestStep, Action) tuples
        """
        return [
            (SetWaitTimeout, ImplementationSetWaitTimeoutAction),
        ]


def setup() -> Conf:
    """
    Setup function that returns the implementation-specific Conf instance.

    :return: ImplementationSpecificMapping instance with implementation-specific mappings
    """
    return ImplementationSpecificMapping()
