# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import argparse
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, NamedTuple
from itertools import product, cycle, islice

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from .resource import Resource
from .base_builder import BaseBuilder
from .adapters import BringupTestAdapter, BringupArgsAdapter
from riescue.dtest_framework.config import FeatMgrBuilder, FeatMgr
from riescue.compliance.lib.fpgen_intf import FpGenInterface


class ResourceConfig(NamedTuple):
    """
    Metadata about how a Resource was configured. Used in :meth:`products` to track the configuration of each Resource.
    """

    resource: Resource
    priv_mode: RV.RiscvPrivileges
    paging_mode: RV.RiscvPagingModes
    extension: str
    seed: int
    repro: str


@dataclass
class ResourceBuilder(BaseBuilder):
    """
    Constructs :class:`Resource` for use in :class:`BringupMode`
    """

    resource: Resource = field(default_factory=Resource)
    featmgr_builder: FeatMgrBuilder = field(default_factory=FeatMgrBuilder)

    # TODO: helper fields / methods for easily setting high-level configs

    def with_args(self, args: argparse.Namespace) -> "ResourceBuilder":
        return BringupArgsAdapter().apply(self, args)

    def with_bringup_test_json(self, bringup_test_json: Path) -> "ResourceBuilder":
        return BringupTestAdapter().apply(self, bringup_test_json)

    def build(
        self,
        seed: int,
        run_dir: Path,
        featmgr: Optional[FeatMgr] = None,
    ) -> Resource:
        """
        Start of randomization. :class:`FeatMgrBuilder` builds :class:`FeatMgr` with randomization.
        Ensures that ``RandNum`` is tied to the ``Resource`` instance.

        The :class:`Toolchain` must be configured with ISS before building. This can be done either by:

        1. Calling :meth:`with_args` before :meth:`build` (typical CLI usage)
        2. Passing a configured :class:`Toolchain` via the ``toolchain`` parameter (programmatic usage)


        :param seed: The seed to use for the random number generator.
        :param featmgr: Optional :class:`FeatMgr` to use for the resource. Builds a new :class:`FeatMgr` if None
        :param toolchain: Optional :class:`Toolchain` to use for the resource. Uses default :class:`Toolchain` if not provided.

        Example usage (using CLI arguments):

        .. code-block:: python

            builder = ResourceBuilder()
            builder = builder.with_args(args)  # configures toolchain from CLI args
            resource = builder.build(seed=42)

        Example usage (using library code):

        .. code-block:: python

            builder = ResourceBuilder()
            toolchain = Toolchain(spike_path="/path/to/spike", whisper_path="/path/to/whisper")
            resource = builder.build(seed=42, toolchain=toolchain)
        """
        rng = RandNum(seed)  # create a new RandNum instance. Allows RiescueC generate() methods to be re-runnable
        if featmgr is not None:
            featmgr = featmgr
        else:
            featmgr = self.featmgr_builder.build(rng)
        resource = self.resource.duplicate(featmgr=featmgr)  # create a new resource instance to avoid mutating the original
        resource.seed = seed

        # resolve paths to config files - needed to find files relative to riescue directory, prioritizes riescue-relative over cwd-relative
        resource.default_config = self.find_config(resource.default_config)
        if resource.user_config is not None:
            resource.user_config = self.find_config(resource.user_config)
        resource.fp_config = self.find_config(resource.fp_config)
        resource.run_dir = run_dir

        # fpgen
        resource.fpgen_on = resource.fpgen_on or bool(int(os.getenv("FPGEN_ENABLED", "0")))
        if resource.fpgen_on:
            resource.fpgen_intf = FpGenInterface()
            resource.fpgen_intf.configure(resource.seed, resource.fast_fpgen)

        # RiescueC-specific configuration
        resource.featmgr.pbmt_ncio_randomization = 0
        resource.featmgr.disable_wfi_wait = True  # RVTOOLS-4204

        # tests require that repeat times is set to 1, e.g. amoswap can only be ran a single time
        # If disable_pass is set, repeat times should be the repeat_runtime
        if resource.disable_pass:
            resource.featmgr.repeat_times = resource.repeat_runtime
        else:
            resource.featmgr.repeat_times = 1

        return resource

    def products(
        self,
        seed: int,
        run_dir: Path,
        priv_modes: list[RV.RiscvPrivileges],
        paging_modes: list[RV.RiscvPagingModes],
        extensions: list[str],
        max_tests: int,
    ) -> list[ResourceConfig]:
        """
        Generate Resource objects for all combinations of configurations.

        This assumes that the ResourceBuilder has already been configured (with_args, with_bringup_test_json, etc.)
        It copies previously applied configurations


        :param seed: Starting seed for RandNum instances
        :param priv_modes: List of privilege modes to test
        :param paging_modes: List of paging modes to test
        :param extensions: List of extension names to test
        :param max_tests: Maximum number of :class:`ResourceConfig` objects to generate
        :return: List of configured :class:`ResourceConfig` objects, one for each combination of configurations
        """

        def is_valid_combo(priv, paging):
            if paging != RV.RiscvPagingModes.DISABLE and priv == RV.RiscvPrivileges.MACHINE:
                return False
            return True

        combinations = [(p, pg, ext) for p, pg, ext in product(priv_modes, paging_modes, extensions) if is_valid_combo(p, pg)]

        configs: list[ResourceConfig] = []
        for idx, (priv, paging, ext) in enumerate(islice(cycle(combinations), max_tests)):
            new_builder = ResourceBuilder()
            new_builder.featmgr_builder = self.featmgr_builder.duplicate().with_priv_mode(priv).with_paging_mode(paging)
            new_builder.resource = self.resource.duplicate()
            new_builder.resource.include_extensions = [ext]
            new_builder.resource.testcase_name = f"{ext}_{priv.name.lower()}_{paging.name.lower()}_{idx}"

            repro = f"riescuec --seed {seed + idx} --test_priv_mode {priv.name} --test_paging_mode {paging.name}"
            repro += f" --json compliance/tests/special/nothing.json --include_extensions {ext} "

            resource = new_builder.build(seed + idx, run_dir)
            config = ResourceConfig(resource, priv, paging, ext, seed + idx, repro)
            configs.append(config)

        return configs
