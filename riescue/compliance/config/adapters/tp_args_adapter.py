# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
import argparse
from typing import TYPE_CHECKING

from .base import BaseAdapter
from riescue.dtest_framework.config import Candidate
from riescue.lib.toolchain import Toolchain, Spike, Whisper, Compiler, Disassembler
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

        # handle pass-through args
        builder.featmgr_builder.with_args(args)

        # build tool chain
        cfg.toolchain = Toolchain(
            compiler=Compiler.from_clargs(args),
            disassembler=Disassembler.from_clargs(args),
            spike=Spike.from_clargs(args),
            whisper=Whisper.from_clargs(args),
        )
        return builder
