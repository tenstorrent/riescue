# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
discrete_test module holds the implementation details of each discrete test
defined in the .s file. It implements following for each test
  - app/vm
  - code block
  - data block
  - page_map
  - pages
"""

import riescue.lib.enums as RV


class DiscreteTest:
    """
    Base class for the discrete test. Store and implement common logic between
    app and vm type of the tests
    """

    def __init__(self, name, priv: RV.RiscvPrivileges):
        self.name = name
        self.priv = priv

        self.paging_mode = RV.RiscvPagingModes.SV32

        # Setup the DiscreteTest
        self.page_map = None
        self.pages = dict()

    def setup_test(self):
        # Setup page_map, code_page etc
        pass

    def __str__(self) -> str:
        s = ""
        s += f"Discrete test: {self.name}\n"
        s += f"\tlabel: {self.name}\n"
        s += f"\tprivilege mode: {self.priv}\n"
        s += f"\tpaging mode: {self.paging_mode}\n"

        return s
