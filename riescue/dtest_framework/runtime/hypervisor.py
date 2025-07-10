# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Hypervisor implementation for RISC-V virtualized environments.

Creates and manages virtual machines in virtualized mode. Handles VM execution,
exception delegation, and trap handling for guest VMs running in VS mode.
"""

import riescue.lib.enums as RV
import riescue.lib.common as common
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class Hypervisor(AssemblyGenerator):
    """Hypervisor implementation for virtualized environments.

    Creates and manages virtual machines in virtualized mode. The machine starts in
    Machine mode, switches to HS mode, and runs hypervisor code. Each VM starts in
    VS mode and executes OS code similar to non-virtualized mode.

    Provides VM creation with discrete test setup including code, data, and paging maps.
    Intercepts VM events per RISC-V ISA specification. Supports nested page tables
    and interrupt virtualization.

    Environment Configuration:
        virtualized: Runs test in VS or VU mode depending on test.priv_mode setting

    Paging Configuration:
        paging_mode_s2: Selects stage-2 (G-stage) page table translation mode
        Supported modes: bare, sv39, sv48, sv57

    :param generate_trap_handler: Generate trap handler code when True
    """

    def __init__(self, generate_trap_handler: bool, **kwargs):

        super().__init__(**kwargs)
        self.generate_trap_handler = generate_trap_handler

    def generate(self) -> str:
        code = f"""
        .section .text

        enter_hypervisor:
            nop
            {self.setup_vmm()}
            {self.launch_a_guest()}
            {self.vmm()}
        """

        return code

    def setup_vmm(self):
        """
        Setup hypervisor CSRs
        """
        hedelg_val = self.featmgr.cmdline.hedeleg
        hidelg_val = self.featmgr.cmdline.hideleg
        henvcfg_val = self.featmgr.cmdline.henvcfg

        # Handle pbmt randomization
        if self.featmgr.pbmt_ncio:
            henvcfg_val |= 1 << 62

        # Handle svadu randomization
        if self.featmgr.svadu:
            henvcfg_val |= 1 << 61

        code = f"""
            setup_vmm:
                nop
            setup_hedeleg:
                # Make sure that we are handling VS/VU ECALL in VS mode OS
                # i.e. setup hedeleg[8] = 1
                # csrr t0, hedeleg
                # li t1, 0x100
                # or t0, t0, t1
                li t0, {hedelg_val}
                csrw hedeleg, t0

            setup_hideleg:
                # Setup hideleg as well
                li t0, {hidelg_val}
                csrw hideleg, t0

            setup_henvcfg:
                # Setup henvcfg
                li t0, {henvcfg_val}
                csrw henvcfg, t0
        """

        # if smstateen extension is enabled, then we need to setup hstateen0 to allow senvcfg writes
        if self.featmgr.setup_stateen:
            code += """
                hypervisor_setup_hstateen:
                li t0, 1<<63 | 1<<62
                csrw hstateen0, t0
            """

        # Setup tvec to point to the trap handler for hypervisor
        tvec_reg = "stvec"

        code += """
           la t0, excp_entry
           csrw stvec, t0
        """

        # Setup hgatp if g-stage translation is enabled
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            os_map = self.pool.get_page_map("map_hyp")
            os_sptbr = os_map.sptbr

            vmid_val = self.rng.random_in_range(0, 2**14)
            os_paging_mode = self.featmgr.paging_g_mode
            os_mode_val = 0
            if os_paging_mode == RV.RiscvPagingModes.SV39:
                os_mode_val = 0x8
            elif os_paging_mode == RV.RiscvPagingModes.SV48:
                os_mode_val = 0x9
            elif os_paging_mode == RV.RiscvPagingModes.SV57:
                os_mode_val = 0xA
            else:
                raise ValueError(f"OS does not support paging mode {os_paging_mode} yet")

            # Set sptbr, vmid and mode field values in the hgatp csr
            hgatp_val = os_sptbr >> 12
            hgatp_val |= common.set_bits(original_value=hgatp_val, bit_hi=63, bit_lo=60, value=os_mode_val)
            hgatp_val |= common.set_bits(original_value=hgatp_val, bit_hi=57, bit_lo=54, value=vmid_val)

            code += f"""
                    # Setup hgatp, it has following structure
                    # [63:60] - MODE, [59:58] - 0, [57:44] - VMID, [43:0] - sptbr
                    li t0, {hgatp_val}
                    csrw hgatp, t0
            """

        # Also write vssatp if g-stage translation is enabled
        if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
            os_map = self.pool.get_page_map("map_os")
            os_sptbr = os_map.sptbr

            vmid_val = self.rng.random_in_range(0, 2**14)
            os_paging_mode = self.featmgr.paging_mode
            os_mode_val = 0
            if os_paging_mode == RV.RiscvPagingModes.SV39:
                os_mode_val = 0x8
            elif os_paging_mode == RV.RiscvPagingModes.SV48:
                os_mode_val = 0x9
            elif os_paging_mode == RV.RiscvPagingModes.SV57:
                os_mode_val = 0xA
            else:
                raise ValueError(f"OS does not support paging mode {os_paging_mode} yet")

            # Set sptbr, vmid and mode field values in the vssatp csr
            vssatp_val = os_sptbr >> 12
            vssatp_val |= common.set_bits(original_value=vssatp_val, bit_hi=63, bit_lo=60, value=os_mode_val)
            vssatp_val |= common.set_bits(original_value=vssatp_val, bit_hi=57, bit_lo=54, value=vmid_val)

            code += f"""
                    # Setup vssatp, it has following structure
                    # [63:60] - MODE, [59:58] - 0, [57:44] - VMID, [43:0] - sptbr
                    li t0, {vssatp_val}
                    csrw vsatp, t0
            """

        code += """
                # # Make sure we point to the trap handler for VS/VU ECALL
                # # i.e. setup vstvec = excp_entry
                # la t0, excp_entry
                # csrw vstvec, t0

                # Set vsstatus.sum=1, fs and vs
                csrr t0, vsstatus
                li t1, 0x42200 #sum fs vs
                or t0, t0, t1
                csrw vsstatus, t0

        """

        return code

    def launch_a_guest(self):
        """
        Launch a guest VM
        """
        code = """
            # Launching a guest VM
            launch_guest:
                nop

        """
        if self.featmgr.cmdline.vmm_hooks:
            code += """
                run_user_code_pre_launch:
                    li t0, vmm_handler_pre_addr
                    ld t0, 0(t0)
                    jalr ra, t0
            """

        code += self.switch_test_privilege(
            RV.RiscvPrivileges.SUPER,
            RV.RiscvPrivileges.SUPER,
            "post_switch_to_super",
            switch_to_vs=True,
        )

        return code

    def vmm(self):
        """
        VMM handler, responsible for maintaining the guests and performing actual hypervisor tasks
        """
        code = f"""
            enter_vmm:
                # Save the guest context
                {self.save_guest_context()}

                # Check if we have any pending interrupts from the guest
                {self.check_guest_interrupts()}

                # Check if we have any pending interrupts from the host
                {self.check_host_interrupts()}

                # Check if we have any pending exceptions from the guest
                {self.check_guest_exceptions()}

                # Check if we have any pending exceptions from the host
                {self.check_host_exceptions()}

                # Restore the guest context
                {self.restore_guest_context()}

                # Return to guest
                {self.return_to_guest()}
        """

        return code

    def save_guest_context(self):
        """
        Save guest context
        """
        code = f"""
            save_guest_context:
                # Save guest context
                {self.save_guest_regs()}
                {self.save_guest_csr()}
        """
        return code

    def save_guest_regs(self):
        """
        Save guest registers
        """
        code = f"""
            save_guest_regs:
                # Save guest registers
                {self.save_guest_gprs()}
                {self.save_guest_fprs()}
        """
        return code

    def save_guest_gprs(self):
        """
        Save guest GPRs
        """
        code = f"""
            save_guest_gprs:
                # Save guest GPRs
                {self.save_guest_gprs_common()}
        """
        return code

    def save_guest_fprs(self):
        """
        Save guest FPRs
        """
        code = f"""
            save_guest_fprs:
                # Save guest FPRs
                {self.save_guest_fprs_common()}
        """
        return code

    def save_guest_gprs_common(self):
        """
        Save guest GPRs
        """
        code = """
            save_guest_gprs_common:
                # Save guest GPRs
                nop
        """
        return code

    def save_guest_fprs_common(self):
        """
        Save guest FPRs
        """
        code = """
            save_guest_fprs_common:
                # Save guest FPRs
                nop
        """
        return code

    def save_guest_csr(self):
        """
        Save guest CSRs
        """
        code = """
            save_guest_csr:
                # Save guest CSRs
                nop
        """
        return code

    def check_guest_interrupts(self):
        """
        Check guest interrupts
        """
        code = """
            check_guest_interrupts:
                # Check guest interrupts
                nop
        """
        return code

    def check_host_interrupts(self):
        """
        Check host interrupts
        """
        code = """
            check_host_interrupts:
                # Check host interrupts
                nop
        """
        return code

    def check_guest_exceptions(self):
        """
        Check guest exceptions
        """
        self.xcause = "scause"
        self.xepc = "sepc"
        self.xtval = "stval"
        self.xret = "sret"
        if self.featmgr.deleg_excp_to == "machine":
            self.xcause = "mcause"
            self.xepc = "mepc"
            self.xtval = "mtval"
            self.xret = "mret"

        code = ""
        if self.featmgr.cmdline.vmm_hooks:
            code += """
                run_user_code_post:
                    li t0, vmm_handler_post_addr
                    ld t0, 0(t0)
                    jalr ra, t0
            """

        code += f"""
            check_guest_exceptions:
                # Check guest exceptions by reading vscause
                # csrr t0, vscause
                # csrr t0, scause
                # bnez t0, handle_guest_exception
                # Save the exception cause / code
                csrr t1, {self.xcause}
                li t3, check_excp_actual_cause
                sd t1, 0(t3)

                # Save exception PC
                csrr t0, {self.xepc}
                li t3, check_excp_actual_pc
                sd t0, 0(t3)
                """

        if self.generate_trap_handler:
            code += self.os_check_excp(return_label="return_from_excp_check", xepc=self.xepc, xret=self.xret)

        code += """
            return_from_excp_check:
                nop
        """
        return code

    def check_host_exceptions(self):
        """
        Check host exceptions
        """
        code = """
            check_host_exceptions:
                # Check host exceptions
                nop
        """
        return code

    def restore_guest_context(self):
        """
        Restore guest context
        """
        code = f"""
            restore_guest_context:
                # Restore guest context
                {self.restore_guest_regs()}
                {self.restore_guest_csr()}
        """
        return code

    def restore_guest_regs(self):
        """
        Restore guest registers
        """
        code = f"""
            restore_guest_regs:
                # Restore guest registers
                {self.restore_guest_gprs()}
                {self.restore_guest_fprs()}
        """
        return code

    def restore_guest_gprs(self):
        """
        Restore guest GPRs
        """
        code = """
            restore_guest_gprs:
                # Restore guest GPRs
                nop
        """
        return code

    def restore_guest_fprs(self):
        """
        Restore guest FPRs
        """
        code = """
            restore_guest_fprs:
                # Restore guest FPRs
                nop
        """
        return code

    def restore_guest_csr(self):
        """
        Restore guest CSRs
        """
        code = """
            restore_guest_csr:
                # Restore guest CSRs
                nop
        """
        return code

    def return_to_guest(self):
        """
        Return to guest
        """
        code = f"""
            return_to_guest:
                # Return to guest
                li t3, check_excp_return_pc
                ld t0, 0(t3)
                sd x0, 0(t3)
                csrw {self.xepc}, t0
        """

        if self.featmgr.cmdline.vmm_hooks:
            code += """
                run_user_code_pre:
                li t0, vmm_handler_pre_addr
                ld t0, 0(t0)
                jalr ra, t0
            """

        code += f"""
                # Return from exception
                {self.xret}
        """
        return code
