# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import logging
from dataclasses import dataclass, field, replace

from riescue.dtest_framework.config import FeatMgr
from riescue.lib.toolchain import Toolchain, Whisper

log = logging.getLogger(__name__)


@dataclass
class TpCfg:
    """
    Configuration for ``TpMode``.
    """

    featmgr: FeatMgr = field(default_factory=FeatMgr)
    toolchain: Toolchain = field(default_factory=lambda: Toolchain(whisper=Whisper()))

    isa: str = ""
    test_plan_name: str = ""
    seed: int = 0

    # copy method
    def duplicate(self, featmgr: FeatMgr) -> "TpCfg":
        """
        Duplicate the configuration
        """
        new_cfg = replace(self)
        new_cfg.featmgr = featmgr
        return new_cfg
