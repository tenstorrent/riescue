# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
import argparse
from pathlib import Path
from argparse import Namespace
from dataclasses import dataclass, field, replace
from typing import Optional

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.parser import ParsedTestHeader
from .candidate import Candidate
from .adapaters import TestConfigAdapter, CpuConfigAdapter, CliAdapter
from .cpu_config import CpuConfig
from .memory import Memory
from .featmanager import FeatMgr
from .conf import Conf
import riescue.dtest_framework.config.cmdline as cmdline
from riescue.lib.feature_discovery import FeatureDiscovery

log = logging.getLogger(__name__)


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

        from riescue import FeatMgrBuilder
        from riescue.lib.rand import RandNum

        rng = RandNum(seed=42)
        builder = FeatMgrBuilder()
        builder.with_test_header(header)
        builder.with_cpu_json(path)
        builder.with_args(args)
        featmgr = builder.build(rng=rng)

    """

    featmgr: FeatMgr = field(default_factory=FeatMgr)
    conf: Optional[Conf] = None  #: ``Conf`` class applied to FeatMgrBuilder right before building the ``FeatMgr``
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

        .. seealso:: :doc:`/reference/riescue_test_file/test_headers_reference` for the support Test Header format.

        """

        return TestConfigAdapter().apply(self, header)

    def with_cpu_json(self, path: Path) -> FeatMgrBuilder:
        """
        Update builder with cpu config contents

        :param path: The path to the cpu config file

        .. seealso:: :doc:`/reference/config/configuration_schema` for the required JSON format.

        """
        return CpuConfigAdapter().apply(self, path)

    def with_args(self, args: Namespace) -> FeatMgrBuilder:
        """
        Update builder with command line arguments

        :param args: The command line arguments to update the builder with

        .. seealso:: :doc:`/reference/config/cli` for the command line interface.

        """
        return CliAdapter().apply(self, args)

    def build(self, rng: RandNum) -> FeatMgr:
        """
        Where randomization happens. Verbose, but allows for easier debugging

        :param rng: The ``RandNum`` generator should be using randomization.

        :returns: A ``FeatMgr`` object with the randomization applied
        """

        featmgr = self.featmgr.duplicate()

        if self.conf is not None:
            self.conf.pre_build(self)

        if featmgr.wysiwyg:
            self.priv_mode = Candidate(RV.RiscvPrivileges.MACHINE)  # Always run in Machine mode for wysiwyg mode

        if self.paging_g_mode == RV.RiscvPagingModes.SV39:
            self.paging_mode = Candidate(RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.DISABLE)

        # validate priv_mode, only use priv_modes supported by platform
        priv_mode_candiadtes: list[RV.RiscvPrivileges] = []
        for priv_mode in self.priv_mode:
            if priv_mode in featmgr.supported_priv_modes:
                priv_mode_candiadtes.append(priv_mode)
        if len(priv_mode_candiadtes) == 0:
            platform_supported_modes = ", ".join(str(priv_mode) for priv_mode in featmgr.supported_priv_modes)
            config_chosen_modes = ", ".join(str(priv_mode) for priv_mode in self.priv_mode)
            raise ValueError(f"No privilege mode supported by the platform (platform supports {platform_supported_modes} but user requested {config_chosen_modes})")
        self.priv_mode = Candidate(*priv_mode_candiadtes)

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
        if featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            # if test env virtualized, should not be running machine mode tests
            featmgr.priv_mode = RV.RiscvPrivileges.SUPER
            featmgr.deleg_excp_to = RV.RiscvPrivileges.SUPER
        elif featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
            featmgr.deleg_excp_to = RV.RiscvPrivileges.MACHINE

        # disable WFI wait if interrupts are disabled
        if not featmgr.interrupts_enabled:
            log.info("Interrupts are disabled, cannot use WFI wait in Runtime loops.")
            featmgr.disable_wfi_wait = True

        if self.conf is not None:
            self.conf.post_build(featmgr)
            self.conf.add_hooks(featmgr)

        return featmgr

    def duplicate(self) -> "FeatMgrBuilder":
        """
        Used to duplicate a FeatMgrBuilder for each test case.
        """
        new_builder = replace(self)
        new_builder.featmgr = self.featmgr.duplicate()
        return new_builder

    def with_priv_mode(self, priv_mode: RV.RiscvPrivileges) -> FeatMgrBuilder:
        """
        Shortcut for setting privilege mode candidate to a single item
        """
        self.priv_mode = Candidate(priv_mode)
        return self

    def with_paging_mode(self, paging_mode: RV.RiscvPagingModes) -> FeatMgrBuilder:
        """
        Shortcut for setting paging mode candidate to a single item
        """

        self.paging_mode = Candidate(paging_mode)
        return self
