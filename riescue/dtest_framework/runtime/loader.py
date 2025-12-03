# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
import logging
from pathlib import Path
from typing import Any

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.counters import Counters
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator

log = logging.getLogger(__name__)


class Loader(AssemblyGenerator):
    """
    Genereates Loader assembly code for initializing the test runtime environment.

    Assumes booting at _start address in machine mode.
    Initializes registers (int, fp, vector if supported) and CSRs
    Sets up test runtime environment (paging, interrupts, etc.)

    .. note::
        This sets initial mtvec to loader panic (fast fail during loader)

        If virtualized test, will set vstvec to trap handler.

        If delegating exceptions to supervisor, will set stvec to trap handler.
        Otherwise, mtvec gets trap handler.

    Jumps to runtime privilege mode (``handler_priv``), and hands control to scheduler when finished.

    Interface:

    - ``_start``: entry point, required for linker. Initialize GPRs
    - ``loader__initialize_runtime``: Initialize runtime environment, setup CSRs
    - ``loader__done``: Routine used to jump to scheduler. Includes any post-loader hooks
    - ``loader__panic``: Routine used to jump to ``eot__failed``. Assumes in M mode and jumps directly to ``eot__failed`` sequence.
    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.paging_mode = self.featmgr.paging_mode
        self.counters = Counters(rng=self.rng)

        self.trap_entry_label = self.featmgr.trap_handler_label  #: Trap entry label. Defaults to trap_entry Value ``*tvec`` is loaded with
        self.scheduler_start_label = self.scheduler_init_label  #: Scheduler start label. This is where the loader jumps to when finished.

        if self.featmgr.bringup_pagetables:
            # If bringup_pagetables is enabled, skip scheduler
            self.scheduler_start_label = list(self.pool.discrete_tests.keys())[0]

        # registere loader variables
        self.zero_data = self.variable_manager.register_shared_variable("zero_data", 0x0)

    def generate(self) -> str:
        """
        Generate loader code. If linux/wysiwyg mode is enabled, use the appropriate loader.
        Otherwise, use the generic loader with loader interface.


        .. note:: Loader Hooks

            PRE_LOADER is after GPR initialization and panic address is loaded.
            POST_LOADER is before jumping to scheduler.

            Hooks should return control back to loader code after they are done.
            Writes to CSRs in PRE_LOADER may be overwritten by loader code. USe featmgr.init_csr or --init_csr instead
            POST_LOADER code will be in the handler privilege mode.
        """

        if self.featmgr.linux_mode:
            return self.linux_loader()
        elif self.featmgr.wysiwyg:
            return self.wysiwyg_loader()

        code = f"""
.section .text
.globl _start
.option norvc

_start:
    {self.init_int_registers()}
    {self.set_initial_panic()}

loader__initialize_runtime:
    {self.featmgr.call_hook(RV.HookPoint.PRE_LOADER)}
    {self.initialize_runtime()}

loader__done:
    {self.featmgr.call_hook(RV.HookPoint.POST_LOADER)}
    j {self.scheduler_start_label}

loader__panic:
    j eot__failed

"""
        return code

    # Loader code
    def linux_loader(self) -> str:
        "Returns the loader used for linux mode"
        return f"""
.section .text
.global _start
_start:
call main
li a7, 93 # SYS_exit
li a0, 0  # exit code

main:
    call {self.scheduler_init_label}
    ret
"""

    def wysiwyg_loader(self) -> str:
        """
        Returns the loader used for `What You See Is What You Get` (wysiwyg) mode.

        Since wysiwyg mode doesn't have a scheduler, the loader just initializes the GPRs
        and any init_csr_code and continues to test.
        """

        code = f"""
.section .text
.globl _start
.option norvc

_start:
    {self.init_int_registers()}

loader__initialize_runtime:
    {self.init_csr_code()}

loader__done:

"""
        return code

    def initialize_runtime(self) -> str:
        """
        Generate code to initialize runtime environment, setup CSRs

        """
        code = ""

        if self.featmgr.big_endian:
            code += self.enable_big_endian()
        if self.featmgr.csr_init or self.featmgr.csr_init_mask:
            code += self.init_csr_code()

        if self.featmgr.counter_event_path is not None:
            code += self.enable_counters(event_path=self.featmgr.counter_event_path)

        code += self.set_misa_bits()
        code += self.set_mstatus()

        if not self.featmgr.disable_wfi_wait:
            # RVTOOLS-4204 for mcounteren force enable
            code += """
        loader__enable_mcounteren:
            li t0, 0x2
            csrw mcounteren, t0
        """

        if self.featmgr.feature.is_supported("f") or self.featmgr.feature.is_supported("d"):
            code += self.init_fp_registers()
        if self.featmgr.feature.is_supported("v"):
            code += self.init_vector_registers()
        code += self.hart_context_loader()

        if self.featmgr.needs_pma:
            code += self.setup_pma()
        if self.featmgr.setup_pmp:
            code += self.setup_pmp()
        if self.featmgr.secure_mode:
            code += self.setup_secure_mode()

        # Handle menvcfg
        if self.test_priv != RV.RiscvPrivileges.MACHINE:
            code += self.setup_menvcfg()

        # Switch to handler privilege mode
        if self.handler_priv != RV.RiscvPrivileges.MACHINE:
            code += self.setup_trap_delegation()
            code += self.setup_tvec()

            if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
                code += "\nloader__switch_to_vs:\n"
                code += "csrw satp, x0\n"
                code += "csrw vsatp, x0\n"  # Clear satp registers before switching to VS mode
                # Switch to HS mode
                code += self.switch_test_privilege(
                    from_priv=RV.RiscvPrivileges.MACHINE,
                    to_priv=RV.RiscvPrivileges.SUPER,
                    jump_label="enter_hypervisor",
                    switch_to_vs=True,
                )
            else:
                # Clear satp before switching to supervisor mode
                code += "\nloader__switch_to_super:\n"
                code += "csrw satp, x0\n"
                code += self.switch_test_privilege(
                    from_priv=RV.RiscvPrivileges.MACHINE,
                    to_priv=self.handler_priv,
                    jump_label="loader__post_switch_to_handler",
                )
            code += "\nloader__post_switch_to_handler:\n"
        else:
            # If we are in machine mode, we need to setup mtvec to point to the trap handler
            code += self.setup_tvec()

        # Handle senvcfg
        if self.featmgr.senvcfg != 0:
            code += f"""
                load__setup_senvcfg:
                    li t0, {self.featmgr.senvcfg}
                    csrw senvcfg, t0

                    """

        # Enable paging, must be in M / S to enable paging
        if self.paging_mode != RV.RiscvPagingModes.DISABLE:
            code += self.enable_paging()
        return code

    # env setup code
    def enable_big_endian(self) -> str:
        "Generate code to enable big-endian mode"
        mstatus_big_endian_set = "0x0000003000000040"
        return f"""
            set_mstatus_bigendian:
                # set mstatus.MBE == 1, mstatus.SBE==1, mstatus.UBE == 1 to automatically switch to big-endian mode
                li t0, {mstatus_big_endian_set}
                csrrs t0, mstatus, t0
            """

    def enable_counters(self, event_path: Path) -> str:
        """Assume you are in machine mode.

        Steps:

        - generate event ID
        - assign event ID to hpmcounter
        - enable counters

        These are all handled within counters package of dtest lib.
        """
        code = ""
        code += "\n        counter_enable:\n"
        code += self.counters.init_regs(event_path=event_path)
        return code

    def set_misa_bits(self):
        "Generate code to set MISA bits. Adds MISA bits based on enabled features"
        code = f"""
loader__set_misa_bits:
    li t0, 0x{self.featmgr.get_misa_bits():x} # OR in new MISA bits with existing value
    csrrs t0, misa, t0
            """

        return code

    def set_mstatus(self) -> str:
        """
        Generate code to set mstatus.
        Sets mstatus.SUM=1, so we can access user pages from supervisor
        Conditionally sets mstatus.FS and mstatus.VS to 01 and 01, so we can access user pages from supervisor
        """
        code = "\n# Set mstatus.SUM=1, enables accessing user pages from supervisor"
        mstatus_value = 1 << 18  # Set mstatus.SUM, so we can access user pages from supervisor

        if self.featmgr.feature.is_supported("f") or self.featmgr.feature.is_supported("d"):
            mstatus_value |= (0b01) << 13  # Set mstatus.FS = 01
            code += "\n# Set mstatus.FS = 01"
        if self.featmgr.feature.is_supported("v"):
            mstatus_value |= (0b01) << 9  # Set mstatus.VS = 11
            code += "\n# Set mstatus.VS = 01"

        if self.featmgr.interrupts_enabled:
            mstatus_value |= 1 << 3  # Enable interrupts
            code += "\n# Interrupts enabled"
            if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.SUPER:
                mstatus_value |= 1 << 1  # Enable supervisor
                code += "\n# Supervisor interrupts enabled"

        code += f"""
loader__set_mstatus:
    li t0, 0x{mstatus_value:x}
    csrrs t0, mstatus, t0
"""
        return code

    def setup_secure_mode(self) -> str:
        """
        Generate code to setup secure mode.

        .. note::

            Uses custom CSR, matp.
            Future runtime improvements need to make this flexible where users can define secure mode.
            Alternatively, users can avoid using secure mode.
        """
        code = """
        loader__setup_secure_mode:
            .equ matp_csr, 0x7c7
            # Set MATP.SWID=1, so we can access secure pages from supervisor
            ori x1, x1, 0x1
            csrs	0x7c7, x1  #set swid bit in matp
            """
        return code

    def enable_paging(self) -> str:
        enable_paging_code = ""
        os_map = self.pool.get_page_map("map_os")
        os_sptbr = os_map.sptbr

        # satp value = [31]:mode, [30:22]: asid, [21:0]: sptbr[31:10]
        asid_val = self.rng.random_in_range(0, 2**9)
        mode_val = 0
        mode_name = None
        if self.paging_mode == RV.RiscvPagingModes.SV39:
            mode_val = 0x8
            mode_name = "Sv39"
        elif self.paging_mode == RV.RiscvPagingModes.SV48:
            mode_val = 0x9
            mode_name = "Sv48"
        elif self.paging_mode == RV.RiscvPagingModes.SV57:
            mode_val = 0xA
            mode_name = "Sv57"
        else:
            raise ValueError(f"OS does not support paging mode {self.paging_mode} yet")

        # Set sptbr, asid and mode field values in the satp csr
        satp_val = os_sptbr >> 12
        satp_val |= common.set_bits(original_value=satp_val, bit_hi=59, bit_lo=44, value=asid_val)
        satp_val |= common.set_bits(original_value=satp_val, bit_hi=63, bit_lo=60, value=mode_val)

        enable_paging_code += f"""
        loader__enable_paging:
            # Enable paging by writing CSR SATP.MODE = {mode_name} (1)
            ;os_sptbr = 0x{os_sptbr:x}
            li x1, 0x{satp_val:x}
            csrw satp, x1"""

        # enable paging in machine mode using mstatus.MPRV (bit 17)
        # note: If y≠M, xRET also sets MPRV=0
        # When MPRV=1, load and store memory addresses are translated and protected, and endianness is applied, as though the current privilege mode were set to MPP.
        # Need to also set MPP to S
        # This gets reset when mRET is executed
        if self.test_priv == RV.RiscvPrivileges.MACHINE:
            enable_paging_code += """
                li t0, ((1 << 17) | (1 << 11))    # Set MPRV=1 and MPP=01 (supervisor)
                csrrs x0, mstatus, t0
                li t0, (1<<12) # Clear mstatus[12]
                csrrc x0, mstatus, t0
            """
        return enable_paging_code + "\n"

    def hart_context_loader(self) -> str:
        """
        Code for loading scratch register with hart context.
        Places hart context pointer in scratch register(s).

        Some configurations have just M, so can't be greedy with scratch registers (M + S)
        But if we can write to S then supervisor-level code can use S scratch register and avoid ecalls

        Since test scheduler runs in M mode if test is M/U, or S mode if test is S mode,
        need to store in whatever scratch register is accessible by scheduler

        FIXME: If it's possilbe to get the scheduler and the exception handler to run in the same privilege mode that would be great :)
        It would mean that we only need one scratch register for all Runtime code
        """
        scratch_regs = [self.scratch_reg]
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            scratch_regs.append("vsscratch")

        if self.test_priv == RV.RiscvPrivileges.SUPER:
            scratch_regs.append("sscratch")
        return "\n" + self.variable_manager.initialize(scratch_regs=scratch_regs) + "\n"

    def setup_pmp(self) -> str:
        "Generate code to setup PMP registers"
        # Implement a queue to keep track of pmp numbers from 0-63
        log.info(f"Setting up PMPs: {self.pool.pmp_regions}")

        code = ""
        for pmp in self.pool.pmp_regions.encode():
            for addr in pmp.addr:
                addr_start, addr_size = addr.range()

                code += f"""
                # Setup {addr.name} covering [0x{addr_start:x}, 0x{addr_start + addr_size:x}] A={addr.addr_matching.name}
                li t0, 0x{addr.address:x}
                csrw {addr.name}, t0
                """
            code += f"""
            # Setup {pmp.cfg.name} for {pmp.addr[0].name} to {pmp.addr[-1].name}
            li t0, 0x{pmp.cfg.value:x}
            csrw {pmp.cfg.name}, t0
            """
        return code

    def setup_pma(self) -> str:
        "Generate code to setup PMA registers"
        pmas = self.pool.pma_regions.consolidated_entries()
        if not pmas:
            return ""

        if len(pmas) > self.featmgr.num_pmas:
            raise ValueError(f"Number of PMAs requested ({len(pmas)}) is less than the number of PMAs available ({self.featmgr.num_pmas})")

        pmacfg_start_addr = 0x7E0
        code = "\nloader__setup_pma:\n"
        pma_addr = pmacfg_start_addr
        log.info("Setting up PMAs")
        for i, pma in enumerate(pmas):
            code += f"""
                # Setting up pmacfg{i} for {str(pma)}
                li t0, 0x{pma.generate_pma_value():x}
                csrw 0x{pma_addr:x}, t0
            """
            pma_addr += 1
        return code

    def setup_trap_delegation(self) -> str:
        """
        Generate code to setup exception and interrupt delegation.
        """
        medeleg_val = 0
        if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.SUPER:
            # delegate all exceptions to supervisor
            medeleg_val = 0xFFFFFFFFFFFFFFFF

        if self.featmgr.medeleg != 0xFFFFFFFFFFFFFFFF:
            medeleg_val = self.featmgr.medeleg
        code = f"""
loader__setup_medeleg:
    li t0, 0x{medeleg_val:x}
    csrw medeleg, t0

loader__setup_mideleg:"""

        mideleg_val = 0
        if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.SUPER:
            mideleg_val = (1 << 9) | (1 << 5) | (1 << 1) | (1 << 11) | (1 << 7) | (1 << 3)  # Enables SEI, STI, SSI and MEI, MTI, MSI
        code += f"""
    li t0, 0x{mideleg_val:x}
    csrw mideleg, t0
                """
        return code

    def setup_menvcfg(self) -> str:
        """
        Generate code to setup menvcfg.
        Configuration's menvcfg is OR'd with default menvcfg.
        """

        code = "\nloader__setup_menvcfg:"
        menvcfg_val = 1 << 63  # Enable stce by default
        if self.featmgr.disable_wfi_wait:
            menvcfg_val = 0  # FIXME: temporarily enable stce at all times to deal with stimecmp. RVTOOLS-4204
        if self.featmgr.pbmt_ncio:
            menvcfg_val |= 1 << 62  # Enable svpbmt when randomization kicks in for pbmp NCIO
        if self.featmgr.svadu:
            menvcfg_val |= 1 << 61  # Enable svadu when randomization kicks in for ad-bit randomization

        if self.featmgr.menvcfg != 0:
            menvcfg_val |= self.featmgr.menvcfg
        if menvcfg_val != 0:
            code += f"""
                    li t0, 0x{menvcfg_val:x}
                    csrs menvcfg, t0
                    """
        return code

    def setup_tvec(self) -> str:
        """
        Generate code to setup mtvec and/or stvec.

        If handler privilege is super, set stvec. mtvec still points to loader panic
        If handler privilege is machine, set mtvec.

        If running the code in VS mode, will be delegating exceptions to virtual supervior .
        Need to point vstvec to trap handler, and mtvec / stvec to loader panic
        """
        code = "\nloader__setup_tvec:\n"

        if self.featmgr.user_interrupt_table:
            log.error("Using user_interrupt_table is deprecated. Use ;#vectored_interrupt(index, label) in source code instead.")
            code += "li t0, user_interrupt_table_addr\n"
            code += "ld t0, 0(t0)\n"
        else:
            code += f"la t0, {self.trap_entry_label}\n"
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            # delegating exceptions to VS; runtime and test should be staying in this mode
            # M and HS modes shouldn't be getting any exceptions
            code += "csrw vstvec, t0\n"
        elif self.handler_priv == RV.RiscvPrivileges.SUPER:
            code += "csrw stvec, t0\n"
        else:
            code += "csrw mtvec, t0\n"
        return code

    def setup_mstateen(self) -> str:
        """
        Generate code to setup mstateen.
        If smstateen is requested, need to setup mstateen0 to allow hstateen writes from hypervisor

        """
        code = """
loader__setup_mstateen:
    li t0, 1<<63 | 1<<62
    csrw mstateen0, t0"""

        return code

    # register initialization code
    def init_csr_code(self):
        """Generate code to initialize CSRs."""
        if not self.featmgr.csr_init and not self.featmgr.csr_init_mask:
            return ""
        code = "\n# Initialize CSRs"
        code += "\n\tinit_cmdline_csrs:"

        # Handle direct CSR writes
        if self.featmgr.csr_init:
            for csr_init in self.featmgr.csr_init:
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
        if self.featmgr.csr_init_mask:
            for csr_init in self.featmgr.csr_init_mask:
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

    def init_int_registers(self) -> str:
        "generates code to initialize all registers to 0"
        code = "loader__init_int_register:\n"
        for i in range(1, 32):
            code += f"    li x{i}, 0x0\n"
        return code

    def init_fp_registers(self, use_fmv: bool = False):
        """
        Generate FP register initialization code if FP extensions are supported.
        Default behavior is to load from zero_data (poitner to a zeroed out double word in memory)

        :param use_fmv: use fmv.d.x to initialize FP registers instead of an fld. Some implementations might prefer fld if it's quicker than casting
        """
        if not (self.featmgr.feature.is_supported("f") or self.featmgr.feature.is_supported("d")):
            raise RuntimeError("FP extensions are not supported but init_fp_registers is called")
        code = ["# Initialize FP and Vector registers if supported", "loader__init_fp_registers:"]

        if not use_fmv:
            code.append(self.zero_data.load_immediate("t0"))
        for i in range(32):
            if use_fmv:
                code.append(f"fmv.d.x   f{i}, x0")
            else:
                code.append(f"fld   f{i}, 0(t0)")
        return "\n" + "\n".join(code) + "\n"

    def init_vector_registers(self):
        """Generate vector register initialization code if vector extension is supported"""
        if not self.featmgr.feature.is_supported("v"):
            raise RuntimeError("Vector extension is not supported but init_vector_registers is called")
        code = ["# Initialize Vector Registers", "loader__init_vector_registers:"]
        code += [
            "li x4, 0x0",
            "li x5, 0x4",
            "li x6, 0xd8",
            "vsetvl x4,x5,x6",
        ]
        for i in range(32):
            code.append(f"vmv.v.x v{i},  x0")
        return "\n" + "\n".join(code) + "\n"

    # panic code
    # Making these separate jump tables for simpler debug, rather than having code go through trap handler
    def set_initial_panic(self) -> str:
        """
        load panic address to mtvec.
        Jumps to eot__failed to end the test directly since already in M mode.
        """
        return """
loader__load_panic_address:
    la t0, eot__failed
    csrw mtvec, t0
"""
