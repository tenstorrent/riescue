# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict


from abc import ABC
from typing import Optional

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.variable.variable import Variable


class BaseMemory(ABC):
    """
    Base class for a memory container.
    These are containers that store and retrieve Variable objects

    They also need to provide some way to allocate code
    """

    def __init__(self, xlen: RV.Xlen, amo_enabled: bool):
        self.xlen = xlen
        self.amo_enabled = amo_enabled

        if self.xlen == RV.Xlen.XLEN32:
            self.swap_type = "word"
        elif self.xlen == RV.Xlen.XLEN64:
            self.swap_type = "dword"
        else:
            raise ValueError(f"Invalid xlen: {self.xlen}")

        self._variables: dict[str, Variable] = {}
        self._offset = 0  # Starting offset for variable from the start of the memory container
        self.variable_size = self.xlen // 8  #: size of variables used as swap space for registers

    def __contains__(self, name: str) -> bool:
        """
        Check if a variable is registered in the memory.
        """
        return name in self._variables

    def register(self, name: str, value: int, size: Optional[int] = None, description: str = "", hart_variable: bool = True) -> Variable:
        """
        Register a variable with the memory.

        Subclasses should just wrap this method
        """

        if name in self._variables:
            raise ValueError(f"Variable {name} already registered in HartContext")
        if size is None:
            size = self.variable_size
        new_variable = Variable(
            name=name,
            value=value,
            size=size,
            offset=self._offset,
            description=description,
            amo_enabled=self.amo_enabled,
            hart_variable=hart_variable,
        )
        # for alignment reasons, going to keep all variables the same size even if they only need a 1 or 2 bytes
        self._offset += self.variable_size
        self._variables[name] = new_variable
        return self._variables[name]

    def get_variable(self, name: str) -> Optional[Variable]:
        """
        Get a variable by name.

        :param name: Name of the variable
        :return: The variable object
        """
        return self._variables.get(name)
