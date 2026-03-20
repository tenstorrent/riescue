# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, field, replace
from typing import TypeVar, Optional, Union, Callable, Any, List

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
    compiler_include_dir: Optional[Path] = None  # When set, added as -I <path> to compiler args

    # non-cmdline
    cpu_config: CpuConfig = field(default_factory=CpuConfig)  # This is really a dict for cpu config, later it should be a typed class
    memory: Memory = field(default_factory=Memory)
    feature: FeatureDiscovery = field(default_factory=lambda: FeatureDiscovery({}))
    hooks: dict[RV.HookPoint, list[Hookable]] = field(default_factory=lambda: defaultdict(list))
    # Per-vector default interrupt handler overrides registered via register_default_handler().
    # Maps vec_num -> (label, assembly_fn) where assembly_fn(featmgr) -> str.
    interrupt_handler_overrides: dict[int, tuple[str, Hookable]] = field(default_factory=dict)

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

    supported_priv_modes: set[RV.RiscvPrivileges] = field(default_factory=lambda: {RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER, RV.RiscvPrivileges.USER})

    reset_pc: int = 0x8000_0000
    io_htif_addr: Optional[int] = None  # This could probably be use a better name, e.g. eot_addr + note that it defaults to htif in memmap
    io_maplic_addr: Optional[int] = None
    io_maplic_size: Optional[int] = None
    io_saplic_addr: Optional[int] = None
    io_saplic_size: Optional[int] = None
    io_imsic_mfile_addr: Optional[int] = None
    io_imsic_mfile_stride: Optional[int] = None
    io_imsic_sfile_addr: Optional[int] = None
    io_imsic_sfile_stride: Optional[int] = None
    eot_pass_value: int = 1
    eot_fail_value: int = 3

    # MP mode
    mp: RV.RiscvMPEnablement = RV.RiscvMPEnablement.MP_ON
    mp_mode: RV.RiscvMPMode = RV.RiscvMPMode.MP_PARALLEL
    parallel_scheduling_mode: RV.RiscvParallelSchedulingMode = RV.RiscvParallelSchedulingMode.ROUND_ROBIN
    num_cpus: int = 1

    # Generation options
    single_assembly_file: bool = False
    force_alignment: bool = False
    c_used: bool = False
    more_os_pages: bool = False
    small_bss: bool = False
    big_bss: bool = False
    big_endian: bool = False
    add_gcc_cstdlib_sections: bool = False
    addrgen_limit_indices: bool = False
    code_offset: Optional[int] = None  # unused?
    randomize_code_location: bool = False
    identity_map_code: bool = False
    repeat_times: int = 3
    cfiles: Optional[list[Path]] = None
    inc_path: Optional[list[Path]] = None
    selfcheck: bool = False
    save_restore_gprs: bool = False
    log_test_execution: bool = False

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
    excp_hooks: bool = False
    interrupts_enabled: bool = True
    skip_instruction_for_unexpected: bool = False

    # CSR R/W handling
    machine_mode_jump_table_for_csr_rw: str = "csr_machine_0"
    supervisor_mode_jump_table_for_csr_rw: str = "csr_super_0"

    # PTE handling (unified read/write)
    machine_mode_jump_table_for_pte: str = "pte_machine_0"

    # PMA / PMP
    setup_pmp: bool = False
    needs_pma: bool = False
    num_pmas: int = 16

    # Debug mode (RISC-V Debug Spec Ch.4): ;#discrete_debug_test() and/or config
    debug_mode: bool = False
    # debug_rom_address: must be identity-mapped (VA=PA) so the ROM is reachable when the hart enters debug mode.
    debug_rom_address: Optional[int] = None
    debug_rom_size: Optional[int] = None

    # CSR Initialization
    # FIXME: This should probably be managed by it's own configuration class instead of being part of FeatMgr
    csr_init: Optional[str] = None
    csr_init_mask: Optional[str] = None
    no_random_csr_reads: bool = False
    max_random_csr_reads: int = 16
    random_machine_csr_list: Optional[str] = None
    random_supervisor_csr_list: Optional[str] = None
    random_user_csr_list: Optional[str] = None

    medeleg: int = 0xFFFFFFFF_FFFFF0FF  # Don't delegate any ecall exceptions (bits 8-11 clear)
    mideleg: int = 0xFFFFFFFF_FFFFFFFF
    hedeleg: int = 0xFFFFFFFF_FFFFF0FF  # Don't delegate any ecall exceptions (bits 8-11 clear)
    hideleg: int = 0xFFFFFFFF_FFFFFFFF
    menvcfg: int = 0
    henvcfg: int = 0
    senvcfg: int = 0
    mstateen: int = -1
    hstateen: int = -1
    sstateen: int = -1

    # enabled features?
    pbmt_ncio: bool = False
    svadu: bool = False
    private_maps: bool = False

    # Feature randomization?
    a_d_bit_randomization: int = 0  # unused?
    pbmt_ncio_randomization: int = 0  # unused?
    fs_randomization: int = 0
    fs_randomization_values: List[int] = field(default_factory=lambda: [2])  # 0=Off, 1=Initial, 2=Clean, 3=Dirty
    vs_randomization: int = 0
    vs_randomization_values: List[int] = field(default_factory=lambda: [2])
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

        # Sdtrig (Trigger Module)
        presence["SDTRIG_SUPPORTED"] = 1 if self.is_feature_supported("sdtrig") and self.is_feature_enabled("sdtrig") else 0

        # Test generation (probabilities 0-100)
        presence["SECURE_ACCESS_PROBABILITY"] = self.secure_access_probability
        presence["SECURE_PT_PROBABILITY"] = self.secure_pt_probability
        presence["A_D_BIT_RANDOMIZATION"] = self.a_d_bit_randomization
        presence["PBMT_NCIO_RANDOMIZATION"] = self.pbmt_ncio_randomization
        presence["FS_RANDOMIZATION"] = self.fs_randomization
        presence["VS_RANDOMIZATION"] = self.vs_randomization
        presence["ALL_4KB_PAGES"] = self.all_4kb_pages

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

    def register_default_handler(self, vec: int, label: str, assembly: Hookable) -> None:
        """
        Override the default interrupt handler for a vector.

        Replaces the framework's built-in handler for ``vec`` with a user-supplied label and
        assembly body for the **entire test**.  The handler is active for all discrete_tests
        that do not install a per-segment handler via ``add_custom_intr_handler()``.

        Called from :meth:`riescue.dtest_framework.config.conf.Conf.add_hooks`.

        :param vec: Interrupt vector number (e.g. 1 for SSI, 3 for MSI, 25 for ZKR).
        :param label: Assembly label for the handler (must be unique in the test).
        :param assembly: Callable ``(featmgr: FeatMgr) -> str`` returning the handler body.
            The body should end with the appropriate ``xret`` (``mret`` for M-mode vectors).

        .. code-block:: python

            from riescue import Conf, FeatMgr

            def my_msi_handler(featmgr: FeatMgr) -> str:
                return \"\"\"
                    csrci mip, 8   # clear MSIP
                    mret
                \"\"\"

            class MyConf(Conf):
                def add_hooks(self, featmgr: FeatMgr) -> None:
                    featmgr.register_default_handler(3, "my_msi_handler", my_msi_handler)

            def setup() -> Conf:
                return MyConf()
        """
        if vec in self.interrupt_handler_overrides:
            existing_label = self.interrupt_handler_overrides[vec][0]
            log.warning(f"register_default_handler: vec {vec} already has handler '{existing_label}', " f"overwriting with '{label}'")
        self.interrupt_handler_overrides[vec] = (label, assembly)

    def register_hook(self, hook_point: RV.HookPoint, hook: Hookable):
        """
        Register a hook for a given hook point
        Multiple hooks can be registered for the same hook point. The order of hooks registered is preserved.

        :param hook_point: The :py:class:`riescue.lib.enums.HookPoint` enum value to register the hook.
        :param hook: Callable hook function to register; returns a string of assembly code

        .. code-block:: python

            def hook(featmgr: FeatMgr) -> str:
                return "nop"
            featmgr.register_hook(RV.HookPoint.PRE_PASS, hook)

        """
        self.hooks[hook_point].append(hook)

    def call_hook(self, hook_point: RV.HookPoint) -> str:
        """Get a hook for a given hook point"""
        if hook_point not in self.hooks:
            log.debug(f"Hook point {hook_point} was not registered")
            return ""
        else:
            return "\n".join([hook(self) for hook in self.hooks[hook_point]])
