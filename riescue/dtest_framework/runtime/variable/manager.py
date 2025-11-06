# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.variable.hart_memory import HartContext, HartStack
from riescue.dtest_framework.runtime.variable.shared_memory import SharedMemory
from riescue.dtest_framework.runtime.variable.variable import Variable


class VariableManager:
    """
    Manager for runtime environment variables.

    Instantiates per-hart variables

    """

    def __init__(
        self,
        data_section_name: str,
        xlen: RV.Xlen,
        hart_count: int,
        amo_enabled: bool,
        hart_stack_size: int = 0x1000,
    ):
        self.data_section_name = data_section_name
        self.hart_count = hart_count
        self.xlen = xlen
        self.amo_enabled = amo_enabled

        self.hart_context_table_name = "hart_context_table"

        self._hart_context = HartContext(xlen=xlen, amo_enabled=self.amo_enabled)
        self._hart_stack = HartStack(xlen=xlen, stack_size=hart_stack_size)
        self._shared_memory = SharedMemory(xlen=xlen, section_name="os_data", amo_enabled=self.amo_enabled)

        # default variables:
        self.register_hart_variable("mhartid", value=0, description="mhartid")

    def register_hart_variable(self, name: str, value: int = 0, **kwargs: Any) -> Variable:
        """
        Register a hart-local variable with the variable manager.
        """
        return self._hart_context.register(name=name, value=value, **kwargs)

    def register_shared_variable(self, name: str, value: int = 0, **kwargs: Any) -> Variable:
        """
        Register a shared variable with the variable manager.
        """
        return self._shared_memory.register(name=name, value=value, **kwargs)

    def get_variable(self, name: str) -> Variable:
        """
        Get a variable by name.

        :param name: Name of the variable
        :return: The variable object
        """

        # checks all memory containers for variable, if adding more conatiners just add them to the list
        for memory_container in [self._hart_context, self._shared_memory]:
            variable = memory_container.get_variable(name)
            if variable:
                return variable
        raise ValueError(f"Variable {name} not found")

    def initialize(self, scratch_regs: list[str]) -> str:
        """
        Initialize the hart context pointer into the scratch register.
        For single hart, loads the hart context pointer directly.
        For multi-hart, creates a table of pointers to the hart contexts.
        """
        if self.hart_count == 1:
            return "\n\t".join(
                [
                    "li t0, hart_context",
                ]
                + [f"csrw {scratch}, t0" for scratch in scratch_regs]
            )

        if self.xlen == RV.Xlen.XLEN32:
            load_instr = "lw"
            var_type = ".word"
            hart_table_offset = 2
        else:
            load_instr = "ld"
            var_type = ".dword"
            hart_table_offset = 3

        code = [
            "load_hart_context:",
            "csrr t0, mhartid",
            f"la t1, {self.hart_context_table_name}",
            f"slli t0, t0, {hart_table_offset}",  # multiple hartid * offset of hart table entry (2^2 bytes for 32-bit, 2&3 bytes for 64-bit)
            "add t0, t0, t1",
            f"{load_instr} t0, 0(t0)",
            "j load_hart_context_done",
            "# table of hart context pointers",
            f"{self.hart_context_table_name}:",
        ]

        for hartid in range(self.hart_count):
            code.append(f"    {var_type} {self._hart_context.context_name(hartid)}")
        code.append("load_hart_context_done:")
        code.extend([f"csrw {scratch}, t0" for scratch in scratch_regs])
        return "\n\t".join(code) + "\n"

    def generate_pointer_table(self) -> str:
        """
        Build a contiguous array of pointers to every hart-context. Used to load the correct hart context pointer based on hartid.

        :param label: Symbol name of the table
        :returns: Assembly string containing .dword/.word directives
        """
        if self.xlen == RV.Xlen.XLEN32:
            directive = ".word"
        else:
            directive = ".dword"

        lines = [f"{self.hart_context_table_name}:"]
        for hid in range(self.hart_count):
            lines.append(f"    {directive} {self._hart_context.context_name(hid)}")
        return "\n\t".join(lines) + "\n"

    def enter_trap_context(self, scratch: str) -> str:
        """
        Generate code to enter the trap context.
        Loads tp with hart context pointer, by swapping tp with scratch register.
        Uses tp to swap sp with hart context's sp.

        Doesn't handle putting temporary registers on stack, just swaps tp and sp.
        """
        enter_trap = [
            "# Enter trap context",
            f"csrrw tp, {scratch}, tp",
        ]
        return "\n\t" + "\n\t".join(enter_trap)

    def exit_trap_context(self, scratch: str) -> str:
        """
        Generates code to exit the trap context and restore text context.
        Swaps tp with scratch register.
        Uses tp to swap sp with hart context's sp.

        Doesn't restore any registers spilled to stack, just restores tp and sp. Call last before returning to test code
        """

        exit_trap = [
            "# Exit trap context",
            f"csrrw tp, {scratch}, tp",
        ]
        return "\n\t" + "\n\t".join(exit_trap)

    def allocate(self) -> str:
        """
        Generates code that allocates all variables for the runtime environment.
        Includes hart_context section and hart_stack_n sections

        """

        allocate_code = [self._shared_memory.allocate(), "\n"]

        allocate_code.append("# Hart-local storage")
        allocate_code.append(f'.section .{self.data_section_name}, "aw"')
        for hartid in range(self.hart_count):
            allocate_code.append(self._hart_context.allocate(hart_id=hartid))
        for hartid in range(self.hart_count):
            allocate_code.append(self._hart_stack.allocate(hart_id=hartid))
        return "\n" + "\n".join(allocate_code)

    def equates(self, offset: int = 0) -> str:
        """
        Generate .equ directives. Used to create constants so variables can be loaded from anywhere in code.

        :param offset: offset to be added to equates. Used to account for pointers in .os_data
        """

        return "\n# Runtime Variables\n" + self._shared_memory.equates(offset) + "\n"
