# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
import json
from typing import TYPE_CHECKING
from pathlib import Path

import riescue.lib.enums as RV
from riescue.dtest_framework.parser import ParsedTestHeader
from .adapter import Adapter
from ..cpu_config import CpuConfig

if TYPE_CHECKING:
    from ..builder import FeatMgrBuilder

log = logging.getLogger(__name__)


class CpuConfigAdapter(Adapter):
    """
    Adapater for test environment configuration from the ``cpu_config.json`` file.

    Currently just sets the ``cpu_config``, ``memory``, and ``reset_pc`` fields of the ``FeatMgrBuilder``.
    Any additional fields are ignored.
    """

    def apply(self, builder: FeatMgrBuilder, src: Path) -> FeatMgrBuilder:
        featmgr = builder.featmgr

        with open(src, "r") as f:
            cfg = json.load(f)

        cpu_config = CpuConfig.from_dict(cfg, feature_overrides=" ".join(builder.features))

        # legacy support for htif in memmap. This should probably be an explict field to make it clear where it's coming from
        try:
            featmgr.io_htif_addr = int(cfg["mmap"]["io"]["htif"]["address"], 0)
        except KeyError:
            log.warning("Unable to find htif address in cpu_config at cfg['mmap']['io']['htif']['address']. Using any available address")
            featmgr.io_htif_addr = None

        featmgr.cpu_config = cpu_config
        featmgr.memory = cpu_config.memory
        featmgr.reset_pc = cpu_config.reset_pc
        featmgr.feature = cpu_config.features
        featmgr.wysiwyg = cpu_config.features.is_feature_enabled("wysiwyg")

        # Test Generation configuration
        if cpu_config.test_gen.repeat_times is not None:
            featmgr.repeat_times = cpu_config.test_gen.repeat_times
        if cpu_config.test_gen.big_endian is not None:
            featmgr.big_endian = cpu_config.test_gen.big_endian
        if cpu_config.test_gen.counter_event_path is not None:
            featmgr.counter_event_path = cpu_config.test_gen.counter_event_path
        if cpu_config.test_gen.disable_wfi_wait is not None:
            featmgr.disable_wfi_wait = cpu_config.test_gen.disable_wfi_wait
        if cpu_config.test_gen.secure_access_probability is not None:
            featmgr.secure_access_probability = cpu_config.test_gen.secure_access_probability
        if cpu_config.test_gen.secure_pt_probability is not None:
            featmgr.secure_pt_probability = cpu_config.test_gen.secure_pt_probability
        if cpu_config.test_gen.a_d_bit_randomization is not None:
            featmgr.a_d_bit_randomization = cpu_config.test_gen.a_d_bit_randomization
        if cpu_config.test_gen.pbmt_ncio_randomization is not None:
            featmgr.pbmt_ncio_randomization = cpu_config.test_gen.pbmt_ncio_randomization

        return builder
