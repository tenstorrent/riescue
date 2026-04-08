# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.trap_handler import TrapHandler


class SysCalls(TrapHandler):
    """System call handlers for test execution environment.

    Provides handlers for test control, privilege transitions, and system
    information requests via ECALL interface.

    Syscalls must load t0 with the address to jump to return to. If returning to test code, they should call  self.return_to_test()
    to generate code (Machine + paging mode requires special handling to restore paging mode after syscall)



    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        if self.deleg_mode != RV.RiscvPrivileges.MACHINE:
            raise ValueError("SysCalls only supports MACHINE mode")

        self.xepc = "mepc"
        self.paging_mode = self.featmgr.paging_mode

        self.label_prefix = "syscalls__"
        self.os_save_ecall_fn_epc = self.variable_manager.register_hart_variable("os_save_ecall_fn_epc", -1)

    def generate(self) -> str:
        """
        Generate all system call handlers
        """
        code = ""
        code += self.syscall_table()
        code += f"\n    j {self.check_exception_label}"
        code += self.os_fn_f0001001()  # Switch priv to machine
        code += self.os_fn_f0001002()  # Switch priv to super
        code += self.os_fn_f0001003()  # Switch priv to user
        code += self.os_fn_f0001004()  # Switch priv to original test privilege
        code += self.os_fn_f0001005()  # Machine mode jump table for CSR R/W
        code += self.os_fn_f0001006()  # Supervisor mode jump table for CSR R/W
        code += self.os_fn_f0001007()  # Loop-based PTE read walk (inline in handler)
        code += self.os_fn_f0001008()  # Loop-based PTE write walk (inline in handler)
        code += self.os_get_hart_context()  # Used to get hartid
        if self.featmgr.cfiles is not None:
            code += self.os_fn_70003001()  # Memory allocation API
        code += self.ret_from_os_fn()  # return

        return code

    def syscall_table(self) -> str:
        """
        Check for OS functions
        """
        code = f'\n.section .runtime, "ax"\n{self.syscall_table_label}:'

        # if M-mode paging, disable paging during syscall handling
        # Might need to move this to trap handler
        if self.paging_mode != RV.RiscvPagingModes.DISABLE and self.test_priv == RV.RiscvPrivileges.MACHINE:
            code += """
                li t1, (1<<17)
                csrrc x0, mstatus, t1  # Clear MPRV
                li t1, (1<<12) | (1<<11)
                csrrs x0, mstatus, t1  # Set MPP to 11, machine
            """

        if self.featmgr.selfcheck:
            selfcheck_code = """
                jal selfcheck__save_or_check
            """
        else:
            selfcheck_code = ""

        # FIXME: This is a hacky fix for the scheduler not being able to get hartid in os_end_test if test_priv is in U mode.
        # os_end_test should be able to assume tp is set, scheduler should already have tp set, atm it doesn't
        # OR os_end_test shouldn't rely on hartid / tp
        code += f"""
            # The function number is in x31

        {self.label_prefix}syscall_check_schedule_next_test:
            li t0, 0xf0000001  # schedule next test
            # special case since this isn't going to do an mret/sret. Need to restore context here.
            bne t0, x31, {self.label_prefix}syscall_check_fail_test
            {selfcheck_code}
            {self.restore_trap_handler()}
            j enter_scheduler

        {self.label_prefix}syscall_check_fail_test:
            li t0, 0xf0000002  # fail test
            bne t0, x31, {self.label_prefix}syscall_check_end_test_without_failure
            {self.restore_trap_handler()}
            j test_failed

        {self.label_prefix}syscall_check_end_test_without_failure:
            li t0, 0xf0000003  # end test without failure
            bne t0, x31, {self.label_prefix}syscall_check_switch_machine_mode
            {self.restore_trap_handler()}
            j os_end_test
            """

        # non scheduler system calls
        # these all use trap_exit to return to test code, so they should automatically restore context
        code += f"""

            {self.label_prefix}syscall_check_switch_machine_mode:
            li t0, 0xf0001001    # Switch to machine mode
            beq x31, t0, {self.label_prefix}os_fn_f0001001

            {self.label_prefix}syscall_check_switch_super_mode:
            li t0, 0xf0001002    # Switch to super mode
            beq x31, t0, {self.label_prefix}os_fn_f0001002

            {self.label_prefix}syscall_check_switch_user_mode:
            li t0, 0xf0001003    # Switch to user mode
            beq x31, t0, {self.label_prefix}os_fn_f0001003

            {self.label_prefix}syscall_check_switch_test_mode:
            li t0, 0xf0001004    # Switch to test mode
            beq x31, t0, {self.label_prefix}os_fn_f0001004

            {self.label_prefix}syscall_machine_csr_rw:
            li t0, 0xf0001005    # Machine mode jump table for CSR R/W
            beq x31, t0, {self.label_prefix}os_fn_f0001005

            {self.label_prefix}syscall_supervisor_csr_rw:
            li t0, 0xf0001006    # Supervisor mode jump table for CSR R/W
            beq x31, t0, {self.label_prefix}os_fn_f0001006

            {self.label_prefix}syscall_machine_pte_read:
            li t0, 0xf0001007    # Machine mode jump table for PTE read
            beq x31, t0, {self.label_prefix}os_fn_f0001007

            {self.label_prefix}syscall_machine_pte_write:
            li t0, 0xf0001008    # Machine mode jump table for PTE write
            beq x31, t0, {self.label_prefix}os_fn_f0001008

            {self.label_prefix}syscall_check_get_hart_context:
            li t0, 0xf0002001    # Get hart context
            beq x31, t0, {self.label_prefix}os_get_hart_context
        """
        if self.featmgr.cfiles is not None:
            code += f"""
                li t0, 0x70003001   # memmap
                beq x31, t0, {self.label_prefix}os_fn_70003001

            """

        return code

    def os_fn_f0001001(self) -> str:
        """
        f0001001 : Switch to machine mode
        Does not return to syscall invocation
        """
        code = f"""
            {self.label_prefix}os_fn_f0001001:
                # f0001001 : Switch to machine mode
            """

        # Decide the page used for transfering control back to test
        switch_page = self.featmgr.switch_to_machine_page

        if self.test_priv == RV.RiscvPrivileges.MACHINE:
            code += f"""
                # If already in machine mode, do nothing
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_mpv_update(switch_to_priv="machine")
            code += f"""
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        return code

    def os_fn_f0001002(self) -> str:
        """
        f0001002 : Switch to super mode
        """
        code = f"""
            {self.label_prefix}os_fn_f0001002:
                # f0001002 : Switch to super mode

            """

        switch_page = self.featmgr.switch_to_super_page

        if self.test_priv == RV.RiscvPrivileges.SUPER and self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_BARE_METAL:
            code += f"""
                # If already in machine mode, do nothing
                # When switching to supervisor mode, we will need to switch a new page
                # that has u=0
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_mpv_update(switch_to_priv="super")
            code += f"""
                sfence.vma
                # When switching to supervisor mode, we will need to switch a new page
                # that has u=0
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        return code

    def os_fn_f0001003(self) -> str:
        """
        f0001003 : Switch to user mode
        Does not return to syscall invocation
        """
        code = f"""
            {self.label_prefix}os_fn_f0001003:
                # f0001003 : Switch to user mode
            """

        switch_page = self.featmgr.switch_to_user_page

        if self.test_priv == RV.RiscvPrivileges.USER and self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_BARE_METAL:
            code += f"""
                # If already in machine mode, do nothing
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_mpv_update(switch_to_priv="user")
            code += f"""
                # Load return pc from os_save_ecall_fn_epc and move it to t0
                # which will be used to update epc
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        return code

    def os_fn_f0001004(self) -> str:
        """
        f0001004 : Switch to test mode
        """
        code = f"""
            {self.label_prefix}os_fn_f0001004:
                # f0001004 : Switch to test mode
            """

        priv_map = {
            RV.RiscvPrivileges.USER: "user",
            RV.RiscvPrivileges.SUPER: "super",
            RV.RiscvPrivileges.MACHINE: "machine",
        }
        code += self.mstatus_mpp_mpv_update(switch_to_priv=priv_map[self.test_priv], switch_to_v=self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        code += f"""
            # Here, we want to go back to the test code. The PC is saved in os_save_ecall_epc
            # Load it into t0 and ret_from_os_fn will move t0 to epc
            {self.os_save_ecall_fn_epc.load(dest_reg="t0")}

            {self.return_from_syscall()}
        """

        return code

    def os_fn_f0001005(self) -> str:
        """
        f0001005 : Machine mode jump table for CSR R/W
        Does not return to syscall invocation
        """
        code = f"""
            {self.label_prefix}os_fn_f0001005:
                # f0001005 : Machine mode jump table for CSR R/W
            """

        # Decide the page used for transfering control back to test
        switch_page = self.featmgr.machine_mode_jump_table_for_csr_rw

        if self.test_priv == RV.RiscvPrivileges.MACHINE:
            code += f"""
                # If already in machine mode, do nothing
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_mpv_update(switch_to_priv="machine")
            code += f"""
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        return code

    def os_fn_f0001006(self) -> str:
        """
        f0001006 : Supervisor mode jump table for CSR R/W
        """
        code = f"""
            {self.label_prefix}os_fn_f0001006:
                # f0001006 : Supervisor mode jump table for CSR R/W

            """

        switch_page = self.featmgr.supervisor_mode_jump_table_for_csr_rw

        if self.test_priv == RV.RiscvPrivileges.SUPER and self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_BARE_METAL:
            code += f"""
                # If already in machine mode, do nothing
                # When switching to supervisor mode, we will need to switch a new page
                # that has u=0
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_mpv_update(switch_to_priv="super")
            code += f"""
                sfence.vma
                # When switching to supervisor mode, we will need to switch a new page
                # that has u=0
                li t0, {switch_page}
                j {self.label_prefix}ret_from_os_fn
            """
        return code

    def os_fn_f0001007(self) -> str:
        """
        f0001007 : Read PTE via loop-based page table walk
        """
        code = f"""
            {self.label_prefix}os_fn_f0001007:
                # f0001007 : Read PTE via page table walk
        """
        code += self._generate_pte_walk(is_write=False)
        return code

    def os_fn_f0001008(self) -> str:
        """
        f0001008 : Write PTE via loop-based page table walk
        """
        code = f"""
            {self.label_prefix}os_fn_f0001008:
                # f0001008 : Write PTE via page table walk
        """
        code += self._generate_pte_walk(is_write=True)
        return code

    def _generate_pte_walk(self, is_write: bool) -> str:
        """
        Generate a loop-based page table walk inline in the syscall handler.

        Parameters are passed via shared memory (pte_access_flags):
          [0] = virtual address
          [1] = target level (0 = root, increasing toward leaf)
          [2] = g_level (-1 = no g-stage walk; >= 0 = walk g-stage to this level)

        When g_level >= 0, the walk proceeds in two stages:
          1. Walk v-stage (satp) to the specified level to find the PTE address (a GPA)
          2. Walk g-stage (hgatp) from that GPA to g_level to find the final PTE

        Registers clobbered: t0-t6, x31
        For write, t2 must contain the value to write (set by caller before ecall).
        After the walk, saves epc+4 and jumps to os_fn_f0001004.

        Register contract for the shared walk loop:
          pte_access_flags[0] = address to translate (reloaded each iteration)
          t0  = target level
          t1  = current PT base physical address
          t3  = g_level (-1 = no g-stage walk; consumed after v-stage walk)
          t4  = current VPN shift
          t5  = current level counter
        """
        rw = "write" if is_write else "read"
        pte_access_flags = self.variable_manager.get_variable("pte_access_flags")
        loop_label = f"{self.label_prefix}pte_walk_loop_{rw}"
        done_label = f"{self.label_prefix}pte_walk_done_{rw}"
        gstage_label = f"{self.label_prefix}pte_walk_gstage_{rw}"
        final_label = f"{self.label_prefix}pte_walk_final_{rw}"
        fail_label = f"{self.label_prefix}pte_walk_fail_{rw}"
        final_op = "sd t2, 0(t1)" if is_write else "ld t2, 0(t1)"

        is_virtualized = self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED
        paging_disabled = self.paging_mode == RV.RiscvPagingModes.DISABLE
        g_paging_disabled = self.featmgr.paging_g_mode == RV.RiscvPagingModes.DISABLE

        code = f"""
            # Load parameters from shared memory
            {pte_access_flags.load(dest_reg="x31", index=0)}
            {pte_access_flags.load(dest_reg="t0", index=1)}
            {pte_access_flags.load(dest_reg="t3", index=2)}
        """

        # Validate paging mode / g-stage / virtualization combinations
        if paging_disabled and not is_virtualized:
            # Non-virtualized with paging disabled: fail if target_level != 0
            code += f"""
        {self.label_prefix}pte_walk_check_paging_disabled_nonvirt_target_level_{rw}:
            bne t0, zero, {fail_label}  # paging disabled + not virtualized: target_level must be 0
            """
        if not is_virtualized or g_paging_disabled:
            # g_level must be -1 when not virtualized or g-stage paging is disabled
            code += f"""
        {self.label_prefix}pte_walk_check_glevel_without_gstage_{rw}:
            li t6, -1
            bne t3, t6, {fail_label}   # g_level specified but g-stage walk not available
            """
        if paging_disabled and is_virtualized:
            if g_paging_disabled:
                # Both paging modes disabled: unconditional fail
                code += f"""
        {self.label_prefix}pte_walk_check_both_stages_disabled_{rw}:
            j {fail_label}              # both v-stage and g-stage paging disabled
                """
            else:
                # V-stage disabled but g-stage enabled: skip v-stage, go directly to g-stage
                # Set t1 = address (GPA) from x31 so gstage_label can store it
                code += f"""
            mv t1, x31                  # address is already a GPA when v-stage disabled
            j {gstage_label}            # v-stage paging disabled, skip to g-stage walk
                """

        if not paging_disabled:
            code += """
            # V-stage setup: read satp, extract root PT and compute initial shift
            csrr t1, satp
            srli t4, t1, 60            # t4 = MODE (8=sv39, 9=sv48, 10=sv57)
            slli t1, t1, 20
            srli t1, t1, 20
            slli t1, t1, 12            # t1 = root PT physical address

            # Compute initial VPN shift: 9*(MODE-8) + 30
            addi t4, t4, -8            # MODE offset (0=sv39, 1=sv48, 2=sv57)
            slli t5, t4, 3             # t5 = offset * 8
            add t4, t5, t4             # t4 = offset * 9
            addi t4, t4, 30            # t4 = initial shift (30, 39, or 48)

            li t5, 0                   # t5 = current level counter
            """

        code += f"""
        {loop_label}:
            # Extract VPN index for current level
            # NOTE: t6 IS x31 in RISC-V, so we must reload the address each
            # iteration from memory rather than keeping it in x31.
            {pte_access_flags.load(dest_reg="t6", index=0)}
            srl t6, t6, t4             # shift right by current shift amount
            andi t6, t6, 0x1ff         # mask 9 VPN bits
            slli t6, t6, 3             # * 8 (PTE entry size in bytes)
            add t1, t1, t6             # t1 = address of PTE entry

            beq t5, t0, {done_label}  # reached target level?

            # Intermediate level: load PTE, verify not a leaf, follow pointer
            ld t6, 0(t1)               # load PTE

            srli t6, t6, 10            # extract PPN from PTE
            slli t1, t6, 12            # convert PPN to physical address

            addi t5, t5, 1             # level++
            addi t4, t4, -9            # shift -= 9
            j {loop_label}

        {done_label}:
            # Check if g-stage walk is needed
            li t6, -1
            beq t3, t6, {final_label}  # g_level == -1 -> skip to final op

        {gstage_label}:
            # G-stage setup: use v-stage PTE address as GPA for hgatp walk
            # Store GPA to pte_access_flags[0] so loop reloads it each iteration
            {pte_access_flags.store("t1", index=0)}
            mv t0, t3                  # target level = g_level
            li t3, -1                  # clear g_level so we don't loop again

            csrr t1, hgatp             # read g-stage root PT
            srli t4, t1, 60            # t4 = MODE (8=sv39, 9=sv48, 10=sv57)
            slli t1, t1, 20
            srli t1, t1, 20
            slli t1, t1, 12            # t1 = root PT physical address

            # Compute initial VPN shift for g-stage
            addi t4, t4, -8            # MODE offset (0=sv39, 1=sv48, 2=sv57)
            slli t5, t4, 3             # t5 = offset * 8
            add t4, t5, t4             # t4 = offset * 9
            addi t4, t4, 30            # t4 = initial shift (30, 39, or 48)

            li t5, 0                   # t5 = current level counter
            j {loop_label}             # reuse the same walk loop

        {final_label}:
            {final_op}                 # read PTE into t2 / write t2 to PTE

            # Save ecall return address (epc+4) and return to test mode
            csrr t0, {self.xepc}
            addi t0, t0, 4
            {self.os_save_ecall_fn_epc.store("t0")}
            j {self.label_prefix}os_fn_f0001004

        {fail_label}:
            j test_failed
        """
        return code

    def os_get_hart_context(self) -> str:
        """
        f0002001 : Place hart-local storage pointer in a0 to return
        """
        gpr_save_area = self.variable_manager.get_variable("gpr_save_area")
        code = f"""
        {self.label_prefix}os_get_hart_context:
        """

        # In M-mode trap handler, tp holds PA (from mscratch). When paging is enabled,
        # return the VA from sscratch so the test can access hart context through
        # page-table-mapped virtual addresses. This covers both S/U-mode tests and
        # M-mode paging tests (MPRV=1 + MPP=S where data accesses use S-mode translation).
        if self.paging_mode != RV.RiscvPagingModes.DISABLE:
            hart_context_source = "csrr a0, sscratch"
        else:
            hart_context_source = "mv a0, tp"

        if self.featmgr.save_restore_gprs:
            code += f"""
            # Store hart-local storage pointer to gpr_save_area[10] (a0 slot)
            # so it will be restored to a0 when GPRs are restored
            {hart_context_source}
            {gpr_save_area.store("a0", index=10)}
            """
        else:
            code += f"""
            {hart_context_source}
            """

        code += f"""
            # skip to next pc
            csrr t0, {self.xepc}
            addi t0, t0, 4
            {self.return_from_syscall()}
        """
        return code

    def os_fn_70003001(self) -> str:
        """
        70003001 : memmap
        """
        gpr_save_area = self.variable_manager.get_variable("gpr_save_area")
        code = f"""
        {self.label_prefix}os_fn_70003001:
            ld a0, 80(x30)
            ld a1, 88(x30)
            ld a2, 96(x30)
            jal {self.label_prefix}os_memmap
            sd a0, 80(x30)
        """
        if self.featmgr.save_restore_gprs:
            code += f"""
            # Store return value to gpr_save_area[10] (a0 slot)
            {gpr_save_area.store("a0", index=10)}
            """
        code += f"""
            csrr t0, {self.xepc}
            addi t0, t0, 4
            j {self.label_prefix}ret_from_os_fn
        .weak {self.label_prefix}os_memmap
        {self.label_prefix}os_memmap:
            li a0, -1
            ret
        """

        return code

    def mstatus_mpp_mpv_update(self, switch_to_priv: str, switch_to_v: bool = False) -> str:
        """
        Helper function to update mstatus.mpp based on privilege we want to switch to
        """
        xstatus_csr = "mstatus"
        xstatus_mpp_clear = "0x00001800"  # mstatus[12:11] = 00
        if switch_to_priv == "machine":
            xstatus_mpp_set = "0x00001800"  # mstatus[12:11] = 11
        elif switch_to_priv == "super":
            xstatus_mpp_set = "0x00000800"  # mstatus[12:11] = 01, mstatus.SUM[18]=1
        elif switch_to_priv == "user":
            xstatus_mpp_set = "0x00000000"  # mstatus[12:11] = 00
        else:
            raise ValueError(f"Privilege {switch_to_priv} not yet supported for switching privilege")

        code = f"""
            # Update mstatus csr to switch to {switch_to_priv} mode
            li t0, {xstatus_mpp_clear}
            csrrc x0, {xstatus_csr}, t0
            li t0, {xstatus_mpp_set}
            csrrs x0, {xstatus_csr}, t0
            li t0, 1<<39
            {'csrrs' if switch_to_v else 'csrrc'} x0, {xstatus_csr}, t0
        """
        return code

    def return_from_syscall(self) -> str:
        """
        Returns from system call to test code. Assumes t0 contains the address to jump to.


        If paging is enabled, need to restore paging mode after returning to test code. (mret + set MPRV=1, MPP)
        Otherwise, just return to test code. (mret)
        """
        if self.paging_mode != RV.RiscvPagingModes.DISABLE and self.test_priv == RV.RiscvPrivileges.MACHINE:
            # Restores machine paging mode after a syscall. Used before returning to test code.
            # Jumps to address in t0
            # mret clears MPRV=1, this resets it
            # jumping to `trap_exit_label` restores contex and `ret_from_os_fn_m_paging` restores context.
            # Can't restore twice, so just mret to ret_from_os_fn_m_paging instead
            return f"""
                la t1, {self.label_prefix}ret_from_os_fn_m_paging
                csrw {self.xepc}, t1
                li x31, -1 # Clear x31, so we don't accidentally jump to an OS function next time
                mret
            """
        else:
            return f"j {self.label_prefix}ret_from_os_fn"

    def ret_from_os_fn(self) -> str:
        """
        Generates ret_from_os_fn. Assumes t0 contains the address to jump to.

            - Stores epc in os_save_ecall_fn_epc
            - loads t0 into epc
            - does ``mret``
        """

        ret_from_os_fn = f"""
        {self.label_prefix}ret_from_os_fn:
            csrr t1, {self.xepc} # save current epc to os_save_ecall_fn_epc
            addi t1, t1, 4
            {self.os_save_ecall_fn_epc.store(src_reg="t1")}
            csrw {self.xepc}, t0

            li x31, -1 # Clear x31, so we don't accidentally jump to an OS function next time
            # Return from exception
            j {self.trap_exit_label}
            """

        if self.paging_mode != RV.RiscvPagingModes.DISABLE and self.test_priv == RV.RiscvPrivileges.MACHINE:
            ret_from_os_fn += f"""
            {self.label_prefix}ret_from_os_fn_m_paging:
                li t1, (1<<17) | (1<<11)
            csrrs x0, mstatus, t1 # re-enable paging
            {self.restore_trap_handler()}
            {self.variable_manager.exit_hart_context(scratch=self.scratch_reg)}
            jr t0
            """
        return ret_from_os_fn
