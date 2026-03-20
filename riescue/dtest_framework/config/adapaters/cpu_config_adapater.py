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

        try:
            featmgr.io_imsic_mfile_addr = int(cfg["mmap"]["io"]["imsic_mfile"]["address"], 0)
            featmgr.io_imsic_mfile_stride = int(cfg["mmap"]["io"]["imsic_mfile"]["stride"], 0)
        except KeyError:
            featmgr.io_imsic_mfile_addr = None
            featmgr.io_imsic_mfile_stride = None
        try:
            featmgr.io_imsic_sfile_addr = int(cfg["mmap"]["io"]["imsic_sfile"]["address"], 0)
            featmgr.io_imsic_sfile_stride = int(cfg["mmap"]["io"]["imsic_sfile"]["stride"], 0)
        except KeyError:
            featmgr.io_imsic_sfile_addr = None
            featmgr.io_imsic_sfile_stride = None
        try:
            featmgr.io_maplic_addr = int(cfg["mmap"]["io"]["maplic"]["address"], 0)
            featmgr.io_maplic_size = int(cfg["mmap"]["io"]["maplic"]["size"], 0)
        except KeyError:
            featmgr.io_maplic_addr = None
            featmgr.io_maplic_size = None
        try:
            featmgr.io_saplic_addr = int(cfg["mmap"]["io"]["saplic"]["address"], 0)
            featmgr.io_saplic_size = int(cfg["mmap"]["io"]["saplic"]["size"], 0)
        except KeyError:
            featmgr.io_saplic_addr = None
            featmgr.io_saplic_size = None

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
        if cpu_config.test_gen.secure_access_probability is not None:
            featmgr.secure_access_probability = cpu_config.test_gen.secure_access_probability
        if cpu_config.test_gen.secure_pt_probability is not None:
            featmgr.secure_pt_probability = cpu_config.test_gen.secure_pt_probability
        if cpu_config.test_gen.a_d_bit_randomization is not None:
            featmgr.a_d_bit_randomization = cpu_config.test_gen.a_d_bit_randomization
        if cpu_config.test_gen.pbmt_ncio_randomization is not None:
            featmgr.pbmt_ncio_randomization = cpu_config.test_gen.pbmt_ncio_randomization
        if cpu_config.test_gen.fs_randomization is not None:
            featmgr.fs_randomization = cpu_config.test_gen.fs_randomization
        if cpu_config.test_gen.fs_randomization_values is not None:
            vals = cpu_config.test_gen.fs_randomization_values
            if any(v < 0 or v > 3 for v in vals):
                raise ValueError("fs_randomization_values: each value must be 0-3 (0=Off 1=Initial 2=Clean 3=Dirty)")
            featmgr.fs_randomization_values = vals
        if cpu_config.test_gen.vs_randomization is not None:
            featmgr.vs_randomization = cpu_config.test_gen.vs_randomization
        if cpu_config.test_gen.vs_randomization_values is not None:
            vals = cpu_config.test_gen.vs_randomization_values
            if any(v < 0 or v > 3 for v in vals):
                raise ValueError("vs_randomization_values: each value must be 0-3 (0=Off 1=Initial 2=Clean 3=Dirty)")
            featmgr.vs_randomization_values = vals

        # Debug mode (from features.debug) and debug ROM (from mmap.io.debug_rom)
        featmgr.debug_mode = cpu_config.debug_mode
        if cpu_config.debug_rom_address is not None:
            featmgr.debug_rom_address = cpu_config.debug_rom_address
        if cpu_config.debug_rom_size is not None:
            featmgr.debug_rom_size = cpu_config.debug_rom_size

        return builder
