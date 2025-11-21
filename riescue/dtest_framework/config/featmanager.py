# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from pathlib import Path
from dataclasses import dataclass, field, replace
from typing import TypeVar, Optional, Union, Callable, Any

import riescue.lib.enums as RV
from riescue.lib.feature_discovery import FeatureDiscovery
from .cpu_config import CpuConfig
from .memory import Memory

log = logging.getLogger(__name__)

T = TypeVar("T")
Hookable = Callable[["FeatMgr"], str]  #: shorthand for function that takes the FeatMgr and returns a string of assembly code

"""
The package centralises unrelated concerns in a single monolithic FeatMgr;
its constructor reads files twice, performs random decisions, mutates its arguments, creates attributes dynamically with setattr, and mixes configuration storage with policy.
TestConfig suffers similarly, keeping file-I/O, randomisation, and validation in the same class while still relying on raw strings instead of enums for many fields.
Both classes violate single-responsibility, make unit testing difficult, and hide errors because unknown keys are silently accepted.

Separate immutable data objects from the logic that produces them, load each configuration layer exactly once,
replace dynamic attributes with explicit dataclasses or Pydantic models, and use a builder or layered strategy to compose the final runtime view.
Randomisation and feature discovery should be injected as independent strategies rather than run inside constructors.
Validation should run immediately after all sources are merged, raising on unexpected keys.
This yields testable, declarative, and extensible configuration.

want to support
```
class FeaMgrBuilder:
    deleg_excp_to: str = candidate("machine", "super", "vs")
```

so that candidates can be filtered later,. e.g.
```
if not featmgr.virtualized:
    featmgr.deleg_excp_to.candidates.discard("vs")
```

"""


def feature_field(*flags: str, default: bool = False, help: str = ""):
    """
    Used to create a feature entry in the ``FeatMgr``. Adds support for inferred CLI switches, documentation, etc.

    Keeps help and docstrings in one place for argparse and sphinx




    :param flags: flag strings passed to ``ArgumentParser.add_argument`` (``--foo -f``)
    :param default: default value of the option
    :param help: help text shown by ``--help``
    :returns: a fully-populated dataclass field
    """
    return field(
        default=default,
        metadata=dict(
            cli=flags,
            help=help,
            action="store_true",
        ),
    )


@dataclass
class FeatMgr:
    """
    Configuration manager; aata structure containing configuration for test generation.

    ``FeatMgrBuilder`` class provides interface for constructing ``FeatMgr`` objects.
    Users can choose to override any fields after construction.
    """

    # Paths
    counter_event_path: Optional[Path] = None

    # non-cmdline
    cpu_config: CpuConfig = field(default_factory=CpuConfig)  # This is really a dict for cpu config, later it should be a typed class
    memory: Memory = field(default_factory=Memory)
    feature: FeatureDiscovery = field(default_factory=lambda: FeatureDiscovery({}))
    hooks: dict[RV.HookPoint, Callable[[FeatMgr], str]] = field(default_factory=dict)

    # Labels
    trap_handler_label: str = "trap_entry"
    syscall_table_label: str = "syscall_table"
    check_exception_label: str = "check_exception_cause"
    trap_exit_label: str = "trap_exit"

    # Run options
    tohost_nonzero_terminate: bool = False
    max_logger_file_gb: float = 1

    # Test options
    priv_mode: RV.RiscvPrivileges = RV.RiscvPrivileges.MACHINE
    paging_mode: RV.RiscvPagingModes = RV.RiscvPagingModes.DISABLE
    paging_g_mode: RV.RiscvPagingModes = RV.RiscvPagingModes.DISABLE
    env: RV.RiscvTestEnv = RV.RiscvTestEnv.TEST_ENV_BARE_METAL
    arch: RV.RiscvBaseArch = RV.RiscvBaseArch.ARCH_RV64I
    secure_mode: RV.RiscvSecureModes = RV.RiscvSecureModes.NON_SECURE
    deleg_excp_to: RV.RiscvPrivileges = RV.RiscvPrivileges.MACHINE

    supported_priv_modes: set[RV.RiscvPrivileges] = field(default_factory=lambda: {RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER, RV.RiscvPrivileges.USER})

    reset_pc: int = 0x8000_0000
    io_htif_addr: Optional[int] = None  # This could probably be use a better name, e.g. eot_addr + note that it defaults to htif in memmap
    eot_pass_value: int = 1
    eot_fail_value: int = 3
    more_os_pages: bool = False

    # MP mode
    mp: RV.RiscvMPEnablement = RV.RiscvMPEnablement.MP_ON
    mp_mode: RV.RiscvMPMode = RV.RiscvMPMode.MP_PARALLEL
    parallel_scheduling_mode: RV.RiscvParallelSchedulingMode = RV.RiscvParallelSchedulingMode.ROUND_ROBIN
    num_cpus: int = 1

    # Generation options
    single_assembly_file: bool = False
    force_alignment: bool = False
    c_used: bool = False
    small_bss: bool = False
    big_bss: bool = False
    big_endian: bool = False
    add_gcc_cstdlib_sections: bool = False
    addrgen_limit_indices: bool = False
    code_offset: Optional[int] = None  # unused?
    randomize_code_location: bool = False
    repeat_times: int = 3
    cfiles: Optional[list[Path]] = None

    # bringup mode
    fe_tb: bool = False
    wysiwyg: bool = False
    linux_mode: bool = False
    enable_machine_paging: bool = False
    bringup_pagetables: bool = False

    # Address generation
    linear_addr_bits: int = 57
    physical_addr_bits: int = 52
    reserve_partial_phys_memory: bool = False
    all_4kb_pages: bool = False
    disallow_mmio: bool = False
    addrgen_limit_way_predictor_multihit: bool = False

    # Trap handling
    switch_to_machine_page: str = "code_machine_0"
    switch_to_super_page: str = "code_super_0"
    switch_to_user_page: str = "code_user_0"
    user_interrupt_table: bool = False
    excp_hooks: bool = False
    interrupts_enabled: bool = True
    skip_instruction_for_unexpected: bool = False
    disable_wfi_wait: bool = False

    # CSR R/W handling
    machine_mode_jump_table_for_csr_rw: str = "csr_machine_0"
    supervisor_mode_jump_table_for_csr_rw: str = "csr_super_0"

    # PTE handling (unified read/write)
    machine_mode_jump_table_for_pte: str = "pte_machine_0"

    # PMA / PMP
    setup_pmp: bool = False
    needs_pma: bool = False
    num_pmas: int = 16

    # Hypervisor
    setup_stateen: bool = False
    vmm_hooks: bool = False
    # hypervisor: bool = True

    # CSR Initialization
    # FIXME: This should probably be managed by it's own configuration class instead of being part of FeatMgr
    csr_init: Optional[str] = None
    csr_init_mask: Optional[str] = None
    no_random_csr_reads: bool = False
    max_random_csr_reads: int = 16
    random_machine_csr_list: Optional[str] = None
    random_supervisor_csr_list: Optional[str] = None
    random_user_csr_list: Optional[str] = None

    medeleg: int = 0xFFFFFFFF_FFFFFFFF
    mideleg: int = 0xFFFFFFFF_FFFFFFFF
    hedeleg: int = 0xFFFFFFFF_FFFFFFFF
    hideleg: int = 0xFFFFFFFF_FFFFFFFF
    menvcfg: int = 0
    henvcfg: int = 0
    senvcfg: int = 0

    # enabled features?
    pbmt_ncio: bool = False
    svadu: bool = False
    private_maps: bool = False

    # Feature randomization?
    a_d_bit_randomization: int = 0  # unused?
    pbmt_ncio_randomization: int = 0  # unused?
    secure_access_probability: int = 30
    secure_pt_probability: int = 0

    # unused?
    opts: dict[str, str] = field(default_factory=dict)

    def duplicate(self) -> "FeatMgr":
        """
        Duplicate / deepcopy a FeatMgr instance.
        """
        # reuse CpuConfig, Memory since they are frozen dataclasses
        new_featmgr = replace(self)
        new_featmgr.feature = self.feature
        return new_featmgr

    def get_summary(self) -> dict[str, Union[bool, int]]:
        """
        Returns an array representation of feature presence.

        """
        # FIXME: Should this be a dataclass with a dedicated generate .equ method?
        presence: dict[str, Union[bool, int]] = dict()
        priv_mode = self.priv_mode
        presence["PRIV_MODE_MACHINE"] = priv_mode == RV.RiscvPrivileges.MACHINE
        presence["PRIV_MODE_SUPER"] = priv_mode == RV.RiscvPrivileges.SUPER
        presence["PRIV_MODE_USER"] = priv_mode == RV.RiscvPrivileges.USER

        presence["SECURE_MODE"] = bool(self.secure_mode)

        env = self.env
        presence["ENV_BARE_METAL"] = env == RV.RiscvTestEnv.TEST_ENV_BARE_METAL
        presence["ENV_VIRTUALIZED"] = env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED

        paging_mode = self.paging_mode
        presence["PAGING_MODE_DISABLE"] = paging_mode == RV.RiscvPagingModes.DISABLE
        presence["PAGING_MODE_SV32"] = paging_mode == RV.RiscvPagingModes.SV32
        presence["PAGING_MODE_SV39"] = paging_mode == RV.RiscvPagingModes.SV39
        presence["PAGING_MODE_SV48"] = paging_mode == RV.RiscvPagingModes.SV48
        presence["PAGING_MODE_SV57"] = paging_mode == RV.RiscvPagingModes.SV57

        presence["NUM_CPUS"] = self.num_cpus
        presence["MP_ENABLED"] = self.mp == RV.RiscvMPEnablement.MP_ON
        presence["MP_SIMULTANEOUS"] = self.mp_mode == RV.RiscvMPMode.MP_SIMULTANEOUS
        presence["MP_PARALLEL"] = self.mp_mode == RV.RiscvMPMode.MP_PARALLEL
        presence["MP_PARALLEL_SCHEDULING_MODE_ROUND_ROBIN"] = self.parallel_scheduling_mode == RV.RiscvParallelSchedulingMode.ROUND_ROBIN
        presence["MP_PARALLEL_SCHEDULING_MODE_EXHAUSTIVE"] = self.parallel_scheduling_mode == RV.RiscvParallelSchedulingMode.EXHAUSTIVE

        # Export g-stage paging modes
        paging_g_mode = self.paging_g_mode
        presence["PAGING_G_MODE_DISABLE"] = paging_g_mode == RV.RiscvPagingModes.DISABLE
        presence["PAGING_G_MODE_SV32"] = paging_g_mode == RV.RiscvPagingModes.SV32
        presence["PAGING_G_MODE_SV39"] = paging_g_mode == RV.RiscvPagingModes.SV39
        presence["PAGING_G_MODE_SV48"] = paging_g_mode == RV.RiscvPagingModes.SV48
        presence["PAGING_G_MODE_SV57"] = paging_g_mode == RV.RiscvPagingModes.SV57

        # Add pbmt information
        presence["PBMT_NCIO"] = self.pbmt_ncio

        return presence

    def mp_mode_on(self) -> bool:
        return self.mp == RV.RiscvMPEnablement.MP_ON

    #  Legacy methods for backward compatibility - delegate to FeatureDiscovery
    def is_feature_supported(self, feature: str) -> bool:
        """Check if a feature is supported - delegate to FeatureDiscovery"""
        return self.cpu_config.features.is_feature_supported(feature)

    def is_feature_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled - delegate to FeatureDiscovery"""
        return self.cpu_config.features.is_feature_enabled(feature)

    def get_feature_randomize(self, feature: str) -> int:
        """Get the randomization probability for a feature - delegate to FeatureDiscovery"""
        return self.cpu_config.features.get_feature_randomize(feature)

    def get_misa_bits(self) -> int:
        """Get MISA bits based on enabled features"""
        misa = 0

        # Base ISA
        if self.cpu_config.features.is_feature_enabled("rv64"):
            misa |= 1 << 62  # MXL = 2 for RV64
        elif self.cpu_config.features.is_feature_enabled("rv32"):
            misa |= 1 << 30  # MXL = 1 for RV32

        # Base extensions
        extension_bits = {"i": 8, "m": 12, "a": 0, "f": 5, "d": 3, "c": 2, "v": 21}  # I  # M  # A  # F  # D  # C  # V

        for ext, bit in extension_bits.items():
            if self.cpu_config.features.is_feature_enabled(ext):
                misa |= 1 << bit

        # Additional extensions (Z*)
        for feature in self.cpu_config.features.features:
            if feature.startswith("z"):
                # Map Z extensions to their MISA bits
                # This mapping should be expanded based on your specific Z extensions
                z_bits = {"zfh": 5, "zvfh": 5, "zba": 0, "zbb": 0, "zbs": 0, "zfbfmin": 5, "zvbb": 21, "zbc": 21, "zvfbfmin": 5}  # F  # F  # A  # A  # A  # F  # V  # V  # F
                if feature in z_bits and self.cpu_config.features.is_feature_enabled(feature):
                    misa |= 1 << z_bits[feature]

        return misa

    def get_compiler_march_string(self) -> str:
        """Generate a compiler march string from enabled features"""
        return self.cpu_config.features.get_compiler_march_string()

    def register_hook(self, hook_point: RV.HookPoint, hook: Hookable):
        """
        Register a hook for a given hook point

        :param hook_point: The :py:class:`riescue.lib.enums.HookPoint` enum value to register the hook.
        :param hook: Callable hook function to register; returns a string of assembly code

        .. code-block:: python

            def hook(featmgr: FeatMgr) -> str:
                return "nop"
            featmgr.register_hook(RV.HookPoint.PRE_PASS, hook)

        """
        self.hooks[hook_point] = hook

    def call_hook(self, hook_point: RV.HookPoint) -> str:
        """Get a hook for a given hook point"""
        if hook_point not in self.hooks:
            log.debug(f"Hook point {hook_point} was not registered")
            return ""
        else:
            return self.hooks[hook_point](self)
