# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import json
import logging
from typing import TYPE_CHECKING

import riescue.lib.enums as RV
from riescue.dtest_framework.parser import ParsedTestHeader
from .adapter import Adapter
from ..candidate import Candidate

if TYPE_CHECKING:
    from ..builder import FeatMgrBuilder

log = logging.getLogger(__name__)


class TestConfigAdapter(Adapter):
    """
    Adapater for test environment configuration from test header.

    FIXME: The ``ParsedTestHeader`` uses only strings. Should use enums to avoid ambiguity. typos in values are not caught and are silently ignored
    """

    def apply(self, builder: FeatMgrBuilder, src: ParsedTestHeader) -> FeatMgrBuilder:
        test_header = src
        featmgr = builder.featmgr

        # Handle ;#test.cpus
        if test_header.cpus:
            featmgr.num_cpus = self.setup_num_cpus(test_header.cpus)

        # Handle ;#test.arch
        if test_header.arch:
            builder.arch = self.setup_arch(test_header.arch)

        # Handle ;#test.env
        if test_header.env:
            builder.env = self.setup_env(test_header.env)

        # Handle ;#test.priv
        if test_header.priv:
            builder.priv_mode = self.setup_priv(test_header.priv)

        # Handle ;#test.secure
        if test_header.secure_mode:
            builder.secure_mode = self.setup_secure_mode(test_header.secure_mode)

        # Handle ;#test.paging
        if test_header.paging:
            builder.paging_mode = self.setup_paging_mode(test_header.paging)

        # Handle ;#test.mp_mode
        if test_header.mp_mode:
            builder.mp_mode = self.setup_mp_mode(test_header.mp_mode)

        # Handle ;#test.parallel_scheduling_mode
        if test_header.parallel_scheduling_mode:
            builder.parallel_scheduling_mode = self.setup_parallel_scheduling_mode(test_header.parallel_scheduling_mode)

        # Handle ;#test.mp
        if test_header.mp:
            builder.mp = self.setup_mp(test_header.mp)

        # Handle ;#test.paging_g
        if test_header.paging_g:
            builder.paging_g_mode = self.setup_paging_mode(test_header.paging_g)

        # Handle ;#test.features
        if test_header.features:
            builder.features = self.setup_features(test_header.features)

        # Handle ;#test.opts
        if test_header.opts:
            featmgr.opts = self.setup_test_opts(test_header.opts)  # Currently unused, double check that's intentional

        return builder

    def setup_arch(self, arch_header: str) -> Candidate[RV.RiscvBaseArch]:
        entries: list[RV.RiscvBaseArch] = []
        if "rv32" in arch_header:
            entries.append(RV.RiscvBaseArch.ARCH_RV32I)
        if "rv64" in arch_header:
            entries.append(RV.RiscvBaseArch.ARCH_RV64I)
        if "any" in arch_header:
            entries.append(RV.RiscvBaseArch.ARCH_RV32I)
            entries.append(RV.RiscvBaseArch.ARCH_RV64I)
        return Candidate(*entries)

    def setup_num_cpus(self, cpu_header: str) -> int:
        num_cpus_raw = cpu_header
        num_cpus_raw = num_cpus_raw.strip()
        if "+" in num_cpus_raw:
            # TODO: need to support <num>+ in ;test.cpus
            return 4  # More than 2, as we are to support '2+', and otherwise we lack more sophisticated random constraints.
        elif not num_cpus_raw.isnumeric():
            raise ValueError(f"Invalid number of CPUs: {num_cpus_raw}, expected unsigned integer")
        else:
            return int(num_cpus_raw)

    def setup_env(self, env_header: str) -> Candidate[RV.RiscvTestEnv]:
        "CLI args needs to double check that the test_env_any is not specified"
        entries: list[RV.RiscvTestEnv] = []
        if "bare_metal" in env_header:
            entries.append(RV.RiscvTestEnv.TEST_ENV_BARE_METAL)
        if "virtualized" in env_header:
            entries.append(RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        if "any" in env_header:
            entries.append(RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
            entries.append(RV.RiscvTestEnv.TEST_ENV_BARE_METAL)

        return Candidate(*entries)

    def setup_priv(self, priv_header: str) -> Candidate[RV.RiscvPrivileges]:
        entries: list[RV.RiscvPrivileges] = []
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
        return Candidate(*entries)

    def setup_secure_mode(self, secure_header: str) -> Candidate[RV.RiscvSecureModes]:
        entries: list[RV.RiscvSecureModes] = []
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

        return Candidate(*entries)

    def setup_paging_mode(self, paging_header: str) -> Candidate[RV.RiscvPagingModes]:
        # TODO: Check project_cfg before adding if the mode is supported
        # TODO: SV32 is only supported in RV32 Arch
        entries: list[RV.RiscvPagingModes] = []
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

        return Candidate(*entries)

    def setup_mp_mode(self, mp_mode_header: str) -> Candidate[RV.RiscvMPMode]:
        entries: list[RV.RiscvMPMode] = []
        if "simultaneous" in mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_SIMULTANEOUS)
        if "parallel" in mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_PARALLEL)
        if "any" in mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_SIMULTANEOUS)
            entries.append(RV.RiscvMPMode.MP_PARALLEL)
        if "" == mp_mode_header:
            entries.append(RV.RiscvMPMode.MP_PARALLEL)

        return Candidate(*entries)

    def setup_parallel_scheduling_mode(self, parallel_scheduling_mode_header: str) -> Candidate[RV.RiscvParallelSchedulingMode]:
        entries: list[RV.RiscvParallelSchedulingMode] = []
        if "round_robin" in parallel_scheduling_mode_header:
            entries.append(RV.RiscvParallelSchedulingMode.ROUND_ROBIN)
        if "exhaustive" in parallel_scheduling_mode_header:
            entries.append(RV.RiscvParallelSchedulingMode.EXHAUSTIVE)
        if not parallel_scheduling_mode_header:
            entries.append(RV.RiscvParallelSchedulingMode.ROUND_ROBIN)

        return Candidate(*entries)

    def setup_mp(self, mp_header: str) -> Candidate[RV.RiscvMPEnablement]:
        entries: list[RV.RiscvMPEnablement] = []
        if "on" in mp_header:
            entries.append(RV.RiscvMPEnablement.MP_ON)
        if "off" in mp_header:
            entries.append(RV.RiscvMPEnablement.MP_OFF)
        if "any" in mp_header:
            entries.append(RV.RiscvMPEnablement.MP_ON)
            entries.append(RV.RiscvMPEnablement.MP_OFF)
        if "" == mp_header:
            entries.append(RV.RiscvMPEnablement.MP_OFF)

        return Candidate(*entries)

    def setup_features(self, features: str) -> list[str]:
        """
        Setup features from config.json and apply test header overrides
        Test header format: "ext_v.enable ext_fp.disable ext_zicbom.enable"

        """
        if features:

            return features.strip().split()
        else:
            return []

    def setup_test_opts(self, opts: str) -> dict[str, str]:
        "Not sure if this is used anywhere"
        additional_opts: dict[str, str] = {}
        for o in opts.strip().split(" "):
            if "=" in o:
                k, v = o.split("=")
                additional_opts[k] = v
        return additional_opts
