# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
import logging
import json
import os
import dataclasses
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .resource import Resource
from .mode_cfg import ModeCfg

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.compliance.lib.fpgen_intf import FpGenInterface
from riescue.dtest_framework.config import FeatMgrBuilder, Candidate
from riescue.lib.toolchain import Toolchain, Spike, Whisper, Compiler, Disassembler

log = logging.getLogger(__name__)


@dataclass
class BringupTest:
    """
    Test configuration for ``BringupMode``. Used to validate user-input JSON files for a single test.

    Required fields:
    :param arch: Architecture
    :param include_extensions: List of extensions to include
    :param include_groups: List of groups to include
    :param include_instrs: List of instructions to include
    :param exclude_groups: List of groups to exclude
    :param exclude_instrs: List of instructions to exclude


    Optional fields:
    :param iss: ISS to use, ``spike`` or ``whisper``
    :param first_pass_iss: First pass ISS to use, ``spike`` or ``whisper``
    :param second_pass_iss: Second pass ISS to use, ``spike`` or ``whisper``
    """

    arch: RV.RiscvBaseArch = field(default=RV.RiscvBaseArch.ARCH_RV64I)
    include_extensions: list[str] = field(default_factory=list)
    include_groups: list[str] = field(default_factory=list)
    include_instrs: list[str] = field(default_factory=list)
    exclude_groups: list[str] = field(default_factory=list)
    exclude_instrs: list[str] = field(default_factory=list)
    iss: str = ""
    first_pass_iss: str = ""
    second_pass_iss: str = ""

    @classmethod
    def from_dict(cls, test: dict) -> "BringupTest":
        """
        Construct from a dictionary. Validates user dictionary for required fields
        """
        # validate dict
        valid_arch = [x.value for x in RV.RiscvBaseArch]
        for required_field in ["arch", "include_extensions", "include_groups", "include_instrs", "exclude_groups", "exclude_instrs"]:
            if required_field not in test:
                raise ValueError(f"{required_field} is a required field in bringup test")
            if required_field == "arch" and test[required_field] not in valid_arch:
                raise ValueError(f"{required_field} must be one of {valid_arch}")

        return cls(
            arch=RV.RiscvBaseArch(test["arch"]),
            include_extensions=test["include_extensions"],
            include_groups=test["include_groups"],
            include_instrs=test["include_instrs"],
            exclude_groups=test["exclude_groups"],
            exclude_instrs=test["exclude_instrs"],
            iss=test.get("iss", ""),
            first_pass_iss=test.get("first_pass_iss", ""),
            second_pass_iss=test.get("second_pass_iss", ""),
        )

    @classmethod
    def from_json(cls, json_file: Path) -> "BringupTest":
        with open(json_file, "r") as f:
            test = json.load(f)
        return cls.from_dict(test)


class BringupCfg(ModeCfg):
    """
    Configuration for ``BringupMode``. Essentially a builder for ``Resource``.

    This allows for power library users to modify configuration easily and for ``ComplianceMode`` to more easily modify different modes.

    Ensures that ``Resource`` is fully configured before ``BringupMode`` is run.
    """

    def __init__(self):
        super().__init__()
        self.resource = Resource()
        self.featmgr_builder = FeatMgrBuilder()
        self.toolchain = Toolchain()

    def with_args(self, args: argparse.Namespace) -> "BringupCfg":
        self.resource = self._resource_with_args(args)
        self.toolchain = self._toolchain_with_args(args)
        return self

    def with_bringup_test_json(self, bringup_test_json: Path) -> "BringupCfg":
        bringup_test = BringupTest.from_json(self.find_config(bringup_test_json))
        self.resource = self._resource_with_bringup_test(bringup_test)
        return self

    def build(self, rng: RandNum, run_dir: Path) -> Resource:
        """
        Build and return a copy of the configured Resource. This ensures that the same``BringupCfg`` can be built with different rng and run_dir.

        - Sets rng and fpgen_on (if ``FPGEN_ENABLED`` is set)
        - Finds paths to config files

        :raises FileNotFoundError: If config files are not found
        """
        resource = dataclasses.replace(self.resource)
        resource.with_rng(rng)
        resource.run_dir = run_dir
        resource.fpgen_on = resource.fpgen_on or bool(int(os.getenv("FPGEN_ENABLED", "0")))

        # resolve paths to config files - needed to find files relative to riescue directory, prioritizes riescue-relative over cwd-relative
        resource.fp_config = self.find_config(resource.fp_config)
        resource.default_config = self.find_config(resource.default_config)
        if resource.user_config is not None:
            resource.user_config = self.find_config(resource.user_config)

        if resource.fpgen_on:
            resource.fpgen_intf = FpGenInterface()
            resource.fpgen_intf.configure(resource.seed, resource.fast_fpgen)

        resource.featmgr = self.featmgr_builder.build(rng)

        # RiescueC-specific configuration
        resource.featmgr.pbmt_ncio_randomization = 0
        resource.featmgr.disable_wfi_wait = True  # RVTOOLS-4204
        # tests require that repeat times is set to 1, e.g. amoswap can only be ran a single time
        # If disable_pass is set, repeat times should be the repeat_runtime
        if resource.disable_pass:
            resource.featmgr.repeat_times = resource.repeat_runtime
        else:
            resource.featmgr.repeat_times = 1

        resource.testfile = Path(resource.testcase_name + ".s")
        resource.toolchain = self.toolchain

        return resource

    def _resource_with_bringup_test(self, bringup_test: BringupTest) -> Resource:
        "All ``BringupTest`` related configuration."
        resource = self.resource
        resource.arch = bringup_test.arch
        # validate extensions
        for extension in bringup_test.include_extensions:
            if resource.check_extension(extension):
                resource.include_extensions.append(extension)
            else:
                raise ValueError(f"{extension} is not supported")
        resource.include_groups = bringup_test.include_groups
        resource.include_instrs = bringup_test.include_instrs
        resource.exclude_groups = bringup_test.exclude_groups
        resource.exclude_instrs = bringup_test.exclude_instrs

        # legacy behavior was to only use BringupTest.iss if --first_pass_iss=""
        # this isn't documented anywhere, so going to use iss here if passed in explicitly
        if bringup_test.iss:
            resource.first_pass_iss = bringup_test.iss
            resource.second_pass_iss = bringup_test.iss
        if bringup_test.first_pass_iss:
            resource.first_pass_iss = bringup_test.first_pass_iss
        if bringup_test.second_pass_iss:
            resource.second_pass_iss = bringup_test.second_pass_iss

        return resource

    def _toolchain_with_args(self, args: argparse.Namespace) -> Toolchain:
        "Toolchain-specific configuration."
        whisper = Whisper.from_clargs(args)
        spike = Spike.from_clargs(args)

        experimental_enabled = any(
            [
                args.rv_zvknhb_experimental,
                args.rv_zvkg_experimental,
                args.rv_zvbc_experimental,
                args.rv_zvfbfwma_experimental,
                args.rv_zvfbfmin_experimental,
                args.rv_zfbfmin_experimental,
                args.rv_zvbb_experimental,
            ]
        )

        # FIXME: this should probably be part of the toolchain package, as experimental_tool.py
        if experimental_enabled:
            # legacy experimental features support
            compiler_march = ["rv64imafdcv_zfh_zba_zbb_zbc_zbs"]
            disassembler_opts = []
            if args.rv_zfbfmin_experimental:
                compiler_march.append("_zfbfmin0p6")
                disassembler_opts.append("zfbfmin")
            if args.rv_zvfbfmin_experimental:
                compiler_march.append("_zvfbfmin0p6")
                disassembler_opts.append("zvfbfmin")
            if args.rv_zvbb_experimental:
                compiler_march.append("_zvbb1")
                disassembler_opts.append("zvbb")
            if args.rv_zvfbfwma_experimental:
                compiler_march.append("_zvfbfwma0p6")
                disassembler_opts.append("zvfbfwma")
            if args.rv_zvbc_experimental:
                compiler_march.append("_zvbc1")
                disassembler_opts.append("zvbc")
            if args.rv_zvkg_experimental:
                compiler_march.append("_zvkg1")
                disassembler_opts.append("zvkg")
            if args.rv_zvknhb_experimental:
                compiler_march.append("_zvknhb1")
                disassembler_opts.append("zvknhb")
            compiler_march.extend(["_zifencei_zicsr"])

            compiler_path = args.compiler_path or args.experimental_compiler or os.getenv("EXPERIMENTAL_COMPILER")
            if not compiler_path:
                raise ValueError("Experimental compiler path is not set. Explicitly set --compiler_path or --experimental_compiler, or define EXPERIMENTAL_COMPILER environment variable.")
            compiler_opts = args.compiler_opts + ["-menable-experimental-extensions"]
            compiler = Compiler(
                compiler_path=Path(compiler_path),
                compiler_opts=compiler_opts,
                compiler_march="".join(compiler_march),
                test_equates=args.test_equates,
            )

            diassembler_path = args.disassembler_path or args.experimental_objdump or os.getenv("EXPERIMENTAL_OBJDUMP")
            if not diassembler_path:
                raise ValueError("Experimental objdump path is not set. Explicitly set --disassembler_path or --experimental_objdump, or define EXPERIMENTAL_OBJDUMP environment variable.")
            disassembler = Disassembler(
                disassembler_path=Path(diassembler_path),
                disassembler_opts=disassembler_opts,
            )
        else:
            compiler = Compiler.from_clargs(args)
            disassembler = Disassembler.from_clargs(args)
        return Toolchain(compiler=compiler, disassembler=disassembler, spike=spike, whisper=whisper)

    def _resource_with_args(self, args: argparse.Namespace) -> Resource:
        "Configure command line arguments. Verbose, but type safe and more IDE friendly than setattr()"

        # deprecated args
        if args.privilege_mode is not None:
            log.warning("Deprecated argument --privilege_mode is ignored. Use --test_priv_mode instead.")
            if args.privilege_mode == "supervisor":
                args.test_priv_mode = "super"
            else:
                args.test_priv_mode = args.privilege_mode.upper()

        # configure feat manager builder
        if args.cpuconfig is not None:
            cpu_config = self.find_config(args.cpuconfig)
            self.featmgr_builder.with_cpu_json(cpu_config)
        else:
            log.error("Using default memory map (not default cpuconfig). This is likely in error")
        self.featmgr_builder.with_args(args)
        resource = self.resource

        # filename
        if args.output_file is not None:
            resource.testcase_name = args.output_file
            resource.use_output_filename = True
        elif args.json is not None:
            resource.testcase_name = args.json.stem

        # rc args
        if args.default_config is not None:
            resource.default_config = self.find_config(args.default_config)
        if args.user_config is not None:
            resource.user_config = self.find_config(args.user_config)
        if args.fp_config is not None:
            resource.fp_config = self.find_config(args.fp_config)
        if args.first_pass_iss is not None:
            resource.first_pass_iss = args.first_pass_iss
        if args.second_pass_iss is not None:
            resource.second_pass_iss = args.second_pass_iss
        if args.compare_iss is not None:
            resource.compare_iss = args.compare_iss
        if args.dump_instrs is not None:
            resource.dump_instrs = bool(args.dump_instrs)
        if args.combine_compliance_tests is not None:
            resource.combine_compliance_tests = bool(args.combine_compliance_tests)

        # bringup test targets (instructions, groups, extensions)
        if args.instrs is not None:
            print(args.instrs)
            resource.include_instrs.extend(args.instrs.split(","))
        if args.exclude_instrs is not None:
            resource.exclude_instrs.extend(args.exclude_instrs.split(","))
        if args.groups is not None:
            resource.include_groups.extend(args.groups.split(","))

        # rd args
        if args.rpt_cnt is not None:
            resource.rpt_cnt = args.rpt_cnt
        if args.max_instrs_per_file is not None:
            resource.max_instr_per_file = args.max_instrs_per_file
        if args.vector_bringup is not None:
            resource.vector_bringup = args.vector_bringup
        if args.disable_pass is not None:
            resource.disable_pass = args.disable_pass
        if args.output_format is not None:
            resource.output_format = args.output_format
        if args.load_fp_regs is not None:
            resource.load_fp_regs = args.load_fp_regs
        if args.force_alignment is not None:
            resource.force_alignment = args.force_alignment

        # FeatMgrBuilder overrides
        if args.parallel_scheduling_mode is not None:
            self.featmgr_builder.parallel_scheduling_mode = Candidate(RV.RiscvParallelSchedulingMode.str_to_enum(args.parallel_scheduling_mode))
        if args.big_endian is not None:
            resource.big_endian = args.big_endian
            self.featmgr_builder.featmgr.num_cpus = 1  # from legacy code, not sure what the restriction is
            resource.first_pass_iss = "spike"
            resource.second_pass_iss = "spike"
            log.info("Big endian enabled, disabling mp and setting num_cpus to 1")
        if args.repeat_runtime is not None:
            resource.repeat_runtime = args.repeat_runtime
        if args.fe_tb is not None:
            resource.fe_tb = args.fe_tb

        # fpgen
        if args.fpgen_on is not None:
            resource.fpgen_on = args.fpgen_on

        return self.resource

    def duplicate(self) -> "BringupCfg":
        new_cfg = BringupCfg()
        new_cfg.resource = dataclasses.replace(self.resource)
        new_cfg.featmgr_builder = self.featmgr_builder.duplicate()
        new_cfg.toolchain = self.toolchain
        return new_cfg
