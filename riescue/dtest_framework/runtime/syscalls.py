# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class SysCalls(AssemblyGenerator):
    """System call handlers for test execution environment.

    Provides handlers for test control, privilege transitions, and system
    information requests via ECALL interface.

    Syscalls must load t0 with the address to jump to return to. If returning to test code, they should call  self.return_to_test()
    to generate code (Machine + paging mode requires special handling to restore paging mode after syscall)



    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.syscall_table_label = self.featmgr.syscall_table_label
        self.check_exception_label = self.featmgr.check_exception_label
        # Pick correct CSRs based on delegation
        if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.MACHINE:
            self.xepc = "mepc"
            self.xret = "mret"
        else:
            self.xepc = "sepc"
            self.xret = "sret"

        self.priv_mode = self.featmgr.priv_mode
        self.paging_mode = self.featmgr.paging_mode

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
        code += self.os_get_hartdid()  # Used to get hartid
        code += self.ret_from_os_fn()  # return

        return code

    def syscall_table(self) -> str:
        """
        Check for OS functions
        """
        code = f"\n{self.syscall_table_label}:"

        # if M-mode paging, disable paging during syscall handling
        # Might need to move this to trap handler
        if self.paging_mode != RV.RiscvPagingModes.DISABLE and self.priv_mode == RV.RiscvPrivileges.MACHINE:
            code += """
                li t1, (1<<17)
                csrrc x0, mstatus, t1  # Clear MPRV
                li t1, (1<<12) | (1<<11)
                csrrs x0, mstatus, t1  # Set MPP to 11, machine
            """

        code += """
            # The function number is in x31

            li t0, 0xf0000001  # schedule next test
            beq t0, x31, enter_scheduler

            li t0, 0xf0000002  # fail test
            beq t0, x31, test_failed

            li t0, 0xf0000003  # end test without failure
            beq t0, x31, os_end_test

            li t0, 0xf0001001    # Switch to machine mode
            beq x31, t0, os_fn_f0001001

            li t0, 0xf0001002    # Switch to super mode
            beq x31, t0, os_fn_f0001002

            li t0, 0xf0001003    # Switch to user mode
            beq x31, t0, os_fn_f0001003

            li t0, 0xf0001004    # Switch to test mode
            beq x31, t0, os_fn_f0001004

            li t0, 0xf0002001    # Get hartid
            beq x31, t0, os_get_hartdid

        """
        return code

    def os_fn_f0001001(self) -> str:
        """
        f0001001 : Switch to machine mode
        Does not return to syscall invocation
        """
        code = """
            os_fn_f0001001:
                # f0001001 : Switch to machine mode
            """

        # Decide the page used for transfering control back to test
        switch_page = self.featmgr.switch_to_machine_page

        if self.priv_mode == RV.RiscvPrivileges.MACHINE:
            code += f"""
                # If already in machine mode, do nothing
                li t0, {switch_page}
                j ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_update(switch_to_priv="machine")
            code += f"""
                li t0, {switch_page}
                j ret_from_os_fn
            """
        return code

    def os_fn_f0001002(self) -> str:
        """
        f0001002 : Switch to super mode
        """
        code = """
            os_fn_f0001002:
                # f0001002 : Switch to super mode

            """

        switch_page = self.featmgr.switch_to_super_page

        if self.priv_mode == RV.RiscvPrivileges.SUPER:
            code += f"""
                # If already in machine mode, do nothing
                # When switching to supervisor mode, we will need to switch a new page
                # that has u=0
                li t0, {switch_page}
                j ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_update(switch_to_priv="super")
            code += f"""
                sfence.vma
                # When switching to supervisor mode, we will need to switch a new page
                # that has u=0
                li t0, {switch_page}
                j ret_from_os_fn
            """
        return code

    def os_fn_f0001003(self) -> str:
        """
        f0001003 : Switch to user mode
        Does not return to syscall invocation
        """
        code = """
            os_fn_f0001003:
                # f0001003 : Switch to user mode
            """

        switch_page = self.featmgr.switch_to_user_page

        if self.priv_mode == RV.RiscvPrivileges.USER:
            code += f"""
                # If already in machine mode, do nothing
                li t0, {switch_page}
                j ret_from_os_fn
            """
        else:
            code += self.mstatus_mpp_update(switch_to_priv="user")
            code += f"""
                # Load return pc from os_save_ecall_fn_epc and move it to t0
                # which will be used to update epc
                li t0, {switch_page}
                j ret_from_os_fn
            """
        return code

    def os_fn_f0001004(self) -> str:
        """
        f0001004 : Switch to test mode
        """
        code = """
            os_fn_f0001004:
                # f0001004 : Switch to test mode
            """

        if self.priv_mode == RV.RiscvPrivileges.USER:
            code += self.mstatus_mpp_update(switch_to_priv="user")
        elif self.priv_mode == RV.RiscvPrivileges.SUPER:
            code += self.mstatus_mpp_update(switch_to_priv="super")
        elif self.priv_mode == RV.RiscvPrivileges.MACHINE:
            code += self.mstatus_mpp_update(switch_to_priv="machine")

        code += f"""
            # get hartid
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}

            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset

            # Here, we want to go back to the test code. The PC is saved in os_save_ecall_epc
            # Load it into t0 and ret_from_os_fn will move t0 to epc
            li t3, os_save_ecall_fn_epc
            add t3, t3, {self.hartid_reg} # Add offset for this harts os_save_ecall_epc element
            ld t0, 0(t3)

            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset

            {self.return_from_syscall()}
        """

        return code

    def os_get_hartdid(self) -> str:
        """
        f0002001 : Get hartid, or otherwise used to skip instruction
        """
        code = f"""
        os_get_hartdid:
            # get hartid already done in ret_from_os_fn

            # skip to next pc
            csrr t0, {self.xepc}
            addi t0, t0, 4

            {self.return_from_syscall()}
        """
        return code

    def mstatus_mpp_update(self, switch_to_priv: str) -> str:
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
        """
        return code

    def return_from_syscall(self) -> str:
        """
        Returns from system call to test code. Assumes t0 contains the address to jump to.


        If paging is enabled, need to restore paging mode after returning to test code. (mret + set MPRV=1, MPP)
        Otherwise, just return to test code. (mret)
        """
        if self.paging_mode != RV.RiscvPagingModes.DISABLE and self.priv_mode == RV.RiscvPrivileges.MACHINE:
            # Restores machine paging mode after a syscall. Used before returning to test code.
            # Jumps to address in t0
            # mret clears MPRV=1, this resets it
            return f"""
                la t1, ret_from_os_fn_m_paging
                csrw {self.xepc}, t1
                li x31, -1 # Clear x31, so we don't accidentally jump to an OS function next time
                {self.xret}

            """
        else:
            return "j ret_from_os_fn"

    def ret_from_os_fn(self) -> str:
        """
        Generates ret_from_os_fn. Assumes t0 contains the address to jump to.

            - Stores epc in os_save_ecall_fn_epc
            - loads t0 into epc
            - does ``mret``
        """

        ret_from_os_fn = f"""
        ret_from_os_fn:
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)} # get hartid
            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset

            csrr t1, {self.xepc} # save current epc to os_save_ecall_fn_epc
            addi t1, t1, 4
            li t3, os_save_ecall_fn_epc
            add t3, t3, {self.hartid_reg} # Add offset for this harts os_save_ecall_fn_epc element
            sd t1, 0(t3)
            csrw {self.xepc}, t0

            li x31, -1 # Clear x31, so we don't accidentally jump to an OS function next time
            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset
            # Return from exception
            {self.xret}
            """

        if self.paging_mode != RV.RiscvPagingModes.DISABLE and self.priv_mode == RV.RiscvPrivileges.MACHINE:
            ret_from_os_fn += """
            ret_from_os_fn_m_paging:
                li t1, (1<<17) | (1<<11)
            csrrs x0, mstatus, t1 # re-enable paging
            jr t0
            """
        return ret_from_os_fn
