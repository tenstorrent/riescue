# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.variable.variable import Variable
from .memory import BaseMemory

"""
Classes for generating hart-local memory and variables
"""


class HartContext(BaseMemory):
    """
    Generates hart-local context and allocates variables.

    :attr var_offset: Offset for reserved, internal variables in the context.

    :param xlen: The width of the variables. Since context contains swap variables, needs to match the xlen of the hart.
    :param amos_enabled: Whether AMOS is enabled. If False, hart context needs to have both test sp and runtime sp. If true can just swap the values.


    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._num_internal_variables = 2
        self._offset = self.variable_size * self._num_internal_variables  #: Number of bytes allocated for internal variables

    def __contains__(self, name: str) -> bool:
        """
        Check if a variable is registered in the hart context.
        """
        return name in self._variables

    def register(self, *args: Any, **kwargs: Any) -> Variable:
        """
        Register a variable with the hart context.
        """
        return super().register(*args, **kwargs, hart_variable=True)

    def context_name(self, hart_id: int) -> str:
        "Helper to keep context name consistent"
        return f"hart_context_{hart_id}"

    def hart_stack_name(self, hart_id: int, end: bool = False) -> str:
        """
        Helper to keep stack name consistent.

        :param hart_id: ID of the hart to generate the stack for
        :param end: Whether to generate the end of the stack name
        :returns: Name of the stack
        """
        hart_stack_name = f"hart_stack_{hart_id}"
        if end:
            hart_stack_name += "_end"
        return hart_stack_name

    def allocate(self, hart_id: int, include_padding: bool = True) -> str:
        """
        Generic hart context. Aligned to 64 bytes.

        Includes internal variables for:

        - hart stack pointer
        - swap space for the test stack pointer if amo isn't supported
        - mhartid

        .. note::

            Internal variables are hardcoded here at the beginning of the context.
            This is tracked by the _num_internal_variables attribute.
            Update this number if more variables are added

        .. note::

            This is using the swap_type, which uses XLEN/register size to determine the data type.
            This is so all data is memory aligned (better performance, don't need if block for forced alignment)
            This might cause problems for big endian systems (since lb might access the wrong byte)

        :param hart_id: ID of the hart to generate the context for
        :param variables: List of variables to include in the context
        :param include_padding: Whether to include padding to 64-byte boundary. Defaults to True.
        """
        test_stack_pointer_default_val = 0

        intial_context = f"""
.align 6
hart_context_{hart_id}:
.{self.swap_type} {self.hart_stack_name(hart_id, end=True):<30} # hart's sp
.{self.swap_type} {test_stack_pointer_default_val:<30} # test's sp
"""
        hart_local_variables: list[str] = []
        bytes_allocated = self.variable_size * self._num_internal_variables

        for variable in self._variables.values():
            # special case for hartid
            if variable.name == "mhartid":
                value = hart_id
            else:
                value = variable.value

            if variable.description:
                comment = f"# {variable.description}"
            else:
                comment = f"# {variable.name}"
            hart_local_variables.append(f".{self.swap_type:<5} {value:<30} {comment}")
            bytes_allocated += self.variable_size

        if include_padding:
            # padding context to 64-byte boundary, to allow for alignment on 64-byte blocks
            padding_needed = (64 - (bytes_allocated % 64)) % 64
            if padding_needed > 0:
                hart_local_variables.append(f".space {padding_needed:<30} # padding to 64-byte boundary")

        return intial_context + "\n".join(hart_local_variables)


class HartStack:
    """
    Generates hart-local stack area of memory. Could be a function if needed. Just generates a hart stack

    Using definitino of stack from RISC-V ABI 2.1:

    - Aligned to 128-bit (16 bytes = 2^4 = .algin 4)
    - Stack grows downwards (towards lower addresses; initialize with {stack_name}_end)

    :param stack_size: size of the stack in bytes. Defaults to 4096 bytes.
    """

    def __init__(self, xlen: RV.Xlen, stack_size: int = 0x1000):
        self.xlen = xlen
        self.stack_size = stack_size

    def allocate(self, hart_id: int) -> str:
        """
        Generic hart stack. Aligned to 16 bytes.
        """

        memory_name = f"hart_stack_{hart_id}"
        return f"""
.section .{memory_name}, "aw"
{memory_name}:
.align 4
.space {self.stack_size}
{memory_name}_end:
"""
