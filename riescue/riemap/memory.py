# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from dataclasses import dataclass, field, replace
from abc import ABC, abstractmethod
from typing import Any, Union, Optional, Mapping

import riescue.lib.enums as RV

log = logging.getLogger(__name__)

MAX_ADDRESS = 0xFFFFFFFF_FFFFFFFF


def _validate_geometry(start: Any, size: Any, context: str, allow_empty: bool = False) -> None:
    """
    Shared geometry check for every range type, used by both the direct constructors and ``from_dict``.

    :param context: description of the caller included in error messages
    :param allow_empty: permit the all-zero default sentinel produced by ``DramRange()``/``IoRange()``
    :raises ValueError: start/size are not plain integers, or the range is empty or outside the 64-bit space
    """
    for label, value in (("address", start), ("size", size)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} must be an integer, got {value!r} in {context}")
    if start < 0:
        raise ValueError(f"address must be non-negative, got {start} in {context}")
    if allow_empty and start == 0 and size == 0:
        return
    if size <= 0:
        raise ValueError(f"size must be positive, got {size} in {context}")
    if start + size - 1 > MAX_ADDRESS:
        raise ValueError(f"range 0x{start:x}+0x{size:x} exceeds the 64-bit address space in {context}")


class BaseMem(ABC):
    """
    Base class for all memory objects. Supports a ``from_dict`` method.
    """

    start: int
    size: int
    name: str = ""
    permissions: RV.PmpAttributes = RV.PmpAttributes.NONE  # ABC so this shouldn't ever be default
    tags: tuple[str, ...] = ()  # free-form labels a ``;#random_addr(custom_region=...)`` may select on

    @property
    def end(self) -> int:
        """
        End address of the range.
        """
        return self.start + self.size - 1

    @classmethod
    @abstractmethod
    def from_dict(cls, cfg: dict[str, Any], name: str = "") -> BaseMem:
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
            start = int(raw_start, 0) if isinstance(raw_start, str) else raw_start
            size = int(raw_size, 0) if isinstance(raw_size, str) else raw_size
        except (TypeError, ValueError):
            raise ValueError(f"non-integer address or size in mem config {cfg}") from None

        _validate_geometry(start, size, f"mem config {cfg}")
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

    @staticmethod
    def get_tags(cfg: Mapping[str, Any], context: str) -> tuple[str, ...]:
        """
        Parse the optional ``tags`` list: free-form string labels a ``custom_region=`` may select the range by.

        Declaration order is preserved so log messages and generated names read the way the config does.

        :param context: description of the caller included in error messages
        :raises ValueError: ``tags`` is not a list of unique non-empty strings
        """
        raw = cfg.get("tags", ())
        if isinstance(raw, str):
            raise ValueError(f"tags must be a list of strings, got the bare string {raw!r} in {context}")
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"tags must be a list of strings, got {type(raw).__name__} {raw!r} in {context}")
        tags = tuple(raw)
        for tag in tags:
            if not isinstance(tag, str) or not tag:
                raise ValueError(f"tags must be non-empty strings, got {tag!r} in {context}")
        duplicates = sorted({tag for tag in tags if tags.count(tag) > 1})
        if duplicates:
            raise ValueError(f"duplicate tags {duplicates} in {context}")
        return tags


@dataclass(frozen=True)
class DramRange(BaseMem):
    """
    Immutable memory range with start address, end address, size, and secure flag

    :param start: start address
    :param size: size of the range
    :param secure: if True, the range is secure
    :param cacheable: if True, range is cacheable.
    :param configurable: if True, range is configurable and can be modified by Runtime
    :param permissions: PMP attributes of the range. E.g. "rwx", "rw", "r", "none". Determins the PMP permissions of the given range.Defaults to "rwx".
    :param tags: free-form labels; a ``;#random_addr(custom_region=<tag>)`` may select the range by any of them
    :param pma_randomization: if False, the range is a fixed window: kept out of the general DRAM pool and
        left alone by PMA randomization. Defaults to True; only an explicit key makes a fixed window.
    :raises ValueError: if start or size is not an integer
    :raises ValueError: if secure is not a boolean

    Example JSON:

    .. code-block:: JSON

        {
            "address": "0x8000_0000",
            "size": "0x8000_0000",
            "secure": true,
        }

    ``address`` and ``size`` are required. Note that ``secure``, ``cacheable``, and ``configurable`` are optional. If not present, the range is not secure, cacheable, or configurable.
    E.g.

    .. code-block:: JSON

        {
            "address": "0x8000_0000",
            "size": "0x8000_0000",
        }

    Would result in a non-secure, non-cacheable, and non-configurable DRAM range.

    """

    start: int = 0
    size: int = 0
    secure: bool = False
    cacheable: bool = False
    configurable: bool = False
    name: str = ""
    permissions: RV.PmpAttributes = RV.PmpAttributes.R_W_X
    tags: tuple[str, ...] = ()
    pma_randomization: bool = True

    def __post_init__(self) -> None:
        _validate_geometry(self.start, self.size, f"{type(self).__name__}(name={self.name!r})", allow_empty=True)
        if not isinstance(self.tags, tuple):
            object.__setattr__(self, "tags", tuple(self.tags))

    @classmethod
    def from_dict(cls, cfg: dict[str, Union[str, int, bool]], name: str = "") -> DramRange:
        start, size = cls.range_from_dict(cfg)

        permissions = cfg.get("permissions", "rwx")
        if not isinstance(permissions, str):
            raise ValueError(f"permissions must be a string in {cfg=}")
        pmp_attributes = RV.PmpAttributes.from_str(permissions)
        tags = cls.get_tags(cfg, f"dram range {name!r}")
        return cls(
            name=name,
            start=start,
            size=size,
            secure=cls.get_bool(cfg, "secure", False),
            cacheable=cls.get_bool(cfg, "cacheable", False),
            configurable=cls.get_bool(cfg, "configurable", False),
            permissions=pmp_attributes,
            tags=tags,
            pma_randomization=cls.get_bool(cfg, "pma_randomization", True),
        )

    def make_secure(self) -> DramRange:
        """
        Create a new DramRange with bit-55 set to 1, to indicate it's secure.
        """
        return replace(
            self,
            start=self.start | 0x0080000000000000,
            secure=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.start,
            "size": self.size,
            "secure": self.secure,
            "cacheable": self.cacheable,
            "configurable": self.configurable,
            "permissions": self.permissions.value,
            "tags": list(self.tags),
            "pma_randomization": self.pma_randomization,
        }

    def split(self, size: int) -> tuple[DramRange, DramRange]:
        """
        split the dram range into two separate regions.
        If size is greater than the size of the range, raise a ValueError.
        If range isn't configurable, raise a ValueError.
        """
        if not 0 < size < self.size:
            raise ValueError(f"split size must be between 0 and range size {self.size}, got {size}")
        if not self.configurable:
            raise ValueError(f"range {self.name} is not configurable")
        return (
            replace(self, size=size),
            replace(self, start=self.start + size, size=self.size - size),
        )


@dataclass(frozen=True)
class CustomRange(BaseMem):
    """
    Immutable user-defined memory region at a fixed address, referenced by name from tests and testbench.

    Platform-specific attributes for this region are the consumer's responsibility.

    Construct with ``from_dict(cfg, name)`` using a Memory Map entry structured as:

    .. code-block:: JSON

        {
            "address": "0x6000_0000",
            "size": "0x100_0000"
        }

    ``address`` and ``size`` are required. ``permissions`` and ``tags`` are optional (``permissions`` defaults to ``"rwx"``).
    """

    start: int = 0
    size: int = 0
    name: str = ""
    permissions: RV.PmpAttributes = RV.PmpAttributes.R_W_X
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_geometry(self.start, self.size, f"{type(self).__name__}(name={self.name!r})", allow_empty=True)
        if not isinstance(self.tags, tuple):
            object.__setattr__(self, "tags", tuple(self.tags))

    @classmethod
    def from_dict(cls, cfg: Mapping[str, Union[str, int, bool]], name: str = "") -> CustomRange:
        start, size = cls.range_from_dict(cfg)
        cfg_permissions = cfg.get("permissions", "rwx")
        if not isinstance(cfg_permissions, str):
            raise ValueError(f"permissions must be a string in {cfg=}")
        permissions = RV.PmpAttributes.from_str(cfg_permissions)
        return cls(name=name, start=start, size=size, permissions=permissions, tags=cls.get_tags(cfg, f"custom range {name!r}"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.start,
            "size": self.size,
            "permissions": self.permissions.value,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class IoRange(BaseMem):
    """
    Immutable IO range with start address, end address, size, and test_access flag

    :param start: start address
    :param size: size of the range
    :param test_access: if False, the range is test_access and not accessible for testing

    Construct with ``from_dict(cfg)`` using a Memory Map structured as (JSON format):

    .. code-block:: JSON

        {
            "address": "0x200_c000",
            "size": "0x5ff_4000",
            "test_access": true,
        }

    By default, ``test_access`` is ``False``.
    """

    start: int = 0
    size: int = 0
    test_access: bool = False
    name: str = ""
    permissions: RV.PmpAttributes = RV.PmpAttributes.R_W
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_geometry(self.start, self.size, f"{type(self).__name__}(name={self.name!r})", allow_empty=True)
        if not isinstance(self.test_access, bool):
            raise ValueError(f"test_access must be a bool, got {self.test_access!r} in {type(self).__name__}(name={self.name!r})")
        if not isinstance(self.tags, tuple):
            object.__setattr__(self, "tags", tuple(self.tags))

    @classmethod
    def from_dict(cls, cfg: Mapping[str, Union[str, int, bool]], name: str = "") -> IoRange:
        start, size = cls.range_from_dict(cfg)
        cfg_permissions = cfg.get("permissions", "rw")
        if not isinstance(cfg_permissions, str):
            raise ValueError(f"permissions must be a string in {cfg=}")
        permissions = RV.PmpAttributes.from_str(cfg_permissions)

        return cls(
            name=name,
            start=start,
            size=size,
            test_access=cls.get_bool(cfg, "test_access", False),
            permissions=permissions,
            tags=cls.get_tags(cfg, f"io range {name!r}"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.start,
            "size": self.size,
            "test_access": self.test_access,
            "permissions": self.permissions.value,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class Memory:
    """
    Memory object with DRAM, IO, secure, and reserved ranges. Defaults to non-secure DRAM and IO ranges.
    Fully immutable: attribute assignment is frozen, the collections are tuples, and the contained ranges are
    frozen dataclasses. Any sequence may be passed to the constructor; it is coerced to a tuple. Use
    :func:`dataclasses.replace` to derive a modified memory map.

    :param dram_ranges: DRAM ranges
    :param io_ranges: IO ranges
    :param secure_ranges: secure ranges
    :param pma_fixed_ranges: DRAM ranges with ``pma_randomization: false`` (fixed windows, see below)
    :param reserved_ranges: ranges excluded from automatic allocation

    .. note::
        Default DRAM starts at ``0x8000_0000`` and has size ``2**56``
        Default IO Range is ``0x0`` - ``0x7FFFFFFF``

    Construct with ``from_dict(cfg)`` using a Memory Map structured as:

    .. code-block:: JSON

        {
            "dram": {
                "dram": { "address": "0x9000_0000", "size": "0x8000_0000", "secure": false},
                "dram0": { "address": "0x8000_0000", "size": "0x8000_0000", "secure": true},
                "secure0": { "address": "0x10000_0000", "size": "0x8000_0000", "secure": true},
                "poison_window": { "address": "0x1_0000_0000", "size": "0x1_0000", "tags": ["derr"]},
                "low_bank": { "address": "0x1_1000_0000", "size": "0x1_0000", "tags": ["bank0", "fast"]},
            },
            "io": {
                "io0": {"address" : "0x0", "size" : "0x1_0000"},
                "io1": {"address" : "0x1_0000", "size" : "0x1_0000", "test_access": true},
            },
        }

    - ``dram`` is a required key
    - ``dram`` can contain a ``secure`` key and boolean value, which marks the range as DRAM and secure.
    - ``dram`` regions that start with ``secure*`` are also marked as DRAM and secure.
    - ``io`` is an optional key
    - ``io`` can contain multiple ranges

    **Tags.** Any range may carry ``tags``, a list of free-form string labels. RiescueD attaches no
    meaning to a tag beyond making the range selectable by it: ``;#random_addr(..., custom_region=<tag>)``
    places the address inside a range carrying that tag, exactly as ``custom_region=<range name>`` does
    (see :meth:`resolve_custom_region`). When several ranges share a tag, one is picked per address.

    **Fixed windows (``pma_randomization``).** A DRAM range with ``pma_randomization: false`` is split
    out of ``dram_ranges`` into :attr:`pma_fixed_ranges` and becomes a window nothing random touches:

    - a named PMA region programmed at high priority, so randomized decoy regions never shadow it
    - excluded from decoy placement and from scattered random ``pmamask`` windows
    - exempt from carve-out mask stress (``--pma_carveout_mask_pct``)
    - not part of the general DRAM pool, so address generation never lands test content there by chance
    - reachable deliberately: ``pma_<range name>_base`` / ``_size`` / ``_end`` equates, plus
      ``custom_region=`` by name or tag

    The key defaults to ``true``, so a range is a fixed window only when the config says
    ``"pma_randomization": false``. No tag implies it: ``{"tags": ["derr"]}`` alone stays in the
    general pool.

    The two mechanisms are independent: tagging a range only makes it selectable, and clearing
    ``pma_randomization`` only isolates it. A range can do either, both, or neither.

    Note that ``dram`` is a required key and ``io`` is optional. If ``io`` is not present, the default IO range is ``0x0`` - ``0x7FFFFFFF``.
    If an empty dictionary is provided, the default memory ranges are used.

    """

    dram_ranges: tuple[DramRange, ...] = field(default_factory=lambda: (DramRange(0x8000_0000, 2**56, False),))
    io_ranges: tuple[IoRange, ...] = field(default_factory=lambda: (IoRange(0x0, 0x8000_0000),))
    secure_ranges: tuple[DramRange, ...] = ()
    pma_fixed_ranges: tuple[DramRange, ...] = ()
    reserved_ranges: tuple[BaseMem, ...] = ()
    custom_ranges: tuple[CustomRange, ...] = ()

    def __post_init__(self) -> None:
        for name in ("dram_ranges", "io_ranges", "secure_ranges", "pma_fixed_ranges", "reserved_ranges", "custom_ranges"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                object.__setattr__(self, name, tuple(value))

    def to_dict(self) -> dict[str, Any]:
        """
        Returns a dictionary representation of the memory object.
        """
        return {
            "dram": [dram.to_dict() for dram in self.dram_ranges],
            "io": [io.to_dict() for io in self.io_ranges],
            "secure": [secure.to_dict() for secure in self.secure_ranges],
            "pma_fixed": [fixed.to_dict() for fixed in self.pma_fixed_ranges],
            "reserved": [reserved.to_dict() for reserved in self.reserved_ranges],
            "custom": [custom.to_dict() for custom in self.custom_ranges],
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
            log.warning("No memory map provided, using default memory ranges")
            return cls()
        # validate dram
        dram = cfg.get("dram")
        if dram is None:
            raise ValueError('"dram" is a required key in the memory map')
        if not isinstance(dram, dict):
            raise ValueError('"dram" must be a dictionary')
        if not dram:
            raise ValueError('"dram" cannot be empty')

        dram_ranges, secure_ranges, pma_fixed_ranges = cls._classify_dram_ranges(dram)
        log.debug(f"DRAM ranges: {dram_ranges}")
        log.debug(f"Secure ranges: {secure_ranges}")
        log.debug(f"Fixed PMA ranges: {pma_fixed_ranges}")
        all_io_ranges = [IoRange.from_dict(value, name) for name, value in cls._section(cfg, "io").items()]
        io_ranges, reserved_ranges = cls._split_io_ranges_by_test_access(all_io_ranges)
        log.debug(f"IO ranges: {io_ranges}")
        custom_ranges = [CustomRange.from_dict(value, name) for name, value in cls._section(cfg, "custom").items()]
        log.debug(f"Custom ranges: {custom_ranges}")

        all_ranges = dram_ranges + io_ranges + secure_ranges + pma_fixed_ranges
        if not all_ranges:
            log.warning("No memory ranges provided in memory map, using default memory ranges")
            return cls()

        # The helpers build lists; the fields are tuples (__post_init__ would coerce, but converting
        # here keeps the declared types honest)
        return cls(
            dram_ranges=tuple(dram_ranges),
            io_ranges=tuple(io_ranges),
            secure_ranges=tuple(secure_ranges),
            pma_fixed_ranges=tuple(pma_fixed_ranges),
            reserved_ranges=tuple(reserved_ranges),
            custom_ranges=tuple(custom_ranges),
        )

    @staticmethod
    def _section(cfg: dict[str, Any], name: str) -> dict[str, Any]:
        """
        Returns the named optional section of a memory map, or an empty mapping when absent.

        :raises ValueError: the key is present but not a mapping of named ranges
        """
        if name not in cfg:
            return {}
        section = cfg[name]
        if not isinstance(section, dict):
            raise ValueError(f'"{name}" must be a dictionary of named ranges, got {type(section).__name__}: {section!r}')
        return section

    @staticmethod
    def _classify_dram_ranges(dram_dict: dict[str, dict[str, Union[str, int, bool]]]) -> tuple[list[DramRange], list[DramRange], list[DramRange]]:
        """
        Splits all DRAM ranges into plain, secure, and fixed (``pma_randomization: false``) ranges.

        Fixed-window membership comes from the range's own ``pma_randomization`` key, never from its
        name - the name stays a human-readable label the test refers to. ``secure`` keeps its legacy
        ``secure*`` name prefix as well, since configs already depend on it: a ``secure*`` name
        normalizes the range's ``secure`` flag, and the address is left alone, since bit-55 tagging is
        a PMP-only concern handled by :meth:`DramRange.make_secure`.

        Secure wins over fixed: a secure range is already excluded from the general pool and from PMA
        randomization, so its ``pma_randomization`` is never consulted.
        """
        secure_ranges: list[DramRange] = []
        pma_fixed_ranges: list[DramRange] = []
        dram_ranges: list[DramRange] = []
        for name, value in dram_dict.items():
            dram_range = DramRange.from_dict(value, name)
            if name.startswith("secure"):
                dram_range = replace(dram_range, secure=True)
            if dram_range.secure:
                secure_ranges.append(dram_range)
            elif not dram_range.pma_randomization:
                pma_fixed_ranges.append(dram_range)
            else:
                dram_ranges.append(dram_range)
        return dram_ranges, secure_ranges, pma_fixed_ranges

    @staticmethod
    def _split_io_ranges_by_test_access(io_ranges: list[IoRange]) -> tuple[list[IoRange], list[BaseMem]]:
        "Returns a list of all IO ranges that are test_access and a list of all IO ranges that are reserved"
        test_access_ranges = []
        reserved_ranges = []
        for io_range in io_ranges:
            if io_range.test_access:
                test_access_ranges.append(io_range)
            else:
                reserved_ranges.append(io_range)
        return test_access_ranges, reserved_ranges

    def targetable_ranges(self) -> tuple[BaseMem, ...]:
        """
        Every range a ``;#random_addr(custom_region=...)`` may select: custom ranges, fixed PMA windows,
        and any range carrying at least one tag.

        Untagged ``dram``/``io``/``secure`` ranges are deliberately left out. They are the general
        allocation pool, and turning each into a pinned region would change how ordinary addresses are
        drawn; a config that wants one selectable says so by tagging it.
        """
        tagged = tuple(rng for rng in self.dram_ranges + self.secure_ranges + self.io_ranges if rng.tags)
        return self.custom_ranges + self.pma_fixed_ranges + tagged

    def resolve_custom_region(self, spec: str) -> tuple[BaseMem, ...]:
        """
        Resolve a ``custom_region=`` spec to the ranges it selects, in memory-map declaration order.

        An exact range name wins over a tag, so naming a range always reaches that one range. Otherwise
        every targetable range carrying ``spec`` as a tag matches, and it is the caller's job to pick
        among them (:class:`~riescue.dtest_framework.generator.pt_request_builder.PageTableRequestBuilder`
        draws one per address, so several addresses spread across the matching windows).

        :return: matching ranges, empty when ``spec`` is neither a known name nor a known tag
        """
        targetable = self.targetable_ranges()
        by_name = [rng for rng in targetable if rng.name == spec]
        if by_name:
            return tuple(by_name)
        return tuple(rng for rng in targetable if spec in rng.tags)

    def custom_region_choices(self) -> tuple[list[str], list[str]]:
        """Every name and tag :meth:`resolve_custom_region` accepts, sorted; for error messages."""
        targetable = self.targetable_ranges()
        names = sorted({rng.name for rng in targetable if rng.name})
        tags = sorted({tag for rng in targetable for tag in rng.tags})
        return names, tags

    def address_qualifier_of(self, range_: BaseMem) -> RV.AddressQualifiers:
        """
        The physical-address-space qualifier a draw inside ``range_`` must carry.

        A pinned region's qualifier has to match the segment :class:`~riescue.riemap.addrgen.AddrGen`
        declared over that span, or the draw finds no backing memory. Kept in step with the
        ``define_segment`` calls in ``AddrGen.__init__``.
        """
        if range_ in self.custom_ranges or range_ in self.pma_fixed_ranges:
            return RV.AddressQualifiers.ADDRESS_CUSTOM
        if range_ in self.secure_ranges:
            return RV.AddressQualifiers.ADDRESS_SECURE
        if range_ in self.io_ranges:
            return RV.AddressQualifiers.ADDRESS_MMIO
        return RV.AddressQualifiers.ADDRESS_DRAM

    def get_dram_size(self) -> int:
        """
        Returns the size of the DRAM.
        """
        return sum(dram.size for dram in self.dram_ranges)
