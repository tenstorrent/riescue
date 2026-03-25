# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
import argparse
from typing import TYPE_CHECKING

from .base import BaseAdapter
from riescue.dtest_framework.config import Candidate
from riescue.lib.toolchain import Toolchain
import riescue.lib.enums as RV

if TYPE_CHECKING:
    from .. import TpBuilder


log = logging.getLogger(__name__)


class TpArgsAdapter(BaseAdapter):
    """
    Adapter for :class:`BringupTest`.
    """

    def apply(self, builder: TpBuilder, src: argparse.Namespace) -> TpBuilder:

        args = src
        builder = builder
        cfg = builder.cfg

        cfg.isa = args.isa
        cfg.test_plan_name = args.test_plan_name

        # configure feat manager builder
        # handle pass-through args
        if args.cpuconfig is not None:
            cpu_config = self.find_config(args.cpuconfig)
            builder.featmgr_builder.with_cpu_json(cpu_config)
        else:
            log.error("Using default memory map (not default cpuconfig). This is likely in error")
        builder.featmgr_builder.with_args(args)

        # build tool chain — use build_both=True so missing ISS tools are
        # handled gracefully (FileNotFoundError caught → None) rather than
        # crashing here before the caller's fallback logic runs.
        cfg.toolchain = Toolchain.from_clargs(args, build_both=True)
        return builder
