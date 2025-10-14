# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import argparse
from pathlib import Path
from argparse import Namespace
from dataclasses import dataclass, field, replace
from typing import Optional

from riescue.lib.rand import RandNum
from .resource import Resource
from .base_builder import BaseBuilder
from .tp_cfg import TpCfg
from .adapters import TpArgsAdapter
from riescue.dtest_framework.config import FeatMgrBuilder, FeatMgr
from riescue.lib.toolchain import Toolchain


@dataclass
class TpBuilder(BaseBuilder):
    """
    Constructs :class:`TpCfg` for use in :class:`TpMode`
    """

    cfg: TpCfg = field(default_factory=TpCfg)
    featmgr_builder: FeatMgrBuilder = field(default_factory=FeatMgrBuilder)

    def with_args(self, args: argparse.Namespace) -> "TpBuilder":
        """
        Configure command line arguments.
        """
        return TpArgsAdapter().apply(self, args)

    def build(
        self,
        seed: int,
        featmgr: Optional[FeatMgr] = None,
        toolchain: Optional[Toolchain] = None,
    ) -> TpCfg:
        """
        Start of randomization. :class:`FeatMgrBuilder` builds :class:`FeatMgr` with randomization.
        Ensures that ``RandNum`` is tied to the :class:`TpCfg` instance.

        The :class:`Toolchain` must be configured with ISS before building. This can be done either by:

        1. Calling :meth:`with_args` before :meth:`build` (typical CLI usage)
        2. Passing a configured :class:`Toolchain` via the ``toolchain`` parameter (programmatic usage)


        :param seed: The seed to use for the random number generator.
        :param featmgr: Optional :class:`FeatMgr` to use for the cfg. Builds a new :class:`FeatMgr` if None
        :param toolchain: Optional :class:`Toolchain` to use for the cfg. Uses default :class:`Toolchain` if not provided.

        Example usage (using CLI arguments):

        .. code-block:: python

            builder = TpBuilder()
            builder = builder.with_args(args)  # configures toolchain from CLI args
            tp_cfg = builder.build(seed=42)

        Example usage (using library code):

        .. code-block:: python

            builder = TpBuilder()
            toolchain = Toolchain(spike_path="/path/to/spike", whisper_path="/path/to/whisper")
            tp_cfg = builder.build(seed=42, toolchain=toolchain)
        """

        rng = RandNum(seed)  # create a new RandNum instance. Avoiding tying RandNum to BringupMode
        if featmgr is None:
            featmgr = self.featmgr_builder.build(rng)
        tp_cfg = self.cfg.duplicate(featmgr=featmgr)
        tp_cfg.seed = seed

        if toolchain is not None:
            tp_cfg.toolchain = toolchain
        return tp_cfg
