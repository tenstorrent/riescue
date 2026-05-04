# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# flake8: noqa: F401
"""
Implements exception handling for the dtest framework.

Notes:
    Wherever hartid is used, it is freshly retrieved so that fewer assumptions about GPR use are made.
"""

from typing import Optional

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.trap_context import MACHINE_CTX, SUPERVISOR_CTX


class InterruptServiceRoutine:
    """
    ISR to handle interrupt. Used to generate Interrupt Vector Table.

    Setting an ISR as `indirect` will cause the ISR to be called with a jalr instruction.
    Otherwise, the ISR will be called with a j instruction to the label.

    :param label: Label for the ISR
    :param label_prefix: string to prefix generated labels
    :param code: ISR code to execute
    :param indirect: Whether the ISR is indirect (e.g. a function call)
    :param indirect_pointer: Whether the ISR is indirect (e.g. a function call)
    :param indirect_pointer_label: Memory pointer to ISR. Used to jump to locations very far away (avoids ``relocation truncated to fit`` error)
    """

    def __init__(self, label: str, label_prefix: str, indirect: bool = False):
        self.label = label
        self.label_prefix = label_prefix
        self.indirect = indirect
        self.jump_table_label = f"{self.label_prefix}_{self.label}_jump_table"
        self.interrupt_handler_pointer = f"{self.label_prefix}_{self.label}_handler_pointer"

    def interrupt_table_entry(self) -> str:
        if self.indirect:
            return f"    j {self.jump_table_label}"
        else:
            return f"    j {self.label}"

    def indirect_jump_table_entry(self) -> str:
        """
        Returns a jump table entry for an indirect ISR.
        Emits a .dword pointer inline in .runtime and an ld/jr stub that reads it.
        Used for non-custom-handler indirect vectors (pointer never changes at runtime).

        For custom per-segment handlers the pointer must be writable from test-body
        privilege; those vectors are handled by TrapHandler._generate_interrupt_jump_table()
        which emits ``li t0, intr_handler_ptr_N_pa; ld t0, 0(t0); jr t0`` using an
        .os_data equate instead.

        :raises: ValueError if the ISR is not indirect
        """
        if not self.indirect:
            raise ValueError("cannot generate a jump table entry for a non-indirect ISR")
        # create pointer to ISR and load pointer before jumping. Avoids relocation truncated to fit error.
        jump_table_entry = [
            f"{self.interrupt_handler_pointer}:",
            f"    .dword {self.label}",
            "\n",
            f"{self.jump_table_label}:",
        ]
        jump_table_entry.append(f"    ld t0, {self.interrupt_handler_pointer}")
        jump_table_entry.append(f"    jr t0")
        return "\n".join(jump_table_entry)


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

    def __init__(
        self,
        privilege_mode: RV.RiscvPrivileges,
        trap_entry: str = "trap_entry",
        default_trap_handler: str = "default_trap_handler",
        test_fail_label: str = "test_failed",
        xlen: RV.Xlen = RV.Xlen.XLEN64,
        label_prefix: str = "",
        use_pa: bool = True,
    ):
        self.privilege_mode = privilege_mode
        if self.privilege_mode not in [RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER]:
            raise ValueError(f"Privilege mode {self.privilege_mode.value} not supported, supported modes are Machine and Super")

        if self.privilege_mode == RV.RiscvPrivileges.MACHINE:
            self.xip = "mip"
            self.xret = "mret"
        else:
            self.xip = "sip"
            self.xret = "sret"
        self.vector_count = xlen.value - 1

        self.vector_table: dict[int, InterruptServiceRoutine] = {}  # Maps vector_num -> ISR
        self.label_prefix = label_prefix
        self.use_pa = use_pa

        # Macro and Label names
        self.trap_entry_label = trap_entry
        self.default_trap_handler_label = default_trap_handler
        self.test_fail_label = test_fail_label
        self.check_expected_interrupt_macro = f"{label_prefix}check_expected_interrupt"
        self.clear_interrupt_bit_macro = f"{label_prefix}clear_interrupt_bit"
        self.invalid_interrupt_label = f"{label_prefix}invalid_interrupt"
        self.clear_highest_priority_interrupt_bit_label = f"{label_prefix}clear_highest_priority_interrupt"
        self.interrupt_vector_table_label = f"{label_prefix}interrupt_vector_table"
        self.default_isr_label = f"{label_prefix}clear_all_interrupts"

        # Setting default ISRs and reserved interrupts
        for interrupt_enum in RV.RiscvInterruptCause:
            self.vector_table[interrupt_enum.value] = InterruptServiceRoutine(f"{label_prefix}_CLEAR_{interrupt_enum.name}", self.label_prefix, False)
        for reserved_index in self.reserved_interrupt_indicies:
            self.mark_invalid_vector(reserved_index)
        for i in range(16, self.vector_count):
            self.vector_table[i] = InterruptServiceRoutine(self.clear_highest_priority_interrupt_bit_label, self.label_prefix, False)

    def register_vector(self, vector_num: int, handler_label: str, indirect: bool = False):
        """
        Register a vector handler.

        :param vector_num: The vector number to register the handler for
        :param handler_label: The label of the ISR to call
        :param indirect: Whether the ISR is indirect (e.g. a function call)
        """
        self.vector_table[vector_num] = InterruptServiceRoutine(handler_label, self.label_prefix, indirect)

    def mark_invalid_vector(self, vector_num: int):
        """
        Mark a vector as invalid/reserved. Interrupts that trap to reserved vectors will cause test to fail.

        :param vector_nums: The vector numbers to mark as invalid/reserved
        """
        self.vector_table[vector_num] = InterruptServiceRoutine(f"{self.invalid_interrupt_label}", self.label_prefix)

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
        self.vector_table[vector_num] = InterruptServiceRoutine(self.clear_highest_priority_interrupt_bit_label, self.label_prefix)

    def generate(self, custom_vectors: Optional[set] = None) -> str:
        """
        Generates the Interrupt Vector Table, invalid interrupt, default macros, and any indirect jumps.

        :param custom_vectors: Set of vector numbers that have per-segment custom handlers.
            For these vectors _generate_interrupt_jump_table() emits ``li+ld+jr`` using an
            .os_data equate (intr_handler_ptr_N_pa) instead of an inline .dword in .runtime.
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
        code.append(self._generate_interrupt_jump_table(custom_vectors))
        return "\n".join(code)

    def _generate_interrupt_equates(self) -> str:
        """
        Generates the interrupt equates.
        """
        code = f"\n".join(f".equ {interrupt_enum.name}, {interrupt_enum.value}" for interrupt_enum in RV.RiscvInterruptCause)
        code += f"\n.equ _all_interrupts, ((1 << {(list(RV.RiscvInterruptCause)[0]).name})"
        for interrupt_enum in list(RV.RiscvInterruptCause)[1:]:
            code += f" | (1 << {interrupt_enum.name})"
        code += ")\n"
        return code

    def _check_expected_interrupt_bit_macro(self) -> str:
        """
        Generates macro that checks `__execpted` interrupt cause value is set in `a0`
        """
        return f"""
.macro {self.check_expected_interrupt_macro} __expected
    li t0, \\__expected
    bne t0, a0, {self.test_fail_label}
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
        Using an extra label rather than just j self.test_fail_label to help debug test failures
        """
        return f"""
{self.invalid_interrupt_label}:
    j {self.test_fail_label}
"""

    def _clear_highest_interrupt_bit(self) -> str:
        """
        Routine that clears the lowest-numbered pending interrupt bit in xip.
        Uses neg+and to isolate the lowest set bit in O(1): mask = xip & (-xip).
        This avoids the previous loop which had two bugs:
          1. Off-by-one: sll used t2 (always 1 after loop exit) instead of the bit-position counter.
          2. Infinite loop when bit 0 was the only set bit (shift to 0, loop never exits).
        """
        return f"""
{self.clear_highest_priority_interrupt_bit_label}:
    csrr t0, {self.xip}
    neg t1, t0
    and t0, t0, t1              # isolate lowest set bit: xip & (-xip)
    csrrc x0, {self.xip}, t0
    {self.xret}
"""

    def _generate_interrupt_vector_table(self) -> str:
        """
        Generates the Interrupt Vector Table.
        """
        vector_table = [f"{self.interrupt_vector_table_label}:"]
        vector_table.extend([self.vector_table[i].interrupt_table_entry() for i in range(1, self.vector_count)])
        return "\n".join(vector_table)

    def _generate_interrupt_jump_table(self, custom_vectors: Optional[set] = None) -> str:
        """
        Generates the Interrupt Jump Table.
        User-defined vectors are added to the interrupt table, but might be too far away.
        Instead need to generate a jump table for each vector, in format ``_{interrupt_handler_name}_jump_table``

        :param custom_vectors: Set of vector numbers that have per-segment custom handlers.
            For these vectors the .dword pointer lives in .os_data (emitted by OpSys).
            For M-mode (bare, no paging) the stub uses ``li t0, intr_handler_ptr_N_pa`` (PA equate).
            For S-mode (paging enabled) the stub uses ``li t0, intr_handler_ptr_N`` (VA equate)
            so the load goes through the page tables correctly.
            Non-custom indirect vectors keep the inline .dword in .runtime.
        """
        equate_suffix = "_pa" if self.use_pa else ""
        jump_table = []
        for v_num, vector in self.vector_table.items():
            if vector.indirect:
                if custom_vectors and v_num in custom_vectors:
                    jump_table.append(
                        "\n".join(
                            [
                                f"{vector.jump_table_label}:",
                                f"    li t0, intr_handler_ptr_{v_num}{equate_suffix}",
                                f"    ld t0, 0(t0)",
                                f"    jr t0",
                            ]
                        )
                    )
                else:
                    jump_table.append(vector.indirect_jump_table_entry())
        return "\n" + "\n".join(jump_table)

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
            code.extend([f"{self.label_prefix}_CLEAR_{interrupt_enum.name}:", f"    {self.clear_interrupt_bit_macro} {interrupt_enum.name}"])
        return "\n".join(code)


class TrapHandler(AssemblyGenerator):
    """Exception and interrupt handler for the dtest framework.

    Provides default exception and interrupt handling. Tests generally should not
    encounter exceptions or interrupts unless specifically configured. When they
    occur unexpectedly, the test fails.

    Supports exception validation by allowing tests to configure expected trap
    codes and return addresses. Handles both machine and supervisor mode
    exception delegation.

    All internal labels are prefixed with `trap_handler_<mode>__` where <mode>
    is 'm' for machine mode or 's' for supervisor mode, allowing multiple trap
    handlers with different delegation modes to coexist in the same assembly file.

    :param deleg_mode: The delegation mode (MACHINE or SUPER).
    :param deleg_virtualized: Whether V=1 (True only valid for deleg_mode = SUPER). Note: when in non-virtualized environment the S mode handler is deleg_virtualized = True. Default: False

    .. note::
        Example usage in dtest_framework/tests/test_excp.s

    Interface:

    - ``self.featmgr.trap``
    """

    def __init__(self, deleg_mode: RV.RiscvPrivileges, deleg_virtualized: bool = False, **kwargs):
        super().__init__(**kwargs)

        if deleg_mode == RV.RiscvPrivileges.MACHINE and deleg_virtualized:
            raise ValueError("deleg_virtualized can only be True when deleg_mode is SUPER")

        self.deleg_mode = deleg_mode
        self.deleg_virtualized = deleg_virtualized

        # Create label prefix based on delegation mode
        if deleg_mode == RV.RiscvPrivileges.MACHINE:
            self.deleg_mode_str = "m"
        elif deleg_mode == RV.RiscvPrivileges.SUPER and not deleg_virtualized:
            self.deleg_mode_str = "hs"
        elif deleg_mode == RV.RiscvPrivileges.SUPER and deleg_virtualized:
            self.deleg_mode_str = "s"
        self.label_prefix = f"trap_handler_{self.deleg_mode_str}__"

        # default labels
        self.trap_handler_label = f"{self.label_prefix}trap_handler"  #: Default trap handler routine. Used for default trap behavior.
        self.interrupt_handler_label = f"{self.label_prefix}interrupt_handler"  #: Default interrupt handler routine. Clears mip and ends trap
        self.exception_handler_label = f"{self.label_prefix}excp_entry"  #: Exception Handler routine. Assumes context has already been saved
        self.trap_panic_label = f"{self.label_prefix}trap_panic"  #: Kernel panic label. Used to end test early if an fatal error occurs in trap handler.

        self.trap_entry_label = f"{self.label_prefix}trap_entry"  #: Trap entry label. Value ``*tvec`` is loaded with
        self.trap_exit_label = f"{self.label_prefix}trap_exit"  #: Trap exit routine. Can restore context before jumping back to test code.
        self.syscall_table_label = f"{self.label_prefix}syscal_table"  #: Syscall table routine. Used to evaluate ECALL / syscalls. Implemented in :class:`syscalls.SysCalls`
        self.check_exception_label = f"{self.label_prefix}check_exception_label"  #: Check exception routine, used to check for expected exceptions
        self.test_fail_label = "test_failed" if self.deleg_mode == RV.RiscvPrivileges.MACHINE else f"{self.label_prefix}test_failed"  #: Test failure label. Jumps to common test failure routine.

        self.interrupt_handler = InterruptHandler(
            trap_entry=self.trap_entry_label,
            privilege_mode=self.deleg_mode,
            default_trap_handler=self.trap_handler_label,
            label_prefix=self.label_prefix,
            test_fail_label=self.test_fail_label,
            use_pa=(deleg_mode == RV.RiscvPrivileges.MACHINE),
        )

        self.env = self.featmgr.env
        self.paging_mode = self.featmgr.paging_mode

        # M-mode runs with bare addressing (no translation), so shared variable
        # accesses need to use physical addresses (PA equates).
        # S-mode runs with translation, so uses virtual addresses (VA equates).
        self.bare = self.deleg_mode == RV.RiscvPrivileges.MACHINE

        self.xcause = "scause"
        self.xepc = "sepc"
        self.xret = "sret"
        self.xip = "sip"
        self.xstatus = "sstatus"
        self.tvec = "stvec"
        self.tval = "stval"
        self.scratch_reg = "sscratch"
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            self.xcause = "mcause"
            self.xepc = "mepc"
            self.xret = "mret"
            self.xip = "mip"
            self.xstatus = "mstatus"
            self.tvec = "mtvec"
            self.tval = "mtval"
            self.scratch_reg = "mscratch"
        self.panic_cause = f"{self.label_prefix}TRAP_HANDLER_PANIC_CAUSE"

    def _call_excp_hook(self, hook: str) -> str:
        address_label = f"{hook}_pa" if self.bare else hook
        code = f"""
            li t0, {address_label}
            ld t0, 0(t0)
        """
        # We loaded VA of the hook; we need to relocate to physical space if
        # we're in M mode
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            code += f"""
            li t1, code
            # Align code base address (PA is aligned but VA is not)
            srli t1, t1, 12
            slli t1, t1, 12
            sub t0, t0, t1
            li t1, code_pa
            add t0, t0, t1
            """
        code += """
            jalr ra, t0
        """
        return code

    def generate(self) -> str:
        self.register_equate(self.panic_cause, "11")

        # Register FeatMgr-level default handler overrides (set via Conf.add_hooks()).
        # Each vector is routed to the TrapHandler whose privilege level matches its
        # mideleg bit: delegated vectors (bit set) go to the S-mode TrapHandler;
        # non-delegated vectors go to the M-mode TrapHandler.
        for vec, (label, _) in self.featmgr.interrupt_handler_overrides.items():
            vec_delegated = bool((self.featmgr.mideleg >> vec) & 1)
            vec_mode = RV.RiscvPrivileges.SUPER if vec_delegated else RV.RiscvPrivileges.MACHINE
            if vec_mode == self.deleg_mode:
                self.interrupt_handler.register_vector(vec, label)

        for interrupts in self.pool.parsed_vectored_interrupts:
            self.interrupt_handler.register_vector(interrupts.index, interrupts.label, indirect=True)

        # Ensure indirect slot for vectors used by ;#custom_handler (Voyager2 per-generator handlers)
        vectored_labels = {vi.index: vi.label for vi in self.pool.parsed_vectored_interrupts}
        for ch in self.pool.parsed_custom_handlers:
            if ch.vector_num not in self.interrupt_handler.vector_table or not self.interrupt_handler.vector_table[ch.vector_num].indirect:
                default_label = vectored_labels.get(ch.vector_num, self.interrupt_handler.clear_highest_priority_interrupt_bit_label)
                self.interrupt_handler.register_vector(ch.vector_num, default_label, indirect=True)

        # Collect the set of vector numbers that have custom (per-segment) handlers.
        # Their .dword pointers are registered in pool here and emitted by OpSys into .os_data.
        # The jump stubs use ``li t0, intr_handler_ptr_N_pa; ld t0, 0(t0); jr t0`` so they are
        # reachable regardless of distance, and .os_data is writable from any privilege level.
        custom_vectors = {ch.vector_num for ch in self.pool.parsed_custom_handlers}
        for v in custom_vectors:
            if v in self.interrupt_handler.vector_table:
                isr = self.interrupt_handler.vector_table[v]
                self.pool.add_interrupt_handler_pointer(f"intr_handler_ptr_{v}", isr.label)

        section_name = "runtime" if self.deleg_mode == RV.RiscvPrivileges.MACHINE else "runtime_s"
        custom_macros = self._generate_custom_handler_macros()

        # Emit handler bodies for FeatMgr-level default handler overrides.
        # Each override is emitted by the TrapHandler whose privilege level matches the
        # vector's mideleg bit; the TrapContext passed to the callable carries the
        # correct CSR names (xip, xret, etc.) for that privilege level.
        override_handlers = ""
        for vec, (label, assembly_fn) in self.featmgr.interrupt_handler_overrides.items():
            vec_delegated = bool((self.featmgr.mideleg >> vec) & 1)
            vec_mode = RV.RiscvPrivileges.SUPER if vec_delegated else RV.RiscvPrivileges.MACHINE
            if vec_mode == self.deleg_mode:
                ctx = SUPERVISOR_CTX if vec_delegated else MACHINE_CTX
                override_handlers += f"\n.balign 4, 0\n{label}:\n{assembly_fn(ctx)}\n"

        # Emit handler bodies for FeatMgr-level default exception handler overrides.
        # Routing parallels the interrupt side but uses medeleg: causes whose medeleg
        # bit is set go to the S-mode TrapHandler, others to M-mode. Dispatch to these
        # handlers is inlined at exception_path in default_trap_handler() (both APLIC
        # and non-APLIC paths) — see there for the (test-wide, no per-segment
        # switching) matching scheme.
        excp_override_handlers = ""
        for cause, (label, assembly_fn) in self.featmgr.exception_handler_overrides.items():
            cause_delegated = bool((self.featmgr.medeleg >> cause) & 1)
            cause_mode = RV.RiscvPrivileges.SUPER if cause_delegated else RV.RiscvPrivileges.MACHINE
            if cause_mode == self.deleg_mode:
                ctx = SUPERVISOR_CTX if cause_delegated else MACHINE_CTX
                excp_override_handlers += f"\n.balign 4, 0\n{label}:\n{assembly_fn(ctx)}\n"

        code = f"""
        .section .{section_name}, "ax"
        {custom_macros}
        {self.interrupt_handler.generate(custom_vectors=custom_vectors)}
        {override_handlers}
        {excp_override_handlers}
        {self.test_fail()}
        {self.default_trap_handler()}
        .balign 4, 0
        {self.exception_handler_label}:
        """

        # Call pre handler user code
        if self.featmgr.excp_hooks:
            code += self._call_excp_hook("excp_handler_pre_addr")

        check_excp_actual_pc = self.variable_manager.get_variable("check_excp_actual_pc")
        check_excp_actual_cause = self.variable_manager.get_variable("check_excp_actual_cause")
        check_excp_return_pc = self.variable_manager.get_variable("check_excp_return_pc")
        check_excp_re_execute = self.variable_manager.get_variable("check_excp_re_execute")

        code += f" csrr t1, {self.xcause}\n"
        code += self.ecall_handler()

        # TODO: Do we really need to save the trap info to memory?
        # Does it make sense if it's only used for the trap and then not used again?
        # Test code shouldn't rely on runtime-variables for this unless it's through a macro
        # Only place that relies on this is the check_excp function.
        # Should this be removed if they aren't really useful and only add latency?
        code += f"""
        {self.check_exception_label}:
            {check_excp_actual_cause.store(src_reg='t1'):<40}  # Save check_excp_actual_cause
            csrr t0, {self.xepc}
            {check_excp_actual_pc.store(src_reg='t0'):<40}  # Save check_excp_actual_pc

            {self.check_excp(return_label=f'{self.label_prefix}return_to_host', xepc=self.xepc, xret=f"j {self.trap_exit_label}")}

            {self.label_prefix}ecall_from_machine:
            {self.label_prefix}ecall_from_supervisor:
            {self.label_prefix}return_to_host:

            # Always consume the stashed return_pc so every OS_SETUP_CHECK_EXCP is single-use.
            {check_excp_return_pc.load_and_clear(dest_reg='t0'):<35}  # check_excp_return_pc
            # When re-execute is set, leave xepc pointing at the faulting PC so xret
            # resumes at the same instruction (sdtrig icount/mcontrol6 use cases).
            {check_excp_re_execute.load_and_clear(dest_reg='t1'):<35}  # check_excp_re_execute
            bnez t1, {self.label_prefix}skip_xepc_write
            csrw {self.xepc}, t0
            {self.label_prefix}skip_xepc_write:
        """

        # AssertException(disable_triggers_after=True) walker. Fires when
        # check_excp_disable_triggers != 0 AND cause != BREAKPOINT. Walks
        # every implemented sdtrig trigger and clears only the priv-enable
        # bits per trigger type so the trigger can no longer match without
        # clobbering its action/match/count/hit/chain/etc. fields.
        # M-mode only: tselect / tdata1 are M-mode CSRs, so emitting from
        # an S-mode trap handler would raise ILLEGAL_INSTRUCTION.
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            check_excp_disable_triggers = self.variable_manager.get_variable("check_excp_disable_triggers")
            code += f"""
            # disable_triggers_after path: load+clear the flag; if set and
            # cause != BREAKPOINT, walk all triggers and clear priv-enables.
            {check_excp_disable_triggers.load_and_clear(dest_reg='t1'):<35}  # check_excp_disable_triggers
            beqz t1, {self.label_prefix}skip_assert_disable_triggers
            csrr t1, {self.xcause}
            li t2, 3                                     # RISC-V BREAKPOINT cause
            beq t1, t2, {self.label_prefix}skip_assert_disable_triggers
            li t1, 0                                     # candidate trigger index
        {self.label_prefix}assert_disable_triggers_loop:
            csrw tselect, t1
            csrr t2, tselect
            bne t2, t1, {self.label_prefix}skip_assert_disable_triggers   # past last implemented

            csrr t4, tdata1
            srli t5, t4, 60                              # t5 = tdata1[63:60] = trigger type
            li t3, 0                                     # default: unknown type, mask = 0 (no-op)

            li t6, 6                                     # mcontrol6
            bne t5, t6, {self.label_prefix}assert_check_icount
            li t3, 0x01800058
            j {self.label_prefix}assert_apply_priv_mask
        {self.label_prefix}assert_check_icount:
            li t6, 3                                     # icount
            bne t5, t6, {self.label_prefix}assert_check_itrig_etrig
            li t3, 0x060002C0
            j {self.label_prefix}assert_apply_priv_mask
        {self.label_prefix}assert_check_itrig_etrig:
            li t6, 4                                     # itrigger
            beq t5, t6, {self.label_prefix}assert_set_itrig_etrig_mask
            li t6, 5                                     # etrigger
            bne t5, t6, {self.label_prefix}assert_apply_priv_mask
        {self.label_prefix}assert_set_itrig_etrig_mask:
            li t3, 0x000018C0
        {self.label_prefix}assert_apply_priv_mask:
            csrc tdata1, t3                              # clear priv enables only

            addi t1, t1, 1
            li t2, 64                                    # safety upper bound
            blt t1, t2, {self.label_prefix}assert_disable_triggers_loop
        {self.label_prefix}skip_assert_disable_triggers:
            """

        # Call post handler user code
        if self.featmgr.excp_hooks:
            code += self._call_excp_hook("excp_handler_post_addr")

        # Return from trap
        code += "\n" + self.trap_exit()

        # kernel panic code, should be unreachable since trap_exit does an xret

        code += "\n" + self.kernel_panic(name=self.trap_panic_label)

        if self.pool.init_aplic_interrupts:
            code += "\n"
            code += f"""
                .section .data
                .size __{self.label_prefix}isr_table, 512
                __{self.label_prefix}isr_table:
                    .zero 88
                    .dword __{self.label_prefix}aplic_isr
                    .zero 416
                .size __{self.label_prefix}aplic_isr_table, 8192
                .globl __{self.label_prefix}aplic_isr_table
                __{self.label_prefix}aplic_isr_table:
                    .zero 2048
            """

        return code

    def _generate_custom_handler_macros(self) -> str:
        """Generate per-vector CUSTOM_HANDLER_PROLOGUE_V and CUSTOM_HANDLER_EPILOGUE_V macros.

        PROLOGUE_V takes the handler label as an argument: CUSTOM_HANDLER_PROLOGUE_V my_label
        EPILOGUE_V takes no argument and restores the default handler.

        The pointer address is loaded via ``li`` using an .os_data equate (intr_handler_ptr_N
        for VA-translated access in paged tests, intr_handler_ptr_N_pa for bare M-mode tests).
        This avoids PC-relative ``la`` which cannot reach .os_data from .code.
        """
        if not self.pool.parsed_custom_handlers:
            return ""
        vectors = list({ch.vector_num for ch in self.pool.parsed_custom_handlers})
        vectors.sort()
        # Choose VA or PA equate depending on whether paging is active.
        # The PROLOGUE/EPILOGUE run at test-body privilege; with paging enabled the VA equate
        # produces the correct translated address; without paging VA=PA so either works.
        use_pa = self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE
        equate_suffix = "_pa" if use_pa else ""
        nl = "\n"
        macro_defs = []
        for v in vectors:
            isr = self.interrupt_handler.vector_table[v]
            default = isr.label
            ptr_equate = f"intr_handler_ptr_{v}{equate_suffix}"
            # Prologue: caller passes the handler label as \handler_label argument
            macro_defs.append(f".macro CUSTOM_HANDLER_PROLOGUE_{v} handler_label")
            macro_defs.append(f"    li t0, {ptr_equate}")
            macro_defs.append(r"    la t1, \handler_label")
            macro_defs.append("    sd t1, 0(t0)")
            macro_defs.append(".endm")
            # Epilogue: restore default handler (no argument needed)
            macro_defs.append(f".macro CUSTOM_HANDLER_EPILOGUE_{v}")
            macro_defs.append(f"    li t0, {ptr_equate}")
            macro_defs.append(f"    la t1, {default}")
            macro_defs.append("    sd t1, 0(t0)")
            macro_defs.append(".endm")
        # Define macros only once; trap handler generate() can be called for both .runtime and .runtime_s
        return f"""
.ifndef __CUSTOM_HANDLER_MACROS_DEFINED
.set __CUSTOM_HANDLER_MACROS_DEFINED, 1
{nl.join(macro_defs)}
.endif
"""

    def ecall_handler(self) -> str:
        """
        Generates the ECALL handler code.
        For machine mode: jumps to syscall table if ECALL_FROM_USER <= cause <= ECALL_FROM_MACHINE
        For non-machine mode: jumps to panic on any ECALL

        Assumes that t1 contains the exception cause
        """
        ecall_target = self.syscall_table_label if self.deleg_mode == RV.RiscvPrivileges.MACHINE else self.trap_panic_label
        return f"""li t0, {RV.RiscvExcpCauses.ECALL_FROM_USER.value} # Checking for ecall
        blt t1, t0, {self.check_exception_label}
        li t0, {RV.RiscvExcpCauses.ECALL_FROM_MACHINE.value}
        bgt t1, t0, {self.check_exception_label}
        j {ecall_target}
        """

    def save_context(self) -> str:
        """
        Code to save context before handling trap.

        - enters hart context (swaps tp with scratch)
        - saves all GPRs to gpr_save_area
        - sets tvec to trap_panic label. Assumes trap panic label is la-able (within 32-bits)
        - if using c_used save all registers. This assumes the stack is loaded into sp already
        """
        save_context = ""
        save_context += self.variable_manager.enter_hart_context(scratch=self.scratch_reg)
        if self.featmgr.save_restore_gprs:
            save_context += "\n\t" + self.save_gprs(self.scratch_reg)

        save_context += f"""
            la t0, {self.trap_panic_label}
            csrw {self.tvec}, t0
        """
        return save_context

    def restore_trap_handler(self) -> str:
        """
        Code to restore trap handler before returning to test code after trap handler.

        - restores tvec to trap_entry label
        """
        restore_context = ""

        restore_context += f"""
            la t1, {self.trap_entry_label}
            csrw {self.tvec}, t1
        """
        return restore_context

    def test_fail(self) -> str:
        """
        Generates the test failure routine if non-M mode (in M-mode, we jump to test_failed directly).
        """
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            return ""

        return f"""
        {self.test_fail_label}:
            li x31, 0xf0000002
            ecall
        """

    def default_trap_handler(self):
        """
        Generates the default trap handler code. Checks for interrupt vs exception.
        Exceptions get handled in exception entry.
        Interrupts get dispatched to the interrupt vector table based on xcause.

        Consists of:

        - routine :py:attr:`trap_handler_label` reads xcause **before** saving context
        - if interrupt (MSB set): dispatches to the interrupt vector table at
          ``trap_entry + 4 * cause``.  The vector table entries are ``j <handler>``
          instructions whose targets handle the interrupt and execute ``xret``
          directly (no context save/restore needed — identical to HW vectored mode).
        - if exception (MSB clear): runs any FeatMgr exception handler overrides
          before saving context, then (on fall-through) saves context and branches
          to :py:attr:`exception_handler_label` for full exception processing.
        - routine :py:attr:`interrupt_handler_label` handles the APLIC dispatch
          path; it saves context at its own entry and re-reads xcause.
        - jumps to :py:attr:`trap_exit_label` to restore context and return to test code
        """

        # Build FeatMgr-registered exception handler override dispatch once — it runs
        # before save_context() in both APLIC and non-APLIC paths so the handler body
        # is responsible for its own register discipline and ends with ctx.xret.
        # t0 still holds xcause from the initial read; t1 is used as a scratch for
        # the cause compare.  Non-overridden causes fall through to save_context().
        excp_dispatch_body = ""
        for cause, (label, _) in self.featmgr.exception_handler_overrides.items():
            cause_delegated = bool((self.featmgr.medeleg >> cause) & 1)
            cause_mode = RV.RiscvPrivileges.SUPER if cause_delegated else RV.RiscvPrivileges.MACHINE
            if cause_mode == self.deleg_mode:
                excp_dispatch_body += f"            li t1, {cause}\n            beq t0, t1, {label}\n"

        # Only emit the comment block when at least one override is registered for
        # this privilege level, so the generated asm stays clean otherwise.
        if excp_dispatch_body:
            excp_dispatch = f"""            # FeatMgr exception handler overrides — matched before context save.
            # t0 still holds {self.xcause} from the initial read above; only t1
            # was clobbered by the interrupt-vs-exception test.
{excp_dispatch_body}"""
        else:
            excp_dispatch = ""

        if self.pool.init_aplic_interrupts:
            # APLIC path: xcause is read BEFORE save_context() so the exception-override
            # dispatch can run with no registers spilled (same contract as non-APLIC).
            # Interrupt handlers save context at their own entry and re-read xcause there.
            topei = f"{self.deleg_mode_str}topei"
            ret = f"""
            {self.trap_handler_label}:
            csrr t0, {self.xcause}
            li t1, (0x1<<(XLEN-1))              # Isolate interrupt bit
            and t1, t1, t0
            beq t1, x0, {self.label_prefix}exception_path  # If the interrupt bit is 0, exception

                {self.interrupt_handler_label}:
                {self.save_context()}
                csrr t0, {self.xcause}
                bclri t0, t0, 63

                li t1, 1
                sll t1, t1, t0
                csrrc x0, {self.xip}, t1

                .equ _INTERRUPT_EXCEPTION_MASK, 0x7fffffffffffffff
                li t1, _INTERRUPT_EXCEPTION_MASK
                and t0, t0, t1
                la t1, __{self.label_prefix}isr_table
                slli t0, t0, 3
                add t1, t1, t0
                ld t1, 0(t1)
                beqz t1, test_failed
                jalr t1

                j {self.trap_exit_label}

                __{self.label_prefix}aplic_isr:
                    la t1, __{self.label_prefix}aplic_isr_table
                    csrrw t0, {topei}, zero
                    srli t0, t0, 16
                    slli t0, t0, 3
                    add t1, t1, t0
                    ld t0, 0(t1)
                    beqz t0, test_failed
                    jr t0

            {self.label_prefix}exception_path:
{excp_dispatch}            {self.save_context()}
            j {self.exception_handler_label}
            """
        else:
            # Non-APLIC path: check xcause BEFORE saving context.
            # Interrupts dispatch directly to the vector table (handlers do their
            # own xret, same as HW vectored mode — no context save/restore).
            # Exceptions fall through to save context and enter the exception handler.
            ret = f"""
            {self.trap_handler_label}:
            csrr t0, {self.xcause}
            li t1, (0x1<<(XLEN-1))              # Isolate interrupt bit
            and t1, t1, t0
            beq t1, x0, {self.label_prefix}exception_path  # If the interrupt bit is 0, exception

                {self.interrupt_handler_label}:
                # Dispatch to the interrupt vector table based on xcause.
                # Strip the interrupt bit (MSB) to get the cause number,
                # then jump into the vector table at trap_entry + 4*cause.
                # Each vector table entry is a 4-byte 'j <handler>' instruction.
                # Handlers execute xret directly (no context save/restore needed).
                li t1, (0x1<<(XLEN-1))
                xor t0, t0, t1                  # Strip interrupt bit to get cause number
                la t1, {self.interrupt_handler.trap_entry_label}
                slli t0, t0, 2                  # cause * 4 (each entry is 4 bytes)
                add t0, t1, t0                  # trap_entry + 4*cause
                jr t0                           # Jump to vector table entry

            {self.label_prefix}exception_path:
{excp_dispatch}            {self.save_context()}
            j {self.exception_handler_label}
            """

        return ret

    def trap_exit(self) -> str:
        """
        Trap exit code. Restores GPRs and returns to test code.

        Implements :py:attr:`trap_exit_label` routine
        """
        # For M-mode paging tests: trapping to M-mode clears MPRV. We need to restore
        # MPRV=1 + MPP=S before returning to test code so data accesses use S-mode translation.
        #
        # We can't use mret for this because mret consumes MPP to set the privilege level.
        # Setting MPP=S before mret would drop us to S-mode instead of staying in M-mode,
        # and mret would also clear MPRV (since MPP != M).
        #
        # Instead, when returning to M-mode (MPP==M), we manually emulate mret:
        #   1. Restore MIE from MPIE, set MPIE=1, set MPP=S, set MPRV=1
        #   2. Read mepc and jr to it (staying in M-mode)
        # When MPP != M (e.g., privilege switch to user mode), use normal mret.
        m_mode_paging_return = self.deleg_mode == RV.RiscvPrivileges.MACHINE and self.paging_mode != RV.RiscvPagingModes.DISABLE and self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE

        if m_mode_paging_return:
            xret_code = f"""
    # Check if returning to M-mode (MPP==3)
    csrr t0, mstatus
    srli t1, t0, 11
    andi t1, t1, 0x3
    li t2, 3
    bne t1, t2, {self.trap_exit_label}_normal_mret

    # Manual mret for M-mode paging: emulate mret behavior in mstatus,
    # then set MPRV=1 + MPP=S and use jr instead of mret.
    #
    # mret would do: MIE=MPIE, MPIE=1, MPP=least-priv, priv=old MPP
    # We replicate the mstatus updates, then set MPP=S and MPRV=1.
    # Bit positions: MIE=3, MPIE=7, MPP=12:11, MPRV=17

    # Step 1: Read MPIE (bit 7) and set MIE (bit 3) accordingly
    srli t1, t0, 4          # Shift MPIE (bit 7) to MIE position (bit 3)
    andi t1, t1, (1 << 3)   # Isolate the MIE bit value
    li t2, (1 << 3)
    csrrc x0, mstatus, t2   # Clear MIE
    csrrs x0, mstatus, t1   # Set MIE to old MPIE value

    # Step 2: Set MPIE=1
    li t0, (1 << 7)
    csrrs x0, mstatus, t0

    # Step 3: Set MPP=S (01) and MPRV=1
    # Clear MPP field first, then set MPP=01 and MPRV=1
    li t0, (0x3 << 11)      # MPP mask
    csrrc x0, mstatus, t0   # Clear MPP
    li t0, ((1 << 17) | (1 << 11))  # MPRV=1, MPP[0]=1
    csrrs x0, mstatus, t0

    # Step 4: Jump to mepc (stay in M-mode)
    csrr t0, mepc
    jr t0

    {self.trap_exit_label}_normal_mret:
    mret
"""
        else:
            xret_code = self.xret

        return f"""
.balign 4, 0
{self.trap_exit_label}:
    {self.featmgr.call_hook(RV.HookPoint.POST_TRAP)}
    {self.restore_trap_handler()}
    {self.restore_gprs(self.scratch_reg) if self.featmgr.save_restore_gprs else self.variable_manager.exit_hart_context(scratch=self.scratch_reg)}
    {xret_code}
"""

    def check_excp(self, return_label: str, xepc: str, xret: str) -> str:
        """
        Generates code to check for expected exceptions.
        Exceptions can be set to expected using the OS_SETUP_CHECK_EXCP macro.

        .. note::

            Assumes that expected exception cause is loaded into t1

        If skip_instruction_for_unexpected is set, skips the instruction check for unexpected exceptions.

        :param return_label: Label to return to after checking exceptions
        :param xepc: CSR to read the exception PC from
        :param xret: Instruction to return from the exception handler
        :return: Assembly code string
        """
        # Hart-local variables
        check_excp = self.variable_manager.get_variable("check_excp")
        check_excp_expected_mode = self.variable_manager.get_variable("check_excp_expected_mode")
        check_excp_expected_cause = self.variable_manager.get_variable("check_excp_expected_cause")
        check_excp_skip_pc_check = self.variable_manager.get_variable("check_excp_skip_pc_check")
        check_excp_expected_pc = self.variable_manager.get_variable("check_excp_expected_pc")
        check_excp_actual_pc = self.variable_manager.get_variable("check_excp_actual_pc")
        check_excp_expected_tval = self.variable_manager.get_variable("check_excp_expected_tval")
        check_excp_expected_htval = self.variable_manager.get_variable("check_excp_expected_htval")
        check_excp_gva_check = self.variable_manager.get_variable("check_excp_gva_check")

        # label to jump to if invalid exception is encountered
        if self.featmgr.skip_instruction_for_unexpected:
            unexpected_exception = f"{self.label_prefix}count_ignored_excp"
        else:
            unexpected_exception = self.test_fail_label

        # Derive current_mode from deleg_mode_str (set in __init__)
        # matches CHECK_EXCP_MODE_* equates: m=1, hs=2, s=3
        current_mode = {"m": 1, "hs": 2, "s": 3}[self.deleg_mode_str]

        code = f"""
            # Check if check_exception is enabled
            {check_excp.load(dest_reg="t0")}
            bne t0, x0, {self.label_prefix}check_excp

            # restore check_excp, return to return_label
            addi t0, t0, 1
            {check_excp.store(src_reg="t0")}
            j {return_label}

         {self.label_prefix}check_excp:
            # Check expected handler mode (0 = any)
            {check_excp_expected_mode.load_and_clear(dest_reg="t0"):<35}  # check_excp_expected_mode
            beqz t0, {self.label_prefix}skip_mode_check
            li t2, {current_mode}
            bne t0, t2, {unexpected_exception}
         {self.label_prefix}skip_mode_check:

            # Check for correct exception code
            {check_excp_expected_cause.load_and_clear(dest_reg="t0"):<35}  # check_excp_expected_cause
            bne t1, t0, {unexpected_exception}

            # when skip_pc_check is set, skip the pc check
            {check_excp_skip_pc_check.load_and_clear(dest_reg="t0"):<35}  # check_excp_skip_pc_check
            bne t0, x0, {self.label_prefix}skip_pc_check

            # compare expected and actual PC values
            {check_excp_expected_pc.load_and_clear(dest_reg="t1"):<35}  # check_excp_expected_pc
            {check_excp_actual_pc.load_and_clear(dest_reg="t0"):<35}  # check_excp_actual_pc
            bne t1, t0, {unexpected_exception}

            # compare expected and actual tval values
            {check_excp_expected_tval.load_and_clear(dest_reg="t0"):<35}  # check_excp_expected_tval
            beqz t0, {self.label_prefix}skip_nonzero_tval_check

         {self.label_prefix}nonzero_tval_check:
            csrr t1, {self.tval}
            bne t1, t0, {unexpected_exception}

         {self.label_prefix}skip_nonzero_tval_check:
        """

        if not self.deleg_virtualized:
            # compare expected and actual htval values
            code += f"""
            {check_excp_expected_htval.load_and_clear(dest_reg="t0"):<35}  # check_excp_expected_htval
            beqz t0, {self.label_prefix}skip_nonzero_htval_check

         {self.label_prefix}nonzero_htval_check:
            csrr t1, {'htval' if self.deleg_mode == RV.RiscvPrivileges.SUPER else 'mtval2'}
            bne t1, t0, {unexpected_exception}

         {self.label_prefix}skip_nonzero_htval_check:
            """

            if self.deleg_mode == RV.RiscvPrivileges.SUPER:
                gva_csr = "hstatus"
                gva_bit = 6
            else:
                gva_csr = "mstatus"
                gva_bit = 38
            code += f"""
            {check_excp_gva_check.load_and_clear(dest_reg="t0"):<35}  # check_excp_gva_check
            beqz t0, {self.label_prefix}skip_gva_check

         {self.label_prefix}gva_check:
            csrr t1, {gva_csr}
            srli t1, t1, {gva_bit}
            andi t1, t1, 1
            beqz t1, {unexpected_exception}
            li t1, (1 << {gva_bit})
            csrc {gva_csr}, t1

         {self.label_prefix}skip_gva_check:
            """

        code += f"""

         {self.label_prefix}skip_pc_check:
            j {return_label}
        """

        if self.featmgr.skip_instruction_for_unexpected:
            # generates code for skipping trap, incrementing ignored exception count, and ending test if max count is reached
            # otherwise, skips trapped instruction and continues to test

            # When the M-mode handler runs, xepc is a virtual address in the mode
            # that trapped (MPP). M-mode loads bypass paging, so a direct lwu
            # reads the wrong physical address. Set MPRV so the load translates
            # via MPP's paging (and MPV's guest paging if set by H-extension).
            # Skip the MPRV dance for the M-mode paging test mode
            # (priv_mode==MACHINE + paging enabled) because that mode has its
            # own MPRV/MPP semantics in the test code.
            mmode_paging_test = self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE and self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE
            use_mprv = self.deleg_mode == RV.RiscvPrivileges.MACHINE and not mmode_paging_test
            if use_mprv:
                mprv_sum_mask = "(1 << 17) | (1 << 18)"  # MPRV | SUM
            else:
                mprv_sum_mask = "(1 << 18)"  # SUM only
            load_faulting_instr = f"""
                csrr t3, {self.xstatus}
                li t4, {mprv_sum_mask}
                csrs {self.xstatus}, t4
                lwu t1, 0(t0)
                csrw {self.xstatus}, t3
            """
            code += f"""
             {self.label_prefix}count_ignored_excp:
                # Get PC exception {xepc}
                csrr t0, {xepc}
                {load_faulting_instr}
                # Check lower 2 bits to see if it equals 3
                andi t1, t1, 0x3
                li t2, 3
                # If bottom two bits are 0b11, we need to add 4 to the PC
                beq t1, t2, {self.label_prefix}pc_plus_four

            {self.label_prefix}pc_plus_two:
                # Otherwise, add 2 to the PC (compressed instruction)
                addi t0, t0, 2
                j {self.label_prefix}jump_over_pc
            {self.label_prefix}pc_plus_four:
                addi t0, t0, 4
            {self.label_prefix}jump_over_pc:
                # Load to {xepc}
                csrw {xepc}, t0
                {self.excp_ignored_count.load_immediate("t0", bare=self.bare)}
                li t1, 1
                amoadd.w t1, t1, (t0)
                li t0, {self.IGNORED_EXCP_MAX_COUNT}
                bge t1, t0, {self.label_prefix}soft_end_test
                # Jump to new PC
                {xret}


             {self.label_prefix}soft_end_test:
                # Have to os_end_test_addr because we're at an elevated privilege level.
                addi gp, zero, 0x1
                {self._soft_end_test_code()}
            """
        return code

    # helper methods
    def _soft_end_test_code(self) -> str:
        """
        Generates code for soft end test.
        For machine mode: directly jump to os_end_test_addr
        For non-machine mode: trigger syscall to end test without failure
        """
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            return """
                li t0, os_end_test_addr_pa
                ld t1, 0(t0)
                jr t1
            """
        else:
            return """
                li x31, 0xf0000003  # End test without failure
                ecall
            """

    def kernel_panic(self, name: str) -> str:
        """
        Override of ``AssemblyGenerator.kernel_panic`` so that the S/HS/VS-mode
        trap panic handlers do not try to branch directly into the ``.runtime``
        section. ``eot__end_test`` lives in ``.runtime`` which is only
        identity-accessible from M-mode; under S-mode sv39/sv48/sv57 paging the
        runtime's text is not mapped into the supervisor page tables, so a
        direct ``j eot__end_test`` from an S-mode trap_panic faults with
        INST_PAGE_FAULT, re-enters the trap_panic, and spins until the ISS
        instruction cap is hit. Use the 0xf0000002 syscall (fail-test) so that
        M-mode terminates the test cleanly.
        """
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            return super().kernel_panic(name)
        return f"""
{name}:
    li gp, 0
    li x31, 0xf0000002  # fail test
    ecall
        """
