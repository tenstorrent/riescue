# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from collections import deque
from pathlib import Path
import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.lib.counters import Counters
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator

log = logging.getLogger(__name__)


class Loader(AssemblyGenerator):
    """Assembly code generator for RISC-V system initialization and test environment setup.

    The Loader class is responsible for generating assembly code that establishes a known
    system state after reset and prepares the runtime environment for test execution.

    This class handles the complete system initialization sequence including:

    CPU State Initialization
    ------------------------
    - Integer and floating-point register initialization
    - Vector register initialization
    - Stack pointer allocation
    - Endianness configuration
    - Hart ID caching in scratch CSRs

    CSR Configuration
    -----------------
    - ISA and extension configuration
    - Status register configuration
    - Exception and interrupt delegation
    - Trap vector base address setup
    - Counter configuration

    Memory Management
    -----------------
    - Page table setup and configuration
    - SATP register configuration (page table root, ASID, paging mode)
    - PMP (Physical Memory Protection) region setup
    - PMA (Physical Memory Attributes) configuration

    Privilege Mode Management
    -------------------------
    - Machine to supervisor mode transitions
    - Hypervisor mode setup for virtualized environments
    - User mode preparation

    Interrupt and Exception Handling
    ---------------------------------
    - Interrupt delegation configuration
    - Exception handler setup
    - Trap vector initialization

    :param kwargs: Keyword arguments passed to parent AssemblyGenerator
    :type kwargs: dict

    .. note::
       The generated assembly code varies based on the feature manager configuration,
       including WYSIWYG mode, Linux mode, paging mode, and privilege level settings.

    .. warning::
       This class generates low-level assembly code that directly manipulates system
       state and CSRs. Incorrect configuration may result in system instability.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.priv_mode = self.featmgr.priv_mode
        self.counters = Counters(rng=self.rng)

    def generate(self) -> str:
        # If wysiwyg mode, then we don't need any loader code
        allocate_stack = not self.featmgr.wysiwyg
        big_endian = self.featmgr.big_endian
        if self.featmgr.wysiwyg and not self.featmgr.bringup_pagetables:
            return self.base_loader(allocate_stack=allocate_stack, big_endian=big_endian)

        if self.featmgr.linux_mode:
            return """
            .section .text
            .global _start
            _start:
            call main
            li a7, 93 # SYS_exit
            li a0, 0  # exit code

            main:

                call schedule_tests

                ret

            """

        code = self.base_loader(allocate_stack=allocate_stack, big_endian=big_endian)
        if self.featmgr.counter_event_path is not None:
            code += self.enable_counters(event_path=self.featmgr.counter_event_path)

        # High level requirements for each privilege and paging modes
        # * machine
        #   * paging disabled
        #   * do not switch to super
        #   * identity mapping for all the page_mapping
        #   * (temporary) set mcounteren to 0x2 -> enable time control

        # * super
        #   * switch to super and stay in super all along
        #   * paging enabled
        #     * identity map code|data
        #   * paging disabled
        #     * identity mapping for all the page_mapping

        # * user
        #   * switch to super initially
        #   * paging enabled
        #     * identity map code|data
        #     * enable paging
        #     * switch to user in schedule_tests
        #     * 'j passed|failed' => switch to super
        #   * paging disabled
        #     * identity mapping for all the page_mapping
        #     * switch to user in schedule_tests
        #     * 'j passed|failed' => switch to super

        code += """

        init_tests:
            # Initialize test configuration like privilege
            # We should be in Machine mode at this point
            # Set MISA bits based on enabled features
            csrr t0, misa  # Read current MISA value
            """

        # Add MISA bits based on enabled features
        misa_bits = self.featmgr.get_misa_bits()
        code += f"""
            # OR in new MISA bits with existing value
            li t1, 0x{misa_bits:x}
            or t0, t0, t1
            csrw misa, t0
            csrr t1, misa  # Read back to verify
        """

        code += """
        cache_mhartid:
        """
        # No guarantee that s1 remains untouched, but it is assumed that the dest_csr will be left alone.
        code += Routines.place_store_hartid(dest_csr="sscratch", work_reg="t0", priv_mode="M")
        # if we are going to be in virtualized mode, then also update vsscratch csr
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            code += Routines.place_store_hartid(dest_csr="vsscratch", work_reg="t0", priv_mode="M")

        # Set mstatus.SUM, so we can access user pages from supervisor
        mstatus_sum_set = "0x00040000"  # mstatus[SUM] = 1
        code += f"""
        set_mstatus_sum:
            # Set mstatus.SUM=1, so we can access user pages from supervisor
            li t0, {mstatus_sum_set}
            csrrs t0, mstatus, t0

        """

        # Set mstatus.FS and VS to 11, so we can access user pages from supervisor
        mstatus_fsvs_set = "0x2200"
        # RVTOOLS-4204 for mcounteren force enable
        code += f"""
        set_mstatus_fsvs:
            li t0, {mstatus_fsvs_set}
            csrrs x0, mstatus, t0

            {"" if self.featmgr.disable_wfi_wait else "li t0, 0x2"}
            {"" if self.featmgr.disable_wfi_wait else "csrw mcounteren, t0"}

            # Initialize FP registers
            li t0, check_excp
            fld f0 , 0(t0)
            fld f1 , 0(t0)
            fld f2 , 0(t0)
            fld f3 , 0(t0)
            fld f4 , 0(t0)
            fld f5 , 0(t0)
            fld f6 , 0(t0)
            fld f7 , 0(t0)
            fld f8 , 0(t0)
            fld f9 , 0(t0)
            fld f10, 0(t0)
            fld f11, 0(t0)
            fld f12, 0(t0)
            fld f13, 0(t0)
            fld f14, 0(t0)
            fld f15, 0(t0)
            fld f16, 0(t0)
            fld f17, 0(t0)
            fld f18, 0(t0)
            fld f19, 0(t0)
            fld f20, 0(t0)
            fld f21, 0(t0)
            fld f22, 0(t0)
            fld f23, 0(t0)
            fld f24, 0(t0)
            fld f25, 0(t0)
            fld f26, 0(t0)
            fld f27, 0(t0)
            fld f28, 0(t0)
            fld f29, 0(t0)
            fld f30, 0(t0)
            fld f31, 0(t0)

            #Initialize Vector Registers
            li x4, 0x0
            li x5, 0x4
            li x6, 0xd8
            li t0, check_excp
            vsetvl x4,x5,x6
            vmv.v.x v0,  x0
            vmv.v.x v1,  x0
            vmv.v.x v2,  x0
            vmv.v.x v3,  x0
            vmv.v.x v4,  x0
            vmv.v.x v5,  x0
            vmv.v.x v6,  x0
            vmv.v.x v7,  x0
            vmv.v.x v8,  x0
            vmv.v.x v9,  x0
            vmv.v.x v10, x0
            vmv.v.x v11, x0
            vmv.v.x v12, x0
            vmv.v.x v13, x0
            vmv.v.x v14, x0
            vmv.v.x v15, x0
            vmv.v.x v16, x0
            vmv.v.x v17, x0
            vmv.v.x v18, x0
            vmv.v.x v19, x0
            vmv.v.x v20, x0
            vmv.v.x v21, x0
            vmv.v.x v22, x0
            vmv.v.x v23, x0
            vmv.v.x v24, x0
            vmv.v.x v25, x0
            vmv.v.x v26, x0
            vmv.v.x v27, x0
            vmv.v.x v28, x0
            vmv.v.x v29, x0
            vmv.v.x v30, x0
            vmv.v.x v31, x0

        """
        # Setup pmacfg* if requested
        if self.featmgr.cmdline.needs_pma:
            code += self.setup_pma()
        # Setup pmpaddr*/cfg* if requested
        if self.featmgr.cmdline.setup_pmp:
            code += self.setup_pmp()
        # if self.featmgr.cmdline.enable_secure_mode:
        if self.featmgr.secure_mode:
            code += """
              # .equ BIT_SWID, 0
              .equ matp_csr, 0x7c7
              # Set MATP.SWID=1, so we can access secure pages from supervisor
              ori x1, x1, 0x1
              # csrs	matp_csr, x1  #set swid bit in matp
              csrs	0x7c7, x1  #set swid bit in matp
            """
        # Switch to supervisor if the test privilege is SUPER|USER
        # We don't need to switch privilege if MACHINE
        if self.priv_mode != RV.RiscvPrivileges.MACHINE:
            # Setup medeleg, so we can handle all the exceptions at supervisor level since our
            # OS is in supervisor page

            medeleg_val = 0
            if self.featmgr.deleg_excp_to == "super":
                medeleg_val = 0xFFFFFFFFFFFFFFFF

            if self.featmgr.cmdline.medeleg != 0xFFFFFFFFFFFFFFFF:
                medeleg_val = self.featmgr.cmdline.medeleg

            # if self.featmgr.deleg_excp_to == 'super':
            code += f"""
                setup_medeleg:
                    # _if we are in supervisor or user mode, we will handle all the exceptions in
                    # supervisor mode
                    li t0, 0x{medeleg_val:x}
                    csrw medeleg, t0

                    """

            # Also setup intial value of mideleg
            mideleg_val = 0

            mode_enabled_interrupts = ""
            if self.featmgr.interrupts_enabled:
                mode_enabled_interrupts = "ENABLE_MIE"

            if self.featmgr.deleg_excp_to == "super":
                mideleg_val = (1 << 9) | (1 << 5) | (1 << 1) | (1 << 11) | (1 << 7) | (1 << 3)  # Enables SEI, STI, SSI and MEI, MTI, MSI
                if self.featmgr.interrupts_enabled:
                    mode_enabled_interrupts = "ENABLE_SIE # Delegating to supervisor mode, enabling interrupts to supervisor mode by defualt"

            if self.featmgr.cmdline.mideleg != 0xFFFFFFFFFFFFFFFF:
                hideleg_val = self.featmgr.cmdline.mideleg

            code += f"""
                setup_mideleg:
                    {mode_enabled_interrupts}
                    li t0, 0x{mideleg_val:x}
                    csrw mideleg, t0
                    """

            # Handle menvcfg
            menvcfg_val = 1 << 63
            if self.featmgr.disable_wfi_wait:
                menvcfg_val = 0  # FIXME: temporarily enable stce at all times to deal with stimecmp. RVTOOLS-4204
            # Enable svpbmt when randomization kicks in for pbmp NCIO
            if self.featmgr.pbmt_ncio:
                menvcfg_val |= 1 << 62
            # Enable svadu when randomization kicks in for ad-bit randomization
            if self.featmgr.svadu:
                menvcfg_val |= 1 << 61
            if menvcfg_val != 0:
                code += f"""
                    loader_setup_menvcfg:
                        li t0, 0x{menvcfg_val:x}
                        csrs menvcfg, t0
                        """

            if self.featmgr.cmdline.menvcfg != 0:
                menvcfg_val |= self.featmgr.cmdline.menvcfg

                code += f"""
                    setup_menvcfg:
                        li t0, 0x{menvcfg_val:x}
                        csrw menvcfg, t0
                    """

            # Also setup MTVEC with excp_entry
            code += """
                la t0, excp_entry
                csrw mtvec, t0
                """

            # if smstateen is requested, then we need to setup mstateen0 to allow hstateen writes from hypervisor
            if self.featmgr.setup_stateen:
                code += """
                    loader_setup_mstateen:
                        li t0, 1<<63 | 1<<62
                        csrw mstateen0, t0
                    """
            # Initialize CSRs based on command line arguments
            code += f"""
                # Initialize CSRs based on command line arguments
                init_cmdline_csrs:
                    {self.generate_csr_init_code()}
            """

            # Clear satp before switching to supervisor mode
            code += "csrw satp, x0\n"

            # If we are in virtualized mode then we need to switch to hypervisor and execute
            # everything below in VS-mode, since this is almost the OS code
            if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
                code += "csrw vsatp, x0\n"  # Clear satp before switching to VS mode
                # Switch to HS mode
                code += self.switch_test_privilege(
                    from_priv=RV.RiscvPrivileges.MACHINE,
                    to_priv=RV.RiscvPrivileges.SUPER,
                    jump_label="enter_hypervisor",
                    switch_to_vs=True,
                )

            else:
                code += self.switch_test_privilege(
                    from_priv=RV.RiscvPrivileges.MACHINE,
                    to_priv=RV.RiscvPrivileges.SUPER,
                    jump_label="post_switch_to_super",
                )

        elif self.featmgr.interrupts_enabled:
            code += "ENABLE_MIE\n"  # still want to enable interrupts in loader if in machine mode

        # FIXME: these nops get pasted into the assembly file with inconsistent indentation.
        code += "nop\n"
        code += "nop\n"
        code += "nop\n"
        code += "nop\n"
        code += "post_switch_to_super:\n"

        # _If we are in user mode and paging is enabled, we need to use ECALL to get in and out
        # of the USER <-> SUPER modes between test and OS code
        if not self.featmgr.bringup_pagetables:
            if self.priv_mode != RV.RiscvPrivileges.MACHINE:
                xtvec = "stvec"
            else:
                xtvec = "mtvec"

            interrupt_entry_code = ["# Base Address to jump to during trap", f"setup_{xtvec}:"]
            if self.featmgr.user_interrupt_table:
                log.warning("Using user_interrupt_table is deprecated. Use ;#vectored_interrupt(index, label) in source code instead.")
                interrupt_entry_code.extend(["li t0, user_interrupt_table_addr", "ld t0, 0(t0)"])
            else:
                interrupt_entry_code.append(f"la t0, {self.featmgr.trap_handler_label}")
            interrupt_entry_code.append(f"csrw {xtvec}, t0")
            code += "\n".join(interrupt_entry_code)

        # Handle menvcfg
        senvcfg_val = 0
        if self.featmgr.cmdline.senvcfg != 0:
            senvcfg_val = self.featmgr.cmdline.senvcfg

            code += f"""
                setup_senvcfg:
                    li t0, {senvcfg_val}
                    csrw senvcfg, t0

                    """

        # If paging is enabled, switch to supervisor and enable paging
        if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
            code += self.enable_paging()

        # Jump to schedule the tests in opsys
        schedule_label = "schedule_tests"

        # Since --bringup_pagetables imply wysiwyg mode, we don't need to schedule the tests
        # Also, jump to the first test after setting up configuration
        if self.featmgr.bringup_pagetables:
            schedule_label = list(self.pool.discrete_tests.keys())[0]
        code += f"""
        init_mepc_label:
            j {schedule_label}

        """

        return code

    def setup_pmp(self) -> str:
        "Generate code to setup PMP registers"
        # Implement a queue to keep track of pmp numbers from 0-63
        pmpaddr_start_addr = 0x3B0
        pmpcfg_start_addr = 0x3A0
        pmp_q = deque(range(64))
        code = ""

        # Current pmpcfg value being built
        current_pmpcfg = 0
        # Track which pmpcfg* CSR we're writing to
        cfg_index = 0
        # Value for each pmp*cfg: NAPOT (A=11), R/W/X=111, L=0
        pmpcfg_val = 0x1F

        # Setup pmpcfg* if requested
        for i, pmp_region in enumerate(self.pool.pmp_regions):
            pmp_size = pmp_region.size
            pmp_start = pmp_region.start_addr

            # # Calculate the PMP address
            # pmp_addr = (pmp_region.start_addr >> 2) | (napot_mask >> 3)
            pmpaddr_val = (pmp_start >> 2) | ((pmp_size >> 3) - 1)

            # TODO: For now no configuration for pmpcfg, just set it to 0x1f
            pmpcfg_val = 0x1F

            # Find the pmp_number
            pmp_num = pmp_q.popleft()

            # Add this region's pmp*cfg to current_pmpcfg
            cfg_offset = (i % 8) * 8  # 0, 8, 16, ..., 56
            current_pmpcfg |= pmpcfg_val << cfg_offset

            # Setup pmpaddr and pmpcfg
            code += f"""
            # Setup pmpaddr{pmp_num} with start_addr: 0x{pmp_region.start_addr:x} and size: 0x{pmp_region.size:x}
            li t0, 0x{pmpaddr_val:x}
            csrw 0x{pmpaddr_start_addr + pmp_num:x}, t0
            """

            # Write pmpcfg* CSR if we've filled 8 entries or reached the end
            if (i % 8 == 7) or (i == len(self.pool.pmp_regions) - 1):
                code += f"""
                # Setup pmpcfg{cfg_index} for pmpaddr{cfg_index*8} to pmpaddr{min(cfg_index*8 + 7, i)}
                li t0, 0x{current_pmpcfg:x}
                csrw 0x{pmpcfg_start_addr + cfg_index:x}, t0
                """
                cfg_index += 1
                current_pmpcfg = 0  # Reset for next pmpcfg*
        return code

    def setup_pma(self) -> str:
        "Generate code to setup PMA registers"
        pmacfg_start_addr = 0x7E0
        code = ""
        # Setup default pmas for dram
        if self.pool.pma_dram_default:
            pma_dram = self.pool.pma_dram_default
            pma_addr = 0x7E0 + (self.featmgr.cmdline.num_pmas - 1)
            code += f"""
            setup_pma_dram:
                # Setting up dram pma with {pma_dram}
                li t0, 0x{pma_dram.generate_pma_value():x}
                csrw 0x{pma_addr:x}, t0
            """

        # Setup pmas for io
        if self.pool.pma_io_default:
            pma_io = self.pool.pma_io_default
            pma_addr = 0x7E0 + (self.featmgr.cmdline.num_pmas - 2)
            code += f"""
            setup_pma_io:
                # Setting up io pma with {pma_io}
                li t0, 0x{pma_io.generate_pma_value():x}
                csrw 0x{pma_addr:x}, t0
            """

        if self.pool.pma_regions:
            for i, region_name in enumerate(self.pool.pma_regions):
                region = self.pool.pma_regions[region_name]
                pmacfg_addr = pmacfg_start_addr + i
                code += f"""
                setup_pma{i}:
                    # Setting up pmacfg{i} with {region}
                    li t0, 0x{region.generate_pma_value():x}
                    csrw 0x{pmacfg_addr:x}, t0
                """
        return code

    def base_loader(self, allocate_stack: bool = False, big_endian: bool = False) -> str:
        """
        Basic loader, initialize integer registers. Optionally allocated stack pointer and big-endian mode.
        """

        code = """
.section .text
.globl _start
.option norvc

_start:
    nop

"""
        # Initialize all integer registers to 0
        code += "init_int_register:\n"
        for i in range(1, 32):
            code += f"    li x{i}, 0x0\n"

        # Stack doesn't get allocated in generator during wysiwyg
        if allocate_stack:
            if self.featmgr.num_cpus > 1:
                code += """
                    calc_stack_pointer:
                    # Calculate hart's stack os_stack_start = os_stack + (page_size*hartID)
                    # Assumes stack size of 0x1000. Later this should be set with the equates, e.g.

                    csrr sp, mhartid
                    beqz sp, load_stack_pointer

                    li t1, 0x1000
                    # Multiply 0x1000 * hartid, avoids using M instructions in case unsupported
                    mult_stack_pointer:
                        add t0, t0, t1
                        addi sp, sp, -1
                        bnez sp, mult_stack_pointer
                    load_stack_pointer:
                        li sp, os_stack
                        sub sp, sp, t0
                        li t0, 0
                        li t1, 0
                """
            else:
                # When running single cpu, we dont do the stack arithmetic based on hartid. This allows for
                # single hart runs to run on any hart
                code += """
                    load_stack_pointer:
                        li sp, os_stack
                    """

        if big_endian:
            mstatus_big_endian_set = "0x0000003000000040"
            code += f"""
            set_mstatus_bigendian:
                # set mstatus.MBE == 1, mstatus.SBE==1, mstatus.UBE == 1 to automatically switch to big-endian mode
                li t0, {mstatus_big_endian_set}
                csrrs t0, mstatus, t0
            """
        return code

    def enable_paging(self):
        s = ""
        os_map = self.pool.get_page_map("map_os")
        os_sptbr = os_map.sptbr

        paging_mode = self.featmgr.paging_mode

        # satp value = [31]:mode, [30:22]: asid, [21:0]: sptbr[31:10]
        asid_val = self.rng.random_in_range(0, 2**9)
        mode_val = 0
        if paging_mode == RV.RiscvPagingModes.SV39:
            mode_val = 0x8
        elif paging_mode == RV.RiscvPagingModes.SV48:
            mode_val = 0x9
        elif paging_mode == RV.RiscvPagingModes.SV57:
            mode_val = 0xA
        else:
            raise ValueError(f"OS does not support paging mode {paging_mode} yet")

        # Set sptbr, asid and mode field values in the satp csr
        satp_val = os_sptbr >> 12
        satp_val |= common.set_bits(original_value=satp_val, bit_hi=59, bit_lo=44, value=asid_val)
        satp_val |= common.set_bits(original_value=satp_val, bit_hi=63, bit_lo=60, value=mode_val)

        s += f"""
        enable_paging:
            # Enable paging by writing CSR SATP.MODE = Sv32 (1)
            ;os_sptbr = 0x{os_sptbr:x}
            li x1, 0x{satp_val:x}
            csrw satp, x1
        paging_enabled:  # Adding this label for LS testbench to jump to
            nop
        """

        return s

    def switch_to_super(self, label_to_jump):
        """Assume you are in machine mode and you are switching to supervisor mode.

        Steps:

        - update mpec csr with PC where you want to jump after the mret
        - write mstatus.mpp[12:11] = 'b01
        - execute mret => switch to supervisor and jump to pc=mpec
        """
        s = ""
        s += f"""
            # Switch to supervisor first
            # Setup MEPC for the return label of MRET
            la x1, {label_to_jump}
            csrw mepc, x1
        """

        mstatus_mpp = "0x00001000"  # mstatus[12:11] = 01

        s += f"""
            li x1, {mstatus_mpp}
            csrrc x0, mstatus, x1

            # After the execution of mret, we switch to correct privilege
            # mode and jump to the next instruction
            mret
        """

        return s

    def enable_counters(self, event_path: Path) -> str:
        """Assume you are in machine mode.

        Steps:

        - generate event ID
        - assign event ID to hpmcounter
        - enable counters

        These are all handled within counters package of dtest lib.
        """
        code = ""
        if event_path is None:
            raise Exception("Counters should be enabled with event path. No event_path pased in with enable_counters")
        code += "\n        counter_enable:\n"
        code += self.counters.init_regs(event_path=event_path)
        return code

    def generate_csr_init_code(self):
        """Generate code to initialize CSRs based on command line arguments."""
        if not hasattr(self.featmgr.cmdline, "csr_init") and not hasattr(self.featmgr.cmdline, "csr_init_mask"):
            return ""

        code = "\n        # CSR Initialization\n"

        # Handle direct CSR writes
        if self.featmgr.cmdline.csr_init:
            for csr_init in self.featmgr.cmdline.csr_init:
                try:
                    csr, value = csr_init.split("=")
                    # Convert value to hex if it's not already
                    if not value.startswith("0x"):
                        value = hex(int(value))
                    code += f"""
                    # Initialize {csr} with {value}
                    li t0, {value}
                    csrw {csr}, t0
                    """
                except ValueError:
                    log.warning(f"Invalid CSR initialization format: {csr_init}. Expected 'csr=value'")
                    continue

        # Handle masked CSR writes
        if self.featmgr.cmdline.csr_init_mask:
            for csr_init in self.featmgr.cmdline.csr_init_mask:
                try:
                    csr, mask, value = csr_init.split("=")
                    # Convert values to hex if they're not already
                    if not mask.startswith("0x"):
                        mask = hex(int(mask))
                    if not value.startswith("0x"):
                        value = hex(int(value))
                    code += f"""
                    # Initialize {csr} with mask {mask} and value {value}
                    csrr t0, {csr}
                    li t1, {mask}
                    not t1, t1
                    and t0, t0, t1  # Clear masked bits
                    li t2, {value}
                    or t0, t0, t2  # Set masked bits
                    csrw {csr}, t0
                    """
                except ValueError:
                    log.warning(f"Invalid CSR initialization format: {csr_init}. Expected 'csr=mask=value'")
                    continue

        return code
