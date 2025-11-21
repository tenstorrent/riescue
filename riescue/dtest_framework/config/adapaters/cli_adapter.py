# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Union, Sequence
from pathlib import Path
from argparse import Namespace

import riescue.lib.enums as RV
from .adapter import Adapter
from ..candidate import Candidate

if TYPE_CHECKING:
    from ..builder import FeatMgrBuilder

log = logging.getLogger(__name__)


class CliAdapter(Adapter):
    """
    Adapater for test environment configuration from command line arguments.

    Expects command line arguments to be an argparse.Namespace object and all fields are expected to be ``Optional`` typed.
    """

    def apply(self, builder: FeatMgrBuilder, src: Namespace) -> FeatMgrBuilder:
        cmdline = src
        featmgr = builder.featmgr

        if cmdline.tohost_nonzero_terminate is not None:
            featmgr.tohost_nonzero_terminate = cmdline.tohost_nonzero_terminate

        if cmdline.single_assembly_file is not None:
            featmgr.single_assembly_file = cmdline.single_assembly_file
        if cmdline.force_alignment is not None:
            featmgr.force_alignment = cmdline.force_alignment
        if cmdline.c_used is not None:
            featmgr.c_used = cmdline.c_used
        if cmdline.cfile is not None:
            featmgr.cfiles = cmdline.cfile
        if cmdline.small_bss is not None:
            featmgr.small_bss = cmdline.small_bss
        if cmdline.big_bss is not None:
            featmgr.big_bss = cmdline.big_bss
        if cmdline.big_endian is not None:
            featmgr.big_endian = cmdline.big_endian

        if cmdline.more_os_pages is not None:
            featmgr.more_os_pages = cmdline.more_os_pages
        if cmdline.add_gcc_cstdlib_sections is not None:
            featmgr.add_gcc_cstdlib_sections = cmdline.add_gcc_cstdlib_sections
        if cmdline.addrgen_limit_indices is not None:
            featmgr.addrgen_limit_indices = cmdline.addrgen_limit_indices
        if cmdline.randomize_code_location is not None:
            featmgr.randomize_code_location = cmdline.randomize_code_location
        if cmdline.repeat_times is not None:
            featmgr.repeat_times = cmdline.repeat_times

        if cmdline.fe_tb is not None:
            featmgr.fe_tb = cmdline.fe_tb

        if cmdline.linux_mode is not None:
            featmgr.linux_mode = cmdline.linux_mode
        if cmdline.enable_machine_paging is not None:
            featmgr.enable_machine_paging = cmdline.enable_machine_paging
        if cmdline.bringup_pagetables is not None:
            featmgr.bringup_pagetables = cmdline.bringup_pagetables

        if cmdline.reserve_partial_phys_memory is not None:
            featmgr.reserve_partial_phys_memory = cmdline.reserve_partial_phys_memory
        if cmdline.all_4kb_pages is not None:
            featmgr.all_4kb_pages = cmdline.all_4kb_pages
        if cmdline.disallow_mmio is not None:
            featmgr.disallow_mmio = cmdline.disallow_mmio
        if cmdline.addrgen_limit_way_predictor_multihit is not None:
            featmgr.addrgen_limit_way_predictor_multihit = cmdline.addrgen_limit_way_predictor_multihit

        if cmdline.user_interrupt_table is not None:
            featmgr.user_interrupt_table = cmdline.user_interrupt_table
        if cmdline.excp_hooks is not None:
            featmgr.excp_hooks = cmdline.excp_hooks
        if cmdline.interrupts_enabled is not None:
            log.warning("(--interrupts_enabled is deprecated. Use --interrupts_disabled instead.)")
            featmgr.interrupts_enabled = cmdline.interrupts_enabled
        if cmdline.interrupts_disabled is not None:
            featmgr.interrupts_enabled = False
        if cmdline.skip_instruction_for_unexpected is not None:
            featmgr.skip_instruction_for_unexpected = cmdline.skip_instruction_for_unexpected
        if cmdline.disable_wfi_wait is not None:
            featmgr.disable_wfi_wait = cmdline.disable_wfi_wait

        if cmdline.setup_pmp is not None:
            featmgr.setup_pmp = cmdline.setup_pmp
        if cmdline.needs_pma is not None:
            featmgr.needs_pma = cmdline.needs_pma
        if cmdline.num_pmas is not None:
            featmgr.num_pmas = cmdline.num_pmas

        if cmdline.setup_stateen is not None:
            featmgr.setup_stateen = cmdline.setup_stateen
        if cmdline.vmm_hooks is not None:
            featmgr.vmm_hooks = cmdline.vmm_hooks

        if cmdline.no_random_csr_reads is not None:
            featmgr.no_random_csr_reads = cmdline.no_random_csr_reads

        if cmdline.eot_pass_value is not None:
            featmgr.eot_pass_value = cmdline.eot_pass_value
        if cmdline.eot_fail_value is not None:
            featmgr.eot_fail_value = cmdline.eot_fail_value
        if cmdline.switch_to_user_page is not None:
            featmgr.switch_to_user_page = cmdline.switch_to_user_page
        if cmdline.switch_to_super_page is not None:
            featmgr.switch_to_super_page = cmdline.switch_to_super_page
        if cmdline.switch_to_machine_page is not None:
            featmgr.switch_to_machine_page = cmdline.switch_to_machine_page

        if cmdline.menvcfg is not None:
            featmgr.menvcfg = cmdline.menvcfg
        if cmdline.henvcfg is not None:
            featmgr.henvcfg = cmdline.henvcfg
        if cmdline.senvcfg is not None:
            featmgr.senvcfg = cmdline.senvcfg
        if cmdline.medeleg is not None:
            featmgr.medeleg = cmdline.medeleg
        if cmdline.mideleg is not None:
            featmgr.mideleg = cmdline.mideleg
        if cmdline.hedeleg is not None:
            featmgr.hedeleg = cmdline.hedeleg
        if cmdline.hideleg is not None:
            featmgr.hideleg = cmdline.hideleg

        if cmdline.csr_init is not None:
            featmgr.csr_init = cmdline.csr_init
        if cmdline.csr_init_mask is not None:
            featmgr.csr_init_mask = cmdline.csr_init_mask
        if cmdline.random_user_csr_list is not None:
            featmgr.random_user_csr_list = cmdline.random_user_csr_list

        # Add missing fields that need None checks
        if cmdline.max_logger_file_gb is not None:
            featmgr.max_logger_file_gb = cmdline.max_logger_file_gb
        if cmdline.code_offset is not None:
            featmgr.code_offset = cmdline.code_offset
        if cmdline.max_random_csr_reads is not None:
            featmgr.max_random_csr_reads = cmdline.max_random_csr_reads
        if cmdline.random_machine_csr_list is not None:
            featmgr.random_machine_csr_list = cmdline.random_machine_csr_list
        if cmdline.random_supervisor_csr_list is not None:
            featmgr.random_supervisor_csr_list = cmdline.random_supervisor_csr_list

        # Check command line for overrides that can be set by test header
        if cmdline.wysiwyg is not None:
            featmgr.wysiwyg = cmdline.wysiwyg
        if cmdline.a_d_bit_randomization is not None:
            featmgr.a_d_bit_randomization = cmdline.a_d_bit_randomization
        if cmdline.secure_access_probability is not None:
            featmgr.secure_access_probability = cmdline.secure_access_probability
        if cmdline.secure_pt_probability is not None:
            featmgr.secure_pt_probability = cmdline.secure_pt_probability
        if cmdline.pbmt_ncio_randomization is not None:
            featmgr.pbmt_ncio_randomization = cmdline.pbmt_ncio_randomization
        if cmdline.num_cpus is not None:
            featmgr.num_cpus = cmdline.num_cpus

        if cmdline.private_maps is not None:
            featmgr.private_maps = cmdline.private_maps

        # overwritting random Candidates
        def to_candidate(enum: Union[RV.MyEnum, Sequence[RV.MyEnum]]) -> Candidate:
            "Helper function to convert enum to candidate"
            if isinstance(enum, list):
                return Candidate(*enum)
            else:
                return Candidate(enum)

        if cmdline.mp:
            builder.mp = to_candidate(RV.RiscvMPEnablement.str_to_enum(cmdline.mp))
        elif featmgr.num_cpus > 1:
            builder.mp = to_candidate(RV.RiscvMPEnablement.MP_ON)

        if cmdline.mp_mode:
            builder.mp_mode = to_candidate(RV.RiscvMPMode.str_to_enum(cmdline.mp_mode))

        if cmdline.parallel_scheduling_mode:
            builder.parallel_scheduling_mode = to_candidate(RV.RiscvParallelSchedulingMode.str_to_enum(cmdline.parallel_scheduling_mode))

        # FIXME: Old behavior was to only allow virtualized if --test_env_any is specified. Should remove --test_env_any when changes are stable
        # --test_env should always win, otherwise only use virtualized if --test_env_any is specified
        if cmdline.test_env:
            builder.env = to_candidate(RV.RiscvTestEnv.str_to_enum(cmdline.test_env))
        elif cmdline.test_env_any:
            builder.env = to_candidate([RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED, RV.RiscvTestEnv.TEST_ENV_BARE_METAL])
        else:
            builder.env = to_candidate(RV.RiscvTestEnv.TEST_ENV_BARE_METAL)

        if cmdline.supported_priv_modes:
            # cmdline restricts choices to MSU, M, MS, MU
            supported_priv_modes = set()
            if "M" in cmdline.supported_priv_modes:
                supported_priv_modes.add(RV.RiscvPrivileges.MACHINE)
            if "S" in cmdline.supported_priv_modes:
                supported_priv_modes.add(RV.RiscvPrivileges.SUPER)
            if "U" in cmdline.supported_priv_modes:
                supported_priv_modes.add(RV.RiscvPrivileges.USER)
            featmgr.supported_priv_modes = supported_priv_modes

        if cmdline.test_priv_mode:
            builder.priv_mode = to_candidate(RV.RiscvPrivileges[cmdline.test_priv_mode.upper()])
            # if self.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            #     #FIXME: We currently do not support USER mode in virtualized mode
            #     self.priv_mode = RV.RiscvPrivileges.PRIV_SUPER

        if cmdline.test_secure_mode == "on":
            builder.secure_mode = to_candidate(RV.RiscvSecureModes.SECURE)
            featmgr.setup_pmp = True
        elif cmdline.test_secure_mode == "off":
            builder.secure_mode = to_candidate(RV.RiscvSecureModes.NON_SECURE)
        elif cmdline.test_secure_mode in ["random", "any"]:
            # Use weighted approach for 20% chance of secure mode
            builder.secure_mode = Candidate.with_weights([(RV.RiscvSecureModes.SECURE, 0.2), (RV.RiscvSecureModes.NON_SECURE, 0.8)])

        # Machine mode only allows PAGING_DISABLE and takes priority over paging/paging_g mode from commandline
        if cmdline.fe_tb:
            if builder.priv_mode == RV.RiscvPrivileges.MACHINE:
                log.warning("FE_TB does not support paging mode, forcing paging and paging_g to disable")
                builder.paging_mode = to_candidate(RV.RiscvPagingModes.DISABLE)
                builder.paging_g_mode = to_candidate(RV.RiscvPagingModes.DISABLE)
        else:
            if cmdline.test_paging_mode:
                if cmdline.test_paging_mode.upper() == "ANY" or cmdline.test_paging_mode.upper() == "ENABLE":
                    pass
                else:
                    builder.paging_mode = to_candidate(RV.RiscvPagingModes[cmdline.test_paging_mode.upper()])
            # Also force g-stage paging mode from commandline

            if cmdline.test_paging_g_mode:
                if cmdline.test_paging_g_mode.upper() == "ANY" or cmdline.test_paging_g_mode.upper() == "ENABLE":
                    pass
                else:
                    builder.paging_g_mode = to_candidate(RV.RiscvPagingModes[cmdline.test_paging_g_mode.upper()])
            elif builder.priv_mode == RV.RiscvPrivileges.MACHINE:
                # Disable guest stage paging mode if in machine, unless explicitly set (this will probably not work if manually set)
                # FIXME: This setup should be revisited with an improved configuration system
                builder.paging_g_mode = to_candidate(RV.RiscvPagingModes.DISABLE)

        if cmdline.counter_event_path:
            featmgr.counter_event_path = Path(cmdline.counter_event_path)

        if cmdline.tohost:
            if cmdline.tohost == "auto":
                log.warning("Using auto for htif address. This will cause htif to be allocated in dram.")
                featmgr.io_htif_addr = None
            else:
                featmgr.io_htif_addr = int(cmdline.tohost, 0)

        # Only override if it was previously set to random.
        # This should probably be a Candidate so it's clearer it's a random choice
        if cmdline.deleg_excp_to is not None:
            builder.deleg_excp_to = to_candidate(RV.RiscvPrivileges[cmdline.deleg_excp_to.upper()])
        return builder
