# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# flake8: noqa: F401
"""
Implements exception handling for the dtest framework.

Notes:
    Wherever hartid is used, it is freshly retrieved so that fewer assumptions about GPR use are made.
"""

from enum import IntFlag
from typing import Optional

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.macros import CHECK_EXCP_FIELD_IGNORE
from riescue.dtest_framework.trap_context import MACHINE_CTX, SUPERVISOR_CTX
from riescue.dtest_framework.parser import ParsedCsrAccess
from riescue.dtest_framework.pool import Pool


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
    :param pool: Test pool holding parsed resources (CSR accesses, vectored interrupts, custom handlers, etc.)
    :param trap_entry: The label of trap entry point; should be same address written to `mtvec`/`stvec`
    :param default_trap_handler: Label of the default trap handler routine
    :param test_fail_label: Label jumped to on test failure
    :param xlen: The XLEN to use - 32 or 64
    :param label_prefix: String prepended to generated labels to keep them unique per delegation mode
    :param use_pa: Whether generated code addresses memory via physical addresses (True in M mode)
    :param variable_manager: VariableManager used to allocate/resolve runtime variables
    :param is_virtualized: Whether the test runs in a virtualized (VS/VU) environment
    :param deleg_virtualized: Whether this interrupt handler is for a virtualized (V=1) mode
    :param sstc_supported: Whether the hardware implements Sstc (cpuconfig ``supported``, not ``enabled``);
        when True the STI/VSTI clears also re-arm stimecmp/vstimecmp, since a test that sets
        menvcfg/henvcfg.STCE makes the pending bit comparator-driven and read-only in xip
    """

    reserved_interrupt_indicies = [4, 8, 14, 15]

    def __init__(
        self,
        privilege_mode: RV.RiscvPrivileges,
        pool: Pool,
        trap_entry: str = "trap_entry",
        default_trap_handler: str = "default_trap_handler",
        test_fail_label: str = "test_failed",
        xlen: RV.Xlen = RV.Xlen.XLEN64,
        label_prefix: str = "",
        use_pa: bool = True,
        variable_manager=None,
        is_virtualized: bool = False,
        deleg_virtualized: bool = False,
        sstc_supported: bool = False,
    ):
        self.privilege_mode = privilege_mode
        if self.privilege_mode not in [RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER]:
            raise ValueError(f"Privilege mode {self.privilege_mode.value} not supported, supported modes are Machine and Super")

        if self.privilege_mode == RV.RiscvPrivileges.MACHINE:
            self.xip = "mip"
            self.xret = "mret"
            self.xepc = "mepc"
            self.scratch_reg = "mscratch"
        else:
            self.xip = "sip"
            self.xret = "sret"
            self.xepc = "sepc"
            self.scratch_reg = "sscratch"
        self.variable_manager = variable_manager
        self.pool = pool
        self.is_virtualized = is_virtualized
        self.deleg_virtualized = deleg_virtualized
        self.sstc_supported = sstc_supported
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
        self.check_intr_helper_label = f"{label_prefix}intr_assert_check"

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
        code.append(self._check_intr_helper())
        code.append(self._generate_default_isrs())
        code.append(self._clear_highest_interrupt_bit())
        # trap_entry MUST be 4-byte aligned: its address is written to xtvec, and the
        # bottom 2 bits of xtvec are the MODE field. An unaligned trap_entry produces a
        # malformed xtvec (MODE = bits[1:0] of label address) — and the BASE the HW
        # derives is ``addr & ~3``, which is 2 bytes earlier than the actual label.
        # Without this .balign 4, any ``la xreg, trap_entry; csrw xtvec, xreg`` sets
        # reserved MODE=2 and points BASE into the middle of the preceding instruction,
        # causing the next trap to fetch a spliced/illegal instruction. Also required
        # so the immediately-following interrupt_vector_table entries line up with
        # ``BASE + 4*cause`` indexing in HW-vectored mode.
        code.append(".balign 4")
        # Entries must stay 4 bytes wide for the ``trap_entry + 4*cause`` indexing above;
        # ld would otherwise compress ``j`` to ``c.j`` and halve the stride.
        code.append(".option push")
        code.append(".option norelax")
        code.append(f"{self.trap_entry_label}:")
        code.append(f"    j {self.default_trap_handler_label}")
        code.append(self._generate_interrupt_vector_table())
        code.append(".option pop")
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

    def _check_intr_helper(self) -> str:
        """
        Shared OS_SETUP_CHECK_INTR fast-path. Every default clear handler calls
        through here with ``jal t2, <helper>`` AFTER the platform-source clear
        and the xip bit clear, but BEFORE its own xret.

        Why a single shared routine instead of the Python-time ``indirect`` gate:
        - The unified trap_handler only runs check_intr() when traps funnel
          through trap_entry (i.e. mtvec/stvec MODE = direct).
        - If the test reconfigures MODE to HW-vectored midstream, traps land
          directly in _CLEAR_<NAME> and that pre-dispatch check is skipped.
        - Doing the check inside every default ISR closes that gap regardless
          of whether the vector was registered as indirect at gen time.

        Why AFTER the clear, not before:
        - The caller's clear body deasserts the platform source (e.g.
          ``RVMODEL_CLR_MSW_INT``) AND clears the xip pending bit. If the
          helper xret'd to the stored return PC before that work happened, the
          interrupt would simply re-fire and we'd trap-loop.
        - Letting the caller finish the clear first means the helper only has
          to maybe-redirect xepc and return — the caller's xret cleans up.

        Contract:
          - Caller invokes with ``jal t2, helper`` (t2 = return PC).
          - Helper enters hart context via ``csrrw tp, <scratch>, tp``.
          - If ``check_intr == 0``, restores tp and ``jr t2`` back to caller
            (xepc untouched — caller's xret returns to the interrupted PC).
          - If ``check_intr != 0``, loads & clears ``check_intr_return_pc``,
            clears the flag, restores tp, writes xepc to the stored return PC,
            then ``jr t2`` back to caller. Caller's xret then lands at the
            redirected PC.
          - Clobbers t0/t1; preserves t2 (callers rely on it for the return).
        """
        if self.variable_manager is None:
            return ""

        check_intr = self.variable_manager.get_variable("check_intr")
        check_intr_return_pc = self.variable_manager.get_variable("check_intr_return_pc")
        helper = self.check_intr_helper_label
        done = f"{helper}__done"

        return f"""
{helper}:
    csrrw tp, {self.scratch_reg}, tp                # enter hart context
    {check_intr.load(dest_reg='t0')}
    beqz t0, {done}
    {check_intr_return_pc.load_and_clear(dest_reg='t0')}
    li t1, 0
    {check_intr.store(src_reg='t1')}
    csrw {self.xepc}, t0                            # redirect return PC
{done}:
    csrrw tp, {self.scratch_reg}, tp                # exit hart context
    jr t2
"""

    def _clear_highest_interrupt_bit(self) -> str:
        """
        Routine that clears the lowest-numbered pending interrupt bit in xip.
        Uses neg+and to isolate the lowest set bit in O(1): mask = xip & (-xip).
        This avoids the previous loop which had two bugs:
          1. Off-by-one: sll used t2 (always 1 after loop exit) instead of the bit-position counter.
          2. Infinite loop when bit 0 was the only set bit (shift to 0, loop never exits).

        Also routes through the shared check_intr helper AFTER the bit clear
        and BEFORE the xret so platform/custom-cause traps honor
        OS_SETUP_CHECK_INTR even when the trap lands here directly via
        HW-vectored dispatch.
        """
        intr_check_call = f"    jal t2, {self.check_intr_helper_label}\n" if self.variable_manager is not None else ""
        return f"""
{self.clear_highest_priority_interrupt_bit_label}:
    csrr t0, {self.xip}
    neg t1, t0
    and t0, t0, t1              # isolate lowest set bit: xip & (-xip)
    csrrc x0, {self.xip}, t0
{intr_check_call}    {self.xret}
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

    def _clear_hvip_via_machine_ecall(self, imm_value: str) -> list[str]:
        """
        Emit the ecall sequence that clears ``hvip`` (bits given by ``imm_value``)
        from M mode, re-using the existing CSR R/W jump table mechanism.

        hvip is an HS-level CSR; reading/writing it from VS mode raises a Virtual
        Instruction exception. So instead of an inline ``csrrc hvip``, we hand the
        access off to the machine-mode CSR jump table (see
        ``OpSys.generate_csr_rw_jump_table`` and ``Macros._csr_ecall_code``):

          - register an ``hvip`` ``clear`` access with ``force_machine_rw=True`` so
            it is emitted in the machine table (which runs in M mode after the
            ``0xf0001005`` syscall switches privilege),
          - load the bit mask into ``t2`` (the jump table does ``csrc hvip, t2``),
          - stash the CSR id into this hart's ``machine_csr_jump_table_flags`` (hart-local) and ecall.

        The syscall mechanism preserves ``t2`` across the privilege switch and
        returns to the instruction following the ecall, so the ISR can continue
        with its xip bit clear / check_intr / xret as usual.

        :param imm_value: assembler expression for the hvip bit mask, e.g. ``(1<<VSEI)``
        """
        csr_name = "hvip"
        operation = "clear"

        # Register the force-machine hvip clear in the pool if not already present so
        # the machine jump table includes a dispatch entry + handler for it. This runs
        # during TrapHandler.generate(), before OpSys.generate() builds the table.
        existing = self.pool.get_parsed_csr_accesses()
        if csr_name not in existing or f"{operation}_force_machine" not in existing[csr_name]:
            csr_id = self.pool.get_next_csr_id()
            label = f"csr_access_{csr_name}_machine_key_{csr_id}_{operation}"
            self.pool.add_parsed_csr_access(
                ParsedCsrAccess(
                    csr_name=csr_name,
                    priv_mode="supervisor",
                    read_write_set_clear=operation,
                    label=label,
                    csr_id=csr_id,
                    hypervisor=True,
                    force_machine_rw=True,
                )
            )
        parsed = self.pool.get_parsed_csr_access(csr_name, operation, force_machine_rw=True)

        # csr_id -> this hart's flag; tp is the test's in the ISR, so address via the scratch CSR.
        assert self.variable_manager is not None
        flag = self.variable_manager.get_variable("machine_csr_jump_table_flags")
        return [
            f"    li t2, {imm_value}",
            f"    csrr t1, {self.scratch_reg}",
            f"    li t5, {parsed.csr_id}",
            "    " + flag.store(src_reg="t5", base_reg="t1"),
            "    li x31, 0xf0001005",
            "    ecall",
        ]

    def _generate_default_isrs(self) -> str:
        """
        Generates default ISRs - clear interrupt bit and return.

        Timer interrupts (MTI/STI) require special handling: the pending bit
        is sourced from the platform timer comparator, not software, so a
        plain ``csrc xip, (1<<cause)`` alone is a no-op and the trap
        immediately re-fires after ``xret``.

        - ``mip.MTIP`` is read-only and reflects the platform machine timer;
          it is only deasserted by writing ``mtimecmp``.
        - ``mip.STIP`` is read-only when ``menvcfg.STCE=1`` (Sstc enabled)
          and reflects ``time >= stimecmp``; it is only deasserted by
          writing ``stimecmp``. When Sstc is disabled, ``mip.STIP`` is
          software-writable from M mode only and the ``csrc xip`` clear
          below is what works.

        Whenever the hardware implements Sstc the STI clear re-arms
        ``stimecmp`` inline (a test may flip ``menvcfg.STCE`` on even when
        the cpuconfig doesn't enable Sstc); without Sstc in hardware no
        comparator exists and the ``csrc xip`` clear suffices. MTI still
        re-arms ``mtimecmp`` via the staged rvmodel_macros.h helper so the
        ACLINT base tracks the platform config. ``clear_interrupt_bit``
        also emits ``xret``, so no separate return is needed.
        """
        # Every default clear path routes through the shared check_intr helper
        # AFTER doing its platform-source clear and xip bit clear, but BEFORE
        # its xret. Doing this unconditionally (rather than gating on the
        # Python-time `indirect` flag) means the OS_SETUP_CHECK_INTR contract
        # still holds if the test reconfigures mtvec/stvec MODE to HW-vectored
        # midstream and the trap lands directly in _CLEAR_<NAME> instead of
        # going through the unified trap_handler entry where check_intr()
        # normally runs. We inline the xip bit clear (rather than using the
        # `clear_interrupt_bit` macro that emits xret) so the helper call can
        # sit between the clear and the xret.
        emit_check = self.variable_manager is not None
        intr_check_call = f"    jal t2, {self.check_intr_helper_label}" if emit_check else ""

        def _bit_clear_and_return(cause_name: str) -> list[str]:
            return (
                [
                    f"    li t0, (1<<{cause_name})",
                    f"    csrc {self.xip}, t0",
                ]
                + ([intr_check_call] if emit_check else [])
                + [f"    {self.xret}"]
            )

        code = []
        default_isr_lines = [
            f"\n{self.default_isr_label}:",
            f"    csrw {self.xip}, x0",
        ]
        if emit_check:
            default_isr_lines.append(intr_check_call)
        default_isr_lines.append(f"    {self.xret}")
        code.append("\n".join(default_isr_lines))

        for interrupt_enum in RV.RiscvInterruptCause:
            label = f"{self.label_prefix}_CLEAR_{interrupt_enum.name}"
            body = [f"{label}:"]
            if interrupt_enum is RV.RiscvInterruptCause.MTI:
                body.append("    RVMODEL_CLR_MTIMER_INT(t0, t1)")
            elif interrupt_enum is RV.RiscvInterruptCause.STI:
                if self.sstc_supported:
                    # Push the stimecmp deadline out whenever the hw implements Sstc — even if the
                    # cpuconfig doesn't enable it, a test can set menvcfg.STCE and make STIP
                    # comparator-driven; the csrc xip below is then a no-op and the trap would
                    # re-fire forever. Without Sstc in hw the csrc clears mip.STIP (M mode only).
                    body.append("    li t0, 0xffffffff")
                    body.append("    csrw stimecmp, t0")
                if self.deleg_virtualized and self.is_virtualized:
                    # In VS mode, this may come from hvip instead of vstimecmp
                    body.extend(self._clear_hvip_via_machine_ecall(f"(1<<VSTI)"))
            elif interrupt_enum is RV.RiscvInterruptCause.MSI:
                body.append("    RVMODEL_CLR_MSW_INT(t0, t1)")
            elif interrupt_enum is RV.RiscvInterruptCause.SSI:
                body.extend(
                    [
                        "#ifdef RVMODEL_CLR_SSW_INT",
                        "    RVMODEL_CLR_SSW_INT(t0, t1)",
                        "#endif",
                    ]
                )
            elif interrupt_enum is RV.RiscvInterruptCause.MEI:
                body.append("    RVMODEL_CLR_MEXT_INT(t0, t1)")
            elif interrupt_enum is RV.RiscvInterruptCause.SEI:
                # Always claim via `stopei`. When this handler runs in VS mode
                # (a VSEI delegated to VS shows up as scause=9/SEI), `stopei`
                # with V=1 acts on the VGEIN-selected guest interrupt file, so
                # it clears the guest file pending bit. `vstopei` is an HS CSR
                # and raises a virtual-instruction exception from VS mode, so it
                # must NOT be used here.
                body.append("    RVMODEL_CLR_SEXT_INT(t0, t1)")
            elif interrupt_enum is RV.RiscvInterruptCause.VSTI:
                # hvip[6] is the VSTI source; undelegated, so this ISR always runs
                # at HS (V=0) where hvip is reachable directly. hip.VSTIP =
                # hvip.VSTIP OR (with Sstc) the vstimecmp comparator, so also
                # reset vstimecmp when Sstc is enabled -- otherwise its
                # independent signal keeps the interrupt pending.
                body.append(f"    li t2, (1<<{interrupt_enum.name})")
                body.append("    csrrc x0, hvip, t2")
                if self.sstc_supported:
                    body.append("    li t0, 0xffffffff")
                    body.append("    csrw vstimecmp, t0")
            elif interrupt_enum is RV.RiscvInterruptCause.VSSI:
                # hvip[2] is the VSSI source. sip[2] is read-only with VTI=1
                # and raises Virtual Instruction from VS mode — clear hvip directly.
                body.append(f"    li t2, (1<<{interrupt_enum.name})")
                body.append("    csrrc x0, hvip, t2")
            elif interrupt_enum is RV.RiscvInterruptCause.VSEI:
                # VSEI (scause=10 in HS) comes from IMSIC guest interrupt file 1.
                # The HGEI macro points hstatus.VGEIN at file 1 (the home), claims
                # via vstopei to clear the guest file pending bit, then restores
                # VGEIN=1. t2 holds the guest interrupt file index. Runs in HS.
                body.append("    li t2, 1")
                body.append("    RVMODEL_CLR_HGEI_INT(t0, t1, t2)")
            elif interrupt_enum is RV.RiscvInterruptCause.SGEI:
                # SGEI (scause=12, HS-only) is raised by IMSIC guest interrupt
                # file 2 via hgeie[2]. The HGEI macro points hstatus.VGEIN at file 2,
                # claims via vstopei to clear hgeip[2]/SGEIP, then restores VGEIN=1.
                # t2 holds the guest interrupt file index.
                body.append("    li t2, 2")
                body.append("    RVMODEL_CLR_HGEI_INT(t0, t1, t2)")
            body.extend(_bit_clear_and_return(interrupt_enum.name))
            code.extend(body)
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

    class XtinstOption(IntFlag):
        """
        The four columns of the privileged-spec table "Values that may be automatically written to
        the trap instruction (``mtinst`` or ``htinst``) register on an exception trap".
        """

        ZERO = 1  #: zero, i.e. the implementation provides no information about the trap
        TRANSFORMED = 2  #: a transformation of the trapping standard instruction
        CUSTOM = 4  #: a custom value (permitted only if the trapping instruction is non-standard)
        PSEUDO = 8  #: one of the guest-page-fault pseudoinstructions

    #: Options the spec permits per exception cause. Causes absent from this map (the reserved
    #: codes, and the custom cause codes at 24 and above) are not checked at all.
    XTINST_CAUSE_OPTIONS = {
        RV.RiscvExcpCauses.INSTRUCTION_ADDRESS_MISALIGNED.value: XtinstOption.ZERO | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.INSTRUCTION_ACCESS_FAULT.value: XtinstOption.ZERO,
        RV.RiscvExcpCauses.ILLEGAL_INSTRUCTION.value: XtinstOption.ZERO,
        RV.RiscvExcpCauses.BREAKPOINT.value: XtinstOption.ZERO | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.LOAD_ADDRESS_MISALIGNED.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.LOAD_ACCESS_FAULT.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.STORE_ADDRESS_MISALIGNED.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.STORE_ACCESS_FAULT.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.ECALL_FROM_USER.value: XtinstOption.ZERO | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.ECALL_FROM_SUPER.value: XtinstOption.ZERO | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.ECALL_FROM_VS.value: XtinstOption.ZERO | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.ECALL_FROM_MACHINE.value: XtinstOption.ZERO | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.INSTRUCTION_PAGE_FAULT.value: XtinstOption.ZERO,
        RV.RiscvExcpCauses.LOAD_PAGE_FAULT.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.STORE_PAGE_FAULT.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.INSTRUCTION_GUEST_PAGE_FAULT.value: XtinstOption.ZERO | XtinstOption.PSEUDO,
        RV.RiscvExcpCauses.LOAD_GUEST_PAGE_FAULT.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM | XtinstOption.PSEUDO,
        RV.RiscvExcpCauses.VIRTUAL_INSTRUCTION.value: XtinstOption.ZERO | XtinstOption.CUSTOM,
        RV.RiscvExcpCauses.STORE_GUEST_PAGE_FAULT.value: XtinstOption.ZERO | XtinstOption.TRANSFORMED | XtinstOption.CUSTOM | XtinstOption.PSEUDO,
    }

    #: Causes raised by a reading access. Loads and load-reserved raise load causes; stores,
    #: store-conditional and AMOs raise store/AMO causes.
    XTINST_LOAD_CAUSES = (
        RV.RiscvExcpCauses.LOAD_ADDRESS_MISALIGNED.value,
        RV.RiscvExcpCauses.LOAD_ACCESS_FAULT.value,
        RV.RiscvExcpCauses.LOAD_PAGE_FAULT.value,
        RV.RiscvExcpCauses.LOAD_GUEST_PAGE_FAULT.value,
    )
    #: Causes raised by a writing access. See :attr:`XTINST_LOAD_CAUSES`.
    XTINST_STORE_CAUSES = (
        RV.RiscvExcpCauses.STORE_ADDRESS_MISALIGNED.value,
        RV.RiscvExcpCauses.STORE_ACCESS_FAULT.value,
        RV.RiscvExcpCauses.STORE_PAGE_FAULT.value,
        RV.RiscvExcpCauses.STORE_GUEST_PAGE_FAULT.value,
    )

    #: Guest-page-fault pseudoinstruction values (read, write) for VSXLEN=64. The 0x2000/0x2020
    #: pair is VSXLEN=32 only; RiescueD is RV64, so VSXLEN=64 is assumed rather than checked.
    XTINST_PSEUDO_VALUES = (0x3000, 0x3020)

    # Major opcodes, from the spec's RVG base opcode map (inst[1:0]=11). Both the transformed-
    # standard-instruction case and the custom-value case are defined in terms of the value with
    # bit 1 set, so every encoding this check classifies has inst[1:0]=11 and the map covers it.
    XTINST_OPCODE_CUSTOM = (0x0B, 0x2B, 0x5B, 0x7B)  #: custom-0 .. custom-3
    XTINST_OPCODE_RESERVED = (0x1F, 0x3F, 0x5F, 0x7F, 0x6B)  #: the >32b column, plus inst[6:2]=11010
    XTINST_OPCODE_LOAD = (0x03, 0x07)  #: LOAD, LOAD-FP
    XTINST_OPCODE_STORE = (0x23, 0x27)  #: STORE, STORE-FP
    XTINST_OPCODE_AMO = (0x2F,)  #: AMO: LR, SC and the AMOs
    XTINST_OPCODE_HYPER = (0x73,)  #: SYSTEM: HLV, HLVX, HSV
    XTINST_OPCODE_CMO = (0x0F,)  #: MISC-MEM: the Zicbom/Zicboz cbo.* instructions

    XTINST_RS1_MASK = 0x1F << 15  #: bits 19:15, the instruction's rs1 field

    #: Fields each transformation preserves from the trapping instruction. Two chapters define
    #: transformations: the hypervisor extension covers basic loads and stores, atomics and the
    #: virtual-machine load/stores, and the CMO extension (``norm:h_trans_cache``) covers the
    #: cache-block instructions. For a standard instruction outside these kinds the spec says the
    #: trap instruction register shall be written with zero.
    #:
    #: - basic load: funct3, rd, opcode -- the immediate (bits 31:20) is zeroed
    #: - basic store: rs2, funct3, opcode -- the immediate (bits 31:25 and 11:7) is zeroed
    #: - atomic and virtual-machine load/store: every field
    #: - cache-block: opcode, funct3, and ``operation`` (bits 31:20)
    #:
    #: In the first four, bits 19:15 (rs1) are replaced by Addr. Offset, so the field is listed as
    #: kept here -- it must not be checked for zero -- and taken back out of the comparison by
    #: :meth:`_xtinst_compare_mask`. The cache-block transformation is the exception: it has no
    #: Addr. Offset and requires bits 19:15 to be zero, which listing them as not-kept expresses.
    #:
    #: The fields marked not-kept are checked for zero, deliberately, even though a spec NOTE says
    #: decoding software need not confirm a basic load/store's immediate fields are zero and that a
    #: future revision may put performance information there. That NOTE is advice to software
    #: *interpreting* the register; the normative text says the transformation replaces those fields
    #: with zero, so for the current revision a nonzero one is a DUT bug worth catching. An
    #: extension that starts writing them would need this table updated anyway.
    XTINST_TRANSFORM_KEEP = {
        0x03: 0x00007FFF | XTINST_RS1_MASK,
        0x07: 0x00007FFF | XTINST_RS1_MASK,
        0x23: 0x01F0707F | XTINST_RS1_MASK,
        0x27: 0x01F0707F | XTINST_RS1_MASK,
        0x2F: 0xFFFFFFFF,
        0x73: 0xFFFFFFFF,
        0x0F: (0xFFF << 20) | 0x00007000 | 0x0000007F,
    }

    # A small integer per transformation kind, used to index the per-opcode class table and the
    # per-kind mask table below. 0 is reserved for "no transformation defined" so a table entry of
    # 0 is self-describing.
    XTINST_KIND_NONE = 0
    XTINST_KIND_LOAD = 1
    XTINST_KIND_STORE = 2
    XTINST_KIND_AMO = 3
    XTINST_KIND_HYPER = 4
    XTINST_KIND_CMO = 5

    #: Every transformable opcode's kind. Keys are exactly :attr:`XTINST_TRANSFORM_KEEP`'s.
    XTINST_OPCODE_KIND = {
        0x03: XTINST_KIND_LOAD,
        0x07: XTINST_KIND_LOAD,
        0x23: XTINST_KIND_STORE,
        0x27: XTINST_KIND_STORE,
        0x2F: XTINST_KIND_AMO,
        0x73: XTINST_KIND_HYPER,
        0x0F: XTINST_KIND_CMO,
    }

    # Bit layout of a `_xtinst_cause_table` entry.
    XTINST_CAUSE_TRANSFORMED = 0x01
    XTINST_CAUSE_CUSTOM = 0x02
    XTINST_CAUSE_PSEUDO = 0x04
    XTINST_CAUSE_LOAD_DIR = 0x08
    XTINST_CAUSE_STORE_DIR = 0x10
    XTINST_CAUSE_KNOWN = 0x20  #: set iff the cause appears in XTINST_CAUSE_OPTIONS at all

    # Bit layout of a `_xtinst_opcode_table` entry: RESERVED and CUSTOM as flags, KIND packed
    # into the bits above them.
    XTINST_OPCODE_RESERVED_BIT = 0x01
    XTINST_OPCODE_CUSTOM_BIT = 0x02
    XTINST_KIND_SHIFT = 2

    @classmethod
    def _xtinst_cause_mask(cls, option: "TrapHandler.XtinstOption") -> int:
        """Bitmask over exception cause codes of the causes that permit ``option``."""
        return cls._bitmask(cause for cause, options in cls.XTINST_CAUSE_OPTIONS.items() if options & option)

    @classmethod
    def _xtinst_known_cause_mask(cls) -> int:
        """Bitmask of the cause codes :attr:`XTINST_CAUSE_OPTIONS` covers."""
        return cls._bitmask(cls.XTINST_CAUSE_OPTIONS)

    @classmethod
    def _xtinst_opcode_mask(cls, opcodes) -> int:
        """Bitmask over ``inst[6:2]`` of the given major opcodes, for indexing by ``opcode >> 2``."""
        return cls._bitmask(opcode >> 2 for opcode in opcodes)

    @classmethod
    def _xtinst_compare_mask(cls, opcode: int) -> int:
        """Fields of ``opcode``'s transformation that must equal the trapping instruction's."""
        return cls.XTINST_TRANSFORM_KEEP[opcode] & ~cls.XTINST_RS1_MASK & 0xFFFFFFFF

    @classmethod
    def _xtinst_zero_field_mask(cls, opcode: int) -> int:
        """Fields ``opcode``'s transformation must zero."""
        return ~cls.XTINST_TRANSFORM_KEEP[opcode] & 0xFFFFFFFF

    @classmethod
    def _xtinst_cause_table(cls) -> list:
        """
        Byte per cause code from 0 through ``max(XTINST_CAUSE_OPTIONS)``: the permission/direction bits a
        cause not in :attr:`XTINST_CAUSE_OPTIONS` reads back as 0, which :attr:`XTINST_CAUSE_KNOWN`
        distinguishes from a covered cause that happens to permit nothing beyond zero.

        This is the data the generated code loads instead of re-deriving each of the six
        per-cause bitmasks (:meth:`_xtinst_cause_mask` et al.) from scratch on every test.
        """
        table = [0] * (max(cls.XTINST_CAUSE_OPTIONS) + 1)
        for cause, options in cls.XTINST_CAUSE_OPTIONS.items():
            byte = cls.XTINST_CAUSE_KNOWN
            if options & cls.XtinstOption.TRANSFORMED:
                byte |= cls.XTINST_CAUSE_TRANSFORMED
            if options & cls.XtinstOption.CUSTOM:
                byte |= cls.XTINST_CAUSE_CUSTOM
            if options & cls.XtinstOption.PSEUDO:
                byte |= cls.XTINST_CAUSE_PSEUDO
            if cause in cls.XTINST_LOAD_CAUSES:
                byte |= cls.XTINST_CAUSE_LOAD_DIR
            if cause in cls.XTINST_STORE_CAUSES:
                byte |= cls.XTINST_CAUSE_STORE_DIR
            table[cause] = byte
        return table

    @classmethod
    def _xtinst_opcode_table(cls) -> list:
        """
        Byte per major opcode 0..31 (indexed by ``opcode >> 2``): reserved/custom flags plus the
        transformation kind, packed at :attr:`XTINST_KIND_SHIFT`. An opcode with kind
        :attr:`XTINST_KIND_NONE` (0) has no defined transformation.
        """
        table = [0] * 32
        for opcode in cls.XTINST_OPCODE_RESERVED:
            table[opcode >> 2] |= cls.XTINST_OPCODE_RESERVED_BIT
        for opcode in cls.XTINST_OPCODE_CUSTOM:
            table[opcode >> 2] |= cls.XTINST_OPCODE_CUSTOM_BIT
        for opcode, kind in cls.XTINST_OPCODE_KIND.items():
            table[opcode >> 2] |= kind << cls.XTINST_KIND_SHIFT
        return table

    @classmethod
    def _xtinst_kind_masks_table(cls):
        """
        ``(zero_mask, compare_mask)`` per transformation kind, indexed by the kind constants
        above (index 0, :attr:`XTINST_KIND_NONE`, is unused filler). One table replaces the whole
        per-opcode zero-field/compare dispatch and its duplicated per-mask blocks.
        """
        entries = [(0, 0)] * (max(cls.XTINST_OPCODE_KIND.values()) + 1)
        for opcode, kind in cls.XTINST_OPCODE_KIND.items():
            entries[kind] = (cls._xtinst_zero_field_mask(opcode), cls._xtinst_compare_mask(opcode))
        return entries

    @staticmethod
    def _bitmask(indices) -> int:
        """OR of ``1 << i`` over ``indices``."""
        mask = 0
        for index in indices:
            mask |= 1 << index
        return mask

    def _xtinst_enabled(self) -> bool:
        """
        Whether this handler should check its trap instruction CSR.

        Opt-in via ``--check_xtinst``.

        Beyond the flag, ``mtinst``/``htinst`` only exist when the H extension does, and only
        M-mode and HS-mode have one at all -- there is no ``vstinst``. The mode test is on the
        reported handler mode rather than ``deleg_virtualized`` because in a bare_metal environment
        the ``_s`` handler *is* the HS handler, and ``htinst`` is written for traps into it.
        """
        if not self.featmgr.check_xtinst:
            return False
        if not self.featmgr.feature.is_feature_enabled("h"):
            return False
        return self._current_mode_for_handler() != 3  # 1=M, 2=HS, 3=VS

    def _xtinst_interrupt_check(self) -> str:
        """
        Check that the trap instruction CSR is zero on an interrupt.

        The spec allows no other value for an interrupt, so this is the whole check. Emitted in the
        interrupt dispatch path of :meth:`default_trap_handler`, where ``t0`` holds xcause and
        ``t1`` has already been clobbered by the interrupt-vs-exception test.
        """
        if not self._xtinst_enabled():
            return ""
        return f"""
            {self.label_prefix}xtinst_intr_check:
                # On an interrupt the value written to {self.tinst} is always zero.
                csrr t1, {self.tinst}
                beqz t1, {self.label_prefix}xtinst_intr_ok
                j {self.label_prefix}xtinst_fail
            {self.label_prefix}xtinst_intr_ok:
            """

    def _xtinst_kind_lookup(self) -> str:
        """
        Load this trapping encoding's transformation kind into ``t1``.

        Rederives the opcode from ``{self.tinst}`` and indexes :meth:`_xtinst_opcode_table`, so
        this costs only ``t1`` and ``t2`` and leaves ``t0`` alone -- needed because the compare
        stage calls this with ``t0`` holding the fetched instruction.
        """
        prefix = self.label_prefix
        return f"""                csrr t1, {self.tinst}
                ori t1, t1, 0x2
                andi t1, t1, 0x7f
                srli t1, t1, 2
                la t2, {prefix}xtinst_opcode_class
                add t2, t2, t1
                lbu t2, 0(t2)
                srli t1, t2, {self.XTINST_KIND_SHIFT}                     # t1 = transformation kind"""

    def _xtinst_tables(self) -> str:
        """
        The three lookup tables :meth:`xtinst_check` indexes, emitted as data inline in the
        handler's own section (``.runtime``/``.runtime_s``).

        Placed here rather than in ``.rodata`` because the section the check's own code lives in
        is already readable, wherever it ends up mapped -- so a plain ``la`` (PC-relative) reaches
        the table correctly regardless of VA vs PA, with none of the ``code``/``code_pa``
        relocation math cross-section data references would need (see ``xtinst_check``'s
        docstring). Placed physically between ``xtinst_fail`` and ``xtinst_exit``, both of which
        are reached only by jump, so nothing ever falls through into the data.
        """
        prefix = self.label_prefix
        cause_bytes = ", ".join(f"{byte:#04x}" for byte in self._xtinst_cause_table())
        opcode_bytes = ", ".join(f"{byte:#04x}" for byte in self._xtinst_opcode_table())
        kind_words = ", ".join(f"{word:#010x}" for pair in self._xtinst_kind_masks_table() for word in pair)
        return f"""            .balign 4
            {prefix}xtinst_cause_options:                 # permission/direction bits, indexed by cause
                .byte {cause_bytes}
            .balign 4
            {prefix}xtinst_opcode_class:                   # reserved/custom/kind bits, indexed by opcode>>2
                .byte {opcode_bytes}
            .balign 4
            {prefix}xtinst_kind_masks:                     # (zero_mask, compare_mask) pairs, indexed by kind
                .word {kind_words}
            .balign 4"""

    def xtinst_check(self) -> str:
        """
        Check the value the hardware wrote to ``mtinst``/``htinst`` against the values the spec
        permits for the reported exception cause.

        On a synchronous exception the spec permits zero, a transformation of the trapping
        instruction, a custom value (only when the trapping instruction is non-standard), or one of
        the guest-page-fault pseudoinstructions -- and restricts which of those may appear per
        cause. This accepts any value the cause permits and fails the test on anything else.

        Zero is a legal value for every cause. The one case the spec forbids it
        (``norm:H_trap_xtinst_guestpage``) requires the implementation to have written a nonzero
        faulting guest physical address to ``htval``/``mtval2``, which is its own choice to make --
        both CSRs are WARL and may hold only zero -- so there is no value of these registers that
        makes zero illegal on its own. The converse is checked: a pseudoinstruction implies a
        guest-page-fault cause and a nonzero ``htval``/``mtval2``.

        **What's a lookup table and what isn't.** Which options a cause permits, and whether an
        opcode is custom/reserved/transformable, are genuine lookup tables:
        :meth:`_xtinst_cause_table` and :meth:`_xtinst_opcode_table` pack the spec tables into
        byte arrays (plus :meth:`_xtinst_kind_masks_table` for the per-kind zero/compare masks),
        emitted as data by :meth:`_xtinst_tables` and indexed with a single ``la``/``add``/``lbu``
        or ``lw`` -- one load regardless of how many causes or opcodes it covers, in place of
        rebuilding a bitmask from scratch per test. What's left as a branch is only genuinely
        small, fixed-arity dispatch with no table to speak of: the pseudoinstruction-value compare
        (2 literals) and the kind-to-direction-label dispatch (5 kinds).

        **Register footprint: ``t0``, ``t1`` and ``t2`` only, with nothing spilled.** Those are the
        three the exception path already clobbers, and generated tests do keep live values in
        ``t3``-``t6`` across a trap, so touching those breaks them. Staying inside three registers
        works because everything the check needs is either a re-readable CSR (``{tinst}``,
        ``xcause``, ``xepc``, ``htval``/``mtval2``) or a cheap re-lookup in one of the tables
        above, so no value has to stay live: ``t0`` holds the trap value during the legality
        checks and the fetched instruction during the comparison, and every table lookup is
        redone rather than carried across a branch. ``t1`` is reloaded with ``xcause`` on the way
        out, because the code that follows expects it there.

        Emitted inline at the head of the non-ecall exception path. Sitting behind the ecall
        dispatch rather than in front of it is deliberate: the syscall path carries its arguments
        in ``t2``-``t6``. The cost is that xtinst on an ecall trap goes unchecked, where the spec
        defines no transformation anyway.
        """
        if not self._xtinst_enabled():
            return ""

        prefix = self.label_prefix
        cause_limit = max(self.XTINST_CAUSE_OPTIONS) + 1

        pseudo_value_checks = "\n".join(
            f"""                li t1, {value:#x}
                beq t0, t1, {prefix}xtinst_pseudo_value_ok"""
            for value in self.XTINST_PSEUDO_VALUES
        )

        kind_lookup = self._xtinst_kind_lookup()

        return f"""
            {prefix}xtinst_check:
                # Verify {self.tinst} holds one of the values the spec permits for this cause.
                # Touches t0, t1 and t2 only, and spills nothing -- see the method docstring.
                csrr t0, {self.tinst}
                beqz t0, {prefix}xtinst_done             # zero is a legal value for every cause
                srli t1, t0, 32
                bnez t1, {prefix}xtinst_fail    # nonzero above bit 31; every defined value is 32 bits

                # Cause permission/direction bits, looked up once instead of rebuilt per test.
                csrr t1, {self.xcause}
                li t2, {cause_limit}
                bgeu t1, t2, {prefix}xtinst_exit          # cause outside the spec table: not checked
                la t2, {prefix}xtinst_cause_options
                add t2, t2, t1
                lbu t2, 0(t2)                              # t2 = permission/direction byte for this cause
                andi t1, t2, {self.XTINST_CAUSE_KNOWN:#04x}
                beqz t1, {prefix}xtinst_exit              # cause not covered by the spec table

                andi t1, t0, 0x3
                bnez t1, {prefix}xtinst_not_pseudo

                # bits[1:0] == 0b00: the value can only be a guest-page-fault pseudoinstruction.
                # Not conditioned on htval/mtval2: the allowed-value list
                # (norm:H_trap_xtinst_exception_list) permits a pseudoinstruction on any
                # guest-page-fault cause outright. norm:H_trap_xtinst_guestpage only implies the
                # other direction -- an implicit VS-stage access *with* a nonzero faulting guest
                # physical address must write one -- so requiring a nonzero htval here would fail a
                # conforming DUT whose WARL htval reads zero.
                andi t1, t2, {self.XTINST_CAUSE_PSEUDO:#04x}
                beqz t1, {prefix}xtinst_fail    # cause does not permit a pseudoinstruction
{pseudo_value_checks}
                j {prefix}xtinst_fail    # bits[1:0] == 0b00 but not a defined pseudoinstruction
            {prefix}xtinst_pseudo_value_ok:
                j {prefix}xtinst_exit

            {prefix}xtinst_not_pseudo:
                andi t1, t0, 0x1
                beqz t1, {prefix}xtinst_fail    # bits[1:0] == 0b10, which no defined value has

                # Both remaining cases are defined in terms of the value with bit 1 set, so they
                # are decided by the opcode-class byte for that encoding's major opcode.
                csrr t1, {self.tinst}
                ori t1, t1, 0x2
                andi t1, t1, 0x7f
                srli t1, t1, 2
                la t0, {prefix}xtinst_opcode_class
                add t0, t0, t1
                lbu t0, 0(t0)                              # t0 = reserved/custom/kind byte for this opcode

                andi t1, t0, {self.XTINST_OPCODE_RESERVED_BIT:#04x}
                bnez t1, {prefix}xtinst_fail    # a reserved encoding is not a legal custom value
                andi t1, t0, {self.XTINST_OPCODE_CUSTOM_BIT:#04x}
                beqz t1, {prefix}xtinst_not_custom
                andi t1, t2, {self.XTINST_CAUSE_CUSTOM:#04x}
                beqz t1, {prefix}xtinst_fail    # cause does not permit a custom value
                j {prefix}xtinst_exit

            {prefix}xtinst_not_custom:
                # A standard encoding: it must be the transformation of the trapping instruction,
                # so the trapping instruction must be a kind a transformation is defined for.
                srli t1, t0, {self.XTINST_KIND_SHIFT}
                beqz t1, {prefix}xtinst_fail    # standard instruction with no defined transformation
                andi t0, t2, {self.XTINST_CAUSE_TRANSFORMED:#04x}
                beqz t0, {prefix}xtinst_fail    # cause does not permit a transformation

                # The access direction the value describes must match the exception cause. AMO and
                # cache-block operations accept either direction (LR raises load causes while SC
                # and the AMOs raise store/AMO causes, and the CMO spec leaves the direction
                # implementation-specific), so they skip straight to the zero-fields check.
                li t0, {self.XTINST_KIND_LOAD}
                beq t1, t0, {prefix}xtinst_dir_load
                li t0, {self.XTINST_KIND_STORE}
                beq t1, t0, {prefix}xtinst_dir_store
                li t0, {self.XTINST_KIND_HYPER}
                beq t1, t0, {prefix}xtinst_dir_hyper
                j {prefix}xtinst_dir_any
            {prefix}xtinst_dir_any:
                # AMO and cache-block: either direction is legal, so there is nothing to check.
                # Named rather than a bare fall-through so the trace shows which path a trap took,
                # and so a kind added to the table later lands somewhere explicit.
                j {prefix}xtinst_zero_fields
            {prefix}xtinst_dir_hyper:
                # SYSTEM: HSV differs from HLV and HLVX in funct7 bit 0, i.e. bit 25.
                csrr t0, {self.tinst}
                srli t0, t0, 25
                andi t0, t0, 1
                bnez t0, {prefix}xtinst_dir_store
            {prefix}xtinst_dir_load:
                andi t1, t2, {self.XTINST_CAUSE_LOAD_DIR:#04x}
                beqz t1, {prefix}xtinst_fail    # read/write direction disagrees with the cause
                j {prefix}xtinst_zero_fields
            {prefix}xtinst_dir_store:
                andi t1, t2, {self.XTINST_CAUSE_STORE_DIR:#04x}
                beqz t1, {prefix}xtinst_fail    # read/write direction disagrees with the cause

            {prefix}xtinst_zero_fields:
                # Fields the transformation must have zeroed, looked up by kind. t2 (the cause
                # byte) is dead from here on, so this and the compare below are free to clobber it.
{kind_lookup}
                la t2, {prefix}xtinst_kind_masks
                slli t1, t1, 3
                add t2, t2, t1
                lw t1, 0(t2)                                # zero_mask
                csrr t2, {self.tinst}
                and t1, t1, t2
                bnez t1, {prefix}xtinst_fail    # a field the transformation must zero is nonzero

{self._xtinst_fetch_instruction()}

            {prefix}xtinst_compare:
                # t0 now holds the trapping instruction; every field the transformation keeps must
                # match the value, looked up by kind the same way.
{kind_lookup}
                la t2, {prefix}xtinst_kind_masks
                slli t1, t1, 3
                add t2, t2, t1
                lw t1, 4(t2)                                # compare_mask
                csrr t2, {self.tinst}
                ori t2, t2, 0x2
                xor t2, t2, t0
                and t1, t1, t2
                bnez t1, {prefix}xtinst_fail    # does not describe the instruction that trapped
                j {prefix}xtinst_exit

            {prefix}xtinst_fail:
                j {self.test_fail_label}

{self._xtinst_tables()}

            {prefix}xtinst_exit:
                csrr t1, {self.xcause}                   # the code below expects the cause here
            {prefix}xtinst_done:
            """

    def _xtinst_fetch_instruction(self) -> str:
        """
        Read the trapping instruction at ``xepc`` into ``t0``.

        bits[1:0] of the value say whether the trapping instruction was compressed, so the two
        cases are split before the read and each fetches only what it needs -- a *word* read of a
        compressed instruction in the last two bytes of a page would touch a page that was never
        fetched at all. A compressed instruction is not compared field by field -- that would mean
        expanding it to its 32-bit equivalent first -- so its path ends at the bits[1:0] agreement
        check.

        Both reads are halfword reads, and the 32-bit case combines two of them, rather than a
        single ``lwu``. With RVC enabled a 32-bit instruction can sit at an address ≡ 2 (mod 4),
        which would make a word read misaligned; on a DUT that does not implement misaligned loads
        that raises a nested load-address-misaligned exception while ``xtvec`` still points at
        ``trap_panic``, turning a passing test into a panic. Halfword reads are always aligned
        here, and they also make the page-straddling case fall out for free.

        How to read depends on where the trap came from. In M-mode, ``MPRV`` makes the load use
        ``MPP``'s privilege and ``MPV``'s virtualization, so one path covers traps from HS, HU, VS
        and VU. HLVX is not usable there: it takes its effective privilege from ``hstatus.SPVP``,
        which only a trap into HS updates, so in the M-mode handler that field is stale. In HS-mode
        there is no ``MPRV``, so ``hstatus.SPV`` selects between an ordinary translated load and
        ``hlvx.hu``, which performs the two-stage read with execute permission and the VS-vs-VU
        privilege the trap just recorded in ``hstatus.SPVP``.

        Every path but HLVX sets ``MXR`` alongside ``SUM``, so a page that is executable but not
        readable -- which the original instruction fetch sailed through -- is still readable here.
        HLVX needs no ``MXR``: it reads with execute permission by construction.

        Uses ``t0``, ``t1`` and ``t2`` only: ``t1`` holds the saved ``xstatus`` across the load and
        ``t2`` the mask and then the address, so ``xepc`` is read straight into a scratch register
        rather than kept live.
        """
        prefix = self.label_prefix
        mxr = 1 << 19
        sum_ = 1 << 18
        mprv = 1 << 17

        def read(wide: bool, tag: str) -> str:
            # An ordinary translated read, of one halfword or of two combined into t0. t2 does
            # double duty -- the address, then the high halfword -- because t1 has to hold the
            # saved xstatus across both loads and t0 the result.
            def loads(indent: str) -> str:
                if not wide:
                    return f"{indent}csrr t2, {self.xepc}\n{indent}lhu t0, 0(t2)"
                return f"""{indent}csrr t2, {self.xepc}
{indent}lhu t0, 0(t2)
{indent}lhu t2, 2(t2)"""

            def combine(indent: str) -> str:
                if not wide:
                    return ""
                return f"\n{indent}slli t2, t2, 16\n{indent}or t0, t0, t2"

            body = "                "
            if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
                # Skip MPRV for the M-mode-paging test configuration, which has its own MPRV/MPP
                # semantics in the test code -- the same carve-out the skip-instruction path makes.
                mmode_paging_test = self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE and self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE
                bits = f"{sum_:#x} | {mxr:#x}" if mmode_paging_test else f"{mprv:#x} | {sum_:#x} | {mxr:#x}"
                return f"""                csrr t1, {self.xstatus}
                li t2, {bits}
                csrs {self.xstatus}, t2
{loads(body)}
                csrw {self.xstatus}, t1{combine(body)}"""
            guest = f"{prefix}xtinst_fetch_{tag}_guest"
            done = f"{prefix}xtinst_fetch_{tag}_done"
            # HLVX has no offset form, so the second halfword needs the address bumped. t1 is free
            # on this path -- it is not holding a saved xstatus, since HLVX needs no xstatus change.
            if wide:
                hlvx = f"""                csrr t2, {self.xepc}
                hlvx.hu t0, (t2)
                addi t2, t2, 2
                hlvx.hu t1, (t2)
                slli t1, t1, 16
                or t0, t0, t1"""
            else:
                hlvx = f"""                csrr t2, {self.xepc}
                hlvx.hu t0, (t2)"""
            return f"""                csrr t1, hstatus
                srli t1, t1, 7                           # hstatus.SPV
                andi t1, t1, 1
                bnez t1, {guest}
                csrr t1, {self.xstatus}
                li t2, {sum_:#x} | {mxr:#x}
                csrs {self.xstatus}, t2
{loads(body)}
                csrw {self.xstatus}, t1{combine(body)}
                j {done}
            {guest}:
{hlvx}
            {done}:"""

        return f"""            {prefix}xtinst_fetch:
                # bit 1 of the value: set iff it says the trapping instruction was 32-bit.
                csrr t0, {self.tinst}
                andi t0, t0, 0x2
                beqz t0, {prefix}xtinst_fetch_compressed

{read(True, "word")}
                andi t1, t0, 0x3
                li t2, 0x3
                bne t1, t2, {prefix}xtinst_fail    # value says 32-bit, instruction is compressed
                j {prefix}xtinst_compare

            {prefix}xtinst_fetch_compressed:
{read(False, "half")}
                andi t1, t0, 0x3
                li t2, 0x3
                beq t1, t2, {prefix}xtinst_fail    # value says compressed, instruction is 32-bit
                j {prefix}xtinst_exit"""

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
            variable_manager=self.variable_manager,
            pool=self.pool,
            is_virtualized=(self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED),
            deleg_virtualized=deleg_virtualized,
            sstc_supported=self.featmgr.is_feature_supported("sstc"),
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
        # H-extension trap CSRs. Only exist when H is implemented, so every use is gated on
        # ``featmgr.feature.is_feature_enabled("h")``.
        self.tval2 = "htval"
        self.tinst = "htinst"
        self.scratch_reg = "sscratch"
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            self.xcause = "mcause"
            self.xepc = "mepc"
            self.xret = "mret"
            self.xip = "mip"
            self.xstatus = "mstatus"
            self.tvec = "mtvec"
            self.tval = "mtval"
            self.tval2 = "mtval2"
            self.tinst = "mtinst"
            self.scratch_reg = "mscratch"
        self.panic_cause = f"{self.label_prefix}TRAP_HANDLER_PANIC_CAUSE"

    def _current_mode_for_handler(self) -> int:
        """
        Privilege mode encoding this handler reports to ``CHECK_EXCP_MODE_*`` /
        ``OS_SETUP_CHECK_INTR``: 1=M, 2=HS, 3=VS.

        The ``_s`` label prefix doubles as the supervisor handler in two distinct
        configurations (see ``runtime.py:127-131``):

          - bare_metal env: ``_s`` is the only S-mode handler, and the test runs
            at HS (V=0). Must report 2 (HS).
          - virtualized env: ``_s`` is the VS handler; the separate ``_hs``
            handler covers HS. Must report 3 (VS).
        """
        if self.deleg_mode_str == "m":
            return 1
        if self.deleg_mode_str == "hs":
            return 2
        # deleg_mode_str == "s"
        return 3 if self.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED else 2

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
                override_handlers += f"\n.balign 4\n{label}:\n{assembly_fn(ctx)}\n"

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
                excp_override_handlers += f"\n.balign 4\n{label}:\n{assembly_fn(ctx)}\n"

        code = f"""
        .section .{section_name}, "ax"
        {custom_macros}
        {self.interrupt_handler.generate(custom_vectors=custom_vectors)}
        {override_handlers}
        {excp_override_handlers}
        {self.test_fail()}
        {self.default_trap_handler()}
        .balign 4
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
            {self.xtinst_check()}

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
            # Register .os_data pointer cells + equates so trap-handler code can reach
            # these .data-resident tables via ``li reg, <equate>; ld reg, 0(reg)``.
            # Direct ``la`` would emit R_RISCV_PCREL_HI20, which can't span the >2GiB
            # gap between .runtime/.runtime_s and .data in high-VA paged layouts.
            self.pool.add_interrupt_handler_pointer(f"{self.label_prefix}isr_table_ptr", f"__{self.label_prefix}isr_table")
            self.pool.add_interrupt_handler_pointer(f"{self.label_prefix}aplic_isr_table_ptr", f"__{self.label_prefix}aplic_isr_table")

            # Populate the direct-mode dispatch table with the same per-cause handlers
            # used by the HW-vectored interrupt_vector_table. Without this, SET_DIRECT_INTERRUPTS
            # tests trap into the unified handler, find isr_table[cause] = 0 for any non-APLIC
            # cause (SSI/STI/MTI/SEI/etc.), and hit ``beqz t1, test_failed``. Slot 11 (MEI) is
            # special-cased to aplic_isr so APLIC sources still get claimed via mtopei.
            isr_table_entries = []
            for i in range(64):
                if i == RV.RiscvInterruptCause.MEI.value:
                    label = f"__{self.label_prefix}aplic_isr"
                elif i in self.interrupt_handler.vector_table:
                    isr = self.interrupt_handler.vector_table[i]
                    label = isr.jump_table_label if isr.indirect else isr.label
                else:
                    label = "0"
                isr_table_entries.append(f"                    .dword {label}")
            isr_table_body = "\n".join(isr_table_entries)

            # Populate every slot of aplic_isr_table with the default per-eid handler
            # so an MEI delivered for any eid (without an explicit __set_aplic_isr
            # registration) still routes through the check_intr helper and honors
            # OS_SETUP_CHECK_INTR — tests that just trigger an MEI and expect it to
            # be serviced no longer fall off the dispatcher's beqz to test_failed.
            # 256 slots × 8 bytes = 2048 bytes (matches the original .zero 2048).
            aplic_default_label = f"__{self.label_prefix}aplic_default_isr"
            aplic_isr_table_body = "\n".join(f"                    .dword {aplic_default_label}" for _ in range(256))

            # Emit the ISR tables in-place in the current section (.runtime / .runtime_s)
            # rather than switching to .data. The LD script identity-maps .runtime
            # (VMA == LMA at 0x80000000+), so ``.dword __isr_table_entry`` resolves to
            # an address that's valid in M-mode bare access — unlike .data, whose
            # symbols resolve to VMAs that don't match the LMA where content is loaded.
            # This also keeps the tables within ±2 GiB of dispatcher code, so the
            # ``li reg, <equate>; ld reg, 0(reg)`` indirection remains correct (the
            # cell stores a VMA that's also a usable PA).
            code += "\n"
            code += f"""
                .balign 8
                .size __{self.label_prefix}isr_table, 512
                __{self.label_prefix}isr_table:
{isr_table_body}
                .balign 8
                .size __{self.label_prefix}aplic_isr_table, 8192
                .globl __{self.label_prefix}aplic_isr_table
                __{self.label_prefix}aplic_isr_table:
{aplic_isr_table_body}
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
            # save_gprs spilled the *clobbered* t0/t1 (the trap-entry prologue uses
            # them to classify interrupt-vs-exception). Overwrite their slots with
            # the originals stashed by dispatch_save_prologue() so the interrupted
            # code sees t0/t1 unchanged on return -- required for transparent
            # asynchronous traps (icount/sdtrig breakpoints landing mid-sequence).
            disp = self.variable_manager.get_variable("trap_dispatch_save")
            gpr = self.variable_manager.get_variable("gpr_save_area")
            save_context += "\n\t" + "\n\t".join(
                [
                    "# Restore original t0/t1 into their GPR save slots",
                    disp.load("t0", index=0),
                    gpr.store("t0", index=5),
                    disp.load("t0", index=1),
                    gpr.store("t0", index=6),
                ]
            )

        save_context += f"""
            la t0, {self.trap_panic_label}
            csrw {self.tvec}, t0
        """
        return save_context

    def dispatch_save_prologue(self) -> str:
        """Stash t0/t1 before the mcause classification clobbers them.

        The trap handler reads xcause into t0 and builds the interrupt-bit mask in
        t1 *before* save_context() spills the GPRs, so the interrupted code's t0/t1
        would otherwise be lost. Swap in the hart context, spill t0/t1 to the
        ``trap_dispatch_save`` scratch, then swap back so the classification and any
        FeatMgr exception-override dispatch run with exactly the entry register
        state. save_context() copies these originals into gpr_save_area[5]/[6].
        """
        if not self.featmgr.save_restore_gprs:
            return ""
        disp = self.variable_manager.get_variable("trap_dispatch_save")
        return "\n\t".join(
            [
                "# Preserve dispatch temporaries (t0/t1) across the trap",
                f"csrrw tp, {self.scratch_reg}, tp",
                disp.store("t0", index=0),
                disp.store("t1", index=1),
                f"csrrw tp, {self.scratch_reg}, tp",
            ]
        )

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

    def installed_excp_handler_dispatch(self) -> str:
        """
        Dispatch for the runtime-installed exception handler (OS_INSTALL_EXCP_HANDLER),
        emitted at exception_path before save_context(). t0 holds xcause; t0/t1 are the
        only clobberable registers and the scratch CSR still holds the hart-context
        pointer (pre-swap).

        Loads the hart-local excp_handler_cause; on xcause match (and expected-mode match
        when excp_handler_mode is nonzero) jumps to the hart-local excp_handler_addr
        (test-supplied body ending in xret). Any mismatch falls through to the original
        exception path (FeatMgr overrides -> save_context -> excp_entry).

        The handler address is materialized at the OS_INSTALL_EXCP_HANDLER call site in
        test code, so this dispatch carries no relocation of its own for the jump's
        reach: the jr reaches anywhere in the image regardless of how far the handler
        label is from .runtime. The M-mode dispatch does relocate the loaded VA to a
        PA before jumping (M-mode instruction fetches are never translated), assuming
        the handler lives in .code — same code/code_pa idiom as _call_excp_hook.

        The expected-mode gate guards against medeleg randomization, mid-test medeleg
        writes, and trap origin (an M-mode ebreak lands in the M handler even with
        medeleg[3]=1): a cause match arriving at the wrong-mode handler falls through
        to the original path instead of jumping into a body written for another mode
        (wrong xret, wrong CSR view, VA-vs-bare addressing).
        """
        excp_handler_cause = self.variable_manager.get_variable("excp_handler_cause")
        excp_handler_mode = self.variable_manager.get_variable("excp_handler_mode")
        excp_handler_addr = self.variable_manager.get_variable("excp_handler_addr")
        prefix = f"{self.label_prefix}ih"
        fallthrough = f"{prefix}_fallthrough"
        my_mode = self._current_mode_for_handler()  # CHECK_EXCP_MODE_*: 1=M, 2=HS, 3=VS
        indent = "            "

        lines = [
            f"{indent}# Installed exception-handler dispatch — matched before context save.",
            f"{indent}# On cause (+mode) match: jump to excp_handler_addr (handler ends in {self.xret}); else fall through.",
            f"{indent}csrr t1, {self.scratch_reg}",
            f"{indent}{excp_handler_cause.load(dest_reg='t1', base_reg='t1')}",
            f"{indent}bne t0, t1, {fallthrough}",
            f"{indent}csrr t1, {self.scratch_reg}",
            f"{indent}{excp_handler_mode.load(dest_reg='t1', base_reg='t1')}",
            f"{indent}beqz t1, {prefix}_fire",
            f"{indent}li t0, {my_mode}",
            f"{indent}bne t1, t0, {prefix}_mode_miss",
            f"{prefix}_fire:",
            f"{indent}csrr t1, {self.scratch_reg}",
            f"{indent}{excp_handler_addr.load(dest_reg='t1', base_reg='t1')}",
        ]
        if self.deleg_mode == RV.RiscvPrivileges.MACHINE:
            # M-mode fetches are bare: relocate the stored VA to a PA, assuming the
            # handler lives in .code (pa = va - align4k(code) + code_pa). Identity
            # when paging is off, since then code_pa == align4k(code).
            lines += [
                f"{indent}li t0, code",
                f"{indent}srli t0, t0, 12",
                f"{indent}slli t0, t0, 12",
                f"{indent}sub t1, t1, t0",
                f"{indent}li t0, code_pa",
                f"{indent}add t1, t1, t0",
            ]
        lines += [
            f"{indent}jr t1",
            f"{prefix}_mode_miss:",
            f"{indent}csrr t0, {self.xcause}  # restore xcause for the original path",
            f"{fallthrough}:",
        ]
        return "\n".join(lines) + "\n"

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

        # Installed exception-handler dispatch runs first at exception_path; its fall-through is the original path.
        ih_dispatch = self.installed_excp_handler_dispatch()

        if self.pool.init_aplic_interrupts:
            # APLIC path: xcause is read BEFORE save_context() so the exception-override
            # dispatch can run with no registers spilled (same contract as non-APLIC).
            # Interrupt handlers save context at their own entry and re-read xcause there.
            # HS handler uses "stopei" (not "hstopei" — that CSR doesn't exist in the AIA spec)
            topei_prefix = "s" if self.deleg_mode_str == "hs" else self.deleg_mode_str
            topei = f"{topei_prefix}topei"
            equate_suffix = "_pa" if self.bare else ""
            ret = f"""
            {self.trap_handler_label}:
            {self.dispatch_save_prologue()}
            csrr t0, {self.xcause}
            li t1, (0x1<<(XLEN-1))              # Isolate interrupt bit
            and t1, t1, t0
            beq t1, x0, {self.label_prefix}exception_path  # If the interrupt bit is 0, exception
            {self._xtinst_interrupt_check()}

                {self.interrupt_handler_label}:
                {self.save_context()}
                csrr t0, {self.xcause}
                bclri t0, t0, 63

                .equ _INTERRUPT_EXCEPTION_MASK, 0x7fffffffffffffff
                li t1, _INTERRUPT_EXCEPTION_MASK
                and t0, t0, t1
                li t1, {self.label_prefix}isr_table_ptr{equate_suffix}
                ld t1, 0(t1)
                slli t0, t0, 3
                add t1, t1, t0
                ld t1, 0(t1)
                # Restore {self.tvec} before dispatching. save_context() set {self.tvec} = trap_panic
                # for nested-fault protection, but the _CLEAR_<NAME> handlers (shared with HW-vectored
                # mode) xret directly without going through trap_exit — so if we don't restore here,
                # {self.tvec} stays stuck at trap_panic and the next trap routes to the panic path.
                # Done before the beqz so the failure branch also leaves {self.tvec} sane.
                la t0, {self.trap_entry_label}
                csrw {self.tvec}, t0
                beqz t1, test_failed
                # Un-swap (sscratch <-> tp) right before dispatching so the per-cause
                # ISR sees the same (sscratch=hart_ctx, tp=user_tp) state it would see
                # on a HW-vectored direct entry. Without this, the ISR's call to
                # ``intr_assert_check`` (which does its own enter/exit hart_context swap)
                # would be a *second* swap on top of save_context()'s, ending up with
                # tp=user_tp inside the helper and faulting on the first hart_ctx load.
                # Symmetric re-swap below restores save_context()'s post-state so the
                # rare ISR that ``ret``s instead of ``{self.xret}``ing falls into
                # trap_exit with the state trap_exit expects.
                csrrw tp, {self.scratch_reg}, tp
                jalr t1
                csrrw tp, {self.scratch_reg}, tp

                j {self.trap_exit_label}

                __{self.label_prefix}aplic_isr:
                    li t1, {self.label_prefix}aplic_isr_table_ptr{equate_suffix}
                    ld t1, 0(t1)
                    csrrw t0, {topei}, zero
                    srli t0, t0, 16
                    slli t0, t0, 3
                    add t1, t1, t0
                    ld t0, 0(t1)
                    # Same {self.tvec} restore as above — the per-eiid handler xrets directly.
                    la t1, {self.trap_entry_label}
                    csrw {self.tvec}, t1
                    beqz t0, test_failed
                    jr t0

                # Default per-eid ISR for APLIC external interrupts. Used to populate
                # every aplic_isr_table slot at static-init time so a "trigger MEI and
                # expect it to be serviced" test (OS_SETUP_CHECK_INTR pattern) works
                # without the test code explicitly calling __set_aplic_isr first.
                # The source was already claim-and-cleared by ``csrrw t0, mtopei, zero``
                # in the dispatcher above, so this handler just routes through the
                # check_intr helper (which honors OS_SETUP_CHECK_INTR by redirecting
                # xepc to the stored return PC) and xrets.
                __{self.label_prefix}aplic_default_isr:
                    jal t2, {self.interrupt_handler.check_intr_helper_label}
                    {self.xret}

            {self.label_prefix}exception_path:
{ih_dispatch}{excp_dispatch}            {self.save_context()}
            j {self.exception_handler_label}
            """
        else:
            # Non-APLIC path: check xcause BEFORE saving context.
            # Interrupts dispatch directly to the vector table (handlers do their
            # own xret, same as HW vectored mode — no context save/restore).
            # Exceptions fall through to save context and enter the exception handler.
            ret = f"""
            {self.trap_handler_label}:
            {self.dispatch_save_prologue()}
            csrr t0, {self.xcause}
            li t1, (0x1<<(XLEN-1))              # Isolate interrupt bit
            and t1, t1, t0
            beq t1, x0, {self.label_prefix}exception_path  # If the interrupt bit is 0, exception
            {self._xtinst_interrupt_check()}

                {self.interrupt_handler_label}:
                # Enter hart context so check_intr's tp-relative loads work. Per-cause
                # vector table handlers expect (sscratch=hart_ctx, tp=user_tp) on entry,
                # so we swap back below before dispatching. check_intr's match path
                # jumps to trap_exit, which does its own exit_hart_context.
                {self.variable_manager.enter_hart_context(scratch=self.scratch_reg)}
                {self.check_intr()}
                # Restore (sscratch=hart_ctx, tp=user_tp) for vector-table dispatch.
                {self.variable_manager.exit_hart_context(scratch=self.scratch_reg)}
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
{ih_dispatch}{excp_dispatch}            {self.save_context()}
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
.balign 4
{self.trap_exit_label}:
    {self.featmgr.call_hook(RV.HookPoint.POST_TRAP)}
    {self.restore_trap_handler()}
    {self.restore_gprs(self.scratch_reg) if self.featmgr.save_restore_gprs else self.variable_manager.exit_hart_context(scratch=self.scratch_reg)}
    {xret_code}
"""

    def _check_trap_entry_priv_state(self, unexpected_exception: str) -> str:
        """
        Verify the privilege state a trap entry is required to leave behind:
        ``sstatus``/``vsstatus``.SPP, ``hstatus``.SPV and ``hstatus``.SPVP.

        Each expectation is set by ``OS_SETUP_CHECK_EXCP`` and holds the expected
        bit itself -- 0 or 1 -- or ``CHECK_EXCP_FIELD_IGNORE`` (-1, the macro
        default) to skip the check. Any negative value reads as "ignore", so the
        test is a single ``bltz``.

        The sentinel is written back after each read rather than clearing to zero,
        because zero is a meaningful expectation under this encoding: a leftover
        would otherwise arm a spurious "SPP must be 0" check on the next trap.
        These are hart-local variables, so a plain load/store is sufficient and no
        atomic swap is needed.

        SPP is read from ``sstatus``, which the hardware resolves to ``vsstatus``
        when this handler runs at V=1 -- so one emission covers both the trap-into-HS
        and the trap-into-VS rule. SPV and SPVP live in ``hstatus``, which is only
        accessible at V=0, so they can only be checked in the HS-mode handler. Every
        handler still consumes all three variables; a handler that cannot perform a
        check fails the test on a non-sentinel expectation rather than silently
        passing a mis-specified scenario.

        Clobbers t0 (loaded value), t1 (scratch) and t2 (the sentinel).

        :param unexpected_exception: label to branch to when a check fails
        :return: Assembly code string
        """
        check_excp_expected_spp = self.variable_manager.get_variable("check_excp_expected_spp")
        check_excp_expected_spv = self.variable_manager.get_variable("check_excp_expected_spv")
        check_excp_expected_spvp = self.variable_manager.get_variable("check_excp_expected_spvp")

        h_enabled = self.featmgr.feature.is_feature_enabled("h")
        # 1=M, 2=HS, 3=VS
        handler_mode = self._current_mode_for_handler()
        # A trap into M-mode writes mstatus.MPP/MPV, not SPP -- there is nothing to
        # check there.
        can_check_spp = self.deleg_mode == RV.RiscvPrivileges.SUPER
        # hstatus is inaccessible at V=1, so only the HS handler can read SPV/SPVP.
        can_check_hstatus = h_enabled and handler_mode == 2

        def consume(var, name: str) -> str:
            """Read the expectation into t0 and re-arm the ignore sentinel."""
            return f"""
            {var.load(dest_reg="t0")}
            {var.store(src_reg="t2", temp_reg="t1")}"""

        def field_check(var, name: str, csr: str, bit: int) -> str:
            return f"""{consume(var, name)}
            bltz t0, {self.label_prefix}skip_{name}_check
            csrr t1, {csr}
            srli t1, t1, {bit}
            andi t1, t1, 1
            bne t0, t1, {unexpected_exception}
         {self.label_prefix}skip_{name}_check:
            """

        def reject(var, name: str) -> str:
            return f"""{consume(var, name)}
            bgez t0, {unexpected_exception}   # {name} is not checkable in this handler
            """

        code = f"""
            li t2, {CHECK_EXCP_FIELD_IGNORE}   # trap-entry field check: "do not check" sentinel"""
        if can_check_spp:
            code += field_check(check_excp_expected_spp, "spp", "sstatus", 8)
        else:
            code += reject(check_excp_expected_spp, "spp")

        if can_check_hstatus:
            code += field_check(check_excp_expected_spv, "spv", "hstatus", 7)
            code += field_check(check_excp_expected_spvp, "spvp", "hstatus", 8)
        else:
            code += reject(check_excp_expected_spv, "spv")
            code += reject(check_excp_expected_spvp, "spvp")

        return code

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
        check_excp_expected_spp = self.variable_manager.get_variable("check_excp_expected_spp")
        check_excp_expected_spv = self.variable_manager.get_variable("check_excp_expected_spv")
        check_excp_expected_spvp = self.variable_manager.get_variable("check_excp_expected_spvp")

        # label to jump to if invalid exception is encountered
        if self.featmgr.skip_instruction_for_unexpected:
            unexpected_exception = f"{self.label_prefix}count_ignored_excp"
        else:
            unexpected_exception = self.test_fail_label

        # Derive current_mode from deleg_mode_str (set in __init__)
        # matches CHECK_EXCP_MODE_* equates: m=1, hs=2, s=3 (=VS)
        # The ``_s`` prefix is reused as the supervisor handler for both HS-only
        # (bare_metal) and VS-only (virtualized) tests — see runtime.py. In a
        # bare_metal HS test the handler runs at HS (V=0), so report 2, not 3.
        current_mode = self._current_mode_for_handler()

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

{self._check_trap_entry_priv_state(unexpected_exception)}
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

        # htval/mtval2 only exist when the H extension is implemented; emitting
        # the check on a non-H ISS (e.g., act4 spike without `h`) traps illegal-instr.
        h_enabled = self.featmgr.feature.is_feature_enabled("h")
        if not self.deleg_virtualized and h_enabled:
            # compare expected and actual htval values
            code += f"""
            {check_excp_expected_htval.load_and_clear(dest_reg="t0"):<35}  # check_excp_expected_htval
            beqz t0, {self.label_prefix}skip_nonzero_htval_check

         {self.label_prefix}nonzero_htval_check:
            csrr t1, {self.tval2}
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

    def _check_intr_xip_clear(self, t2_has_cause: bool = True) -> str:
        """Generate xip clear for check_intr, skipping in VS mode where VTI may block sip."""
        is_vs = self.deleg_mode_str == "s" and self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED
        if is_vs:
            return ""
        return f"                li t3, 1\n" f"                sll t3, t3, t2\n" f"                csrc {self.xip}, t3"

    def check_intr(self) -> str:
        """
        Generates code to check for expected interrupts (used by AssertInterrupt).

        When check_intr flag is set, verifies that the interrupt cause matches
        the expected cause, then clears the pending bit and jumps to the return PC
        stored by OS_SETUP_CHECK_INTR.

        Assumes xcause (with interrupt bit stripped) is available -- this is called
        from the interrupt handler path where t0 = xcause from the csrr in default_trap_handler.

        :return: Assembly code string
        """
        check_intr = self.variable_manager.get_variable("check_intr")
        check_intr_expected_cause = self.variable_manager.get_variable("check_intr_expected_cause")
        check_intr_return_pc = self.variable_manager.get_variable("check_intr_return_pc")
        check_intr_expected_mode = self.variable_manager.get_variable("check_intr_expected_mode")

        # Derive current_mode from deleg_mode_str — see _current_mode_for_handler
        # for why bare_metal _s handlers report HS (2), not VS (3).
        current_mode = self._current_mode_for_handler()

        code = f"""
                # -- check_intr: interrupt assertion check --
                {check_intr.load(dest_reg="t2")}
                beqz t2, {self.label_prefix}skip_check_intr

                # Strip interrupt bit from xcause to get cause number
                csrr t2, {self.xcause}
                li t3, (0x1<<(XLEN-1))
                xor t2, t2, t3              # t2 = cause number (interrupt bit cleared)

                # Check expected handler mode (0 = any, skip check)
                {check_intr_expected_mode.load_and_clear(dest_reg="t3")}
                beqz t3, {self.label_prefix}skip_intr_mode_check
                li t4, {current_mode}
                bne t3, t4, {self.test_fail_label}
            {self.label_prefix}skip_intr_mode_check:

                # Check expected cause matches actual cause
                {check_intr_expected_cause.load_and_clear(dest_reg="t3")}
                bne t2, t3, {self.test_fail_label}

                # Clear the pending interrupt bit.
                # Skip in VS mode: sip writes raise Virtual Instruction when VTI=1.
                # The ISR dispatch on the re-fire will clear hvip instead.
{self._check_intr_xip_clear(t2_has_cause=True)}
                # Clear check_intr flag
                li t3, 0
                {check_intr.store(src_reg="t3")}

                # Load return PC and jump there via xepc + xret
                {check_intr_return_pc.load_and_clear(dest_reg="t3")}
                csrw {self.xepc}, t3
                j {self.trap_exit_label}

            {self.label_prefix}skip_check_intr:
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
