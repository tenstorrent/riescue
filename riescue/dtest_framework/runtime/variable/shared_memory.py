# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

from riescue.dtest_framework.runtime.variable.variable import Variable
from .memory import BaseMemory

"""
Classes for generating hart-local memory and variables
"""


class SharedMemory(BaseMemory):
    """
    Generates hart-local context and allocates variables.

    :attr var_offset: Offset for reserved, internal variables in the context.

    :param xlen: The width of the variables. Since context contains swap variables, needs to match the xlen of the hart.
    :param amos_enabled: Whether AMOS is enabled. If False, hart context needs to have both test sp and runtime sp. If true can just swap the values.


    """

    def __init__(self, section_name: str, **kwargs: Any):
        self.section_name = section_name
        super().__init__(**kwargs)

    def register(self, *args: Any, **kwargs: Any) -> Variable:
        """
        Register a variable with the hart context.
        """
        return super().register(*args, **kwargs, hart_variable=False)

    def allocate(self) -> str:
        """
        Allocates shared memory section. Defines and initializes all variables.

        Since these all needed to be constants that can be li'd anywhere, the variable names are suffixed with _mem.
        This avoids conflicts with the ``.equ name,``
        """

        intial_context = f"""
.section .{self.section_name}, "aw"

"""
        shared_variables: list[str] = []

        for variable in self._variables.values():
            shared_variables.append(f"{variable.name}_mem:")
            shared_variables.append(f".{self.swap_type:<8} {variable.value}")

        return intial_context + "\n".join(shared_variables)

    def equates(self, offset: int = 0) -> str:
        """
        Generates .equ directives for the variables.
        """
        equates: list[str] = []
        for variable in self._variables.values():
            equ = f".equ {variable.name},"
            equates.append(f"{equ:<40} os_data + {variable.offset + offset}")
        return "\n".join(equates)
