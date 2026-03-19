# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class PmaAttributes:
    """
    PMA attribute specification.

    :param memory_type: Memory type ('memory' | 'io' | 'ch0' | 'ch1')
    :param cacheability: Cache behavior for memory type ('cacheable' | 'noncacheable')
    :param combining: Combining behavior for IO type ('combining' | 'noncombining')
    :param read: Read permission
    :param write: Write permission
    :param execute: Execute permission
    :param amo_type: Atomic operation type ('none' | 'logical' | 'swap' | 'arithmetic')
    :param routing: Coherency routing ('coherent' | 'noncoherent')
    """

    memory_type: str = "memory"
    cacheability: Optional[str] = None  # 'cacheable' | 'noncacheable' (for memory)
    combining: Optional[str] = None  # 'combining' | 'noncombining' (for io)
    read: bool = True
    write: bool = True
    execute: bool = True
    amo_type: str = "arithmetic"  # 'none' | 'logical' | 'swap' | 'arithmetic'
    routing: str = "coherent"  # 'coherent' | 'noncoherent'

    def __post_init__(self):
        """Validate attributes after initialization"""
        # Validate memory_type
        valid_memory_types = {"memory", "io", "ch0", "ch1"}
        if self.memory_type not in valid_memory_types:
            raise ValueError(f"Invalid memory_type: {self.memory_type}. Must be one of {valid_memory_types}")

        # Validate cacheability for memory type
        if self.memory_type == "memory":
            if self.cacheability is None:
                self.cacheability = "cacheable"  # Default
            valid_cacheability = {"cacheable", "noncacheable"}
            if self.cacheability not in valid_cacheability:
                raise ValueError(f"Invalid cacheability: {self.cacheability}. Must be one of {valid_cacheability}")

        # Validate combining for io type
        if self.memory_type == "io":
            if self.combining is None:
                self.combining = "noncombining"  # Default
            valid_combining = {"combining", "noncombining"}
            if self.combining not in valid_combining:
                raise ValueError(f"Invalid combining: {self.combining}. Must be one of {valid_combining}")

        # Validate amo_type
        valid_amo_types = {"none", "logical", "swap", "arithmetic"}
        if self.amo_type not in valid_amo_types:
            raise ValueError(f"Invalid amo_type: {self.amo_type}. Must be one of {valid_amo_types}")

        # Validate routing
        valid_routing = {"coherent", "noncoherent"}
        if self.routing not in valid_routing:
            raise ValueError(f"Invalid routing: {self.routing}. Must be one of {valid_routing}")

    @classmethod
    def from_dict(cls, cfg: dict) -> PmaAttributes:
        """
        Create PmaAttributes from dictionary.

        :param cfg: Dictionary containing PMA attributes
        :return: PmaAttributes instance
        :raises ValueError: if attributes are invalid
        """
        return cls(
            memory_type=cfg.get("memory_type", "memory"),
            cacheability=cfg.get("cacheability"),
            combining=cfg.get("combining"),
            read=cfg.get("read", True),
            write=cfg.get("write", True),
            execute=cfg.get("execute", True),
            amo_type=cfg.get("amo_type", "arithmetic"),
            routing=cfg.get("routing", "coherent"),
        )

    def to_pma_info_dict(self) -> dict:
        """
        Convert to dictionary compatible with PmaInfo.

        :return: Dictionary with PmaInfo-compatible keys
        """
        result = {"pma_memory_type": self.memory_type, "pma_read": self.read, "pma_write": self.write, "pma_execute": self.execute, "pma_amo_type": self.amo_type, "pma_routing_to": self.routing}

        # Add cacheability or combining based on memory type
        if self.memory_type == "memory":
            result["pma_cacheability"] = self.cacheability or "cacheable"
        elif self.memory_type == "io":
            result["pma_combining"] = self.combining or "noncombining"

        return result


@dataclass
class PmaRegionConfig:
    """
    Configuration for a specific PMA region.

    :param name: Unique name for the region
    :param base: Base address (optional, auto-generated if not specified)
    :param size: Size in bytes (optional, defaults to 16MB if not specified)
    :param attributes: PMA attributes for this region
    :param adjacent_to: Name of adjacent region (optional)
    :param auto_generate: If True, generate automatically from hints
    """

    name: str
    base: Optional[int] = None
    size: Optional[int] = None
    attributes: PmaAttributes = field(default_factory=PmaAttributes)
    adjacent_to: Optional[str] = None
    auto_generate: bool = False

    def __post_init__(self):
        """Validate region configuration"""
        if not self.name:
            raise ValueError("PMA region name cannot be empty")

        if self.size is not None and self.size <= 0:
            raise ValueError(f"PMA region size must be positive, got {self.size}")

        if self.base is not None and self.base < 0:
            raise ValueError(f"PMA region base must be non-negative, got {self.base}")

    @classmethod
    def from_dict(cls, cfg: dict) -> PmaRegionConfig:
        """
        Create PmaRegionConfig from dictionary.

        :param cfg: Dictionary containing region configuration
        :return: PmaRegionConfig instance
        :raises ValueError: if configuration is invalid
        """
        if "name" not in cfg:
            raise ValueError("PMA region configuration must have 'name' field")

        # Parse base address
        base = None
        if "base" in cfg:
            base_value = cfg["base"]
            if isinstance(base_value, str):
                base = int(base_value, 0)  # Supports hex strings
            else:
                base = int(base_value)

        # Parse size
        size = None
        if "size" in cfg:
            size_value = cfg["size"]
            if isinstance(size_value, str):
                size = int(size_value, 0)  # Supports hex strings
            else:
                size = int(size_value)

        # Parse attributes
        attributes = PmaAttributes.from_dict(cfg.get("attributes", {}))

        return cls(name=cfg["name"], base=base, size=size, attributes=attributes, adjacent_to=cfg.get("adjacent_to"), auto_generate=cfg.get("auto_generate", False))


@dataclass
class PmaHintConfig:
    """
    Configuration for PMA hint from JSON.

    :param name: Unique name for the hint
    :param combinations: List of specific PMA attribute combinations
    :param adjacent: If True, request adjacent regions
    :param min_regions: Minimum number of regions to generate
    :param max_regions: Maximum number of regions to generate
    :param size: Size of PMA regions to generate (optional)
    """

    name: str
    combinations: list[dict] = field(default_factory=list)
    adjacent: bool = False
    min_regions: Optional[int] = None
    max_regions: Optional[int] = None
    size: Optional[int] = None

    def __post_init__(self):
        """Validate hint configuration"""
        if not self.name:
            raise ValueError("PMA hint name cannot be empty")

        if self.min_regions is not None and self.min_regions < 0:
            raise ValueError(f"min_regions must be non-negative, got {self.min_regions}")

        if self.max_regions is not None and self.max_regions < 0:
            raise ValueError(f"max_regions must be non-negative, got {self.max_regions}")

        if self.min_regions is not None and self.max_regions is not None and self.min_regions > self.max_regions:
            raise ValueError(f"min_regions ({self.min_regions}) cannot be greater than max_regions ({self.max_regions})")

    @classmethod
    def from_dict(cls, cfg: dict) -> PmaHintConfig:
        """
        Create PmaHintConfig from dictionary.

        :param cfg: Dictionary containing hint configuration
        :return: PmaHintConfig instance
        :raises ValueError: if configuration is invalid
        """
        if "name" not in cfg:
            raise ValueError("PMA hint configuration must have 'name' field")

        # Parse size if provided (supports hex strings)
        size = None
        if "size" in cfg:
            size_val = cfg["size"]
            if isinstance(size_val, str):
                size = int(size_val, 0)  # Supports hex (0x...) and decimal
            else:
                size = int(size_val)

        return cls(name=cfg["name"], combinations=cfg.get("combinations", []), adjacent=cfg.get("adjacent", False), min_regions=cfg.get("min_regions"), max_regions=cfg.get("max_regions"), size=size)


@dataclass
class PmaConfig:
    """
    Top-level PMA configuration.

    :param regions: List of explicitly configured PMA regions
    :param hints: List of PMA hints for automatic generation
    :param max_regions: Maximum number of PMA regions (default 15, leaving 1 for default)
    :param default_region: Configuration for default catch-all region
    """

    regions: list[PmaRegionConfig] = field(default_factory=list)
    hints: list[PmaHintConfig] = field(default_factory=list)
    max_regions: int = 15
    default_region: Optional[dict] = None

    def __post_init__(self):
        """Validate PMA configuration"""
        if self.max_regions < 1 or self.max_regions > 15:
            raise ValueError(f"max_regions must be between 1 and 15, got {self.max_regions}")

        # Check for duplicate region names
        region_names = [r.name for r in self.regions]
        if len(region_names) != len(set(region_names)):
            raise ValueError(f"Duplicate PMA region names found: {region_names}")

        # Check for duplicate hint names
        hint_names = [h.name for h in self.hints]
        if len(hint_names) != len(set(hint_names)):
            raise ValueError(f"Duplicate PMA hint names found: {hint_names}")

    @classmethod
    def from_dict(cls, cfg: dict) -> PmaConfig:
        """
        Load PMA config from JSON dictionary.

        :param cfg: Dictionary containing PMA configuration
        :return: PmaConfig instance
        :raises ValueError: if configuration is invalid
        """
        regions = []
        if "regions" in cfg:
            regions_data = cfg["regions"]
            # Support both list and dict formats
            if isinstance(regions_data, dict):
                # Convert dict to list, using key as name if name not present
                regions_list = []
                for key, region_cfg in regions_data.items():
                    if not isinstance(region_cfg, dict):
                        raise ValueError(f"PMA region '{key}' must be a dictionary")
                    # Add name from key if not present in config
                    if "name" not in region_cfg:
                        region_cfg = {**region_cfg, "name": key}
                    regions_list.append(region_cfg)
                regions_data = regions_list
            elif not isinstance(regions_data, list):
                raise ValueError("PMA 'regions' must be a list or dictionary")

            for idx, region_cfg in enumerate(regions_data):
                try:
                    regions.append(PmaRegionConfig.from_dict(region_cfg))
                except Exception as e:
                    raise ValueError(f"Error parsing PMA region at index {idx}: {e}") from e

        hints = []
        if "hints" in cfg:
            hints_data = cfg["hints"]
            # Support both list and dict formats
            if isinstance(hints_data, dict):
                # Convert dict to list, using key as name if name not present
                hints_list = []
                for key, hint_cfg in hints_data.items():
                    if not isinstance(hint_cfg, dict):
                        raise ValueError(f"PMA hint '{key}' must be a dictionary")
                    # Add name from key if not present in config
                    if "name" not in hint_cfg:
                        hint_cfg = {**hint_cfg, "name": key}
                    hints_list.append(hint_cfg)
                hints_data = hints_list
            elif not isinstance(hints_data, list):
                raise ValueError("PMA 'hints' must be a list or dictionary")

            for idx, hint_cfg in enumerate(hints_data):
                try:
                    hints.append(PmaHintConfig.from_dict(hint_cfg))
                except Exception as e:
                    raise ValueError(f"Error parsing PMA hint at index {idx}: {e}") from e

        max_regions = cfg.get("max_regions", 15)
        if isinstance(max_regions, str):
            max_regions = int(max_regions, 0)
        else:
            max_regions = int(max_regions)

        return cls(regions=regions, hints=hints, max_regions=max_regions, default_region=cfg.get("default_region"))
