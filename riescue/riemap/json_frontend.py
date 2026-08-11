# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""JSON frontend for the standalone page table generator.

Parses the ``riemap`` JSON config (memory map + address spaces + page specs),
drives a :class:`~riescue.riemap.builder.PageTableBuilder` to allocate and build
the tables, and serializes the resulting PTEs and per-page walks back to JSON.
It holds the config/output schema and the random attribute resolution, and
delegates all allocation and page-table construction to riemap.

The generator supports multiple physical memory regions through the ``mmap``
field. Each entry is either an ``[low, high]`` array (normal memory) or a
``{"low": ..., "high": ..., "secure": true}`` object (secure memory). Secure
regions are used for page-table node placement when ``secure_pt_probability`` is
set on a space; bit 55 is set internally on addresses allocated from them. Every
region has to fit the fixed 52-bit physical width this frontend builds with (see
:func:`generate_page_tables`), so a secure region is an ordinary low range -- not a
bit-55 one, which nothing could be allocated from.

Example config::

    {
        "mmap": [
            ["0x80000000", "0x100000000"],
            {"low": "0x0", "high": "0x40000000", "secure": true}
        ],
        "spaces": {
            "space1": {
                "paging_mode": "sv39",
                "secure_pt_probability": 50,
                "pages": [...]
            }
        }
    }
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.attributes import PTE_BASES, PTE_FIELD_WIDTHS, PTE_LEVELS
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.config import PagingParams
from riescue.riemap.result import SpaceResult
from riescue.riemap.request import (
    AddrSpec,
    Choice,
    LEAF,
    Mapping,
    Page,
    PTNode,
    SameAs,
    Space,
    Stage,
)
from riescue.riemap import resolve
from riescue.riemap.resolve import (
    PAGING_MODE_MAP,
    PAGING_MODE_STR_MAP,
    WeightedValue,
    AttributeSpec,
    filter_size_attribute,
    GSTAGE_FORCING_ATTRS_SET,
)

log = logging.getLogger(__name__)

# Bit 55 marks a PA that targets secure memory (set on secure pages' output PA).
_SECURE_BIT = 0x0080000000000000

# RV64's widest physical address is 56 bits (a 44-bit PPN plus a 12-bit offset), and a
# region boundary is a page boundary. An mmap ``high`` is exclusive, so the top of the
# space is a legal bound.
_PAGE_BYTES = 0x1000
_MAX_PHYS_ADDR = 1 << 56

# Geometry attributes: page sizes, not PTE bits, so they carry no bit width.
_GEOMETRY_ATTRS = ("size", "gstage_vs_leaf_size", "gstage_vs_nonleaf_size")


def _pte_attr_bases() -> Dict[str, str]:
    """Every accepted PTE attribute name mapped to the base whose field width it packs into.

    The bare base, the ``{base}_nonleaf`` shorthand, the per-level ``{base}_level{n}``
    and concrete two-stage ``{base}_level{vs}_glevel{g}`` spellings, plus the g-stage
    forcing shorthand :data:`resolve.GSTAGE_FORCING_ATTRS_SET` already enumerates.
    """
    bases = {}
    for base in PTE_BASES:
        bases[base] = base
        bases[f"{base}_nonleaf"] = base
        for level in PTE_LEVELS:
            bases[f"{base}_level{level}"] = base
            for g_level in PTE_LEVELS:
                bases[f"{base}_level{level}_glevel{g_level}"] = base
    for attr in GSTAGE_FORCING_ATTRS_SET:
        bases[attr] = attr.split("_")[0]
    return bases


_PTE_ATTR_BASES = _pte_attr_bases()
# ``secure`` is a placement request rather than a PTE bit, so it is a legal name with no
# field width of its own.
_VALID_PAGE_ATTRS = frozenset(_PTE_ATTR_BASES) | frozenset(_GEOMETRY_ATTRS) | {"secure"}


def _declared_values(spec: AttributeSpec) -> List[Any]:
    """Every value an attribute spec could resolve to, scalar or choice list."""
    if isinstance(spec, list):
        return [resolve.choice_value(item) for item in spec]
    return [spec]


def _validate_page_attribute(name: str, spec: AttributeSpec) -> None:
    """Reject a value the attribute's PTE field cannot hold.

    The name itself is checked by :meth:`PageAttributes.__post_init__`, which drops an
    unrecognized one with a warning, so ``name`` is known by the time it gets here.
    """
    if name == "secure":
        for value in _declared_values(spec):
            if not isinstance(value, bool) and not (isinstance(value, int) and value in (0, 1)):
                raise ValueError(f"page attribute 'secure' must be a boolean, got {value!r}")
        return
    base = _PTE_ATTR_BASES.get(name)
    if base is None:
        return  # a page size, validated against the space's paging mode instead
    width = PTE_FIELD_WIDTHS.get(base, 1)
    limit = 1 << width
    for value in _declared_values(spec):
        if isinstance(value, bool):
            continue
        if not isinstance(value, int):
            raise ValueError(f"page attribute '{name}' must be an integer PTE field value, got {value!r}")
        if not 0 <= value < limit:
            raise ValueError(f"page attribute '{name}' does not fit its {width}-bit PTE field: {value} is outside [0, {limit - 1}]")


# ============================================================================
# Input Dataclass Definitions
# ============================================================================


@dataclass
class MemoryRegion:
    """Represents a physical memory region with optional secure attribute."""

    low: int
    high: int
    secure: bool = False


def _mmap_bound(value: Any, idx: int, which: str) -> int:
    """Parse one ``mmap`` region bound, rejecting anything that is not a page boundary.

    A bound is a hex string or a plain integer -- ``bool`` (an ``int`` subclass), floats
    and ``None`` are rejected rather than coerced, since ``int(True)`` and ``int(1.5)``
    both silently produce an address the config never named. The parsed value must be a
    non-negative, 4 KiB-aligned address inside RV64's 56-bit physical space.
    """
    if isinstance(value, str):
        try:
            bound = int(value, 16)
        except ValueError:
            raise ValueError(f"mmap region {idx} {which} is not a hex string: {value!r}") from None
    elif isinstance(value, int) and not isinstance(value, bool):
        bound = value
    else:
        raise ValueError(f"mmap region {idx} {which} must be a hex string or an integer, got {value!r}")

    if bound < 0:
        raise ValueError(f"mmap region {idx} {which} must be non-negative, got {bound}")
    if bound > _MAX_PHYS_ADDR:
        raise ValueError(f"mmap region {idx} {which} 0x{bound:x} is outside the 56-bit physical address space")
    if bound % _PAGE_BYTES:
        raise ValueError(f"mmap region {idx} {which} 0x{bound:x} is not 4 KiB aligned")
    return bound


@dataclass
class PageAttributes:
    """A page's PTE attribute dict, each value either a scalar or a randomization spec.

    Attribute names come from a closed vocabulary: the base PTE bits, their per-level and
    g-stage forcing spellings, the page-geometry sizes, and ``secure``. Anything else is
    dropped with a warning, so a typo (or a forcing form only valid in a two-stage config)
    leaves the page without the bit the config asked for. A value that does not fit its
    PTE field is still rejected.
    """

    attrs: Dict[str, AttributeSpec] = field(default_factory=dict)

    def __post_init__(self):
        # FIXME(RVBBL-4833): warn-and-drop until MMU TB stops passing unknown attributes.
        for name in [name for name in self.attrs if name not in _VALID_PAGE_ATTRS]:
            log.warning("ignoring unknown page attribute '%s'", name)
            del self.attrs[name]
        for name, spec in self.attrs.items():
            _validate_page_attribute(name, spec)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageAttributes":
        """Deserialize from dictionary."""
        attrs = {k: cls._deserialize_attr(k, v) for k, v in data.items()}
        return cls(attrs=attrs)

    @staticmethod
    def _deserialize_attr(name: str, value: Any) -> AttributeSpec:
        """Deserialize attribute from JSON format."""
        if isinstance(value, list) and len(value) > 0:
            description = f"page attribute '{name}'"
            if resolve.choice_list_is_weighted(value, description):
                resolve.validate_choice_weights(value, description)
                return [item if isinstance(item, WeightedValue) else WeightedValue(value=item["value"], weight=item["weight"]) for item in value]
        return value


@dataclass
class PageSpec:
    """Specification for a group of pages to generate."""

    num_pages: int = 1
    id: Optional[str] = None
    va: Optional[str] = None
    va_and: Optional[str] = None
    va_or: Optional[str] = None
    pa: Optional[str] = None
    pa_and: Optional[str] = None
    pa_or: Optional[str] = None
    attributes: PageAttributes = field(default_factory=PageAttributes)

    def __post_init__(self):
        """Validate page spec."""
        if isinstance(self.num_pages, bool) or not isinstance(self.num_pages, int) or self.num_pages < 0:
            raise ValueError(f"num_pages must be a non-negative integer, got {self.num_pages!r}")
        # The id is an output dictionary key, so a non-string would come back out of
        # ``to_dict`` as something the JSON caller cannot look its page up by.
        if self.id is not None and not isinstance(self.id, str):
            raise ValueError(f"page id must be a string, got {self.id!r}")
        # An exact address is a pin, and a mask constrains a random draw: a page that
        # declares both is stating two incompatible intents, and the pin takes precedence.
        for exact_field, mask_fields in (("va", ("va_and", "va_or")), ("pa", ("pa_and", "pa_or"))):
            exact = getattr(self, exact_field)
            if exact is None:
                continue
            declared = [mask for mask in mask_fields if getattr(self, mask) is not None]
            if declared:
                raise ValueError(f"page '{self.id or '(unnamed)'}' pins {exact_field}={exact} and also declares {', '.join(declared)}; " f"an exact address takes no random-draw mask")
        # A pinned VA names exactly one page, so a group of several cannot all have it: the
        # builder rejects the resulting duplicate anyway, and catching it here can name the
        # offending spec. A pinned PA stays legal over num_pages > 1 -- that is the alias
        # pattern (several VAs, one frame); a VA may not be aliased, a PA may.
        if self.va is not None and self.num_pages > 1:
            raise ValueError(f"page '{self.id or '(unnamed)'}' pins va={self.va} with num_pages={self.num_pages}: a pinned VA can name only one page")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageSpec":
        """Deserialize from dictionary."""
        data_copy = data.copy()
        attributes_data = data_copy.pop("attributes", {})
        attributes = PageAttributes.from_dict(attributes_data)

        # Filter out comment fields
        data_copy = {k: v for k, v in data_copy.items() if not k.startswith("_comment")}

        # Check for unknown fields
        known_fields = {"num_pages", "id", "va", "va_and", "va_or", "pa", "pa_and", "pa_or"}
        unknown_fields = set(data_copy.keys()) - known_fields
        if unknown_fields:
            raise ValueError(f"Unknown fields in page specification: {unknown_fields}")

        return cls(**data_copy, attributes=attributes)


@dataclass
class SpaceConfig:
    """Configuration for a single virtual address space."""

    pages: List[PageSpec]
    twostage: bool = False
    paging_mode: Union[str, List[str], List[WeightedValue]] = "sv39"
    gstage_paging_mode: Union[str, List[str], List[WeightedValue]] = "sv39"
    secure_pt_probability: int = 0

    def __post_init__(self):
        """Validate space configuration."""
        if len(self.pages) == 0:
            raise ValueError("At least one page specification is required per space")
        # ``num_pages: 0`` is a useful way to disable one group while its siblings stay
        # live, so it is only an error when it leaves the space with no pages at all: such
        # a space originates no mapping, so it has no page table and its output is an
        # empty shell. Rejecting it here names the offending space instead of surfacing as
        # a KeyError on a Space object during read-back.
        if sum(page.num_pages for page in self.pages) == 0:
            raise ValueError("A space must declare at least one page: every page specification has num_pages 0")
        # Each spec's id keys its group in the output, and a spec without one falls back to
        # its index. Two specs resolving to one key would silently merge in the output, so
        # the fallback participates in the uniqueness check: an explicit id "0" collides
        # with an unnamed first spec.
        seen_ids: Dict[str, int] = {}
        for index, page in enumerate(self.pages):
            display_id = page.id if page.id is not None else str(index)
            if display_id in seen_ids:
                raise ValueError(f"page specs {seen_ids[display_id]} and {index} both resolve to the id '{display_id}'; page ids must be unique within a space")
            seen_ids[display_id] = index
        # Two specs pinning the same VA in one space is the same declaration error one spec
        # over num_pages > 1 is. Compare parsed values, not strings, so "1000" and "0x1000"
        # collide -- both are base-16 to _hex.
        seen_vas: Dict[int, str] = {}
        for page in self.pages:
            if page.va is None:
                continue
            va = int(page.va, 16)
            name = page.id if page.id is not None else "(unnamed)"
            if va in seen_vas:
                raise ValueError(f"pages '{seen_vas[va]}' and '{name}' both pin va=0x{va:x} in one space; only one page can occupy a VA")
            seen_vas[va] = name

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpaceConfig":
        """Deserialize from dictionary."""
        data_copy = data.copy()

        # Filter out comment fields
        data_copy = {k: v for k, v in data_copy.items() if not k.startswith("_comment")}

        pages_data = data_copy.pop("pages", [])
        pages = [PageSpec.from_dict(page_data) for page_data in pages_data]

        paging_mode = cls._deserialize_paging_mode(data_copy.get("paging_mode", "sv39"))
        gstage_paging_mode = cls._deserialize_paging_mode(data_copy.get("gstage_paging_mode", "sv39"))

        secure_pt_probability = data_copy.pop("secure_pt_probability", 0)
        if isinstance(secure_pt_probability, bool) or not isinstance(secure_pt_probability, int) or not (0 <= secure_pt_probability <= 100):
            raise ValueError(f"secure_pt_probability must be an integer in [0, 100], got {secure_pt_probability}")

        # ``twostage`` selects the whole translation topology, so an approximate truth
        # value (0/1, "false") is a config the author did not verify. Demand the JSON
        # boolean the schema documents.
        if "twostage" in data_copy and not isinstance(data_copy["twostage"], bool):
            raise ValueError(f"twostage must be a JSON boolean (true or false), got {data_copy['twostage']!r}")

        # Check for unknown fields
        known_fields = {"twostage", "paging_mode", "gstage_paging_mode"}
        unknown_fields = set(data_copy.keys()) - known_fields
        if unknown_fields:
            raise ValueError(f"Unknown fields in SpaceConfig: {unknown_fields}")

        data_copy["paging_mode"] = paging_mode
        data_copy["gstage_paging_mode"] = gstage_paging_mode
        data_copy["secure_pt_probability"] = secure_pt_probability
        data_copy["pages"] = pages

        return cls(**data_copy)

    @staticmethod
    def _deserialize_paging_mode(mode: Any) -> Union[str, List[str], List[WeightedValue]]:
        """Deserialize paging mode from JSON format."""
        if isinstance(mode, list) and len(mode) > 0:
            if resolve.choice_list_is_weighted(mode, "paging_mode"):
                resolve.validate_choice_weights(mode, "paging_mode")
                return [item if isinstance(item, WeightedValue) else WeightedValue(value=item["value"], weight=item["weight"]) for item in mode]
        return mode


@dataclass
class PageTableConfig:
    """Complete page table generation configuration with multiple spaces.

    ``spaces`` maps space id -> :class:`SpaceConfig`; ``mmap`` is the list of
    physical :class:`MemoryRegion` s (each with ``low`` / ``high`` and an
    optional ``secure`` flag).
    """

    spaces: Dict[str, SpaceConfig]
    mmap: List[MemoryRegion]

    def __post_init__(self):
        """Validate configuration."""
        if len(self.spaces) == 0:
            raise ValueError("At least one space is required")
        if len(self.mmap) == 0:
            raise ValueError("At least one memory region is required")
        for idx, region in enumerate(self.mmap):
            if region.low >= region.high:
                raise ValueError(f"Memory region {idx}: low (0x{region.low:x}) must be less than high (0x{region.high:x})")
        # A page's ``secure`` attribute names a memory pool, so it is only answerable once
        # some region is marked secure. Checking the declaration rather than the value it
        # happens to resolve to catches the case at parse time, where the offending page can
        # be named; ``generate_page_tables`` re-checks the resolved value for the region set
        # it actually builds with.
        if not any(region.secure for region in self.mmap):
            for space_id, space in self.spaces.items():
                for index, page in enumerate(space.pages):
                    if "secure" in page.attributes.attrs:
                        name = page.id if page.id is not None else str(index)
                        raise ValueError(f"space '{space_id}' page '{name}' declares the 'secure' attribute but no mmap region is marked secure")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageTableConfig":
        """Deserialize from dictionary."""
        data_copy = data.copy()

        # Filter out comment fields
        data_copy = {k: v for k, v in data_copy.items() if not k.startswith("_comment")}

        spaces_data = data_copy.pop("spaces", {})
        spaces = {space_id: SpaceConfig.from_dict(space_data) for space_id, space_data in spaces_data.items()}

        # Parse mmap (array of [low, high] pairs or {"low": ..., "high": ..., "secure": ...} objects)
        mmap_data = data_copy.get("mmap")
        if not isinstance(mmap_data, list):
            raise ValueError("mmap must be an array")
        if len(mmap_data) == 0:
            raise ValueError("mmap must contain at least one memory region")

        mmap_regions = []
        for idx, region in enumerate(mmap_data):
            if isinstance(region, list):
                if len(region) != 2:
                    raise ValueError(f"mmap region {idx} must be an array of 2 elements [low, high]")
                low_val, high_val = region
                secure = False
            elif isinstance(region, dict):
                unknown_keys = set(region) - {"low", "high", "secure"}
                if unknown_keys:
                    raise ValueError(f"mmap region {idx} has unknown keys: {sorted(unknown_keys)}")
                if "low" not in region or "high" not in region:
                    raise ValueError(f"mmap region {idx} dict must have 'low' and 'high' keys")
                low_val = region["low"]
                high_val = region["high"]
                secure = region.get("secure", False)
                # ``secure`` decides which memory pool backs a page or PT node, so it takes
                # the JSON boolean the schema documents rather than anything truthy.
                if not isinstance(secure, bool):
                    raise ValueError(f"mmap region {idx} 'secure' must be a JSON boolean (true or false), got {secure!r}")
            else:
                raise ValueError(f"mmap region {idx} must be an array [low, high] or an object with 'low'/'high' keys")

            low = _mmap_bound(low_val, idx, "low")
            high = _mmap_bound(high_val, idx, "high")

            mmap_regions.append(MemoryRegion(low=low, high=high, secure=secure))

        # Check for unknown fields
        known_fields = {"mmap"}
        unknown_fields = set(data_copy.keys()) - known_fields
        if unknown_fields:
            raise ValueError(f"Unknown fields in PageTableConfig: {unknown_fields}")

        return cls(spaces=spaces, mmap=mmap_regions)

    @classmethod
    def from_json_file(cls, path: Path) -> "PageTableConfig":
        """Load configuration from JSON file."""
        with open(path, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ============================================================================
# Output Dataclass Definitions
# ============================================================================


@dataclass
class PTEInfo:
    """Information about a single PTE in the page walk."""

    address: int
    level: int
    stage: Optional[int] = None  # 1=VS, 2=G, None=single-stage

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PTEInfo":
        """Deserialize from dictionary."""
        return cls(address=int(data["address"], 16), level=data["level"], stage=data.get("stage"))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        result: Dict[str, Any] = {"address": f"0x{self.address:016x}", "level": self.level}
        if self.stage is not None:
            result["stage"] = self.stage
        return result


@dataclass
class PageEntry:
    """Information about a single page's translation."""

    pa: int
    size: str  # "4kb", "2mb", etc.
    ptes: List[PTEInfo]
    gstage_vs_leaf_size: Optional[str] = None  # G-stage size for VS leaf PTE translation
    gstage_vs_nonleaf_size: Optional[str] = None  # G-stage size for VS non-leaf PTE translation

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageEntry":
        """Deserialize from dictionary."""
        return cls(
            pa=int(data["pa"], 16),
            size=data["size"],
            ptes=[PTEInfo.from_dict(pte) for pte in data["ptes"]],
            gstage_vs_leaf_size=data.get("gstage_vs_leaf_size"),
            gstage_vs_nonleaf_size=data.get("gstage_vs_nonleaf_size"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        result: Dict[str, Any] = {"pa": f"0x{self.pa:016x}", "size": self.size, "ptes": [pte.to_dict() for pte in self.ptes]}
        if self.gstage_vs_leaf_size is not None:
            result["gstage_vs_leaf_size"] = self.gstage_vs_leaf_size
        if self.gstage_vs_nonleaf_size is not None:
            result["gstage_vs_nonleaf_size"] = self.gstage_vs_nonleaf_size
        return result


@dataclass
class SpaceOutput:
    """Output for a single virtual address space."""

    paging_mode: str
    pages: Dict[str, Dict[int, PageEntry]]  # ID -> VA -> PageEntry
    twostage: bool = False
    top_base_addr: Optional[int] = None
    gstage_paging_mode: Optional[str] = None
    gstage_top_base_addr: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpaceOutput":
        """Deserialize from dictionary."""
        pages = {page_id: {int(va, 16): PageEntry.from_dict(page_data) for va, page_data in va_map.items()} for page_id, va_map in data["pages"].items()}
        return cls(
            paging_mode=data["paging_mode"],
            pages=pages,
            twostage=data.get("twostage", False),
            top_base_addr=int(data["top_base_addr"], 16) if data.get("top_base_addr") else None,
            gstage_paging_mode=data.get("gstage_paging_mode"),
            gstage_top_base_addr=int(data["gstage_top_base_addr"], 16) if data.get("gstage_top_base_addr") else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        # Serialize nested pages structure
        pages_dict: Dict[str, Dict[str, Any]] = {}
        for page_id, va_map in self.pages.items():
            pages_dict[page_id] = {}
            for va, entry in va_map.items():
                pages_dict[page_id][f"0x{va:016x}"] = entry.to_dict()

        result: Dict[str, Any] = {
            "paging_mode": self.paging_mode,
            "pages": pages_dict,
            "twostage": self.twostage,
        }
        if self.top_base_addr is not None:
            result["top_base_addr"] = f"0x{self.top_base_addr:016x}"
        if self.gstage_paging_mode is not None:
            result["gstage_paging_mode"] = self.gstage_paging_mode
        if self.gstage_top_base_addr is not None:
            result["gstage_top_base_addr"] = f"0x{self.gstage_top_base_addr:016x}"
        return result


@dataclass
class PageTableOutput:
    """Complete page table generation output with multiple spaces."""

    entries: Dict[int, int]  # PTE address -> PTE value (merged across all spaces)
    spaces: Dict[str, SpaceOutput]  # Space ID -> SpaceOutput

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageTableOutput":
        """Deserialize from dictionary."""
        return cls(
            entries={int(address, 16): int(value, 16) for address, value in data["entries"].items()},
            spaces={space_id: SpaceOutput.from_dict(space_data) for space_id, space_data in data["spaces"].items()},
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "PageTableOutput":
        """Load output from JSON file."""
        with open(path, "r") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "entries": {f"0x{addr:016x}": f"0x{value:016x}" for addr, value in self.entries.items()},
            "spaces": {space_id: space.to_dict() for space_id, space in self.spaces.items()},
        }

    def to_json_file(self, path: Path) -> None:
        """Save output to JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


# ============================================================================
# Utility Functions
# ============================================================================


def resolve_attribute_value(attr_spec: AttributeSpec, rng: RandNum) -> Any:
    """
    Resolve an attribute value according to the specification, drawing from ``rng``:
    - If scalar (string/int/etc), return as-is
    - If list of WeightedValue or dicts with 'value' and 'weight' keys, perform weighted random choice
    - If list of non-dicts, perform uniform random choice
    """
    if not isinstance(attr_spec, list):
        return attr_spec

    if len(attr_spec) == 0:
        raise ValueError("Attribute list cannot be empty")

    # Check if it's a list of WeightedValue dataclasses
    first_item = attr_spec[0]
    if isinstance(first_item, WeightedValue):
        values = [item.value for item in attr_spec]  # type: ignore
        weights = [item.weight for item in attr_spec]  # type: ignore
        return rng.choices(values, weights=weights, k=1)[0]

    # Check if it's a list of dicts with 'value' and 'weight' keys (from JSON)
    if isinstance(first_item, dict) and "value" in first_item and "weight" in first_item:
        values = [item["value"] for item in attr_spec]  # type: ignore
        weights = [item["weight"] for item in attr_spec]  # type: ignore
        return rng.choices(values, weights=weights, k=1)[0]

    # Uniform random selection
    return rng.choice(attr_spec)


def _resolve_pte_attribute(
    attr_spec: AttributeSpec,
    rng: RandNum,
) -> Any:
    """Resolve a JSON PTE policy to a preferred value plus its legal domain."""
    preferred = resolve_attribute_value(attr_spec, rng)
    if not isinstance(attr_spec, list):
        return preferred

    options = []
    for item in attr_spec:
        if isinstance(item, WeightedValue):
            value = item.value
        elif isinstance(item, dict) and "value" in item:
            value = item["value"]
        else:
            value = item
        if not any(value == prior for prior in options):
            options.append(value)
    alternatives = tuple(value for value in options if value != preferred)
    if not alternatives:
        return preferred
    return Choice(preferred=preferred, alternatives=alternatives)


def _could_be_truthy(attr_spec: AttributeSpec) -> bool:
    """Check if an attribute specification could ever resolve to a truthy value.

    Returns True if the spec is a truthy scalar, or a list/weighted-list that
    contains at least one truthy value.
    """
    if not isinstance(attr_spec, list):
        return bool(attr_spec)

    for item in attr_spec:
        if isinstance(item, WeightedValue):
            if bool(item.value):
                return True
        elif isinstance(item, dict) and "value" in item:
            if bool(item["value"]):
                return True
        else:
            if bool(item):
                return True
    return False


def resolve_paging_mode(mode_spec: Union[str, List[str], List[WeightedValue]], rng: RandNum) -> RV.RiscvPagingModes:
    """Resolve a paging mode specification, drawing any random choice from ``rng``."""
    mode_str = resolve_attribute_value(mode_spec, rng)
    if mode_str not in PAGING_MODE_MAP:
        raise ValueError(f"Invalid paging mode: {mode_str}")
    return PAGING_MODE_MAP[mode_str]


def validate_gstage_size_fields(attributes: dict, twostage: bool, gstage_mode: RV.RiscvPagingModes) -> None:
    """Validate gstage_vs_*_size fields are only used when appropriate."""
    gstage_fields = ["gstage_vs_leaf_size", "gstage_vs_nonleaf_size"]
    has_gstage_fields = any(field in attributes for field in gstage_fields)

    if has_gstage_fields:
        if not twostage:
            raise ValueError("G-stage page size fields (gstage_vs_leaf_size, gstage_vs_nonleaf_size) " "can only be used when twostage=true")
        if gstage_mode == RV.RiscvPagingModes.DISABLE:
            raise ValueError("G-stage page size fields cannot be used when gstage_paging_mode=disable")

    # Forcing attributes only have meaning when both stages of translation are active.
    gstage_forcing_attrs = [a for a in attributes if a in GSTAGE_FORCING_ATTRS_SET]
    if gstage_forcing_attrs:
        if not twostage:
            raise ValueError(f"G-stage forcing attributes {sorted(gstage_forcing_attrs)} " f"can only be used when twostage=true")
        if gstage_mode == RV.RiscvPagingModes.DISABLE:
            raise ValueError(f"G-stage forcing attributes {sorted(gstage_forcing_attrs)} " f"cannot be used when gstage_paging_mode=disable")


@dataclass
class _PageOut:
    """One resolved page ready for output: its display id, addresses, and sizes.

    ``va`` is the canonical VA (or GPA, for a G-stage-only space) exactly as the
    walker used it for ``lin_addr``; ``pa`` is the destination page's allocated
    address with bit 55 set when the page is secure.
    """

    display_id: str
    va: int
    pa: int
    size: str
    gpa: Optional[int] = None
    gstage_leaf_size: Optional[str] = None
    gstage_nonleaf_size: Optional[str] = None


def generate_space_output(
    os_result: Optional[SpaceResult],
    g_result: Optional[SpaceResult],
    page_outs: List[_PageOut],
    paging_mode: RV.RiscvPagingModes,
    gstage_paging_mode: RV.RiscvPagingModes,
    is_twostage: bool = False,
) -> SpaceOutput:
    """Build the output for a single space from the built mapping result.

    Each page's full walk is reconstructed from the neutral
    :meth:`~riescue.riemap.result.SpaceResult.walk` primitive -- the frontend never
    reaches into ``PageMap``/``PTTable``. The ``stage`` field on each PTE follows
    the space's configuration:

    - VS enabled + G enabled: interleaved walk with stage=1 (VS) and stage=2 (G)
    - VS enabled + G disabled: VS-only with stage=1 (twostage) or None (single-stage)
    - VS disabled + G enabled: G-only with stage=2
    """
    paging_mode_str = PAGING_MODE_STR_MAP[paging_mode]
    top_base_addr = os_result.root_addr if (os_result is not None and paging_mode != RV.RiscvPagingModes.DISABLE) else None

    gstage_paging_mode_str: Optional[str] = None
    gstage_top_base_addr_int: Optional[int] = None
    if g_result is not None:
        gstage_paging_mode_str = PAGING_MODE_STR_MAP[gstage_paging_mode]
        gstage_top_base_addr_int = g_result.root_addr

    pages: Dict[str, Dict[int, PageEntry]] = {}  # ID -> VA -> PageEntry

    def add_page(page_id: str, va: int, entry: PageEntry) -> None:
        pages.setdefault(page_id, {})[va] = entry

    for page_out in page_outs:
        va = page_out.va
        pa = page_out.pa
        page_id = page_out.display_id
        size_str, gleaf, gnonleaf = page_out.size, page_out.gstage_leaf_size, page_out.gstage_nonleaf_size

        if paging_mode != RV.RiscvPagingModes.DISABLE and g_result is not None and os_result is not None:
            # Two-stage: for each VS-stage PTE (a GPA), interleave the G-stage
            # walk that translates it, then the G-stage walk of the final page GPA.
            vs_steps, _ = os_result.walk(va)
            ptes: List[PTEInfo] = []
            for vs in vs_steps:
                g_steps, vs_pte_pa = g_result.walk(vs.pte_addr)
                ptes.extend(PTEInfo(address=g.pte_addr, level=g.level, stage=2) for g in g_steps)
                resolved = vs_pte_pa if vs_pte_pa is not None else vs.pte_addr
                ptes.append(PTEInfo(address=resolved, level=vs.level, stage=1))
            # ``_PageOut.pa`` is always an int, and PA 0 is a legal address.
            if page_out.gpa is None:
                raise RuntimeError("two-stage output is missing its declared GPA")
            g_steps, _ = g_result.walk(page_out.gpa)
            ptes.extend(PTEInfo(address=g.pte_addr, level=g.level, stage=2) for g in g_steps)
        elif paging_mode != RV.RiscvPagingModes.DISABLE:
            # VS-stage only: stage=1 in a twostage context, otherwise single-stage.
            vs_stage = 1 if is_twostage else None
            vs_steps, _ = os_result.walk(va)
            ptes = [PTEInfo(address=s.pte_addr, level=s.level, stage=vs_stage) for s in vs_steps]
        elif g_result is not None:
            # VS-stage disabled: G-stage-only walk over the page's GPA.
            g_steps, _ = g_result.walk(va)
            ptes = [PTEInfo(address=s.pte_addr, level=s.level, stage=2) for s in g_steps]
        else:
            # Paging fully disabled: no page-table walk to record.
            continue

        add_page(page_id, va, PageEntry(pa=pa, size=size_str, ptes=ptes, gstage_vs_leaf_size=gleaf, gstage_vs_nonleaf_size=gnonleaf))

    return SpaceOutput(
        paging_mode=paging_mode_str,
        pages=pages,
        twostage=is_twostage,
        top_base_addr=top_base_addr,
        gstage_paging_mode=gstage_paging_mode_str,
        gstage_top_base_addr=gstage_top_base_addr_int,
    )


# ============================================================================
# Public API
# ============================================================================


def _hex(value: Optional[str]) -> Optional[int]:
    """Parse an optional hex string to int."""
    return int(value, 16) if value is not None else None


def _memory_from_mmap(mmap: List[MemoryRegion]) -> Memory:
    """Build a riemap Memory from the config's physical memory regions."""
    dram_regions: Dict[str, Dict[str, Any]] = {}
    for idx, region in enumerate(mmap):
        size = region.high - region.low
        name = f"secure_dram{idx}" if region.secure else f"dram{idx}"
        dram_regions[name] = {
            "address": f"0x{region.low:x}",
            "size": f"0x{size:x}",
            "cacheable": True,
            "configurable": True,
        }
        if region.secure:
            dram_regions[name]["secure"] = True
    return Memory.from_dict({"dram": dram_regions})


# The PTE attributes worth showing in a per-page DEBUG line: enough to tell two pages
# apart without printing the whole expanded per-level matrix.
_LOGGED_ATTRS = ("size", "v", "u", "r", "w", "x", "a", "d", "g", "pbmt")


def _resolve_space_pages(
    space_config: SpaceConfig,
    space_id: str,
    paging_mode: RV.RiscvPagingModes,
    gstage_paging_mode: RV.RiscvPagingModes,
    twostage: bool,
    rng: RandNum,
):
    """Yield one ``(page_spec, display_id, resolved_attrs, pagesize, gstage_leaf_ps,
    gstage_nonleaf_ps)`` tuple per concrete page.

    Random attribute specs (including size) are resolved in per-space,
    per-specification, per-page order so attribute policy is reproducible and
    independent of allocator internals.

    ``space_id`` is carried only so the log lines can name the space -- this is where a
    user's question "what did my randomized spec resolve to?" is answered, and an answer
    that does not say which space it belongs to is no answer in a multi-space config.
    """
    # If VS-stage paging is disabled, G-stage governs the valid page sizes.
    effective_paging_mode = gstage_paging_mode if paging_mode == RV.RiscvPagingModes.DISABLE else paging_mode

    for page_spec in space_config.pages:
        validate_gstage_size_fields(page_spec.attributes.attrs, twostage, gstage_paging_mode)

    log.info(
        "Resolving page specs for space '%s': %d page specs, twostage=%s, paging_mode=%s",
        space_id,
        len(space_config.pages),
        space_config.twostage,
        space_config.paging_mode,
    )
    resolved_count = 0
    for page_spec_idx, page_spec in enumerate(space_config.pages):
        attributes = page_spec.attributes.attrs
        display_id = page_spec.id if page_spec.id is not None else f"{page_spec_idx}"
        log.debug(
            "Processing page spec %d (id='%s'): num_pages=%d, size=%s",
            page_spec_idx,
            display_id,
            page_spec.num_pages,
            attributes.get("size", "4kb"),
        )

        filtered = attributes.copy()
        if "size" in filtered:
            filtered["size"] = filter_size_attribute(filtered["size"], effective_paging_mode)
        if "gstage_vs_leaf_size" in filtered:
            filtered["gstage_vs_leaf_size"] = filter_size_attribute(filtered["gstage_vs_leaf_size"], gstage_paging_mode)
        if "gstage_vs_nonleaf_size" in filtered:
            filtered["gstage_vs_nonleaf_size"] = filter_size_attribute(filtered["gstage_vs_nonleaf_size"], gstage_paging_mode)

        for page_num in range(page_spec.num_pages):
            scalar_attributes = {
                "size",
                "gstage_vs_leaf_size",
                "gstage_vs_nonleaf_size",
                "secure",
            }
            resolved_attrs: Dict[str, Any] = {key: (resolve_attribute_value(spec, rng) if key in scalar_attributes else _resolve_pte_attribute(spec, rng)) for key, spec in filtered.items()}
            # Default the accessed/dirty bits to 1 unless the user set them.
            resolved_attrs.setdefault("a", 1)
            resolved_attrs.setdefault("d", 1)

            gstage_leaf = resolved_attrs.get("gstage_vs_leaf_size")
            gstage_nonleaf = resolved_attrs.get("gstage_vs_nonleaf_size")
            pagesize_enum = RV.RiscvPageSizes.str_to_enum(resolved_attrs.get("size", "4kb"))
            gleaf_ps = RV.RiscvPageSizes.str_to_enum(gstage_leaf) if gstage_leaf else None
            gnonleaf_ps = RV.RiscvPageSizes.str_to_enum(gstage_nonleaf) if gstage_nonleaf else None

            log.debug("Page '%s__%d' resolved attributes: %s", display_id, page_num, {k: v for k, v in resolved_attrs.items() if k in _LOGGED_ATTRS})
            if gleaf_ps is not None or gnonleaf_ps is not None:
                log.debug(
                    "G-stage geometry for page '%s__%d': leaf_size=%s, nonleaf_size=%s",
                    display_id,
                    page_num,
                    gstage_leaf or "default",
                    gstage_nonleaf or "default",
                )
            resolved_count += 1
            yield (page_spec, display_id, resolved_attrs, pagesize_enum, gleaf_ps, gnonleaf_ps)

    log.info("Resolved %d pages for space '%s'", resolved_count, space_id)


def _pte_attrs(resolved_attrs: Dict[str, Any]) -> Dict[str, Any]:
    """The PTE attribute dict for a single-stage mapping: the resolved attrs minus geometry.

    The three ``*size`` keys describe page geometry, not PTE bits, and come from the
    :class:`Page` / :class:`PTGPage` objects instead -- so they are dropped here rather than
    passed to an attribute resolver that would not know what to do with them. Everything
    else is already in its final form: the leaf-precedence pre-pass has materialized each
    bare base bit as ``{base}_level{leaf}``, so the caller can pass this straight to
    :func:`resolve.pt_node_levels_with_leaf`.
    """
    return {k: v for k, v in resolved_attrs.items() if k not in ("size", "gstage_vs_leaf_size", "gstage_vs_nonleaf_size")}


def _apply_leaf_base_precedence(attrs: Dict[str, Any], leaf_level: int) -> Dict[str, Any]:
    """Materialize each bare base bit as this page's explicit leaf force.

    In the JSON schema a bare PTE attribute is the leaf value. A
    ``{base}_level{n}`` key addresses another level, so a bare value also takes precedence when
    ``n`` happens to be the leaf level. Materializing that rule as a concrete leaf key
    gives the shared resolver one unambiguous declaration.
    """
    out = dict(attrs)
    for base in resolve.LEAF_FOLD_BASES:
        if out.get(base) is not None:
            out[f"{base}_level{leaf_level}"] = out[base]
    return out


def _expand_gstage_forcing_attrs(
    resolved_attrs: Dict[str, Any],
    pagesize: RV.RiscvPageSizes,
    config: PagingParams,
    paging_mode: RV.RiscvPagingModes,
    paging_g_mode: RV.RiscvPagingModes,
    gstage_leaf_ps: Optional[RV.RiscvPageSizes],
    gstage_nonleaf_ps: Optional[RV.RiscvPageSizes],
) -> Dict[str, Any]:
    """Expand a page's g-stage forcing shorthand into concrete PTE attr keys.

    This is the frontend's own responsibility: :func:`resolve.apply_gstage_leaf_nonleaf_attrs`
    materializes each g-stage forcing shorthand into its concrete
    ``{base}_level{vs}_glevel{g}`` key (or, when VS paging is disabled, a single-stage
    ``{base}_level{n}`` key) on the attrs dict. The builder performs per-level walker
    expansion (page defaults, U/R/W/X/A/D g-stage seeding, and leaf fallbacks) once
    for the VS mapping and its derived g-stage leaf.

    Nothing about geometry is decided here: the g-stage leaf pagesize comes from the destination
    ``Page`` (``hpa_page.pagesize``) and the non-leaf pagesize comes from each non-leaf node's
    ``PTGPage``, so ``gstage_leaf_ps`` / ``gstage_nonleaf_ps`` are inputs only, selecting
    which level each shorthand force applies to. Sibling isolation is the builder's attribute-based
    coloring.

    Returns the forcing-expanded PTE attribute dict (size keys dropped).
    """
    attrs = dict(resolved_attrs)
    resolve.apply_gstage_leaf_nonleaf_attrs(
        attrs=attrs,
        config=config,
        paging_mode=paging_mode,
        paging_g_mode=paging_g_mode,
        final_pagesize_vs=pagesize,
        gstage_vs_leaf_pagesize=gstage_leaf_ps or RV.RiscvPageSizes.S4KB,
        gstage_vs_nonleaf_pagesize=gstage_nonleaf_ps or RV.RiscvPageSizes.S4KB,
    )
    return {k: v for k, v in attrs.items() if k not in ("size", "gstage_vs_leaf_size", "gstage_vs_nonleaf_size")}


def generate_page_tables(config: PageTableConfig, seed: int = 1) -> PageTableOutput:
    """Generate page tables for a config by driving a :class:`PageTableBuilder`.

    Each JSON space is modeled as bare :class:`Page` allocations plus
    :class:`Mapping` s in the constraint-based engine:

    - twostage + g-stage enabled: a VA space (VS mode) whose pages map VA -> GPA into a
      G-stage space (G mode), plus a GPA -> HPA leaf whose HPA (in the physical leaf) is
      constrained ``SameAs`` the GPA (GPA == HPA);
    - twostage + g-stage disabled: a VS-only space mapping into the shared physical
      leaf space, PTEs labeled stage=1;
    - single-stage: a VA space mapping into the shared physical leaf space;
    - VS disabled + g-stage enabled: a single G-mode space mapping GPA -> PA in the
      shared physical leaf space, PTEs labeled stage=2.
    """
    # Two RandNum objects, both seeded with ``seed``: they produce the SAME sequence, but
    # each is drawn from by only one consumer -- one resolves attribute specs, the other
    # (passed to the builder) drives address generation. That is the point: attribute
    # resolution stays reproducible no matter how many draws the allocator makes, which
    # sharing one object would not give. Not "independent streams" -- they are identical
    # streams, independently consumed.
    attr_rng = RandNum(seed=seed)
    rng = RandNum(seed=seed)

    log.info("Starting page table generation: seed=%d, spaces=%s", seed, list(config.spaces.keys()))

    # Validate secure_pt_probability and page-level secure attribute requirements.
    has_secure_region = any(region.secure for region in config.mmap)
    for space_id, space_config in config.spaces.items():
        if space_config.secure_pt_probability > 0 and not has_secure_region:
            raise ValueError(
                f"Space '{space_id}' has secure_pt_probability={space_config.secure_pt_probability} "
                f"but no secure memory regions are defined in mmap. "
                f'Add at least one mmap entry with "secure": true, e.g.: '
                f'{{"low": "0x...", "high": "0x...", "secure": true}}'
            )
        for page_spec in space_config.pages:
            secure_attr = page_spec.attributes.attrs.get("secure", 0)
            if _could_be_truthy(secure_attr) and not has_secure_region:
                pid = page_spec.id if page_spec.id is not None else "(unnamed)"
                raise ValueError(f"Space '{space_id}' page '{pid}' has secure attribute " f"but no secure memory regions are defined in mmap.")

    memory = _memory_from_mmap(config.mmap)
    # The physical address space is the only global config; every paging-environment
    # field is set on each Space. The standalone CLI uses one SUPER environment for every
    # space. There is no secure-mode gate: a space's own ``secure_pt_probability`` decides
    # whether its page-table frames are placed in secure memory, and 0 means none are.
    physical_addr_bits = 52
    priv_mode = RV.RiscvPrivileges.SUPER
    builder = PageTableBuilder(rng=rng, memory=memory, physical_addr_bits=physical_addr_bits)
    # The shared physical leaf space is builder.phys; every single-stage /
    # final mapping targets it.

    full_mask = 0xFFFFFFFFFFFFFFFF
    contexts: List[dict] = []
    for space_id, space_config in config.spaces.items():
        log.info("Processing space '%s'", space_id)
        paging_mode = resolve_paging_mode(space_config.paging_mode, attr_rng)
        gstage_paging_mode = resolve_paging_mode(space_config.gstage_paging_mode, attr_rng) if space_config.twostage else RV.RiscvPagingModes.DISABLE
        twostage = space_config.twostage
        both_stages = twostage and paging_mode != RV.RiscvPagingModes.DISABLE and gstage_paging_mode != RV.RiscvPagingModes.DISABLE
        gonly = twostage and paging_mode == RV.RiscvPagingModes.DISABLE and gstage_paging_mode != RV.RiscvPagingModes.DISABLE

        secure_prob = space_config.secure_pt_probability

        def _space(mode: RV.RiscvPagingModes, stage: Stage, root_frame: "Optional[Page]" = None) -> Space:
            return Space(paging_mode=mode, stage=stage, secure_pt_probability=secure_prob, priv_mode=priv_mode, root_frame=root_frame)

        if both_stages:
            # VA space (VS mode) -> G-stage space (G mode); the GPA is pinned SameAs its HPA.
            # The root pages are constructed first so the VS space can declare its root
            # frame on itself (Space is frozen), then everything is added in one go.
            dst_space = _space(gstage_paging_mode, Stage.G)
            root_hpa = Page(
                space=builder.phys,
                pagesize=RV.RiscvPageSizes.S4KB,
                addr=AddrSpec(
                    qualifiers={
                        RV.AddressQualifiers.ADDRESS_DRAM,
                    }
                ),
            )
            root_gpa = Page(
                space=dst_space,
                pagesize=RV.RiscvPageSizes.S4KB,
                addr=AddrSpec(relation=SameAs(root_hpa)),
            )
            src_space = builder.add_space(_space(paging_mode, Stage.VS, root_frame=root_gpa))
            builder.add_space(dst_space)
            builder.add_page(root_hpa)
            builder.add_page(root_gpa)
            builder.add_mapping(
                Mapping(
                    src=root_gpa,
                    dst=root_hpa,
                    pt_nodes={
                        LEAF: PTNode(
                            attrs={
                                "v": 1,
                                "r": 1,
                                "w": 1,
                                "x": 1,
                                "u": 1,
                                "a": 1,
                                "d": 1,
                            }
                        )
                    },
                )
            )
        elif gonly:
            # A single G-mode space mapping GPA -> PA in the physical leaf space. Role
            # inference only sees table targets, so the g-stage table format (16 KiB root,
            # u=1 leaves) must be declared explicitly via stage=Stage.G: it is a bare
            # g-stage source (never a mapping target), which the builder recognizes as a
            # 16 KiB-aligned hgatp root with u=1 leaves.
            src_space = builder.add_space(_space(gstage_paging_mode, Stage.G))
            dst_space = builder.phys
        else:
            # Single-stage, or VS-only two-stage: a VA space mapping into physical leaf.
            src_space = builder.add_space(_space(paging_mode, Stage.SINGLE))
            dst_space = builder.phys

        # The source VA/GPA free-draw width is NOT set here: leaving ``AddrSpec.bits`` unset
        # lets builder._own_va_bits derive it from the space itself, which is the one place
        # that knows the space's mode and whether it is a bare g-stage source. The two agreed
        # for every branch except the gonly one, where this frontend capped the draw one bit
        # below the g-stage input width -- on the belief that make_canonical_va would
        # sign-extend it. It would not: canonical_va checks gstage_source first and calls
        # make_canonical_gpa, which zero-extends. So the cap bought nothing and cost the top
        # GPA bit (38 of 39 under sv39), making a config that asks for it unsatisfiable.
        if twostage:
            pa_bits = RV.RiscvPagingModes.linear_addr_bits(gstage_paging_mode, gstage=True)
        else:
            pa_bits = 56

        paging_params = PagingParams(
            physical_addr_bits=physical_addr_bits,
            # Every g-stage access is a user access (the guest runs in VS/VU), so a bare
            # g-stage space resolves its forces as USER: under SUPER, ``_pt_attrs_helper``
            # seeds ``u_level{leaf}=0`` and treats a declared ``u: 0`` as the bit's
            # insignificant value, both of which describe a VS-stage privilege this space
            # does not have. An explicit ``u`` in the config still takes precedence.
            priv_mode=RV.RiscvPrivileges.USER if gonly else priv_mode,
            secure_pt_probability=secure_prob,
        )

        page_meta: List[dict] = []
        for page_spec, display_id, resolved_attrs, pagesize, gleaf_ps, gnonleaf_ps in _resolve_space_pages(space_config, space_id, paging_mode, gstage_paging_mode, twostage, attr_rng):
            secure = bool(resolved_attrs.get("secure", 0))

            va_size = RV.RiscvPageSizes.memory(pagesize)
            va_mask = RV.RiscvPageSizes.address_mask(pagesize)
            pa_size = va_size
            pa_mask = va_mask
            mapping_kwargs: Dict[str, Any] = {}
            gstage_leaf_pagesize = gleaf_ps or RV.RiscvPageSizes.S4KB

            src_leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
            # Establish the JSON schema's bare-base leaf value before expanding
            # g-stage forcing shorthand. An explicit g-stage force then has the
            # intended higher priority on the selected g-stage PTE.
            resolved_attrs = _apply_leaf_base_precedence(resolved_attrs, src_leaf_level)

            if both_stages:
                # Keep running the resolver to expand the g-stage forcing shorthand into
                # page_attrs (priority-resolved) and to size the g-stage PT nodes. The
                # Coloring separates conflicting siblings, so src/HPA
                # reservations retain the declared page size.
                page_attrs = _expand_gstage_forcing_attrs(resolved_attrs, pagesize, paging_params, paging_mode, gstage_paging_mode, gleaf_ps, gnonleaf_ps)
                # The g-stage LEAF pagesize comes from ``hpa_page`` (built below with
                # ``pagesize=gstage_leaf_pagesize``) and is inherited by the GPA page, so only
                # the synthesized non-leaf nodes' geometry needs declaring.
                mapping_kwargs = dict(gstage_nonleaf_pagesize=gnonleaf_ps)
            elif gonly:
                # A bare g-stage space accepts the g-stage forcing shorthand too, and
                # resolve.randomize_gstage_pt_attrs already implements the VS-disabled
                # semantics: every VS selector collapses onto a single-stage
                # ``{base}_level{n}`` key in the g-stage tree. Without this call the
                # shorthand stayed a literal attribute name that pt_node_levels_with_leaf
                # dropped -- silently, while validate_gstage_size_fields accepted it.
                #
                # The g-stage LEAF geometry here is the page's OWN pagesize, not
                # ``gstage_vs_leaf_size``: with no VS stage the page IS its own GPA -> HPA
                # leaf. Master selected the level from the 4 KiB default, so on a 2 MiB
                # g-only page ``a_leaf_gleaf`` was applied at level 0 while the real leaf is
                # level 1 and the force reached no PTE. The 4 KiB case is unchanged.
                page_attrs = _expand_gstage_forcing_attrs(resolved_attrs, pagesize, paging_params, paging_mode, gstage_paging_mode, pagesize, gnonleaf_ps)
            else:
                page_attrs = _pte_attrs(resolved_attrs)

            user_va_and = _hex(page_spec.va_and)
            user_pa_and = _hex(page_spec.pa_and)
            va_and_eff = va_mask & (user_va_and if user_va_and is not None else full_mask)
            pa_and_eff = pa_mask & (user_pa_and if user_pa_and is not None else full_mask)

            src_addr = AddrSpec(exact=_hex(page_spec.va), and_mask=va_and_eff, or_mask=_hex(page_spec.va_or))
            src_page = builder.add_page(Page(space=src_space, pagesize=pagesize, addr=src_addr, reserve_size=va_size))

            dst_qualifiers = {RV.AddressQualifiers.ADDRESS_SECURE} if secure else set()
            dst_addr = AddrSpec(exact=_hex(page_spec.pa), and_mask=pa_and_eff, or_mask=_hex(page_spec.pa_or), bits=pa_bits, qualifiers=dst_qualifiers)

            if both_stages:
                hpa_page = builder.add_page(
                    Page(
                        space=builder.phys,
                        pagesize=gstage_leaf_pagesize,
                        addr=dst_addr,
                        reserve_size=pa_size,
                    )
                )
                gpa_page = builder.add_page(
                    Page(
                        space=dst_space,
                        pagesize=gstage_leaf_pagesize,
                        addr=AddrSpec(relation=SameAs(hpa_page)),
                        reserve_size=pa_size,
                    )
                )
                vs_pt_nodes = resolve.pt_nodes_from_levels(
                    resolve.pt_node_levels_with_leaf(
                        page_attrs,
                        src_leaf_level,
                    ),
                    src_leaf_level,
                )
                resolve.attach_gstage_ptgpages(
                    vs_pt_nodes,
                    page_attrs,
                    src_leaf_level,
                    gnonleaf_ps,
                    RV.RiscvPagingModes.max_levels(paging_mode),
                )
                builder.add_mapping(
                    Mapping(
                        src=src_page,
                        dst=gpa_page,
                        pt_nodes=vs_pt_nodes,
                    )
                )
                g_levels = resolve.gstage_leaf_pt_node_levels(
                    page_attrs,
                    vs_paging_mode=paging_mode,
                    gstage_mode=gstage_paging_mode,
                    vs_pagesize=pagesize,
                    gstage_vs_leaf_size=gstage_leaf_pagesize,
                    gstage_vs_nonleaf_size=gnonleaf_ps,
                    secure=secure,
                )
                builder.add_mapping(
                    Mapping(
                        src=gpa_page,
                        dst=hpa_page,
                        pt_nodes={level: PTNode(attrs=dict(attrs)) for level, attrs in g_levels.items()},
                    )
                )
                dst_page = hpa_page
            else:
                # single-stage / g-only: one VA source page -> one physical leaf page. The
                # source leaf + per-level PTE bits are declared as pt_nodes: base bits fold
                # onto the leaf, forced {base}_level{n} keys stay per-level (attribute-based
                # coloring then separates conflicting siblings). This is the whole leaf-PTE
                # source -- there is no freeform Mapping.attrs; secure comes from the dst frame's
                # qualifier. (The two-stage path builds its own pt_nodes inside
                # add_two_stage_mapping, so these are needed only here.)
                src_pt_nodes = resolve.pt_nodes_from_levels(resolve.pt_node_levels_with_leaf(page_attrs, src_leaf_level), src_leaf_level)
                # A bare g-stage (VS-disabled) walk's glevel forces apply to its own
                # synthesized identity nodes, which use a PTGPage per level; a single-stage
                # page has none. max_levels=0: neither declares a synthesized g-stage identity, so
                # only levels carrying an explicit force declare a PTGPage.
                resolve.attach_gstage_ptgpages(src_pt_nodes, page_attrs, src_leaf_level, None, 0)
                dst_page = builder.add_page(Page(space=dst_space, pagesize=pagesize, addr=dst_addr, reserve_size=pa_size))
                builder.add_mapping(Mapping(src=src_page, dst=dst_page, pt_nodes=src_pt_nodes))

            page_meta.append(
                dict(
                    display_id=display_id,
                    src_page=src_page,
                    dst_page=dst_page,
                    gpa_page=(gpa_page if both_stages else None),
                    secure=secure,
                    size=str(pagesize).lower(),
                    gstage_leaf_size=str(gleaf_ps).lower() if gleaf_ps else None,
                    gstage_nonleaf_size=str(gnonleaf_ps).lower() if gnonleaf_ps else None,
                )
            )

        contexts.append(
            dict(
                space_id=space_id,
                src_space=src_space,
                dst_space=dst_space,
                paging_mode=paging_mode,
                gstage_paging_mode=gstage_paging_mode,
                twostage=twostage,
                both_stages=both_stages,
                gonly=gonly,
                page_meta=page_meta,
            )
        )

    result = builder.build()

    # Every PTE across every built map, read back through the public API rather than
    # by traversing PageMap internals. ``entries`` is a MEMORY IMAGE, so each PTE is keyed
    # where its bytes live -- the frame's backing address. ``pte_entries()`` keys the walk
    # domain instead, which for a VS table under a non-identity g-stage is a GPA: loading
    # the image at those addresses would write the page tables into guest-physical
    # addresses that the host image has no such storage for.
    all_entries: Dict[int, int] = {}
    for space_result in result.spaces():
        for view in space_result.tables():
            for entry in view.entries:
                all_entries[view.backing_addr + view.entry_size * entry.index] = entry.value

    space_outputs: Dict[str, SpaceOutput] = {}
    for ctx in contexts:
        page_outs: List[_PageOut] = []
        for pm in ctx["page_meta"]:
            # The source page's VA is the canonical VA/GPA the engine's _install_mapping
            # computed for the walk; the destination page's PA is its allocated address.
            va, _ = result.address_of(pm["src_page"])
            _, pa = result.address_of(pm["dst_page"])
            gpa = result.address_of(pm["gpa_page"])[0] if pm["gpa_page"] is not None else None
            pa |= _SECURE_BIT if pm["secure"] else 0
            log.debug("Placed page '%s' in space '%s': VA=0x%016x, PA=0x%016x, size=%s", pm["display_id"], ctx["space_id"], va, pa, pm["size"])
            page_outs.append(
                _PageOut(
                    display_id=pm["display_id"],
                    va=va,
                    pa=pa,
                    gpa=gpa,
                    size=pm["size"],
                    gstage_leaf_size=pm["gstage_leaf_size"],
                    gstage_nonleaf_size=pm["gstage_nonleaf_size"],
                )
            )

        os_result: Optional[SpaceResult]
        g_result: Optional[SpaceResult]
        if ctx["both_stages"]:
            os_result = result.space(ctx["src_space"])
            g_result = result.space(ctx["dst_space"])
            out_paging = ctx["paging_mode"]
            out_gstage = ctx["gstage_paging_mode"]
        elif ctx["gonly"]:
            os_result = None
            g_result = result.space(ctx["src_space"])
            out_paging = RV.RiscvPagingModes.DISABLE
            out_gstage = ctx["gstage_paging_mode"]
        else:
            # A paging-disabled space has no page table: the build skipped it, so asking
            # the result for one would (rightly) raise. The config is what knows that, so
            # branch on the mode here rather than probing the result object.
            os_result = result.space(ctx["src_space"]) if ctx["paging_mode"] != RV.RiscvPagingModes.DISABLE else None
            g_result = None
            out_paging = ctx["paging_mode"]
            out_gstage = ctx["gstage_paging_mode"]

        space_outputs[ctx["space_id"]] = generate_space_output(os_result, g_result, page_outs, out_paging, out_gstage, ctx["twostage"])

    log.info("Page table generation complete: %d PTEs across %d spaces", len(all_entries), len(space_outputs))
    return PageTableOutput(entries=all_entries, spaces=space_outputs)
