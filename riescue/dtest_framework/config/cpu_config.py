# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional, List

import riescue.lib.enums as RV
from riescue.lib.feature_discovery import FeatureDiscovery
from riescue.dtest_framework.config import Memory
from riescue.dtest_framework.config.pma_config import PmaConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TestGeneration:
    "Making all these optional since defaults are defined in ``FeatMgr``"

    repeat_times: Optional[int] = None
    big_endian: Optional[bool] = None
    counter_event_path: Optional[Path] = None
    secure_access_probability: Optional[int] = None
    secure_pt_probability: Optional[int] = None
    a_d_bit_randomization: Optional[int] = None
    pbmt_ncio_randomization: Optional[int] = None
    fs_randomization: Optional[int] = None
    fs_randomization_values: Optional[List[int]] = None
    vs_randomization: Optional[int] = None
    vs_randomization_values: Optional[List[int]] = None
    pmp_catchall: Optional[bool] = None  # Enable PMP catchall entries for S/U mode tests

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
class InterruptsSupported:
    """
    Per-cause flags declaring which standard RISC-V interrupt causes the target
    supports. Names are the lowercased ``InterruptCause`` enum members from
    coretp (MSI/MEI/MTI/SSI/SEI/STI). RiescueC and Voyager2 read this to filter
    unsupported causes out of delegation/enable/disable/clear bitmasks and to
    NOP unsupported TriggerInterrupt/AssertInterrupt steps.

    All fields default to ``True`` so cpuconfigs without the block keep the
    existing behavior.
    """

    msi: bool = True
    mei: bool = True
    mti: bool = True
    ssi: bool = True
    sei: bool = True
    sti: bool = True

    @classmethod
    def from_dict(cls, cfg: dict) -> InterruptsSupported:
        known = {f.name for f in fields(cls)}
        unknown = set(cfg) - known
        if unknown:
            raise ValueError(f"InterruptsSupported does not support field(s) {sorted(unknown)}")
        return cls(**{k: bool(v) for k, v in cfg.items()})

    def is_cause_supported(self, cause_name: str) -> bool:
        """Causes outside the six-bit set (e.g. COI, PLATFORM) pass through as supported."""
        return getattr(self, cause_name.lower(), True)


@dataclass(frozen=True)
class ClusterTopology:
    """
    Defines the mapping of hart IDs to clusters for multi-processor test generation.

    The cluster topology is the source of truth for which harts belong to which
    physical cluster. This allows MP generators to pin sequences to specific harts
    while automatically resolving their cluster membership.

    cpuconfig schema::

        {
          "cluster_topology": {
            "clusters": {
              "0": { "hart_ids": [0, 1, 2, 3] },
              "1": { "hart_ids": [8, 9, 10, 11] }
            }
          }
        }

    Validation rules:
    - Every listed hart_id must be unique across all clusters
    - Empty clusters are rejected
    - Cluster IDs are stored as integers (string keys in JSON are converted)
    """

    # cluster_id -> list of hart_ids in that cluster
    clusters: dict[int, list[int]] = field(default_factory=dict)
    # Reverse mapping: hart_id -> cluster_id (built from clusters)
    hart_to_cluster: dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, cfg: dict) -> ClusterTopology:
        """
        Construct ClusterTopology from a JSON dictionary.

        :param cfg: dictionary containing the cluster_topology configuration
        :raises ValueError: on validation errors (duplicates, empty clusters)
        """
        clusters_raw = cfg.get("clusters", {})
        clusters: dict[int, list[int]] = {}
        hart_to_cluster: dict[int, int] = {}
        seen_harts: set[int] = set()

        for cluster_id_str, cluster_data in clusters_raw.items():
            # Convert cluster_id to int (JSON keys are strings)
            try:
                cluster_id = int(cluster_id_str)
            except ValueError:
                raise ValueError(f"Cluster ID must be an integer, got: {cluster_id_str}")

            # Get hart_ids list
            if isinstance(cluster_data, dict):
                hart_ids = cluster_data.get("hart_ids", [])
            elif isinstance(cluster_data, list):
                # Allow shorthand: "0": [0, 1, 2, 3]
                hart_ids = cluster_data
            else:
                raise ValueError(f"Cluster {cluster_id} must be a dict with 'hart_ids' or a list, " f"got: {type(cluster_data).__name__}")

            # Validate non-empty
            if not hart_ids:
                raise ValueError(f"Cluster {cluster_id} has no hart_ids (empty clusters not allowed)")

            # Validate uniqueness
            for hart_id in hart_ids:
                if not isinstance(hart_id, int):
                    raise ValueError(f"hart_id must be an integer, got: {hart_id} in cluster {cluster_id}")
                if hart_id in seen_harts:
                    raise ValueError(f"hart_id {hart_id} appears in multiple clusters")
                seen_harts.add(hart_id)
                hart_to_cluster[hart_id] = cluster_id

            clusters[cluster_id] = list(hart_ids)

        return cls(clusters=clusters, hart_to_cluster=hart_to_cluster)

    def get_cluster_id(self, hart_id: int) -> Optional[int]:
        """Return the cluster_id for a given hart_id, or None if not found."""
        return self.hart_to_cluster.get(hart_id)

    def get_harts_in_cluster(self, cluster_id: int) -> list[int]:
        """Return list of hart_ids in the given cluster, or empty list if not found."""
        return self.clusters.get(cluster_id, [])


@dataclass(frozen=True)
class CpuConfig:
    """
    Data class containing infomration about the CPU and memory map.
    """

    DEFAULT_RESET_PC = 0x8000_0000

    memory: Memory = field(default_factory=Memory)
    features: FeatureDiscovery = field(default_factory=lambda: FeatureDiscovery({}))
    interrupts_supported: InterruptsSupported = field(default_factory=InterruptsSupported)
    test_gen: TestGeneration = field(default_factory=TestGeneration)
    cluster_topology: Optional[ClusterTopology] = None
    isa: list[str] = field(default_factory=list)
    reset_pc: int = DEFAULT_RESET_PC
    pma_config: Optional[PmaConfig] = None

    # Debug mode (RISC-V Debug): from features.debug
    debug_mode: bool = False
    # Debug ROM region: from mmap.io.debug_rom (address and size)
    debug_rom_address: Optional[int] = None
    debug_rom_size: Optional[int] = None

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
        interrupts_supported = InterruptsSupported.from_dict(cfg.get("interrupts_supported", {}))
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

        # Load PMA config from mmap.pma if present
        pma_config = None
        if "mmap" in cfg and "pma" in cfg["mmap"]:
            try:
                pma_config = PmaConfig.from_dict(cfg["mmap"]["pma"])
                log.debug(f"Loaded PMA config with {len(pma_config.regions)} regions and {len(pma_config.hints)} hints")
            except Exception as e:
                # Log warning but don't fail - PMA config is optional
                log.warning(f"Failed to load PMA config from cpuconfig: {e}")
                # Optionally re-raise if you want strict validation
                # raise ValueError(f"Invalid PMA configuration: {e}") from e

        # Load cluster topology if present
        cluster_topology = None
        if "cluster_topology" in cfg:
            try:
                cluster_topology = ClusterTopology.from_dict(cfg["cluster_topology"])
                log.debug(f"Loaded cluster topology with {len(cluster_topology.clusters)} clusters")
            except Exception as e:
                raise ValueError(f"Invalid cluster_topology configuration: {e}") from e

        # Debug mode (RISC-V Debug): from features (standard extension)
        debug_mode = features.is_feature_enabled("debug")
        # Debug ROM region: from mmap.io.debug_rom (address and size)
        debug_rom_address = None
        debug_rom_size = None
        mmap_io = cfg.get("mmap", {}).get("io", {})
        debug_rom = mmap_io.get("debug_rom", {})
        if debug_rom:
            raw_addr = debug_rom.get("address")
            if raw_addr is not None:
                debug_rom_address = int(raw_addr, 0) if isinstance(raw_addr, str) else int(raw_addr)
            raw_size = debug_rom.get("size")
            if raw_size is not None:
                debug_rom_size = int(raw_size, 0) if isinstance(raw_size, str) else int(raw_size)

        return cls(
            memory=memory,
            features=features,
            interrupts_supported=interrupts_supported,
            cluster_topology=cluster_topology,
            isa=cfg.get("isa", []),
            reset_pc=reset_pc,
            test_gen=tg,
            pma_config=pma_config,
            debug_mode=debug_mode,
            debug_rom_address=debug_rom_address,
            debug_rom_size=debug_rom_size,
        )
