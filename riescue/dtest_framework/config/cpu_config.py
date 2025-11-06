# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import riescue.lib.enums as RV
from riescue.lib.feature_discovery import FeatureDiscovery
from riescue.dtest_framework.config import Memory


@dataclass(frozen=True)
class TestGeneration:
    "Making all these optional since defaults are defined in ``FeatMgr``"

    repeat_times: Optional[int] = None
    big_endian: Optional[bool] = None
    counter_event_path: Optional[Path] = None
    disable_wfi_wait: Optional[bool] = None
    secure_access_probability: Optional[int] = None
    secure_pt_probability: Optional[int] = None
    a_d_bit_randomization: Optional[int] = None
    pbmt_ncio_randomization: Optional[int] = None

    @classmethod
    def from_dict(cls, cfg: dict) -> TestGeneration:
        """
        Construct TestGeneration object from a JSON dictionary. Ignores fields starting with underscore.
        """
        field_names = set(f.name for f in fields(cls) if not f.name.startswith("_"))
        valid_fields = {}
        for k, v in cfg.items():
            if k.startswith("_"):
                continue
            if k in field_names:
                valid_fields[k] = v
            else:
                raise ValueError(f"TestGeneration object does not support field {k}")
        return cls(**valid_fields)


@dataclass(frozen=True)
class CpuConfig:
    """
    Data class containing infomration about the CPU and memory map.
    """

    DEFAULT_RESET_PC = 0x8000_0000

    memory: Memory = field(default_factory=Memory)
    features: FeatureDiscovery = field(default_factory=lambda: FeatureDiscovery({}))
    test_gen: TestGeneration = field(default_factory=TestGeneration)
    isa: list[str] = field(default_factory=list)
    reset_pc: int = DEFAULT_RESET_PC

    @classmethod
    def from_json(cls, path: Path, feature_overrides: Optional[str] = None) -> CpuConfig:
        """
        Load a CpuConfig from a json file.

        :param path: path to the json file
        :param disallow_mmio: if True, raise an error if mmio is found in the memory map
        :raises ValueError: on schema violations
        """

        with path.open() as f:
            cfg = json.load(f)
        return cls.from_dict(cfg, feature_overrides)

    @classmethod
    def from_dict(cls, cfg: dict, feature_overrides: Optional[str] = None) -> CpuConfig:
        """
        Construct from a dictionary.

        :param cfg: dictionary containing the configuration
        :param feature_overrides: optional string containing feature overrides. E.g. ``ext_v.enable ext_f.disable``
        """

        memory = Memory.from_dict(cfg.get("mmap", {}))
        features = FeatureDiscovery.from_dict_with_overrides(cfg, feature_overrides)
        tg = TestGeneration.from_dict(cfg.get("test_generation", {}))

        # reset PC might be encoded as a string ``0x8000_0000`` or direct integer ``0`` ; need to support both
        # Parse reset_pc
        reset_pc = cfg.get("reset_pc", cls.DEFAULT_RESET_PC)
        if isinstance(reset_pc, str):
            try:
                reset_pc = int(reset_pc, 0)
            except TypeError:
                raise TypeError(f"Invalid reset_pc: {reset_pc}. Supported formatting types are int and hex string, e.g. 0x80000000")
        elif not isinstance(reset_pc, int):
            raise ValueError(f"Invalid reset_pc: {reset_pc}. Supported formatting types are int and hex string, e.g. 0x80000000")

        return cls(
            memory=memory,
            features=features,
            isa=cfg.get("isa", []),
            reset_pc=reset_pc,
            test_gen=tg,
        )
