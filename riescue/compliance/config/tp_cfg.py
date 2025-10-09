# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .mode_cfg import ModeCfg

from riescue.lib.rand import RandNum
from riescue.dtest_framework.config import FeatMgrBuilder, FeatMgr
from riescue.lib.toolchain import Toolchain, Spike, Whisper, Compiler, Disassembler

log = logging.getLogger(__name__)


@dataclass
class TpCfg(ModeCfg):
    """
    Configuration for ``TpMode``
    """

    isa: str = ""
    run_dir: Path = Path(".")
    test_plan_name: str = ""

    featmgr: FeatMgr = field(default_factory=FeatMgr)
    toolchain: Toolchain = field(default_factory=Toolchain)
    _rng: Optional[RandNum] = None
    _featmgr_builder: FeatMgrBuilder = field(default_factory=FeatMgrBuilder)

    def with_args(self, args: argparse.Namespace) -> "TpCfg":
        """
        Configure command line arguments. Verbose, but type safe and more IDE friendly than setattr()
        """
        # handle tp-specific args here
        self.isa = args.isa
        self.test_plan_name = args.test_plan_name

        # handle pass-through args
        self._featmgr_builder = self._featmgr_builder.with_args(args)
        self.toolchain = Toolchain(
            compiler=Compiler.from_clargs(args),
            disassembler=Disassembler.from_clargs(args),
            spike=Spike.from_clargs(args),
            whisper=Whisper.from_clargs(args),
        )
        return self

    def build(self, rng: RandNum, run_dir: Path) -> TpCfg:
        """
        Build the configuration. Requires ``RandNum`` to build ``FeatMgr``.

        :param rng: The random number generator to use, required to build ``FeatMgr``
        :param run_dir: The directory to run the test in
        :returns: The built configuration
        """
        self.featmgr = self._featmgr_builder.build(rng)
        self.toolchain = self.toolchain
        self.run_dir = run_dir
        return self
