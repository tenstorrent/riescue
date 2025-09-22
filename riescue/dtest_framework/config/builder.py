# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import argparse
from pathlib import Path
from argparse import Namespace
from dataclasses import dataclass, field
from typing import Optional

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.parser import ParsedTestHeader
from .candidate import Candidate
from .adapaters import TestConfigAdapter, CpuConfigAdapter, CliAdapter
from .cpu_config import CpuConfig
from .memory import Memory
from .featmanager import FeatMgr
import riescue.dtest_framework.config.cmdline as cmdline
from riescue.lib.feature_discovery import FeatureDiscovery


@dataclass
class FeatMgrBuilder:
    """
    Constructs :class:`FeatMgr` from multiple sources.

    This leads to a lot of repeated code, but it's the only way to get the type checking and traceability of the builder pattern to work

    To add new fields:

        1. add them to the ``FeatMgr`` class
        2. Add Candidate fields to the ``FeatMgrBuilder`` class.
        3. Add a line to the :func:`FeatMgr.build` method that calls ``.choose()`` on the Candidate field.

    .. note::

        Only add fields here that are set by the builder or have a set of possible values.
        Values that are hardcoded should only be added to the ``FeatMgr`` class.

    Usage:
    .. code-block:: python

        builder = FeatMgrBuilder()
        builder.with_test_header(header)
        builder.with_cpu_json(path)
        builder.with_args(args)
        featmgr = builder.build()
    ```
    """

    featmgr: FeatMgr = field(default_factory=FeatMgr)

    features: list[str] = field(default_factory=list)

    # randomized fields
    # Test options
    priv_mode: Candidate[RV.RiscvPrivileges] = field(default_factory=lambda: Candidate.from_enum(RV.RiscvPrivileges))
    paging_mode: Candidate[RV.RiscvPagingModes] = field(
        default_factory=lambda: Candidate(
            RV.RiscvPagingModes.DISABLE,
            RV.RiscvPagingModes.SV39,
            RV.RiscvPagingModes.SV48,
            RV.RiscvPagingModes.SV57,
        )
    )
    paging_g_mode: Candidate[RV.RiscvPagingModes] = field(default_factory=lambda: Candidate(RV.RiscvPagingModes.DISABLE))
    env: Candidate[RV.RiscvTestEnv] = field(default_factory=lambda: Candidate.from_enum(RV.RiscvTestEnv))
    arch: Candidate[RV.RiscvBaseArch] = field(default_factory=lambda: Candidate(RV.RiscvBaseArch.ARCH_RV64I))
    secure_mode: Candidate[RV.RiscvSecureModes] = field(default_factory=lambda: Candidate(RV.RiscvSecureModes.NON_SECURE))
    deleg_excp_to: Candidate[RV.RiscvPrivileges] = field(default_factory=lambda: Candidate(RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER))

    # MP mode
    mp: Candidate[RV.RiscvMPEnablement] = field(default_factory=lambda: Candidate(RV.RiscvMPEnablement.MP_OFF))
    mp_mode: Candidate[RV.RiscvMPMode] = field(default_factory=lambda: Candidate(RV.RiscvMPMode.MP_PARALLEL))
    parallel_scheduling_mode: Candidate[RV.RiscvParallelSchedulingMode] = field(default_factory=lambda: Candidate.from_enum(RV.RiscvParallelSchedulingMode))

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser):
        cmdline.add_arguments(parser)

    def with_test_header(self, header: ParsedTestHeader) -> FeatMgrBuilder:
        """
        Update builder with test header contents

        :param header: The test header to update the builder with
        """

        return TestConfigAdapter().apply(self, header)

    def with_cpu_json(self, path: Path) -> FeatMgrBuilder:
        """
        Update builder with cpu config contents

        :param path: The path to the cpu config file
        """
        return CpuConfigAdapter().apply(self, path)

    def with_args(self, args: Namespace) -> FeatMgrBuilder:
        """
        Update builder with command line arguments

        :param args: The command line arguments to update the builder with
        """
        return CliAdapter().apply(self, args)

    def build(self, rng: RandNum) -> FeatMgr:
        """
        Where randomization happens. Verbose, but allows for easier debugging

        :param rng: The ``RandNum`` generator should be using randomization.

        :returns: A ``FeatMgr`` object with the randomization applied
        """

        featmgr = self.featmgr
        if featmgr.wysiwyg:
            self.priv_mode = Candidate(RV.RiscvPrivileges.MACHINE)  # Always run in Machine mode for wysiwyg mode

        if self.paging_g_mode == RV.RiscvPagingModes.SV39:
            self.paging_mode = Candidate(RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.DISABLE)

        # Handle pbmt randomization using feature system
        # is pbmt_ncio_randomization supposed to be pbmt_ncio_probability?
        feature_discovery = featmgr.cpu_config.features
        if feature_discovery.is_feature_supported("svpbmt") and feature_discovery.get_feature_randomize("svpbmt") > 0:
            if rng.with_probability_of(feature_discovery.get_feature_randomize("svpbmt")):
                featmgr.pbmt_ncio = True
        if feature_discovery.is_feature_supported("svadu") and feature_discovery.get_feature_randomize("svadu") > 0:
            if rng.with_probability_of(feature_discovery.get_feature_randomize("svadu")):
                featmgr.svadu = True

        # Randomization
        featmgr.priv_mode = self.priv_mode.choose(rng)
        featmgr.paging_mode = self.paging_mode.choose(rng)
        featmgr.paging_g_mode = self.paging_g_mode.choose(rng)
        featmgr.env = self.env.choose(rng)
        featmgr.secure_mode = self.secure_mode.choose(rng)
        featmgr.arch = self.arch.choose(rng)
        featmgr.mp = self.mp.choose(rng)
        featmgr.mp_mode = self.mp_mode.choose(rng)
        featmgr.parallel_scheduling_mode = self.parallel_scheduling_mode.choose(rng)
        featmgr.deleg_excp_to = self.deleg_excp_to.choose(rng)

        if featmgr.secure_mode:
            featmgr.setup_pmp = True

        # Disable paging mode if in machine mode and not explicitly set
        if featmgr.priv_mode == RV.RiscvPrivileges.MACHINE and not featmgr.enable_machine_paging:
            featmgr.paging_mode = RV.RiscvPagingModes.DISABLE

        if featmgr.linux_mode:
            featmgr.priv_mode = RV.RiscvPrivileges.MACHINE
            featmgr.paging_mode = RV.RiscvPagingModes.DISABLE

        # exception delegation
        if featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
            featmgr.deleg_excp_to = RV.RiscvPrivileges.MACHINE
        elif featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            featmgr.deleg_excp_to = RV.RiscvPrivileges.SUPER

        return featmgr
