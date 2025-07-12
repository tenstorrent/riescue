#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Feature manager holds configurations of the design.
Dynamically adjusted to reflect user test configurations and queried by other modules to
determine state.
"""

import json
import logging
from pathlib import Path
from argparse import Namespace
from dataclasses import dataclass, field

import riescue.dtest_framework.lib.addrgen as addrgen
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.lib.memory import Memory
from riescue.dtest_framework.parser import PmaInfo, PmpInfo
from riescue.lib.feature_discovery import FeatureDiscovery

log = logging.getLogger(__name__)

# Documentation on available configurations. Move to schema later
configs = {
    "mmap": "memory map configuration",
}


def get_int(value):
    if isinstance(value, int):
        return value
    elif isinstance(value, str):
        return int(value, 0)
    else:
        raise RuntimeError(f"Can't extract int from {value}")


# FIXME: Move this to a different file?
class TestConfig:
    """
    This class implements configuration related methods from the header.
      - get the test header information from Parser
      - pick the correct test configuration based on the headers
    """

    def __init__(self, rng: RandNum, cmdline_args=None):
        self.env = RV.RiscvTestEnv.TEST_ENV_BARE_METAL
        self.num_cpus = 1
        self.priv_mode = None
        self.secure_mode = False
        self.features = {}
        self.opts = {}
        self.arch = RV.RiscvBaseArch.ARCH_RV32I
        self.paging_mode = RV.RiscvPagingModes.SV39
        self.mp = RV.RiscvMPEnablement.MP_OFF
        self.mp_mode = RV.RiscvMPMode.MP_SIMULTANEOUS
        self.parallel_scheduling_mode = RV.RiscvParallelSchedulingMode.ROUND_ROBIN
        self.paging_g_mode = RV.RiscvPagingModes.DISABLE
        self.cmdline_args = cmdline_args
        self.rng = rng

        self.package_path = Path(__file__).parents[1]

    def setup_test(self, **kwargs):
        """
        Setup test level configuration
          - number of threads
          - riscv arch
        """
        # Handle ;#test.cpus
        self.setup_num_cpus(kwargs["cpu_header"])

        # Handle ;#test.arch
        self.setup_arch(kwargs["arch_header"])

        # Handle ;#test.env
        self.setup_env(kwargs["env_header"])

        # Handle ;#test.priv
        self.setup_priv(kwargs["priv_header"])

        # Handle ;#test.secure
        self.setup_secure_mode(kwargs["secure_header"])

        # Handle ;#test.paging
        self.setup_paging_mode(kwargs["paging_header"])

        # Handle ;#test.mp_mode
        self.setup_mp_mode(kwargs["mp_mode_header"])

        self.setup_parallel_scheduling_mode(kwargs["parallel_scheduling_mode_header"])

        # Handle ;#test.mp
        self.setup_mp(kwargs["mp_header"])

        # Handle ;#test.paging_g
        self.setup_paging_mode(kwargs["paging_g_header"], g_stage=True)

        # Handle ;#test.features
        self.setup_features(kwargs["features"])

        # Handle ;#test.opts
        self.setup_test_opts(kwargs.get("opts", ""))

    def setup_arch(self, arch_header: str):
        entries = list()
        if "rv32" in arch_header:
            entries.append(RV.RiscvBaseArch.ARCH_RV32I)
        if "rv64" in arch_header:
            entries.append(RV.RiscvBaseArch.ARCH_RV64I)
        if "any" in arch_header:
            entries.append(RV.RiscvBaseArch.ARCH_RV32I)
            entries.append(RV.RiscvBaseArch.ARCH_RV64I)

        self.arch = self.rng.random_entry_in(entries)

    def setup_num_cpus(self, cpu_header: str):
        num_cpus_raw = cpu_header
        num_cpus_raw = num_cpus_raw.strip()
        if "+" in num_cpus_raw:
            # TODO: need to support <num>+ in ;test.cpus
            self.num_cpus = 4  # More than 2, as we are to support '2+', and otherwise we lack more sophisticated random constraints.
        else:
            self.num_cpus = int(num_cpus_raw)

    def setup_env(self, env_header: str):
        # Setup environment to be virtualized/bare_metal
        entries = list()
        if "bare_metal" in env_header:
            entries.append(RV.RiscvTestEnv.TEST_ENV_BARE_METAL)
        if "virtualized" in env_header:
            # TODO: Check if project supports v-extensions
            if self.cmdline_args and self.cmdline_args.test_env_any:
                entries.append(RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
            else:
                entries.append(RV.RiscvTestEnv.TEST_ENV_BARE_METAL)
        if "any" in env_header:
            # Only enable any if --test_env_any is specified
            if self.cmdline_args and self.cmdline_args.test_env_any:
                # TODO: Check if project supports v-extensions
                entries.append(RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
                entries.append(RV.RiscvTestEnv.TEST_ENV_BARE_METAL)
            else:
                entries.append(RV.RiscvTestEnv.TEST_ENV_BARE_METAL)

        self.env = self.rng.random_entry_in(entries)

    def setup_priv(self, priv_header: str):
        entries = list()
        # TODO: Check project_cfg before adding entry to entries
        if "machine" in priv_header:
            entries.append(RV.RiscvPrivileges.MACHINE)
        if "super" in priv_header:
            entries.append(RV.RiscvPrivileges.SUPER)
        if "user" in priv_header:
            entries.append(RV.RiscvPrivileges.USER)
        if "any" in priv_header:
            entries.append(RV.RiscvPrivileges.MACHINE)
            entries.append(RV.RiscvPrivileges.SUPER)
            entries.append(RV.RiscvPrivileges.USER)

        self.priv_mode = self.rng.random_entry_in(entries)

        # NOTE: Why was this change made?
        # Always run in Machine mode for wysiwyg mode
        if self.cmdline_args and self.cmdline_args.wysiwyg:
            self.priv_mode = RV.RiscvPrivileges.MACHINE

    def setup_secure_mode(self, secure_header: str):
        entries = list()
        if "on" in secure_header:
            entries.append(RV.RiscvSecureModes.SECURE)
        if "off" in secure_header:
            entries.append(RV.RiscvSecureModes.NON_SECURE)
        if "any" in secure_header:
            entries.append(RV.RiscvSecureModes.SECURE)
            entries.append(RV.RiscvSecureModes.NON_SECURE)

        log.info(f"Secure mode entries: {entries}")
        if len(entries) == 0:
            entries.append(RV.RiscvSecureModes.NON_SECURE)

        self.secure_mode = self.rng.random_entry_in(entries) == RV.RiscvSecureModes.SECURE
        if self.secure_mode:
            self.default_whisper_config_json = self.package_path / "dtest_framework/lib/whisper_secure_config.json"
        else:
            self.default_whisper_config_json = self.package_path / "dtest_framework/lib/whisper_config.json"

    def setup_paging_mode(self, paging_header: str, g_stage=False):
        # TODO: Check project_cfg before adding if the mode is supported
        # TODO: SV32 is only supported in RV32 Arch
        entries = list()
        if "disable" in paging_header:
            entries.append(RV.RiscvPagingModes.DISABLE)
        if "sv32" in paging_header:
            entries.append(RV.RiscvPagingModes.SV32)
        if "sv39" in paging_header:
            entries.append(RV.RiscvPagingModes.SV39)
        if "sv48" in paging_header:
            entries.append(RV.RiscvPagingModes.SV48)
        if "sv57" in paging_header:
            entries.append(RV.RiscvPagingModes.SV57)
        if "any" in paging_header:
            entries.append(RV.RiscvPagingModes.DISABLE)
            # entries.append(RV.RiscvPagingModes.SV32)
            entries.append(RV.RiscvPagingModes.SV39)
            entries.append(RV.RiscvPagingModes.SV48)
            entries.append(RV.RiscvPagingModes.SV57)
        if "enable" in paging_header:
            # entries.append(RV.RiscvPagingModes.SV32)
            entries.append(RV.RiscvPagingModes.SV39)
            entries.append(RV.RiscvPagingModes.SV48)
            entries.append(RV.RiscvPagingModes.SV57)

        if g_stage:
            self.paging_g_mode = self.rng.random_entry_in(entries)
            # FIXME: Also, make sure that g-stage paging mode is SV39 then vs-stage is <=SV39
            if self.paging_g_mode == RV.RiscvPagingModes.SV39:
                self.paging_mode = self.rng.random_entry_in([RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.DISABLE])
        else:
            self.paging_mode = self.rng.random_entry_in(entries)

    def setup_mp_mode(self, mp_mode_header: str):
        entries = list()
        if "simultaneous" in mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_SIMULTANEOUS)
        if "parallel" in mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_PARALLEL)
        if "any" in mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_SIMULTANEOUS)
            entries.append(RV.RiscvMPMode.MP_PARALLEL)
        if "" == mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_PARALLEL)

        self.mp_mode = self.rng.random_entry_in(entries)

    def setup_parallel_scheduling_mode(self, parallel_scheduling_mode_header: str):
        entries = list()
        if parallel_scheduling_mode_header is None:
            entries.append(RV.RiscvParallelSchedulingMode.ROUND_ROBIN)
        else:
            if "round_robin" in parallel_scheduling_mode_header:
                entries.append(RV.RiscvParallelSchedulingMode.ROUND_ROBIN)
            if "exhaustive" in parallel_scheduling_mode_header:
                entries.append(RV.RiscvParallelSchedulingMode.EXHAUSTIVE)
            if "" in parallel_scheduling_mode_header:
                entries.append(RV.RiscvParallelSchedulingMode.ROUND_ROBIN)

        self.parallel_scheduling_mode = self.rng.random_entry_in(entries)

    def setup_mp(self, mp_header: str):
        entries = list()
        if "on" in mp_header:
            entries.append(RV.RiscvMPEnablement.MP_ON)
        if "off" in mp_header:
            entries.append(RV.RiscvMPEnablement.MP_OFF)
        if "any" in mp_header:
            entries.append(RV.RiscvMPEnablement.MP_ON)
            entries.append(RV.RiscvMPEnablement.MP_OFF)
        if "" == mp_header:
            entries.append(RV.RiscvMPEnablement.MP_OFF)

        self.mp = self.rng.random_entry_in(entries)

    def setup_features(self, features: str):
        """
        Setup features from config.json and apply test header overrides
        Test header format: "ext_v.enable ext_fp.disable ext_zicbom.enable"

        .. code-block:: json

        Config format: {
            "feature_name": {
                "supported": true/false,
                "enabled": true/false,
                "randomize": percentage
            }
        }
        """
        # Load features from config.json first (lowest priority)
        config_path = self.package_path / "dtest_framework/lib/config.json"
        with open(config_path, "r") as f:
            cfg = json.load(f)
            if "features" in cfg:
                self.features = cfg["features"]
            elif "isa" in cfg:
                # If only ISA is specified, initialize features based on ISA
                self.features = {}
                for ext in cfg["isa"]:
                    if ext.startswith("rv"):
                        continue
                    self.features[ext] = {"supported": True, "enabled": True, "randomize": 0}

        # Parse and apply test header feature directives (higher priority)
        if features:
            feature_tokens = features.strip().split()
            for token in feature_tokens:
                if "." in token:
                    feature_name, action = token.split(".", 1)
                    # Remove common prefixes to get the core feature name
                    if feature_name.startswith("ext_"):
                        feature_name = feature_name[4:]  # Remove "ext_" prefix

                    # Initialize feature if not exists
                    if feature_name not in self.features:
                        self.features[feature_name] = {"supported": True, "enabled": False, "randomize": 0}

                    # Apply the action
                    if action == "enable":
                        self.features[feature_name]["enabled"] = True
                        self.features[feature_name]["supported"] = True
                    elif action == "disable":
                        self.features[feature_name]["enabled"] = False
                elif token == "wysiwyg":
                    # Handle wysiwyg as a special boolean feature
                    self.features["wysiwyg"] = True

    def setup_test_opts(self, opts: str):
        for o in opts.strip().split(" "):
            if "=" in o:
                k, v = o.split("=")
                self.opts[k] = v


# @dataclass(frozen=True)
@dataclass()
class FeatMgr(dict):
    """
    Data class managing user configuration.
    frozen=True makes the object is immutable.
    """

    rng: RandNum
    pool: Pool
    config_path: Path
    test_config: TestConfig
    cmdline: Namespace

    def __post_init__(self):
        """
        Generate a configuration within feature manager.
        We handle overrides by priority: cli > config.json > test.features
        Priority order:
        1. ;#test.features (lowest priority)
        2. cpu_config.json (medium priority)
        3. Command line (highest priority)
        """

        # Initialize feature discovery using centralized FeatureDiscovery
        # Convert test_config.features to string format for FeatureDiscovery
        test_features_str = None
        if hasattr(self.test_config, "features") and self.test_config.features:
            # Convert test_config.features dict to string format
            feature_tokens = []
            for feature_name, feature_config in self.test_config.features.items():
                if isinstance(feature_config, dict):
                    if feature_config.get("enabled", False):
                        feature_tokens.append(f"ext_{feature_name}.enable")
                    else:
                        feature_tokens.append(f"ext_{feature_name}.disable")
                else:
                    # Handle simple boolean features like wysiwyg
                    if feature_config:
                        feature_tokens.append("wysiwyg")
            test_features_str = " ".join(feature_tokens)

        # Create FeatureDiscovery instance with test header overrides
        self.feature_discovery = FeatureDiscovery.from_config_with_overrides(self.config_path, test_features_str)

        # Maintain existing API compatibility - self.feature points to FeatureDiscovery
        self.feature = self.feature_discovery

        # Keep the features dictionary for backward compatibility
        self.features = self.feature_discovery.features

        # FIXME: Make sure this all works the same after removing Singletons, then worry about changing
        self.isa = list()
        self.ignore_exceptions = list()
        self.mp = RV.RiscvMPEnablement.MP_OFF
        self.mp_mode = RV.RiscvMPMode.MP_SIMULTANEOUS
        self.c_used = False
        self.big_bss = False
        self.small_bss = False
        self.add_gcc_cstdlib_sections = False
        self.disallow_mmio = False
        self.trap_handler_label = "trap_entry"
        self.syscall_table_label = "syscall_table"
        self.check_exception_label = "check_exception_cause"  # Label for checking exceptions

        if self.cmdline.c_used:
            self.c_used = True
        if self.cmdline.big_bss:
            self.big_bss = True
            self.small_bss = False
        elif self.cmdline.small_bss:
            self.small_bss = True
            self.big_bss = False
        if self.cmdline.add_gcc_cstdlib_sections:
            self.add_gcc_cstdlib_sections = True
        if self.disallow_mmio:
            self.disallow_mmio = True
        self.skip_instruction_for_unexpected = False
        self.eot_pass_val = self.cmdline.eot_pass_value
        self.eot_fail_val = self.cmdline.eot_fail_value
        self.switch_to_user_page: str = self.cmdline.switch_to_user_page
        self.switch_to_super_page: str = self.cmdline.switch_to_super_page
        self.switch_to_machine_page: str = self.cmdline.switch_to_machine_page
        self.excp_hooks: bool = self.cmdline.excp_hooks
        self.linux_mode: bool = self.cmdline.linux_mode
        self.force_alignment: bool = self.cmdline.force_alignment

        with open(self.config_path, "r") as f:
            cfg = json.load(f)

        self.io_htif_addr = self.get_io_htif_addr(cfg)
        self.reset_pc = get_int(cfg.get("mmap", {}).get("reset_pc", 0x8000_0000))
        self.isa = cfg.get("isa", [])

        memory = Memory.from_cpuconfig(cfg, self.disallow_mmio)

        # Setup PMAs and PMP regions
        for range in memory.dram_ranges:
            # Setup default PMAs for DRAM
            # TODO: Did we really mean to only create a PMA for the last dram range?
            self.pool.pma_dram_default = PmaInfo(pma_address=range.start, pma_size=range.size, pma_memory_type="memory")

            # Also add to pmp regions
            self.pool.pmp_regions.append(PmpInfo(start_addr=range.start, size=range.size))

        for range in memory.secure_ranges:
            # Since this secure region, we need to set bit-55 to 1
            pmp_secure = PmpInfo(start_addr=range.start | 0x0080000000000000, size=range.size, secure=True)
            self.pool.pmp_regions.append(pmp_secure)

            # Setup pmas
            self.pool.pma_regions[f"sec_{range.start:x}"] = PmaInfo(pma_address=range.start, pma_size=range.size, pma_memory_type="memory")
        self.addrgen = addrgen.AddrGen(self.rng, memory, self.cmdline.addrgen_limit_indices, self.cmdline.addrgen_limit_way_predictor_multihit)

        # configuration set within test
        self.env = self.test_config.env
        self.num_cpus = self.test_config.num_cpus
        self.mp = self.test_config.mp
        self.mp_mode = self.test_config.mp_mode
        self.parallel_scheduling_mode = self.test_config.parallel_scheduling_mode

        self.priv_mode = self.test_config.priv_mode
        self.secure_mode = self.test_config.secure_mode
        if self.secure_mode:
            # Setup pmp since secure mode requires pmps to be setup
            self.cmdline.setup_pmp = True
        self.arch = self.test_config.arch
        self.paging_mode = self.test_config.paging_mode
        self.paging_g_mode = self.test_config.paging_g_mode
        self.default_whisper_config_json = self.test_config.default_whisper_config_json
        self.linear_addr_bits = 57
        self.physical_addr_bits = 52
        self.big_endian = False
        self.counter_event_path = None
        self.disable_wfi_wait = False
        self.wysiwyg = self.feature_discovery.is_feature_enabled("wysiwyg")
        self.iss_timeout = 600

        # configuration set within command line
        if self.cmdline.num_cpus:
            self.num_cpus = int(self.cmdline.num_cpus)

        if self.cmdline.mp != "":
            self.mp = RV.RiscvMPEnablement.str_to_enum(self.cmdline.mp)
        elif self.num_cpus > 1:
            self.mp = RV.RiscvMPEnablement.MP_ON

        if self.cmdline.mp_mode != "":
            self.mp_mode = RV.RiscvMPMode.str_to_enum(self.cmdline.mp_mode)

        if self.cmdline.parallel_scheduling_mode != "":
            self.parallel_scheduling_mode = RV.RiscvParallelSchedulingMode.str_to_enum(self.cmdline.parallel_scheduling_mode)

        if self.cmdline.test_env:
            self.env = RV.RiscvTestEnv.str_to_enum(self.cmdline.test_env)

        if self.cmdline.test_priv_mode:
            self.priv_mode = RV.RiscvPrivileges[self.cmdline.test_priv_mode.upper()]
            # if self.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            #     #FIXME: We currently do not support USER mode in virtualized mode
            #     self.priv_mode = RV.RiscvPrivileges.PRIV_SUPER

        # Secure mode can be set from command line
        if not self.secure_mode:
            if self.cmdline.test_secure_mode == "on":
                self.secure_mode = True
                # Also setup pmp since secure mode requires pmps to be setup
                self.cmdline.setup_pmp = True
            elif self.cmdline.test_secure_mode == "off":
                self.secure_mode = False
            elif self.cmdline.test_secure_mode == "random":
                # Enable secure mode with 20% probability
                self.secure_mode = self.rng.with_probability_of(20)
                if self.secure_mode:
                    # Also setup pmp since secure mode requires pmps to be setup
                    self.cmdline.setup_pmp = True

        # Handle pbmt randomization using feature system
        self.pbmt_ncio = 0
        if self.feature_discovery.is_feature_supported("svpbmt") and self.feature_discovery.get_feature_randomize("svpbmt") > 0:
            if self.rng.with_probability_of(self.feature_discovery.get_feature_randomize("svpbmt")):
                self.pbmt_ncio = 1
        if self.cmdline.pbmt_ncio_randomization:
            self.pbmt_ncio_randomization = self.cmdline.pbmt_ncio_randomization

        # Handle svadu randomization using feature system
        self.svadu = 0
        if self.feature_discovery.is_feature_supported("svadu") and self.feature_discovery.get_feature_randomize("svadu") > 0:
            if self.rng.with_probability_of(self.feature_discovery.get_feature_randomize("svadu")):
                self.svadu = 1
        if self.cmdline.a_d_bit_randomization:
            self.a_d_bit_randomization = self.cmdline.a_d_bit_randomization

        # Load test generation parameters from config.json
        self.secure_access_probability = 30  # Default value
        self.secure_pt_probability = 0  # Default value
        self.a_d_bit_randomization = 0  # Default value
        self.pbmt_ncio_randomization = 0  # Default value

        # Load from config.json if available
        with open(self.config_path, "r") as f:
            cfg = json.load(f)
            if "test_generation" in cfg:
                test_gen_config = cfg["test_generation"]
                self.secure_access_probability = test_gen_config.get("secure_access_probability", 30)
                self.secure_pt_probability = test_gen_config.get("secure_pt_probability", 0)
                self.a_d_bit_randomization = test_gen_config.get("a_d_bit_randomization", 0)
                self.pbmt_ncio_randomization = test_gen_config.get("pbmt_ncio_randomization", 0)
        # Check command line for overrides
        if self.cmdline.secure_access_probability:
            self.secure_access_probability = self.cmdline.secure_access_probability
        if self.cmdline.secure_pt_probability:
            self.secure_pt_probability = self.cmdline.secure_pt_probability
        if self.cmdline.pbmt_ncio_randomization:
            self.pbmt_ncio_randomization = self.cmdline.pbmt_ncio_randomization

        if self.cmdline.big_endian:
            self.big_endian = True

        if self.cmdline.counter_event_path is not None:
            self.counter_event_path = Path(self.cmdline.counter_event_path)
        if self.cmdline.disable_wfi_wait:
            self.disable_wfi_wait = True

        if self.linux_mode:
            self.priv_mode = RV.RiscvPrivileges.MACHINE

        # Machine mode only allows PAGING_DISABLE and takes priority over paging/paging_g mode from commandline
        if self.priv_mode == RV.RiscvPrivileges.MACHINE or self.cmdline.fe_tb:
            if self.paging_mode != RV.RiscvPagingModes.DISABLE:
                if not self.cmdline.fe_tb:
                    log.warning("Machine mode only allows PAGING_DISABLE, forcing paging and paging_g to disable")
            self.paging_mode = RV.RiscvPagingModes.DISABLE
            self.paging_g_mode = RV.RiscvPagingModes.DISABLE
        else:
            if self.cmdline.test_paging_mode:
                if self.cmdline.test_paging_mode.upper() == "ANY" or self.cmdline.test_paging_mode.upper() == "ENABLE":
                    pass
                else:
                    self.paging_mode = RV.RiscvPagingModes[self.cmdline.test_paging_mode.upper()]
            # Also force g-stage paging mode from commandline
            if self.cmdline.test_paging_g_mode:
                if self.cmdline.test_paging_g_mode.upper() == "ANY" or self.cmdline.test_paging_g_mode.upper() == "ENABLE":
                    pass
                else:
                    self.paging_g_mode = RV.RiscvPagingModes[self.cmdline.test_paging_g_mode.upper()]

        if self.cmdline.skip_instruction_for_unexpected:
            self.skip_instruction_for_unexpected = True

        # TODO: I think we can do better here wrt handling command line arguments
        for key, value in vars(self.cmdline).items():
            if key == "mp" or key == "mp_mode" or key == "num_cpus" or key == "parallel_scheduling_mode":
                # We don't want to override mp/mp_mode from command line without using enums
                continue
            elif key == "wysiwyg":
                # If wysiwyg in features, ignore command line
                if "wysiwyg" in self.test_config.features:
                    continue

            setattr(self, key, value)

        # exception delegation
        if self.priv_mode == RV.RiscvPrivileges.MACHINE:
            self.deleg_excp_to = "machine"
        elif self.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            # FIXME: RVTOOLS-2841 We currently only support delegation to HS
            self.deleg_excp_to = "super"
        else:
            if self.cmdline.deleg_excp_to == "random":
                if "deleg_excp_to" in self.test_config.opts:
                    self.deleg_excp_to = self.test_config.opts["deleg_excp_to"]
                else:
                    self.deleg_excp_to = self.rng.random_entry_in(["super", "machine"])
            elif self.cmdline.deleg_excp_to == "machine":
                self.deleg_excp_to = "machine"
            elif self.cmdline.deleg_excp_to == "super":
                self.deleg_excp_to = "super"
            else:
                raise ValueError(f"Unknown value of command-line option deleg_excp_to: {self.cmdline.deleg_excp_to}")

        self.single_assembly_file = self.cmdline.single_assembly_file

    def get_io_htif_addr(self, cfg):
        htif_str = "auto"
        # Prioritize command line value for htif address
        if self.cmdline.tohost:
            log.info(f"Using htif address from command line: {self.cmdline.tohost}")
            htif_str = self.cmdline.tohost
        else:
            try:
                htif_str = cfg["mmap"]["io"]["items"]["htif"]["address"]
            except KeyError:
                log.warning("Unable to find htif address in cpu_config at cfg['mmap']['io']['items']['htif']['address']. Using any available address")
                htif_str = "auto"

        if htif_str == "auto":
            log.warn("Using auto for htif address. This will cause htif to be allocated in dram.")
            return None
        else:
            return int(htif_str, 0)

    def get_summary(self):
        """
        Returns an array representation of feature presence.
        """
        presence = dict()
        priv_mode = self.priv_mode
        presence["PRIV_MODE_MACHINE"] = priv_mode == RV.RiscvPrivileges.MACHINE
        presence["PRIV_MODE_SUPER"] = priv_mode == RV.RiscvPrivileges.SUPER
        presence["PRIV_MODE_USER"] = priv_mode == RV.RiscvPrivileges.USER

        presence["SECURE_MODE"] = self.secure_mode

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

    # Legacy methods for backward compatibility - delegate to FeatureDiscovery
    def is_feature_supported(self, feature: str) -> bool:
        """Check if a feature is supported - delegate to FeatureDiscovery"""
        return self.feature_discovery.is_feature_supported(feature)

    def is_feature_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled - delegate to FeatureDiscovery"""
        return self.feature_discovery.is_feature_enabled(feature)

    def get_feature_randomize(self, feature: str) -> int:
        """Get the randomization probability for a feature - delegate to FeatureDiscovery"""
        return self.feature_discovery.get_feature_randomize(feature)

    def get_misa_bits(self) -> int:
        """Get MISA bits based on enabled features"""
        misa = 0

        # Base ISA
        if self.feature_discovery.is_feature_enabled("rv64"):
            misa |= 1 << 62  # MXL = 2 for RV64
        elif self.feature_discovery.is_feature_enabled("rv32"):
            misa |= 1 << 30  # MXL = 1 for RV32

        # Base extensions
        extension_bits = {"i": 8, "m": 12, "a": 0, "f": 5, "d": 3, "c": 2, "v": 21}  # I  # M  # A  # F  # D  # C  # V

        for ext, bit in extension_bits.items():
            if self.feature_discovery.is_feature_enabled(ext):
                misa |= 1 << bit

        # Additional extensions (Z*)
        for feature in self.features:
            if feature.startswith("z"):
                # Map Z extensions to their MISA bits
                # This mapping should be expanded based on your specific Z extensions
                z_bits = {"zfh": 5, "zvfh": 5, "zba": 0, "zbb": 0, "zbs": 0, "zfbfmin": 5, "zvbb": 21, "zbc": 21, "zvfbfmin": 5}  # F  # F  # A  # A  # A  # F  # V  # V  # F
                if feature in z_bits and self.feature_discovery.is_feature_enabled(feature):
                    misa |= 1 << z_bits[feature]

        return misa

    def enable_features_by_randomization(self):
        """Enable features based on their randomization percentages"""
        self.feature_discovery.enable_features_by_randomization(self.rng)

    def get_compiler_march_string(self) -> str:
        """Generate a compiler march string from enabled features"""
        return self.feature_discovery.get_compiler_march_string()
