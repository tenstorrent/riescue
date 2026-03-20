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

    def register_hart_variable(self, name: str, value: int = 0, element_count: int = 1, **kwargs: Any) -> Variable:
        """
        Register a hart-local variable with the variable manager.

        :param name: Name of the variable
        :param value: Initial value of the variable
        :param element_count: Number of elements (1 for scalar, >1 for array)
        """
        return self._hart_context.register(name=name, value=value, element_count=element_count, **kwargs)

    def register_shared_variable(self, name: str, value: int = 0, **kwargs: Any) -> Variable:
        """
        Register a shared variable with the variable manager.
        """
        return self._shared_memory.register(name=name, value=value, **kwargs)

    def get_hart_context_total_size(self) -> int:
        """Return the total size of the hart_context section in bytes."""
        return self._hart_context.per_hart_size() * self.hart_count

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
        Initialize the hart context pointer into the scratch register. Also loads hart context pointer into ``tp``.
        For single hart, loads the hart context pointer directly.
        For multi-hart, creates a table of pointers to the hart contexts.

        mscratch gets the PA (physical address) for M-mode bare access.
        sscratch/vsscratch get the VA (virtual address) for paged access from S/VS mode.
        tp is left with the PA value for use by the loader (M-mode).
        """
        va_scratch_regs = [s for s in scratch_regs if s != "mscratch"]

        if self.hart_count == 1:
            code = """
    li tp, hart_context_pa
    csrw mscratch, tp
            """
            if va_scratch_regs:
                va_writes = "\n    ".join(f"csrw {scratch}, tp" for scratch in va_scratch_regs)
                # Restore tp to PA for loader (M-mode)
                code += f"""
    li tp, hart_context
    {va_writes}
    li tp, hart_context_pa
                """
            return code

        if self.xlen == RV.Xlen.XLEN32:
            load_instr = "lw"
            var_type = ".word"
            hart_table_offset = 2
        else:
            load_instr = "ld"
            var_type = ".dword"
            hart_table_offset = 3

        code = f"""
load_hart_context:
    csrr t0, mhartid
    la t1, {self.hart_context_table_name}_pa
    slli t0, t0, {hart_table_offset}
    add t0, t0, t1
    {load_instr} tp, 0(t0)
    csrw mscratch, tp
        """

        if va_scratch_regs:
            va_writes = "\n    ".join(f"csrw {scratch}, tp" for scratch in va_scratch_regs)
            # Restore tp to PA for loader (M-mode)
            code += f"""
    # Load VA table for sscratch/vsscratch
    csrr t0, mhartid
    la t1, {self.hart_context_table_name}
    slli t0, t0, {hart_table_offset}
    add t0, t0, t1
    {load_instr} tp, 0(t0)
    {va_writes}
    csrr tp, mscratch
            """

        pa_entries = "\n".join(f"    {var_type} {self._hart_context.context_name(hartid)}_pa" for hartid in range(self.hart_count))
        va_entries = "\n".join(f"    {var_type} {self._hart_context.context_name(hartid)}" for hartid in range(self.hart_count))
        code += f"""
    j load_hart_context_done
    # table of hart context PA pointers
{self.hart_context_table_name}_pa:
{pa_entries}
    # table of hart context VA pointers
{self.hart_context_table_name}:
{va_entries}
load_hart_context_done:
"""
        return code

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

    def enter_hart_context(self, scratch: str) -> str:
        """
        Generate code to enter the hart context.
        Loads tp with hart context pointer, by swapping tp with scratch register.
        Uses tp to swap sp with hart context's sp.

        Doesn't handle putting temporary registers on stack, just swaps tp and sp.
        """
        enter_trap = [
            "# Enter hart context",
            f"csrrw tp, {scratch}, tp",
        ]
        return "\n\t" + "\n\t".join(enter_trap)

    def exit_hart_context(self, scratch: str) -> str:
        """
        Generates code to exit the hart context and restore test context.
        Swaps tp with scratch register.
        Uses tp to swap sp with hart context's sp.

        Doesn't restore any registers spilled to stack, just restores tp and sp. Call last before returning to test code
        """

        exit_trap = [
            "# Exit hart context",
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

        va_equates = self._shared_memory.equates(offset)
        pa_equates = self._shared_memory.pa_equates(offset)
        return "\n# Runtime Variables (VA)\n" + va_equates + "\n\n# Runtime Variables (PA)\n" + pa_equates + "\n"

    def single_hart_variable_equates(self) -> str:
        """
        Generate .equ directives for hart-local variables. Should only be used for since hart mode.

        Purpose is to allow users to modify hart local variables in single hart mode.
        """
        return "\n# Single Hart Equates\n" + self._hart_context.equates(name="hart_context") + "\n"

    def hart_context_pa_equates(self) -> str:
        """
        Generate PA equates for per-hart context labels.
        These are needed for multi-hart mode where the PA table references hart_context_N_pa.
        For single hart, hart_context_pa is already generated as a section PA equate.
        """
        lines: list[str] = ["\n# Hart context PA equates"]
        per_hart_size = self._hart_context.per_hart_size()
        for hartid in range(self.hart_count):
            name = self._hart_context.context_name(hartid)
            offset = per_hart_size * hartid
            lines.append(f".equ {name}_pa, hart_context_pa + {offset}")
        return "\n".join(lines) + "\n"
