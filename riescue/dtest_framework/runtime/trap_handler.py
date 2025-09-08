# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# flake8: noqa: F401
"""
Implements exception handling for the dtest framework.

Notes:
    Wherever hartid is used, it is freshly retrieved so that fewer assumptions about GPR use are made.
"""

from typing import Dict

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.lib.routines import Routines


class InterruptServiceRoutine:
    """
    ISR to handle interrupt. Used to generate Interrupt Vector Table.

    Setting an ISR as `indirect` will cause the ISR to be called with a jalr instruction.
    Otherwise, the ISR will be called with a j instruction to the label.

    :param label: Label for the ISR
    :param code: ISR code to execute
    :param indirect: Whether the ISR is indirect (e.g. a function call)
    """

    def __init__(self, label: str, indirect: bool = False):
        self.label = label
        self.indirect = indirect

    def interrupt_table_entry(self) -> str:
        return f"    j {self.label}"


class InterruptHandler:
    """
    Used to generate Interupt Vector Table

    Vectored interrupts will default to a return from interrupt (clearing bit in `mip`/`sip`). Non-vectored interrupts will clear all interrupts.
    Vectored interrupts can be overridden with `register_vector`
    Vectored interrupts can be marked as invalid with `mark_invalid_vectors`which causes test failure if interrupt occurs.

    Standard interrupts (e.g. `SSI`) and platform-custom interrrupts will clear all interrupt bits and return, unless overridden by `register_vector`
    Reserved interrupts default as an "invalid" vector and cause test failure if they occur.

    :param privilege_mode: The privilege mode to use - "M" or "S"
    :param trap_entry: The label of trap entry point; should be same address written to `mtvec`/`stvec`
    :param xlen: The XLEN to use - 32 or 64
    """

    reserved_interrupt_indicies = [2, 4, 6, 8, 10, 12, 14, 15]

    def __init__(self, privilege_mode: str, trap_entry: str = "trap_entry", default_trap_handler: str = "default_trap_handler", xlen: int = 64):
        self.privilege_mode = privilege_mode
        if self.privilege_mode not in ["M", "S"]:
            raise ValueError(f"Privilege mode {self.privilege_mode} not supported")
        if self.privilege_mode == "M":
            self.xip = "mip"
            self.xret = "mret"
        else:
            self.xip = "sip"
            self.xret = "sret"
        self.vector_count = xlen - 1

        self.vector_table: Dict[int, InterruptServiceRoutine] = {}  # Maps vector_num -> ISR

        # Macro and Label names
        self.trap_entry_label = trap_entry
        self.default_trap_handler_label = default_trap_handler
        self.check_expected_interrupt_macro = "check_expected_interrupt"
        self.clear_interrupt_bit_macro = "clear_interrupt_bit"
        self.invalid_interrupt_label = "invalid_interrupt"
        self.clear_highest_priority_interrupt_bit_label = "clear_highest_priority_interrupt"
        self.interrupt_vector_table_label = "interrupt_vector_table"
        self.default_isr_label = "clear_all_interrupts"

        # Setting default ISRs and reserved interrupts
        for interrupt_enum in RV.RiscvInterruptCause:
            self.vector_table[interrupt_enum.value] = InterruptServiceRoutine(f"_CLEAR_{interrupt_enum.name}", False)
        for reserved_index in self.reserved_interrupt_indicies:
            self.mark_invalid_vector(reserved_index)
        for i in range(16, self.vector_count):
            self.vector_table[i] = InterruptServiceRoutine(self.clear_highest_priority_interrupt_bit_label, False)

    def register_vector(self, vector_num: int, handler_label: str, indirect: bool = False):
        """
        Register a vector handler.

        :param vector_num: The vector number to register the handler for
        :param handler_label: The label of the ISR to call
        :param indirect: Whether the ISR is indirect (e.g. a function call)
        """
        self.vector_table[vector_num] = InterruptServiceRoutine(handler_label, indirect)

    def mark_invalid_vector(self, vector_num: int):
        """
        Mark a vector as invalid/reserved. Interrupts that trap to reserved vectors will cause test to fail.

        :param vector_nums: The vector numbers to mark as invalid/reserved
        """
        self.vector_table[vector_num] = InterruptServiceRoutine(f"{self.invalid_interrupt_label}")

    def mark_invalid_vectors(self, vector_nums: list):
        """
        Mark specific vectors as invalid/reserved. Interrupts that trap to reserved vectors will cause test to fail.

        :param vector_nums: The vector numbers to mark as invalid/reserved
        """
        for v in vector_nums:
            self.mark_invalid_vector(v)

    def mark_vector_as_default(self, vector_num: int):
        """
        Mark a vector as the default ISR (clear interrupt bit and return). Useful for platform-specific interrupts.

        :param vector_num: The vector number to mark as the default ISR

        E.g. for platforms that want to set cause=20
        .. code-block:: python

            interrupt_handler.mark_vector_as_default(20)
        """
        self.vector_table[vector_num] = InterruptServiceRoutine(self.clear_highest_priority_interrupt_bit_label)

    def generate(self) -> str:
        """
        Generates the Interrupt Vector Table, invalid interrupt, default macros, and any indirect jumps
        """

        code = []
        code.append(self._generate_interrupt_equates())
        code.append(self._check_expected_interrupt_bit_macro())
        code.append(self._clear_interrupt_bit_macro())
        code.append(self._invalid_interrupt())
        code.append(self._generate_default_isrs())
        code.append(self._clear_highest_interrupt_bit())
        code.append(f"{self.trap_entry_label}:")
        code.append(f"    j {self.default_trap_handler_label}")
        code.append(self._generate_interrupt_vector_table())
        return "\n".join(code)

    def _generate_interrupt_equates(self) -> str:
        """
        Generates the interrupt equates.
        """
        return "\n".join(f".equ {interrupt_enum.name}, {interrupt_enum.value}" for interrupt_enum in RV.RiscvInterruptCause)

    def _check_expected_interrupt_bit_macro(self) -> str:
        """
        Generates macro that checks `__execpted` interrupt cause value is set in `a0`
        """
        return f"""
.macro {self.check_expected_interrupt_macro} __expected
    li t0, \\__expected
    bne t0, a0, test_failed
    li t0, (1<<\\__expected)
    csrc {self.xip}, t0  # Clear expected interrupt bit
    li a0, 0x0
    {self.xret}
.endm"""

    def _clear_interrupt_bit_macro(self) -> str:
        """
        Generates macro to clear interupt bit. `__bit` must be a constant
        """
        return f"""
.macro {self.clear_interrupt_bit_macro} __bit
    li t0, (1<<\\__bit)
    csrc {self.xip}, t0
    {self.xret}
.endm"""

    def _invalid_interrupt(self) -> str:
        """
        Generates the invalid interrupt.
        Using an extra label rather than just j test_failed to help debug test failures
        """
        return f"""
{self.invalid_interrupt_label}:
    j test_failed
"""

    def _clear_highest_interrupt_bit(self) -> str:
        """
        Routine that clears the highest-priority interrupt bit. if zbb is supported, this is a single instruction `ctz` (count trailing zeros).
        Otherwise, this is a loop that clears the lowest-priority bit.
        Need equates to include all extensions so code can be generated conditionally.
        """
        return f"""
{self.clear_highest_priority_interrupt_bit_label}:
    csrr t0, {self.xip}
    li t1, 0
{self.clear_highest_priority_interrupt_bit_label}_loop:
    addi t1, t1, 1
    srli t0, t0, 1
    andi t2, t0, 1
    beq t2, x0, {self.clear_highest_priority_interrupt_bit_label}_loop

    # now t2 has the index of lowest-priority bit to clear
    li t1, 1
    sll t1, t1, t2              # 1 << t2
    csrrc x0, {self.xip}, t1
    {self.xret}
"""

    def _generate_interrupt_vector_table(self) -> str:
        """
        Generates the Interrupt Vector Table.
        """
        vector_table = [f"{self.interrupt_vector_table_label}:"]
        vector_table.extend([self.vector_table[i].interrupt_table_entry() for i in range(1, self.vector_count)])
        return "\n".join(vector_table)

    def _generate_default_isrs(self) -> str:
        """
        Generates default ISRs - clear interrupt bit and return
        """
        code = []
        default_isr = f"""

{self.default_isr_label}:
    csrw {self.xip}, x0
    {self.xret}"""
        code.append(default_isr)
        for interrupt_enum in RV.RiscvInterruptCause:
            code.extend([f"_CLEAR_{interrupt_enum.name}:", f"    {self.clear_interrupt_bit_macro} {interrupt_enum.name}"])
        return "\n".join(code)


class TrapHandler(AssemblyGenerator):
    """Exception and interrupt handler for the dtest framework.

    Provides default exception and interrupt handling. Tests generally should not
    encounter exceptions or interrupts unless specifically configured. When they
    occur unexpectedly, the test fails.

    Supports exception validation by allowing tests to configure expected trap
    codes and return addresses. Handles both machine and supervisor mode
    exception delegation.

    .. note::
        Example usage in dtest_framework/tests/test_excp.s
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.default_trap_handler_label = "trap_handler"
        self.syscall_table_label = self.featmgr.syscall_table_label
        self.check_exception_label = self.featmgr.check_exception_label
        self.interrupt_handler = InterruptHandler(self.handler_priv_mode, self.featmgr.trap_handler_label, default_trap_handler=self.default_trap_handler_label)

        self.env = self.featmgr.env
        self.paging_mode = self.featmgr.paging_mode
        self.deleg_excp_to = self.featmgr.deleg_excp_to

    def generate(self) -> str:
        self.xcause = "scause"
        self.xepc = "sepc"
        self.xret = "sret"
        self.xip = "sip"
        if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.MACHINE:
            self.xcause = "mcause"
            self.xepc = "mepc"
            self.xret = "mret"
            self.xip = "mip"

        for interrupts in self.pool.parsed_vectored_interrupts:
            self.interrupt_handler.register_vector(interrupts.index, interrupts.label)

        code = f"""
        .section .text
        {self.interrupt_handler.generate()}
        {self.default_trap_handler(label=self.default_trap_handler_label)}
        .align 2
        excp_entry:
            nop
        """

        # Call pre handler user code
        if self.featmgr.excp_hooks:
            code += f"""
                li t0, excp_handler_pre_addr
                ld t0, 0(t0)
                jalr ra, t0
            """

        code += f"""
            # get hartid
            {Routines.read_tval(dest_reg="t0", priv_mode=self.handler_priv_mode)}
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}
            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset

            # Save the exception cause / code
            csrr t1, {self.xcause}
            li t3, check_excp_actual_cause
            add t3, t3, {self.hartid_reg} # Add offset for this harts check_excp_actual_cause element
            sd t1, 0(t3)

            # Save exception PC
            csrr t0, {self.xepc}
            li t3, check_excp_actual_pc
            add t3, t3, {self.hartid_reg} # Add offset for this harts check_excp_actual_pc element
            sd t0, 0(t3)
            """

        code += f"""
            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset
        """

        # Check for ECALL functions
        code += f"""
        # 0 if (ECALL_FROM_USER < cause < ECALL_FROM_VS ) j syscall
        # 0 else j check_exception

        li t0, {RV.RiscvExcpCauses.ECALL_FROM_USER.value}
        blt t1, t0, {self.check_exception_label}
        li t0, {RV.RiscvExcpCauses.ECALL_FROM_MACHINE.value}
        bgt t1, t0, {self.check_exception_label}
        j {self.syscall_table_label}
        """

        code += f"""
        {self.check_exception_label}:
            {self.os_check_excp(return_label='return_to_host', xepc=self.xepc, xret=self.xret)}

            ecall_from_machine:
            ecall_from_supervisor:
            return_to_host:

            # get hartid
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}
            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset

            # Update the return PC from check_excp_return_pc
            li t3, check_excp_return_pc
            add t3, t3, {self.hartid_reg} # Add offset for this harts check_excp_return_pc element
            ld t0, 0(t3)
            sd x0, 0(t3)
            csrw {self.xepc}, t0
            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset
        """

        # Call post handler user code
        if self.featmgr.excp_hooks:
            code += f"""
                li t0, excp_handler_post_addr
                ld t0, 0(t0)
                jalr ra, t0
            """

        # Return from exception
        code += f"""
            # Return from exception
            {self.xret}
        """
        return code

    def default_trap_handler(self, label: str):
        """
        Generates the default trap handler code.
        """

        return f"""
            {label}:
            csrr t0, {self.xcause}
            li t1, (0x1<<(XLEN-1))              # Isolate interrupt bit
            and t0, t0, t1
            beq t0, x0, excp_entry              # If the interrupt bit is 0, exception

            {Routines.place_retrieve_hartid(dest_reg="t0", priv_mode=self.handler_priv_mode)} # Get hartid
            bne t0, x0, test_failed                  # FIXME: Only handles interrupts for hartid0

            li t0, 0
            # Clear the pending interrupt by writing a 0;
            # FIXME: This will clear nested interrupts. We don't want that.
            # This needs to clear the highest interrupt bit that was set
            # If zbb
            # csrr a0, {self.xip}
            # ctz a1, a0
            # gives interrupt bit
            # otherwise need a while loop to get lowest bit to clear
            csrw {self.xip}, t0

            {self.xret}
        """
