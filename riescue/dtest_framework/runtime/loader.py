# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
import logging
from pathlib import Path
from typing import Any

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.counters import Counters
from riescue.dtest_framework.lib.pma import PmaInfo
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator

log = logging.getLogger(__name__)


# PMA scheme (num_pmas entries from cpu config / --num_pmas): the first PMA_DIRECT_CSR_ENTRIES are
# direct CSRs (pmacfg 0x7E0+i, pmamask 0x7F0+i); higher entries are reached indirectly with
# miselect = PMA_INDIRECT_SELECT_BASE | entry, mireg = pmacfg[entry], mireg2 = pmamask[entry].
PMA_INDIRECT_SELECT_BASE = 0x8000000000000000
PMA_DIRECT_CSR_ENTRIES = 16
PMA_IO_CATCHALL = 0x7C00000000000007  # IO/non-cacheable 0-2GB (pmacfg14 boot reset value)
PMA_DRAM_CATCHALL = 0xE0000000000001E7  # cacheable-coherent RWX 0-2^56 (pmacfg15 boot reset value)
PMA_NUM_CATCHALLS = 2  # the last two entries (lowest priority) re-established as IO/DRAM catchalls


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
    Point ``tp`` to Hart Context before handing control to scheduler.

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

        smrnmi_init = self.setup_smrnmi() if self.featmgr.is_feature_enabled("smrnmi") else ""

        code = f"""
.section .runtime, "ax"
.globl _start
.option norvc

_start:
    {self.init_int_registers()}
    {self.set_initial_panic()}

loader__initialize_runtime:
    {smrnmi_init}
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
.section .runtime, "ax"
.global _start
_start:
call main
li a7, 93 # SYS_exit
li a0, 0  # exit code

main:
    {self.hart_context_loader()}
    call {self.scheduler_init_label}
    ret
"""

    def wysiwyg_loader(self) -> str:
        """
        Returns the loader used for `What You See Is What You Get` (wysiwyg) mode.

        Since wysiwyg mode doesn't have a scheduler, the loader just initializes the GPRs
        and any init_csr_code and continues to test.
        """
        # Even in wysiwyg mode we must exit the reset-state NMI-handler context when
        # the whisper config models Smrnmi, otherwise the first regular trap routes
        # through the NMI vector (PC=0).
        smrnmi_init = ""
        if self.featmgr.is_feature_enabled("smrnmi"):
            smrnmi_init = self.setup_smrnmi()

        code = f"""
.section .code, "ax"
.globl _start
.option norvc

_start:
    {self.init_int_registers()}

loader__initialize_runtime:
    {smrnmi_init}
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

        if self.featmgr.feature.is_supported("f") or self.featmgr.feature.is_supported("d"):
            code += self.init_fp_registers()
        if self.featmgr.feature.is_supported("v"):
            code += self.init_vector_registers()
        code += self.validate_hartid()
        code += self.hart_context_loader()

        if self.featmgr.needs_pma:
            code += self.setup_pma()
        if self.featmgr.setup_pmp:
            code += self.setup_pmp()
        if self.featmgr.secure_mode:
            code += self.setup_secure_mode()
        if self.featmgr.selfcheck:
            code += """
            jal selfcheck__decide_save_or_check
            """
        if self.featmgr.log_test_execution:
            code += """
            jal test_execution_log_init
            """

        # Setup stateen CSRs
        code += self.setup_stateen()

        # Handle menvcfg
        if self.test_priv != RV.RiscvPrivileges.MACHINE:
            code += self.setup_menvcfg()

        if self.featmgr.is_feature_enabled("s"):
            code += self.setup_trap_delegation()
        code += self.setup_tvec()
        if self.pool.init_aplic_interrupts:
            code += self.setup_aplic_interrupts()
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            code += self.setup_hypervisor()
        code += self.featmgr.call_hook(RV.HookPoint.M_LOADER)

        # Handle senvcfg
        if self.featmgr.is_feature_enabled("s") and self.featmgr.senvcfg != 0:
            code += f"""
                load__setup_senvcfg:
                    li t0, {self.featmgr.senvcfg}
                    csrw senvcfg, t0

                    """

        # Enable paging, must be in M / S to enable paging
        if self.paging_mode != RV.RiscvPagingModes.DISABLE:
            code += self.enable_paging()

        code += "RVMODEL_IO_INIT(t0, t1, t2)\n"

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
            csrr	t0, 0x7c7  #read matp to verify
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

        # MPRV is set in the scheduler's execute_test, not here, because the scheduler
        # runs in M-mode with bare addressing and needs MPRV=0 for data access.
        return enable_paging_code + "\n"

    def validate_hartid(self) -> str:
        """
        Validate once, at boot, that this hart's mhartid is one the test was generated for.

        Only meaningful for MP tests (num_cpus > 1); a mismatch means the test is running on
        a DUT with a different set of hart IDs. Doing it here, before ``hart_context_loader``,
        lets every later mhartid use assume the value is valid.

        A bad hart joins the coordinated MP end-of-test with a fail status (``gp != 1`` marks
        a hard fail; ``gp[31] == 0`` means "not an early bail"), so the single hart that writes
        ``tohost`` reports failure and a passing hart cannot overwrite it.
        """
        if self.featmgr.num_cpus <= 1:
            return ""  # single hart: nothing to validate, boot path stays byte-for-byte unchanged

        if self.variable_manager.discontiguous_hartids:
            # Search hart_id_table (bounded by hart_id_table_end) for this mhartid.
            load = "lw" if self.variable_manager.xlen == RV.Xlen.XLEN32 else "ld"
            step = 4 if self.variable_manager.xlen == RV.Xlen.XLEN32 else 8
            check = f"""
    la t1, hart_id_table
loader__validate_hartid_loop:
    la t2, hart_id_table_end
    beq t1, t2, loader__bad_hartid      # walked off the end -> not an expected hart
    {load} t2, 0(t1)
    beq t2, t0, loader__validate_hartid_done
    addi t1, t1, {step}
    j loader__validate_hartid_loop"""
        else:
            # Contiguous IDs are 0..num_cpus-1, so range-check the mhartid.
            check = f"""
    li t1, {self.featmgr.num_cpus}
    bgeu t0, t1, loader__bad_hartid      # mhartid >= num_cpus -> not an expected hart"""

        return f"""
loader__validate_hartid:
    csrr t0, mhartid
    {check}
    j loader__validate_hartid_done
loader__bad_hartid:
    li gp, 0
    j eot__end_test
loader__validate_hartid_done:
"""

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
        scratch_regs = ["mscratch"]
        if self.featmgr.is_feature_enabled("s"):
            scratch_regs.append("sscratch")
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            scratch_regs.append("vsscratch")

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
        if self.featmgr.pmp_catchall and not self.featmgr.secure_mode:
            # Add catchall PMP entries using high-numbered entries (14/15) that tests
            # are unlikely to modify. This ensures code regions remain accessible even
            # when tests write arbitrary values to lower pmpaddr entries.
            # pmpcfg2 bits [63:48] control entries 14-15 on RV64.
            # This is opt-in via --pmp_catchall or cpu_config test_generation.pmp_catchall.
            code += """
            li t0, 0x1FFFFFFFFFFFF
            csrw pmpaddr14, t0
            csrw pmpaddr15, t0
            csrr t1, pmpcfg2
            li t0, 0x1f1f000000000000
            or t1, t1, t0
            csrw pmpcfg2, t1
            """
        return code

    def setup_pma(self) -> str:
        """
        Generate code to setup PMA registers.

        Entry layout: [0..X) untouched user-reserved entries (X = featmgr.user_programmable_pmacfg),
        then defined regions, then randomized decoys, then (randomization only) invalidated entries
        up to the catchalls at the last two entries (num_pmas - 2 / num_pmas - 1). With randomization
        on, named pma_* carve-outs and decoys are emitted before memory-map regions so both win
        first-match-wins priority. Note: with randomization off, carve-outs remain shadowed by
        lower-index memory-map entries (known quirk of the flag-off emission order).
        """
        randomize = self.featmgr.enable_pma_randomization
        pmas = self.pool.pma_regions.consolidated_entries(merge_named=not randomize)
        random_pmas = self.pool.pma_random_regions
        reserved = self.featmgr.user_programmable_pmacfg
        num_pmas = self.featmgr.num_pmas
        catchall_base = num_pmas - PMA_NUM_CATCHALLS
        if not pmas and not random_pmas:
            return ""

        if randomize:
            carveouts = sorted((p for p in pmas if p.pma_name.startswith("pma_")), key=lambda p: (p.pma_size, p.pma_address))
            memmap_regions = [p for p in pmas if not p.pma_name.startswith("pma_")]
            essential = reserved + len(carveouts) + len(memmap_regions) + PMA_NUM_CATCHALLS
            if essential > num_pmas:
                raise ValueError(
                    f"PMA entries exceed capacity: user_programmable={reserved} + carveouts={len(carveouts)} "
                    f"+ memory_map={len(memmap_regions)} + {PMA_NUM_CATCHALLS} catchalls = {essential} > {num_pmas} PMA entries"
                )
            # Decoys are pure coverage: truncate to whatever fits rather than failing the test
            decoy_budget = num_pmas - essential
            decoys = list(random_pmas[:decoy_budget])
            if len(decoys) < len(random_pmas):
                log.warning(f"PMA randomization: truncating decoys {len(random_pmas)} -> {len(decoys)} to fit " f"{num_pmas} PMA entries")
                del random_pmas[len(decoys) :]  # keep pool/addrgen exclusion consistent with emission
            # Carve-outs first (specific beats containing), decoys before memory-map so they are live
            ordered = carveouts + decoys + memmap_regions
        else:
            if reserved + len(pmas) + PMA_NUM_CATCHALLS > num_pmas:
                raise ValueError(
                    f"PMA entries exceed capacity: user_programmable={reserved} + regions={len(pmas)} "
                    f"+ {PMA_NUM_CATCHALLS} catchalls = {reserved + len(pmas) + PMA_NUM_CATCHALLS} > {num_pmas} PMA entries"
                )
            ordered = pmas

        code = "\nloader__setup_pma:\n"
        log.info("Setting up PMAs")
        used = reserved + len(ordered)
        if randomize:
            # Catchalls at the top entries first: boot entries 14/15 still hold catchall values
            # here, so default memory coverage never lapses while lower entries change.
            code += self._setup_pma_catchall(used, catchall_base, clear_masks=False)
        for i, pma in enumerate(ordered):
            code += self._pma_entry_code(reserved + i, pma, force=randomize)
        if randomize:
            code += self._pma_invalidate_code(used, catchall_base)
        else:
            code += self._setup_pma_catchall(used, catchall_base)
        return code

    def _pma_entry_code(self, index: int, pma: PmaInfo, force: bool = False) -> str:
        """Emit one pmacfg/pmamask entry; direct CSRs below 16, indirect via miselect/mireg/mireg2 above."""
        value = pma.generate_pma_value(force=force)
        mask_value = pma.generate_pma_mask_value()
        if index >= PMA_DIRECT_CSR_ENTRIES:
            # Higher entries have no direct CSRs; access via miselect/mireg (pmacfg) and mireg2 (pmamask)
            code = f"""
                # Setting up pmacfg{index} (indirect, miselect=0x{PMA_INDIRECT_SELECT_BASE + index:x}) for {str(pma)}
                li t1, 0x{PMA_INDIRECT_SELECT_BASE + index:x}
                csrw miselect, t1
                li t0, 0x{value:x}
                csrw mireg, t0
"""
            if mask_value:
                # pmacfg write reset the mask; program pmamask strictly after pmacfg
                code += f"""                # Set pmamask{index}
                li t0, 0x{mask_value:x}
                csrw mireg2, t0
            """
            else:
                code += f"""                # Clear pmamask{index}
                csrw mireg2, x0
            """
            return code
        code = f"""
                # Setting up pmacfg{index} for {str(pma)}
                li t0, 0x{value:x}
                csrw 0x{0x7E0 + index:x}, t0
            """
        if mask_value:
            code += f"""
                # Set pmamask{index}
                li t0, 0x{mask_value:x}
                csrw 0x{0x7F0 + index:x}, t0
            """
        else:
            code += f"""
                # Clear pmamask{index}
                csrw 0x{0x7F0 + index:x}, x0
            """
        return code

    def _pma_invalidate_code(self, start: int, end: int) -> str:
        """Write pmacfg=0 (and clear pmamask) for stale entries in [start, end); kills boot 14/15 shadows."""
        code = ""
        for index in range(start, end):
            if index >= PMA_DIRECT_CSR_ENTRIES:
                code += f"""
                # Invalidate pmacfg{index} (indirect)
                li t1, 0x{PMA_INDIRECT_SELECT_BASE + index:x}
                csrw miselect, t1
                csrw mireg, x0
                csrw mireg2, x0
            """
            else:
                code += f"""
                # Invalidate pmacfg{index}
                csrw 0x{0x7E0 + index:x}, x0
                csrw 0x{0x7F0 + index:x}, x0
            """
        return code

    def _setup_pma_catchall(self, used: int, catchall_base: int, clear_masks: bool = True) -> str:
        """
        Re-establish the boot catchalls (pmacfg14/15 whisper-config resets) at the two
        lowest-priority entries (catchall_base and catchall_base+1, from num_pmas), so
        test-programmed regions never lose default memory coverage.

        clear_masks=False skips the pmamask writes (randomize mode): an explicit pmamask
        write flips whisper's 2^56 DRAM region to masked-compare on bits [51:12], collapsing
        it to page 0. The pmacfg write already zeroes the PMAMASK CSR without that side effect.
        """
        if used > catchall_base:
            log.warning(f"PMA regions occupy entries {catchall_base}/{catchall_base + 1}; catchall writes will overwrite them")
        code = ""
        for idx, value, desc in ((catchall_base, PMA_IO_CATCHALL, "IO 0-2GB"), (catchall_base + 1, PMA_DRAM_CATCHALL, "DRAM 0-2^56")):
            if idx >= PMA_DIRECT_CSR_ENTRIES:
                code += f"""
                # PMA catchall pmacfg{idx} ({desc})
                li t1, 0x{PMA_INDIRECT_SELECT_BASE + idx:x}
                csrw miselect, t1
                li t0, 0x{value:x}
                csrw mireg, t0
            """
                if clear_masks:
                    code += """    csrw mireg2, x0
            """
            else:
                code += f"""
                # PMA catchall pmacfg{idx} ({desc})
                li t0, 0x{value:x}
                csrw 0x{0x7E0 + idx:x}, t0
            """
                if clear_masks:
                    code += f"""    csrw 0x{0x7F0 + idx:x}, x0
            """
        return code

    def setup_hypervisor(self):
        """
        Setup hypervisor CSRs
        """
        hedelg_val = self.featmgr.hedeleg
        hidelg_val = self.featmgr.hideleg
        henvcfg_val = self.featmgr.henvcfg

        # Handle pbmt randomization
        if self.featmgr.pbmt_ncio:
            henvcfg_val |= 1 << 62

        # Handle svadu randomization
        if self.featmgr.svadu:
            henvcfg_val |= 1 << 61

        # Sstc: allow VS-mode access to vstimecmp/time when sstc is enabled.
        if self.featmgr.is_feature_enabled("sstc"):
            henvcfg_val |= 1 << 63

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
        """

        # vstimecmp resets to 0; once henvcfg.STCE=1, time + htimedelta >= vstimecmp fires VSTIP
        # immediately. Pre-write vstimecmp to all-ones BEFORE writing henvcfg so VSTIP cannot
        # spuriously latch. (M-mode can write vstimecmp regardless of STCE.)
        if self.featmgr.is_feature_enabled("sstc"):
            code += """
                setup_vstimecmp:
                    li t0, -1
                    csrw vstimecmp, t0
            """

        code += f"""
                # Setup henvcfg
                li t0, {henvcfg_val}
                csrw henvcfg, t0
        """

        # if smstateen extension is enabled, then we need to setup hstateen0 to allow senvcfg writes
        if self.featmgr.is_feature_enabled("smstateen") or self.featmgr.is_feature_enabled("ssstateen"):
            code += f"""
                hypervisor_setup_hstateen:
                li t0, {self.featmgr.hstateen}
                csrw hstateen0, t0
            """

        # Setup tvec to point to the trap handler for hypervisor
        code += """
           la t0, trap_handler_hs__trap_handler
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

    def setup_trap_delegation(self) -> str:
        """
        Generate code to setup exception and interrupt delegation.
        """
        medeleg_val = self.featmgr.medeleg
        code = f"""
loader__setup_medeleg:
    li t0, 0x{medeleg_val:x}
    csrw medeleg, t0

loader__setup_mideleg:"""

        mideleg_val = self.featmgr.mideleg
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
        menvcfg_val = 0
        if self.featmgr.pbmt_ncio:
            menvcfg_val |= 1 << 62  # Enable svpbmt when randomization kicks in for pbmp NCIO
        if self.featmgr.svadu:
            menvcfg_val |= 1 << 61  # Enable svadu when randomization kicks in for ad-bit randomization
        if self.featmgr.is_feature_enabled("sstc"):
            menvcfg_val |= 1 << 63  # Enable Sstc so S/HS can access stimecmp/time
            # stimecmp resets to 0; once STCE=1, time >= stimecmp fires STIP immediately.
            # Pre-write stimecmp to all-ones before setting STCE so STIP cannot spuriously latch.
            code += """
                    li t0, -1
                    csrw stimecmp, t0
                    """

        if self.featmgr.menvcfg != 0:
            menvcfg_val |= self.featmgr.menvcfg
        if menvcfg_val != 0:
            code += f"""
                    li t0, 0x{menvcfg_val:x}
                    csrs menvcfg, t0
                    """
        return code

    def setup_aplic_interrupts(self) -> str:
        """
        Include C code to setup default interrupt handling
        """

        code = f"""\n__setup_aplic_interrupts:
            # .word 0x74446073 # csrrsi x0, mnstatus, 0x8
            # IMSIC init

            li t0, MAPLIC_MMR_BASE_ADDR + 0x1bc0
            li t1, 0x40000
            sw t1, 0(t0)

            li t0, MAPLIC_MMR_BASE_ADDR + 0x1bc4
            li t1, 0x601000
            sw t1, 0(t0)

            li t0, MAPLIC_MMR_BASE_ADDR + 0x1bc8
            li t1, 0x44000
            sw t1, 0(t0)

            li t0, MAPLIC_MMR_BASE_ADDR + 0x1bcc
            li t1, 0x600000
            sw t1, 0(t0)

            # Enable eidelivery
            li t0, 0x70
            csrw miselect, t0
            csrw siselect, t0
            li t0, 1
            csrw mireg, t0
            csrw sireg, t0

            # Set threshold to 0
            li t0, 0x72
            csrw miselect, t0
            csrw siselect, t0
            li t0, 0
            csrw mireg, t0
            csrw sireg, t0

            # Enable all interrupts - eie 0xc0 to 0xff = -1
            li      t0, 0xc0
            li      t1, -1
            li      t2, {0xc0 + ((self.pool.max_aplic_irq + 1) >> 4)}
__enable_all_interrupts:
            csrw    miselect, t0
            csrw    mireg, t1
            csrw    siselect, t0
            csrw    sireg, t1
            add     t0, t0, 2
            bne     t0, t2, __enable_all_interrupts
        """

        if self.pool.init_guest_imsic:
            eie_top = 0xC0 + ((self.pool.max_aplic_irq + 1) >> 4)
            code += f"""
            # IMSIC guest interrupt file setup.
            # Two guest files are configured so VSEI and SGEI can be exercised
            # independently:
            #   * Guest file 1 -> selected by hstatus.VGEIN, so it drives
            #     vsip[9]=VSEIP. hgeie[1]=0, so poking file 1 does NOT raise
            #     hip[12]=SGEIP.
            #   * Guest file 2 -> hgeie[2]=1, so it drives hip[12]=SGEIP, but it
            #     is NOT selected by VGEIN in the resting state, so poking file 2
            #     does NOT raise VSEIP.
            # vsiselect/vsireg act on the VGEIN-selected file, so each file is
            # configured with VGEIN pointed at it. VGEIN is left = 1 afterwards
            # (the home for VSEI delivery); RVMODEL_CLR_HGEI_INT restores VGEIN=1
            # after it temporarily points VGEIN at the claimed guest file.

            # --- Configure guest file 1 (VSEI): VGEIN = 1 ---
            li t0, (0x3f << 12)
            csrc hstatus, t0
            li t0, (1 << 12)
            csrs hstatus, t0

            # eidelivery = 1
            li t0, 0x70
            csrw vsiselect, t0
            li t0, 1
            csrw vsireg, t0

            # eithreshold = 0
            li t0, 0x72
            csrw vsiselect, t0
            csrw vsireg, zero

            # Enable all interrupt IDs (eie 0xC0..0xC0+N, step 2 for RV64)
            li      t0, 0xc0
            li      t1, -1
            li      t2, {eie_top}
__enable_all_guest_interrupts_f1:
            csrw    vsiselect, t0
            csrw    vsireg, t1
            addi    t0, t0, 2
            bne     t0, t2, __enable_all_guest_interrupts_f1

            # --- Configure guest file 2 (SGEI): VGEIN = 2 ---
            li t0, (0x3f << 12)
            csrc hstatus, t0
            li t0, (2 << 12)
            csrs hstatus, t0

            # eidelivery = 1
            li t0, 0x70
            csrw vsiselect, t0
            li t0, 1
            csrw vsireg, t0

            # eithreshold = 0
            li t0, 0x72
            csrw vsiselect, t0
            csrw vsireg, zero

            # Enable all interrupt IDs (eie 0xC0..0xC0+N, step 2 for RV64)
            li      t0, 0xc0
            li      t1, -1
            li      t2, {eie_top}
__enable_all_guest_interrupts_f2:
            csrw    vsiselect, t0
            csrw    vsireg, t1
            addi    t0, t0, 2
            bne     t0, t2, __enable_all_guest_interrupts_f2

            # --- Leave VGEIN = 1 (home for VSEI delivery) ---
            li t0, (0x3f << 12)
            csrc hstatus, t0
            li t0, (1 << 12)
            csrs hstatus, t0

            # Enable hgeie[2] so hip[12]=SGEIP asserts when guest file 2 has a
            # pending interrupt. hgeie[1] stays 0 so guest file 1 (VSEI) never
            # raises SGEIP, keeping the VSEI and SGEI signals independent.
            li t0, (1 << 2)
            csrs hgeie, t0

            # Delegate SGEI (mideleg[12]) to HS so SGEI is serviced/checked in
            # HS rather than trapping to M. Per the priv spec (norm:mideleg_acc_h)
            # this bit is read-only one whenever GEILEN!=0, so SGEI is always
            # delegated past M to HS; on a target that hardwires it this csrs is
            # a no-op. Whisper only sets GEILEN from the top-level
            # ``guest_interrupt_count`` config tag (not ``imsic.guests``), so
            # unless that tag is present mideleg[12] is left writable/zero and
            # SGEI would otherwise trap to M. Setting it explicitly here keeps
            # SGEI delivery correct regardless of that tag, and is scoped to the
            # guest-IMSIC bring-up so non-hypervisor tests are unaffected.
            li t0, (1 << 12)
            csrs mideleg, t0
        """

        code += f"""
            # APLIC config
            # DomainCfg: IE = 1, DM = 1(MSI)
            li      t1, 0x80000104
            la      t0, MAPLIC_MMR_BASE_ADDR + 0
            sw      t1, 0(t0)
            la      t0, SAPLIC_MMR_BASE_ADDR + 0
            sw      t1, 0(t0)

            # Enable all APLIC input sources
            li      t1, 0x4     # SM EDGE1
            la      t2, MAPLIC_MMR_BASE_ADDR + 4
            la      t3, SAPLIC_MMR_BASE_ADDR + 4
            la      t0, MAPLIC_MMR_BASE_ADDR + {(self.pool.max_aplic_irq + 1) * 4}
__enable_aplic_interrupt_source:
            sw      t1, 0(t2)
            sw      t1, 0(t3)
            add     t2, t2, 4
            add     t3, t3, 4
            bne     t2, t0, __enable_aplic_interrupt_source

            # Route them as corresponding MSI to HART 0
            li      t1, 1
            la      t2, MAPLIC_MMR_BASE_ADDR + 0x3004
            la      t3, SAPLIC_MMR_BASE_ADDR + 0x3004
            li      t0, {self.pool.max_aplic_irq + 1}
__enable_aplic_interrupt_target:
            sw      t1, 0(t2)
            sw      t1, 0(t3)
            add     t2, t2, 4
            add     t3, t3, 4
            add     t1, t1, 1
            bne     t1, t0, __enable_aplic_interrupt_target

            # Enable all interrupts
            li      t0, 8
            csrs    mstatus, t0
            li      t0, 0xa00
            csrs    mie, t0
        """

        for intr_num in self.pool.ext_aplic_interrupts:
            intr = self.pool.ext_aplic_interrupts[intr_num]
            eiid = intr["eiid"]
            isr_func = intr["isr"]
            raw_mode = intr["mode"]
            # mode=v means S-APLIC domain with Guest Index routing; use saplic helpers
            mode = "s" if raw_mode == "v" else ("m" if raw_mode is None else raw_mode)

            if isr_func is not None:
                if eiid is None:
                    log.warning("No eiid specified for interrupt {intr_num}. Mapping to {intr_num}")
                    eiid = intr_num
                code += "\n"
                code += f"""
                    li a0, {eiid}
                    la a1, {isr_func}
                    jal __set_aplic_isr
                """

            state = intr["state"]
            if state is not None:
                code += "\n"
                code += f"""
                    li a0, {intr_num}
                    la a1, {state}
                    jal __set_{mode}aplic_eie
                """

            source_mode = intr["source_mode"]
            if source_mode is not None:
                code += "\n"
                code += f"""
                    li a0, {intr_num}
                    la a1, {source_mode}
                    jal __set_{mode}aplic_sourcecfg_sm
                """

            hart = intr["hart"]
            if hart is not None:
                code += "\n"
                code += f"""
                    li a0, {intr_num}
                    la a1, {hart}
                    jal __set_{mode}aplic_target_hartindex
                """

            if eiid is not None:
                code += "\n"
                code += f"""
                    li a0, {intr_num}
                    la a1, {eiid}
                    jal __set_{mode}aplic_target_eiid
                    """

            if raw_mode == "v":
                code += "\n"
                code += f"""
                    li a0, {intr_num}
                    li a1, 1
                    jal __set_saplic_target_guestindex
                    """

        return code

    def setup_tvec(self) -> str:
        """
        Generate code to setup mtvec and stvec.

        We always program both mtvec and stvec to the respective trap handlers.

        If running the code in VS mode, will be delegating exceptions to virtual supervior .
        Need to point vstvec to supervisor trap handler
        """

        code = ""
        if self.featmgr.c_used:
            code += "la sp, __c__stack_addr\n"
            code += "ld sp, 0(sp)\n"

        code += "\nloader__setup_tvec:\n"
        code += "la t0, trap_handler_m__trap_entry\n"
        code += "csrw mtvec, t0\n"
        if self.featmgr.is_feature_enabled("s"):
            if RV.RiscvPrivileges.SUPER in self.featmgr.supported_priv_modes:
                if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
                    stvec = "trap_handler_hs__trap_entry"
                else:
                    stvec = "trap_handler_s__trap_entry"
                code += f"la t0, {stvec}\n"
                code += "csrw stvec, t0\n"
                if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
                    code += "la t0, trap_handler_s__trap_entry\n"
                    code += "csrw vstvec, t0\n"

        return code

    def setup_stateen(self) -> str:
        """
        Generate code to setup stateen CSRs.
        Sets xstateen0 CSRs to configured values (default: -1 = all bits set).
        This allows access to all optional state (FP, Vector, etc.).
        """
        smstateen = self.featmgr.feature.is_enabled("smstateen")
        ssstateen = self.featmgr.feature.is_enabled("ssstateen")

        if not smstateen and not ssstateen:
            # No stateen CSRs to setup
            return ""

        code = """
loader__setup_stateen:
"""
        if smstateen:
            code += f"    li t0, {self.featmgr.mstateen}\n"
            code += "    csrw mstateen0, t0\n"

        if smstateen or ssstateen:
            code += f"    li t0, {self.featmgr.sstateen}\n"
            code += "    csrw sstateen0, t0\n"

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

        if self.featmgr.feature.is_supported("d"):
            fmv = "fmv.d.x"
            load = "fld"
        else:
            fmv = "fmv.w.x"
            load = "flw"

        if not use_fmv:
            code.append(self.zero_data.load_immediate("t0"))
        for i in range(32):
            if use_fmv:
                code.append(f"{fmv}  f{i}, x0")
            else:
                code.append(f"{load}   f{i}, 0(t0)")
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

    def setup_smrnmi(self) -> str:
        """
        Exit the reset-state NMI-handler context via mnret when Smrnmi is enabled.

        At reset mnstatus.NMIE=0 -- the hart boots as if it had just entered an
        NMI handler. Setting NMIE=1 with csrsi flips the bit but leaves the ISS's
        internal "in NMI handler" flag set (verified on Spike with _smrnmi in
        --isa: any subsequent trap, including a plain S-mode ecall, routes
        through the NMI vector (PC=0) instead of mtvec -- producing an
        instruction-access-fault cascade).

        mnret is the spec-defined atomic exit: clears the in-NMI flag, sets
        NMIE=1, restores mstatus from mnstatus.MNPP/MNPV, and branches to
        mnepc. After mnret, normal trap dispatch via mtvec/stvec works.

        The three CSR/mnret ops are emitted as raw .word so this block also
        assembles under clang-17 (riescuec compliance build), which rejects
        the mnstatus/mnepc/mnret mnemonics unless -march includes _smrnmi.
        """
        return """
loader__setup_smrnmi:
    la t0, loader__setup_smrnmi_after_mnret
    .word 0x74129073         # csrw mnepc (0x741), t0
    li t0, 0x1808            # mnstatus: MNPP=M (bits 12:11=0b11), NMIE=1 (bit 3)
    .word 0x74429073         # csrw mnstatus (0x744), t0
    .word 0x70200073         # mnret
loader__setup_smrnmi_after_mnret:
"""

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
