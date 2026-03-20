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
        code += self.os_fn_f0001007()  # Machine mode jump table for PTE read
        code += self.os_fn_f0001008()  # Machine mode jump table for PTE write
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
        f0001007 : Machine mode jump table for PTE read
        Does not return to syscall invocation
        """
        code = f"""
            {self.label_prefix}os_fn_f0001007:
                # f0001007 : Machine mode jump table for PTE read
            """

        # Decide the page used for transferring control back to test
        switch_page = self.featmgr.machine_mode_jump_table_for_pte

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

    def os_fn_f0001008(self) -> str:
        """
        f0001008 : Machine mode jump table for PTE write
        Does not return to syscall invocation
        """
        code = f"""
            {self.label_prefix}os_fn_f0001008:
                # f0001008 : Machine mode jump table for PTE write
            """

        # Decide the page used for transferring control back to test
        switch_page = self.featmgr.machine_mode_jump_table_for_pte

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
