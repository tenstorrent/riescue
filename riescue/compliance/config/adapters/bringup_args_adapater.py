# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import os
import logging
import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from .base import BaseAdapter
from riescue.dtest_framework.config import Candidate
from riescue.lib.toolchain import Toolchain, Spike, Whisper, Compiler, Disassembler
import riescue.lib.enums as RV

if TYPE_CHECKING:
    from .. import ResourceBuilder


log = logging.getLogger(__name__)


class BringupArgsAdapter(BaseAdapter):
    """
    Adapter for :class:`BringupTest`.
    """

    def apply(self, builder: ResourceBuilder, src: argparse.Namespace) -> ResourceBuilder:
        args = src
        resource_builder = builder
        resource = resource_builder.resource
        featmgr_builder = resource_builder.featmgr_builder

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
            featmgr_builder.with_cpu_json(cpu_config)
        else:
            log.error("Using default memory map (not default cpuconfig). This is likely in error")
        featmgr_builder.with_args(args)

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
            resource.include_instrs.extend(args.instrs.split(","))
        if args.exclude_instrs is not None:
            resource.exclude_instrs.extend(args.exclude_instrs.split(","))
        if args.groups is not None:
            resource.include_groups.extend(args.groups.split(","))
        if args.include_extensions is not None:
            resource.include_extensions.extend(args.include_extensions.split(","))

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
            featmgr_builder.parallel_scheduling_mode = Candidate(RV.RiscvParallelSchedulingMode.str_to_enum(args.parallel_scheduling_mode))
        if args.big_endian is not None:
            resource.big_endian = args.big_endian
            featmgr_builder.featmgr.num_cpus = 1  # from legacy code, not sure what the restriction is
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

        return builder
