# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Any, Union, Optional, Mapping

import riescue.lib.enums as RV

log = logging.getLogger(__name__)


class BaseMem(ABC):
    """
    Base class for all memory objects. Supports a ``from_dict`` method.
    """

    start: int
    size: int

    @property
    def end(self) -> int:
        """
        End address of the range.
        """
        return self.start + self.size - 1

    @classmethod
    @abstractmethod
    def from_dict(cls, cfg: dict[str, Any]) -> BaseMem:
        """
        Method to create a Range object from a dictionary.
        """
        ...

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """
        Method to create a dictionary from a Range object.
        """
        ...

    @staticmethod
    def range_from_dict(cfg: Mapping[str, Union[str, int]]) -> tuple[int, int]:
        """
        Generates a start and size from a dictionary.

        ..note::
            Assumes memory is a base-10 or base-16 integer, with an ``"address"`` and ``"size"`` key.

        :param cfg: mapping containing ``address``, ``size`` and optional ``secure``
        :raises ValueError: malformed or missing fields
        """
        try:
            raw_start = cfg["address"]
            raw_size = cfg["size"]
        except KeyError as e:
            raise ValueError(f"missing key '{e.args[0]}' in mem config {cfg}") from None

        try:
            start = int(raw_start, 0) if isinstance(raw_start, str) else int(raw_start)
            size = int(raw_size, 0) if isinstance(raw_size, str) else int(raw_size)
        except (TypeError, ValueError):
            raise ValueError(f"non-integer address or size in mem config {cfg}") from None

        if start < 0:
            raise ValueError(f"address must be non-negative in mem config {cfg}")
        if size <= 0:
            raise ValueError(f"size must be positive in mem config {cfg}")
        if start + size - 1 > 0xFFFFFFFF_FFFFFFFF:
            raise ValueError(f"range exceeds 64-bit address space in mem config {cfg}")

        return start, size

    @staticmethod
    def get_bool(cfg: Mapping[str, Any], key: str, default: Optional[bool] = None) -> bool:
        """
        Get a boolean from a dictionary.
        If key is present, checks that it's a boolean.
        Otherwise, returns default.
        """
        value = cfg.get(key)
        if value is not None:
            if isinstance(value, bool):
                return value
            else:
                raise ValueError(f"key {key} is not a boolean in {cfg=}")
        else:
            if default is None:
                raise ValueError(f"missing key {key} in {cfg=}")
            else:
                return default


@dataclass
class DramRange(BaseMem):
    """
    Memory range with start address, end address, size, and secure flag

    :param start: start address
    :param size: size of the range
    :param secure: if True, the range is secure
    :raises ValueError: if start or size is not an integer
    :raises ValueError: if secure is not a boolean

    Example JSON:
    .. code-block:: JSON
        {
            "address": "0x8000_0000",
            "size": "0x8000_0000",
            "secure": true,
        }

    ``address`` and ``size`` are required. Note that ``secure`` is optional. If not present, the range is not secure. E.g.
    .. code-block:: JSON
        {
            "address": "0x8000_0000",
            "size": "0x8000_0000",
        }

    Would result in a non-secure DRAM range.

    FIXME: (Documentation) What is secure? Why does it matter to users?
    """

    start: int = 0
    size: int = 0
    secure: bool = False

    @classmethod
    def from_dict(cls, cfg: dict[str, Union[int, bool]]) -> DramRange:
        start, size = cls.range_from_dict(cfg)
        return cls(start=start, size=size, secure=cls.get_bool(cfg, "secure", False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.start,
            "size": self.size,
            "secure": self.secure,
        }


@dataclass
class IoRange(BaseMem):
    """
    IO range with start address, end address, size, and reserved flag

    :param start: start address
    :param size: size of the range
    :param reserved: if True, the range is reserved and not accessible for testing
    """

    start: int = 0
    size: int = 0
    reserved: bool = True

    @classmethod
    def from_dict(cls, cfg: Mapping[str, Union[str, int, bool]]) -> IoRange:
        start, size = cls.range_from_dict(cfg)
        return cls(start=start, size=size, reserved=cls.get_bool(cfg, "reserved", True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.start,
            "size": self.size,
            "reserved": self.reserved,
        }


@dataclass(frozen=True)
class Memory:
    """
    Memory object with DRAM, IO, secure, and reserved ranges. Defaults to non-secure DRAM and IO ranges.
    Frozen after construction to prevent modification of ranges.

    :param dram_ranges: List of DRAM ranges
    :param io_ranges: List of IO ranges
    :param secure_ranges: List of secure ranges
    :param reserved_ranges: List of reserved ranges. Currently unused

    .. note::
        Default DRAM Range is ``0x8000_0000`` - ``0xFFFFFFFF_FFFFFFFF``
        Default IO Range is ``0x0`` - ``0x7FFFFFFF``

    Construct with ``from_dict(cfg)`` using a Memory Map structured as:

    .. code-block:: JSON
        {
            "dram": {
                "dram": { "address": "0x9000_0000", "size": "0x8000_0000", "secure": false},
                "dram0": { "address": "0x8000_0000", "size": "0x8000_0000", "secure": true},
                "secure0": { "address": "0x10000_0000", "size": "0x8000_0000", "secure": true},
            },
            "io": {
                "io0": {"address" : "0x0", "size" : "0x1_0000"},
                "io1": {"address" : "0x1_0000", "size" : "0x1_0000"},
            },
        }

    - ``dram`` is a required key
    - ``dram`` can contain a ``secure`` key and boolean value, which marks the range as DRAM and secure.
    - ``dram`` regions that start with ``secure*`` are also marked as DRAM and secure.
    - ``io`` is an optional key
    - ``io`` can contain multiple ranges

    Note that ``dram`` is a required key and ``io`` is optional. If ``io`` is not present, the default IO range is ``0x0`` - ``0x7FFFFFFF``.
    If an empty dictionary is provided, the default memory ranges are used.

    """

    dram_ranges: list[DramRange] = field(default_factory=lambda: [DramRange(0x8000_0000, 2**56, False)])
    io_ranges: list[IoRange] = field(default_factory=lambda: [IoRange(0x0, 0x8000_0000)])
    secure_ranges: list[DramRange] = field(default_factory=list)
    reserved_ranges: list[DramRange] = field(default_factory=list)

    @staticmethod
    def determine_mmio_region_address_qualifier(disallow_mmio: bool, test_access: str):
        if disallow_mmio or test_access == "reserved":
            return RV.AddressQualifiers.ADDRESS_RESERVED
        elif test_access == "available":
            return RV.AddressQualifiers.ADDRESS_MMIO
        else:
            raise ValueError(f"Unknown test_access level {test_access}")

    def to_dict(self) -> dict[str, Any]:
        """
        Returns a dictionary representation of the memory object.
        """
        return {
            "dram": [dram.to_dict() for dram in self.dram_ranges],
            "io": [io.to_dict() for io in self.io_ranges],
            "secure": [secure.to_dict() for secure in self.secure_ranges],
            "reserved": [reserved.to_dict() for reserved in self.reserved_ranges],
        }

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> Memory:
        """
        Constructs memory object from cpuconfig dictionary.

        Empty dictionaries return default memory ranges.
        If a memory map is provided, it must contain a ``dram`` key. ``io`` is optional. Blank ``io`` entries default to an empty list

        :param cfg: Memory map configuration dictionary
        :return: ``Memory`` object
        :raises ValueError: if ``dram`` is not present
        """
        if not cfg:
            return cls()
        # validate dram
        dram = cfg.get("dram")
        if dram is None:
            raise ValueError('"dram" is a required key in the memory map')
        if not isinstance(dram, dict):
            raise ValueError('"dram" must be a dictionary')
        if not dram:
            raise ValueError('"dram" cannot be empty')

        dram_ranges = [DramRange.from_dict(value) for _, value in dram.items()]
        log.debug(f"DRAM ranges: {dram_ranges}")
        io_ranges = [IoRange.from_dict(value) for _, value in cfg.get("io", {}).items()]
        log.debug(f"IO ranges: {io_ranges}")

        secure_ranges = [DramRange.from_dict(value) for name, value in dram.items() if name.startswith("secure") or value.get("secure", False)]
        log.debug(f"Secure ranges: {secure_ranges}")

        all_ranges = dram_ranges + io_ranges + secure_ranges
        if not all_ranges:
            log.warning("No memory ranges provided in memory map, using default memory ranges")
            return cls()

        return cls(
            dram_ranges=dram_ranges,
            io_ranges=io_ranges,
            secure_ranges=secure_ranges,
            reserved_ranges=[],
        )
