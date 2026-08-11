# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Mid-level page-table attribute resolvers, shared by every riemap consumer.

These helpers translate the user-facing attribute vocabulary (base bits,
``{attr}_nonleaf`` shorthand, and the g-stage ``{attr}_{leaf|nonleaf}_g{leaf|nonleaf}``
/ ``{attr}_level{n}_g{leaf|nonleaf}`` / ``{attr}_{leaf|nonleaf}_glevel{n}`` forcing
forms) into the concrete ``{base}_level{n}`` / ``{base}_level{vs}_glevel{g}`` keys the
low-level page-table walker consumes. (Sibling isolation for forced PTEs is now the
builder's attribute-based coloring, not an address-reservation grown here.)

Everything here operates on a neutral ``attrs: dict`` plus a :class:`PagingParams`
(policy) and explicit paging-mode arguments, so the standalone JSON frontend and
RiescueD's request builder resolve attributes through one implementation instead of
hand-kept copies that drift.
"""

import functools
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import riescue.lib.enums as RV
from riescue.lib import common
from riescue.riemap.attributes import PTE_BASES, PTE_LEVELS
from riescue.riemap.config import PagingParams
from riescue.riemap.request import Choice, LEAF, PTGPage, PTNode

log = logging.getLogger(__name__)

# Compatibility names for consumers of this resolver. Both are aliases of the
# architectural schema, not independently-maintained PTE vocabularies.
LEVEL_TYPES = PTE_BASES
LEVELS = PTE_LEVELS

# Map string paging modes to enums
PAGING_MODE_MAP = {
    "sv32": RV.RiscvPagingModes.SV32,
    "sv39": RV.RiscvPagingModes.SV39,
    "sv48": RV.RiscvPagingModes.SV48,
    "sv57": RV.RiscvPagingModes.SV57,
    "disable": RV.RiscvPagingModes.DISABLE,
}

# Map enum modes back to strings
PAGING_MODE_STR_MAP = {
    RV.RiscvPagingModes.SV32: "sv32",
    RV.RiscvPagingModes.SV39: "sv39",
    RV.RiscvPagingModes.SV48: "sv48",
    RV.RiscvPagingModes.SV57: "sv57",
    RV.RiscvPagingModes.DISABLE: "disable",
}


@dataclass
class WeightedValue:
    """Represents a weighted choice for attribute randomization."""

    value: Any
    weight: float


# Type alias for attribute specifications
AttributeSpec = Union[Any, List[Any], List[WeightedValue]]  # Scalar (string, int, bool)  # Uniform random selection  # Weighted random selection


def _is_weighted_choice(item: Any) -> bool:
    """True for a weighted choice entry, in either the parsed or the raw JSON spelling."""
    return isinstance(item, WeightedValue) or (isinstance(item, dict) and "value" in item and "weight" in item)


def choice_list_is_weighted(values: List[Any], description: str) -> bool:
    """Whether a choice list is the weighted form, rejecting a list that mixes the two.

    A uniform entry beside a weighted one has no weight to compete with, so the mixed
    list silently means something other than what it looks like. Shared by every
    consumer that parses a choice list so the two forms are told apart once.
    """
    weighted = [_is_weighted_choice(item) for item in values]
    if any(weighted) and not all(weighted):
        raise ValueError(f"{description} mixes weighted entries with plain values; a choice list must be entirely weighted or entirely uniform")
    return bool(weighted) and all(weighted)


def choice_weight(item: Any) -> Any:
    """The declared weight of a weighted choice entry, in either spelling."""
    return item.weight if isinstance(item, WeightedValue) else item["weight"]


def choice_value(item: Any) -> Any:
    """The value a choice-list entry offers, weighted or plain."""
    if isinstance(item, WeightedValue):
        return item.value
    if isinstance(item, dict) and "value" in item:
        return item["value"]
    return item


def validate_choice_weights(values: List[Any], description: str) -> None:
    """Reject weights a weighted draw cannot use.

    Every weight must be a finite non-negative number (``bool`` excluded: it is an
    ``int`` subclass and ``True`` would read as the weight 1), and at least one must be
    positive -- an all-zero list selects nothing.
    """
    total = 0.0
    for item in values:
        weight = choice_weight(item)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0:
            raise ValueError(f"{description} has an invalid weight {weight!r}; a weight must be a finite number >= 0")
        total += weight
    if total <= 0:
        raise ValueError(f"{description} has no positive weight; at least one weighted choice must have a weight > 0")


def make_canonical_va(addr: int, paging_mode: RV.RiscvPagingModes) -> int:
    """
    Make a virtual address canonical by sign-extending it.

    In RISC-V, virtual addresses must be sign-extended from the address space width.
    For sv39: bits 63:39 must all equal bit 38
    For sv48: bits 63:48 must all equal bit 47
    For sv57: bits 63:57 must all equal bit 56

    Args:
        addr: The raw address to make canonical
        paging_mode: The paging mode determining the address width

    Returns:
        The canonical (sign-extended) address
    """
    if paging_mode == RV.RiscvPagingModes.DISABLE:
        return addr

    width = RV.RiscvPagingModes.linear_addr_bits(paging_mode)

    if paging_mode == RV.RiscvPagingModes.SV32:
        # For sv32, just mask to 32 bits
        return addr & 0xFFFFFFFF

    # Check the sign bit (highest bit of the address space)
    sign_bit_pos = width - 1
    sign_bit = (addr >> sign_bit_pos) & 1

    # Mask to get only the valid address bits
    valid_bits_mask = (1 << width) - 1
    addr_bits = addr & valid_bits_mask

    if sign_bit == 1:
        # Sign extend: set all upper bits to 1
        upper_bits = ((1 << (64 - width)) - 1) << width
        return addr_bits | upper_bits
    else:
        # Upper bits should be 0 (already masked)
        return addr_bits


def make_canonical_gpa(addr: int, paging_mode: RV.RiscvPagingModes) -> int:
    """
    Make a guest physical address canonical by zero-extending it.

    GPAs must be zero-extended, meaning upper bits beyond the address space width must be 0.

    Args:
        addr: The raw address to make canonical
        paging_mode: The G-stage paging mode determining the address width

    Returns:
        The canonical (zero-extended) address
    """
    if paging_mode == RV.RiscvPagingModes.DISABLE:
        return addr

    width = RV.RiscvPagingModes.linear_addr_bits(paging_mode, gstage=True)

    # Mask to get only the valid address bits (zero-extend)
    valid_bits_mask = (1 << width) - 1
    return addr & valid_bits_mask


def get_valid_page_sizes(paging_mode: RV.RiscvPagingModes) -> List[str]:
    """Get list of valid page sizes for a given paging mode.

    Returns page sizes in lowercase format (e.g., "4kb", "2mb", "1gb").
    """
    if paging_mode == RV.RiscvPagingModes.SV32:
        return ["4kb", "4mb"]
    elif paging_mode == RV.RiscvPagingModes.SV39:
        return ["4kb", "64kb", "2mb", "1gb"]
    elif paging_mode == RV.RiscvPagingModes.SV48:
        return ["4kb", "64kb", "2mb", "1gb", "512gb"]
    elif paging_mode == RV.RiscvPagingModes.SV57:
        return ["4kb", "64kb", "2mb", "1gb", "512gb", "256tb"]
    else:  # DISABLE or unknown
        return ["4kb"]


def filter_size_attribute(size_spec: AttributeSpec, paging_mode: RV.RiscvPagingModes) -> AttributeSpec:
    """Filter size attribute to only include valid sizes for the paging mode.

    Args:
        size_spec: The size attribute specification (scalar, list, or weighted list)
        paging_mode: The paging mode to validate against

    Returns:
        Filtered size specification with only valid sizes for the paging mode.

    Raises:
        ValueError: If the size specification contains no valid sizes for the paging mode
    """
    valid_sizes = get_valid_page_sizes(paging_mode)
    paging_mode_name = PAGING_MODE_STR_MAP[paging_mode]

    # If it's not a list, validate it directly
    if not isinstance(size_spec, list):
        size_str = str(size_spec).lower()
        if size_str not in valid_sizes:
            raise ValueError(f"Size '{size_spec}' is not valid for paging mode {paging_mode_name}. " f"Valid sizes: {', '.join(valid_sizes)}")
        return size_spec

    if len(size_spec) == 0:
        raise ValueError("Size attribute list cannot be empty")

    if choice_list_is_weighted(size_spec, "size attribute"):
        # Filter weighted values
        filtered = [wv for wv in size_spec if str(choice_value(wv)).lower() in valid_sizes]
        removed = [wv for wv in size_spec if str(choice_value(wv)).lower() not in valid_sizes]

        if len(filtered) == 0:
            removed_str = ", ".join([str(choice_value(wv)) for wv in removed])
            raise ValueError(f"No valid sizes in weighted list for paging mode {paging_mode_name}. " f"Invalid sizes: {removed_str}. Valid sizes: {', '.join(valid_sizes)}")

        # Warn about filtered out sizes
        if removed:
            removed_str = ", ".join([str(choice_value(wv)) for wv in removed])
            log.info("Filtered out invalid sizes for %s: %s", paging_mode_name, removed_str)

        return filtered

    # Plain list - filter it
    filtered = [size for size in size_spec if str(size).lower() in valid_sizes]
    removed = [size for size in size_spec if str(size).lower() not in valid_sizes]

    if len(filtered) == 0:
        removed_str = ", ".join([str(s) for s in removed])
        raise ValueError(f"No valid sizes in list for paging mode {paging_mode_name}. " f"Invalid sizes: {removed_str}. Valid sizes: {', '.join(valid_sizes)}")

    # Warn about filtered out sizes
    if removed:
        removed_str = ", ".join([str(s) for s in removed])
        log.info("Filtered out invalid sizes for %s: %s", paging_mode_name, removed_str)

    return filtered


def setup_uwrx_bit(
    attr: str,
    attrs: Dict[str, Any],
    paging_mode: RV.RiscvPagingModes,
    paging_g_mode: RV.RiscvPagingModes,
    final_pagesize: RV.RiscvPageSizes,
    gstage_vs_leaf_final_pagesize: RV.RiscvPageSizes,
    gstage_vs_nonleaf_final_pagesize: RV.RiscvPageSizes,
) -> None:
    """
    Setup initial state of U/R/W/X/A/D bits for g-stage page table entries.

    This sets level-specific attributes like u_level{vs}_glevel{g} based on
    the page sizes chosen for leaf and non-leaf g-stage translations.
    """
    for key, bit_val in _uwrx_bit_writes(
        attr,
        paging_mode,
        paging_g_mode,
        final_pagesize,
        gstage_vs_leaf_final_pagesize,
        gstage_vs_nonleaf_final_pagesize,
    ):
        attrs[key] = bit_val


@functools.lru_cache(maxsize=None)
def _uwrx_bit_writes(
    attr: str,
    paging_mode: RV.RiscvPagingModes,
    paging_g_mode: RV.RiscvPagingModes,
    final_pagesize: RV.RiscvPageSizes,
    gstage_vs_leaf_final_pagesize: RV.RiscvPageSizes,
    gstage_vs_nonleaf_final_pagesize: RV.RiscvPageSizes,
) -> "tuple[tuple[str, int], ...]":
    """The ``(key, value)`` pairs :func:`setup_uwrx_bit` writes for one geometry.

    A mid-size two-stage test calls ``setup_uwrx_bit`` tens of thousands of times with
    only a handful of distinct geometries; caching the key strings and values avoids
    rebuilding them (and the level math) on every call.
    """
    writes: list[tuple[str, int]] = []
    map_vs_max_levels = RV.RiscvPagingModes.max_levels(paging_mode)
    map_max_levels = RV.RiscvPagingModes.max_levels(paging_g_mode)
    pt_vs_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize)
    disable = paging_mode == RV.RiscvPagingModes.DISABLE

    for vs_level in range(pt_vs_leaf_level, map_vs_max_levels):
        # Need to change the pt_leaf level below based on the g_level leaf/nonleaf
        if vs_level == pt_vs_leaf_level:
            pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(gstage_vs_leaf_final_pagesize)
        else:
            pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(gstage_vs_nonleaf_final_pagesize)

        for g_level in range(pt_leaf_level, map_max_levels):
            if g_level == pt_leaf_level:
                # Leaf level - all bits (u/r/w/x/a/d) need to be default to 1 for gstage leaf level
                bit_val = 1
            else:
                # For all the non-leaf levels, all of these bits need to 0 by default
                bit_val = 0
            writes.append((f"{attr}_level{vs_level}_glevel{g_level}", bit_val))
            # If vs-stage is disabled, we need to omit _level*_glevel* attributes
            if disable:
                writes.append((f"{attr}_level{g_level}", bit_val))

    if disable:
        pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize)
        writes.append((f"{attr}_level{pt_leaf_level}", 1))
    return tuple(writes)


# ----------------------------------------------------------------------------
# G-stage leaf/non-leaf attribute forcing
#
# These mirror riescue-d's generator (riescue/dtest_framework/generator/
# generator.py: randomize_gstage_pt_attrs / pt_attrs_helper). They translate
# the user-facing "{attr}_{leaf|nonleaf}_g{leaf|nonleaf}" shorthand into the
# concrete "{attr}_level{vs}_glevel{g}" (or "{attr}_level{n}") attributes that
# the page table builder consumes. Sibling isolation for a forced PTE is now the
# builder's attribute-based coloring, not an address reservation grown here.
# ----------------------------------------------------------------------------

# Full set of g-stage leaf/non-leaf shorthand attributes. Every architectural
# PTE field follows the same forcing grammar.
GSTAGE_LEAF_NONLEAF_ATTRS = [f"{base}_{vs}_{g}" for base in PTE_BASES for vs in ["nonleaf", "leaf"] for g in ["gnonleaf", "gleaf"]]

_PT_LEVELS = PTE_LEVELS

# 1-token mixed forms: one side is a symbolic leaf/nonleaf and the other is an
# explicit level index. VS-explicit listed before G-explicit so that on a
# same-PTE collision the explicit-G form is applied last and takes precedence.
GSTAGE_MIXED_LEVEL_ATTRS = [f"{b}_level{n}_{g}" for b in PTE_BASES for n in _PT_LEVELS for g in ("gnonleaf", "gleaf")] + [
    f"{b}_{vs}_glevel{n}" for b in PTE_BASES for vs in ("nonleaf", "leaf") for n in _PT_LEVELS
]

# Combined iteration order: lowest priority first so later writes overwrite earlier ones.
# 1-token mixed forms (medium) are processed before 2-token symbolic forms (highest).
# The pure-concrete _level*_glevel* form (lowest) is consumed directly and not iterated.
GSTAGE_FORCING_ATTRS = GSTAGE_MIXED_LEVEL_ATTRS + GSTAGE_LEAF_NONLEAF_ATTRS

# Membership-only view: the list order above is significant (later writes overwrite), so it
# stays a list for iteration; use this set for ``x in ...`` probes.
GSTAGE_FORCING_ATTRS_SET = frozenset(GSTAGE_FORCING_ATTRS)


def gstage_leaf_pte_attrs(vs_attrs: Dict[str, Any], vs_pagesize: RV.RiscvPageSizes, gstage_mode: RV.RiscvPagingModes, secure: bool = False) -> Dict[str, Any]:
    """The g-stage leaf PTE attrs for the final data page of a VA -> GPA leaf.

    Mirrors :meth:`pagetables.Pagetables._emit_gstage_identity` for a page's own leaf: the VS
    leaf's ``{base}_level{vs}_glevel{g}`` forcing keys remapped onto the g-stage leaf's own
    ``{base}_level{g}`` keys, plus ``x=1`` (identity data pages stay executable) and, when the
    page is secure, ``secure=1``. When identity was a mapping flag the walker synthesized this
    leaf; now a consumer declares the GPA -> HPA leaf explicitly and must carry these attrs so
    the g-stage leaf PTE forces the same bits (the VS-level attrs alone would never reach the
    g-stage leaf, which reads ``{base}_level{g}``)."""
    vs_leaf_level = RV.RiscvPageSizes.pt_leaf_level(vs_pagesize)
    g_max = RV.RiscvPagingModes.max_levels(gstage_mode)
    attrs: Dict[str, Any] = {"x": 1}
    if secure:
        attrs["secure"] = 1
    for base in PTE_BASES:
        for level in range(g_max):
            val = vs_attrs.get(f"{base}_level{vs_leaf_level}_glevel{level}")
            if val is not None:
                attrs[f"{base}_level{level}"] = val
    return attrs


def gstage_leaf_attrs_for(
    vs_attrs: Dict[str, Any],
    vs_paging_mode: RV.RiscvPagingModes,
    gstage_mode: RV.RiscvPagingModes,
    vs_pagesize: RV.RiscvPageSizes,
    gstage_vs_leaf_size: Optional[RV.RiscvPageSizes],
    gstage_vs_nonleaf_size: Optional[RV.RiscvPageSizes],
    secure: bool = False,
) -> Dict[str, Any]:
    """The GPA -> HPA g-stage leaf PTE attrs for a two-stage data page.

    A g-stage leaf is always user-reachable, so its U/R/W/X/A/D default to 1: this
    seeds those per-level ``{base}_level{vs}_glevel{g}`` defaults with
    :func:`setup_uwrx_bit`, overlays the VS mapping's g-level forcing (a forced key
    overrides the default), and remaps the vs-leaf-level keys onto the g-stage leaf's
    own levels via :func:`gstage_leaf_pte_attrs`. Both the builder's
    :meth:`~riescue.riemap.builder.PageTableBuilder.add_two_stage_mapping` and RiescueD's
    translator derive the explicit leaf through here, so the two cannot drift. Calling
    :func:`gstage_leaf_pte_attrs` on unseeded VS attrs instead yields a U=0 leaf, which
    faults every guest access (g-stage sees all accesses as user)."""
    seeded: Dict[str, Any] = {}
    gstage_leaf_final = gstage_vs_leaf_size or RV.RiscvPageSizes.S4KB
    gstage_nonleaf_final = gstage_vs_nonleaf_size or RV.RiscvPageSizes.S4KB
    for attr in ["u", "r", "w", "x", "a", "d"]:
        setup_uwrx_bit(attr, seeded, vs_paging_mode, gstage_mode, vs_pagesize, gstage_leaf_final, gstage_nonleaf_final)
    for base in LEVEL_TYPES:
        for v_level in LEVELS:
            for g_level in LEVELS:
                key = f"{base}_level{v_level}_glevel{g_level}"
                if key in vs_attrs:
                    seeded[key] = vs_attrs[key]
    return gstage_leaf_pte_attrs(seeded, vs_pagesize, gstage_mode, secure=secure)


def _size_to_mask(size: int) -> int:
    """Return the alignment mask for an address reservation of ``size`` bytes."""
    return (0xFFFFFFFFFFFFFFFF << common.msb(size)) & 0xFFFFFFFFFFFFFFFF


# A g-stage non-leaf (pointer) PTE's natural value: valid, no permissions. The same
# convention the builder's attribute-based coloring uses to decide which forced bits actually
# distinguish two pointer PTEs.
def _is_default_pointer_bit(base: str, value: Any) -> bool:
    return int(value) == (1 if base == "v" else 0)


def gstage_pointer_span(g_mode: RV.RiscvPagingModes, g_level: int) -> "tuple[int, int] | None":
    """``(size, align_mask)`` of the g-stage POINTER PTE at ``g_level``, or None.

    Every GPA beneath one pointer PTE shares it, so a translation that needs that PTE to itself
    must own this whole span: it is the page a level-``g_level`` leaf would cover,
    ``2 ** index_lo(g_level)``. ``None`` when the level has no index field in ``g_mode``.

    Two callers need the same arithmetic, so it lives here once: a :class:`~riescue.riemap.request.PTGPage`
    that forces a non-leaf g-level (:func:`gstage_exclusive_span`, used when placing a VS walk's
    synthesized frame), and a consumer whose runtime rewrites that PTE with a value it cannot
    declare -- RiescueD's ``modify_leaf_pt`` / ``modify_nonleaf_pt``, which say "the test writes
    this pointer at runtime" and therefore imply the same ownership via explicit ``reserve_size``.
    A bare Stage.G mapping that merely DECLAREs a non-default pointer bit does not take this
    span: attribute-based coloring isolates conflicting siblings instead.

    ``g_level`` is an architectural level in this module's usual BOTTOM-UP numbering (0 is the
    4 KiB leaf level), so the pointer above a leaf of pagesize ``ps`` is
    ``pt_leaf_level(ps) + 1``. Levels outside ``g_mode`` return None rather than raising, since
    both callers take the level from consumer-supplied data."""
    if g_level < 0 or g_level >= RV.RiscvPagingModes.max_levels(g_mode):
        return None
    index_bits = RV.RiscvPagingModes.index_bits(g_mode, g_level)
    if index_bits is None:
        return None
    size = 2 ** index_bits[1]
    return (size, _size_to_mask(size))


def gstage_exclusive_span(
    g_mode: RV.RiscvPagingModes,
    pagesize: RV.RiscvPageSizes,
    level_attrs: Dict[int, Dict[str, Any]],
) -> tuple[int, int]:
    """Bytes a synthesized g-stage FRAME must own EXCLUSIVELY, as ``(size, align_mask)``.

    Used when placing a VS walk's :class:`~riescue.riemap.request.PTGPage` identity (and by
    :func:`riescue.riemap.pagetables.gstage_nonleaf_geometry`): a frame that forces one of its
    own non-leaf pointer PTEs must reserve that pointer's whole span, or a sibling frame is placed in
    the same node, demands the default, and the tree build reports a hard slot conflict. The
    span of the pointer PTE at g-level ``L`` is ``2 ** index_bits(g_mode, L)[1]`` -- the page a
    level-``L`` leaf would cover.

    This is NOT applied to a bare Stage.G *mapping* that declares the same bits on its own
    ``pt_nodes``: that is a plain attribute declaration, and attribute-based coloring keeps a
    sibling that demands the default out of the forced node. RiescueD's runtime rewrites
    (``modify_leaf_pt`` / ``modify_nonleaf_pt``) state the span explicitly as ``reserve_size``.

    ``level_attrs`` is the frame's declared per-g-level PTE bits (``PTGPage.level_attrs()``).
    Only NON-LEAF levels carrying a NON-DEFAULT bit grow the span: a level that merely restates
    the g-stage defaults is not a force and must not move any address. Absent any such force
    the answer is just the frame's own ``pagesize``.
    """
    size = RV.RiscvPageSizes.memory(pagesize)
    leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
    for g_level, bits in level_attrs.items():
        if g_level <= leaf_level:
            continue
        if all(value is None or _is_default_pointer_bit(base, value) for base, value in bits.items()):
            continue
        span = gstage_pointer_span(g_mode, g_level)
        if span is None:
            continue
        size = max(size, span[0])
    return (size, _size_to_mask(size))


_VS_LEVEL_RE = re.compile(r"_level(\d+)_g")  # matches a_level1_gleaf, a_level2_gnonleaf
_G_LEVEL_RE = re.compile(r"_glevel(\d+)$")  # matches a_leaf_glevel2, a_nonleaf_glevel0


def _vs_selector(attr: str) -> tuple:
    """Return (kind, level) for the VS-stage portion of a forcing attr name.

    kind is 'leaf', 'nonleaf', or 'level'. level is an int when kind=='level',
    None otherwise.
    """
    m = _VS_LEVEL_RE.search(attr)
    if m:
        return ("level", int(m.group(1)))
    if "_nonleaf_" in attr:
        return ("nonleaf", None)
    if "_leaf_" in attr:
        return ("leaf", None)
    return (None, None)


def _g_selector(attr: str) -> tuple:
    """Return (kind, level) for the G-stage portion of a forcing attr name.

    kind is 'leaf', 'nonleaf', or 'level'. level is an int when kind=='level',
    None otherwise.
    """
    m = _G_LEVEL_RE.search(attr)
    if m:
        return ("level", int(m.group(1)))
    if "_gnonleaf" in attr:
        return ("nonleaf", None)
    if "_gleaf" in attr:
        return ("leaf", None)
    return (None, None)


def _attr_insignificant_value(base_attr: str, priv_mode: RV.RiscvPrivileges) -> int:
    insignificant_value = 1
    if base_attr in ["g", "n", "pbmt"]:
        insignificant_value = 0
    if base_attr == "u" and priv_mode == RV.RiscvPrivileges.SUPER:
        insignificant_value = 0
    return insignificant_value


def _pt_attrs_helper(
    attr: str,
    attrs: Dict[str, Any],
    paging_mode: RV.RiscvPagingModes,
    final_pagesize: RV.RiscvPageSizes,
    priv_mode: RV.RiscvPrivileges,
) -> None:
    """Single-stage attribute forcing, mirroring generator.py: pt_attrs_helper.

    Translates ``{attr}`` and ``{attr}_nonleaf`` into the concrete ``{attr}_level{n}``
    key, selecting the leaf level (or the lowest available non-leaf level). Operates on
    the ``attrs`` dict in place; no address reservation is derived -- attribute-based coloring
    now isolates conflicting siblings.
    """
    map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode)
    pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize)

    insignificant_value = 1
    leaf_bit_val = 1
    if attr in ["g", "n", "pbmt"]:
        insignificant_value = 0
    if attr[0] == "u":
        if priv_mode == RV.RiscvPrivileges.SUPER:
            insignificant_value = 0
            leaf_bit_val = 0
        if paging_mode == RV.RiscvPagingModes.DISABLE:
            leaf_bit_val = 1

    # Default the per-level u/r/w/x/a/d bits (leaf gets leaf_bit_val, non-leaf 0)
    if attr[0] in ["u", "r", "w", "x", "a", "d"]:
        for level in range(pt_leaf_level, map_max_levels):
            attrs[f"{attr}_level{level}"] = leaf_bit_val if level == pt_leaf_level else 0

    # A specified base value maps onto the leaf level for the current pagesize.
    attr_value = attrs.get(attr)
    if attr_value is not None and attr_value != insignificant_value:
        attrs[f"{attr}_level{pt_leaf_level}"] = attr_value
        return

    # A specified non-leaf value maps onto the lowest available non-leaf level.
    nonleaf_value = attrs.get(f"{attr}_nonleaf")
    if nonleaf_value is not None and nonleaf_value != insignificant_value:
        # A pagesize whose leaf IS the root level has no level above it, so the
        # selector names no PTE. Reporting it beats the old fallback, which wrote the
        # force onto a level below the leaf -- a PTE the walk never reads.
        if map_max_levels - pt_leaf_level <= 1:
            raise ValueError(
                f"'{attr}_nonleaf' selects a non-leaf level, but a {final_pagesize.name} leaf under " f"{PAGING_MODE_STR_MAP[paging_mode]} is the root level and has no non-leaf level above it"
            )
        attrs[f"{attr}_level{pt_leaf_level + 1}"] = nonleaf_value


def pt_attrs(
    attr: str,
    attrs: Dict[str, Any],
    paging_mode: RV.RiscvPagingModes,
    final_pagesize: RV.RiscvPageSizes,
    priv_mode: RV.RiscvPrivileges,
) -> Dict[str, Any]:
    """Single-stage attribute forcing that leaves the caller's dict untouched.

    Public, explicitly-signatured wrapper over the in-place :func:`_pt_attrs_helper`:
    translates ``{attr}`` and ``{attr}_nonleaf`` into the concrete ``{attr}_level{n}``
    keys on a *copy* of ``attrs`` and returns the updated dict. Unlike
    ``_pt_attrs_helper`` it does not mutate the input ``attrs``.
    """
    updated = dict(attrs)
    _pt_attrs_helper(attr, updated, paging_mode, final_pagesize, priv_mode)
    return updated


def randomize_gstage_pt_attrs(
    attr: str,
    attrs: Dict[str, Any],
    paging_mode_g: RV.RiscvPagingModes,
    paging_mode_vs: RV.RiscvPagingModes,
    priv_mode: RV.RiscvPrivileges,
    final_pagesize_vs: RV.RiscvPageSizes,
    final_pagesize_gleaf: RV.RiscvPageSizes,
    final_pagesize_gnonleaf: RV.RiscvPageSizes,
) -> None:
    """Two-stage attribute forcing, mirroring generator.py: randomize_gstage_pt_attrs.

    Translates a shorthand attr name (one of the forms in GSTAGE_FORCING_ATTRS) into the
    concrete ``{base}_level{vs}_glevel{g}`` attribute (or, when VS-stage paging is disabled, a
    single-stage ``{base}_level{n}``/``{base}_nonleaf``). Operates on ``attrs`` in place; this
    is pure ATTRIBUTE resolution and moves no address.

    The ``final_pagesize_*`` parameters are inputs, not outputs: they *select* which level a
    shorthand force applies to (a 2 MiB g-stage leaf ends the g-stage walk at g-level 1, so
    ``_gleaf`` means level 1 there). Geometry itself is declared per node -- on the destination
    :class:`~riescue.riemap.request.Page` for the leaf and on each non-leaf node's
    :class:`~riescue.riemap.request.PTGPage`. The function does not modify
    address geometry.

    Supported shorthand forms, by priority (highest last so later writes overwrite earlier ones):
      1-token: {base}_level{N}_g(non)leaf, {base}_(non)leaf_glevel{N}
      2-token: {base}_(non)leaf_g(non)leaf
    """
    base_attr = attr.split("_")[0]

    attr_value = attrs.get(attr)
    # A forced attribute must always be materialized into its concrete
    # {base}_level{vs}_glevel{g} key so it overrides the independently-seeded
    # concrete key -- even when it resolves to the bit's "insignificant" default
    # value.
    if attr_value is None:
        return

    vslevel_to_randomize = None
    glevel_to_randomize = None
    vs_kind, vs_level_arg = _vs_selector(attr)

    # Determine which VS-stage level the forcing applies to.
    if vs_kind == "nonleaf":
        if paging_mode_vs == RV.RiscvPagingModes.DISABLE:
            # VS-stage disabled: apply the forcing to the G-stage map directly.
            # Call _pt_attrs_helper first for per-level defaults, then force the
            # nonleaf level directly -- _pt_attrs_helper's insignificant-value
            # guard would silently drop a force value equal to the default (e.g. a=1).
            _pt_attrs_helper(
                attr=base_attr,
                attrs=attrs,
                paging_mode=paging_mode_g,
                final_pagesize=final_pagesize_gleaf,
                priv_mode=priv_mode,
            )
            map_max_levels_g = RV.RiscvPagingModes.max_levels(paging_mode_g)
            pt_leaf_level_g = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_gleaf)
            available_pt_levels_g = map_max_levels_g - pt_leaf_level_g
            if available_pt_levels_g == 1:
                possible_pt_levels_g = map_max_levels_g - 1
                rnd_pt_level_g = list(range(possible_pt_levels_g - 1, map_max_levels_g))[0]
            else:
                possible_pt_levels_g = map_max_levels_g - available_pt_levels_g
                rnd_pt_level_g = list(range(possible_pt_levels_g + 1, map_max_levels_g))[0]
            attrs[f"{base_attr}_level{rnd_pt_level_g}"] = attr_value
            return

        map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode_vs)
        levels_this_page = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs)
        available_pt_levels = map_max_levels - levels_this_page
        if available_pt_levels == 1:
            possible_pt_levels = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs)
            levels_range = list(range(possible_pt_levels, possible_pt_levels + 1))
        else:
            levels_range = list(range(levels_this_page + 1, map_max_levels))
        vslevel_to_randomize = levels_range[0]

    elif vs_kind == "leaf":
        if paging_mode_vs == RV.RiscvPagingModes.DISABLE:
            # VS-stage disabled: force on the g-stage leaf level directly.
            g_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_gleaf)
            attrs[f"{base_attr}_level{g_leaf_level}"] = attr_value
            return
        vslevel_to_randomize = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs)

    elif vs_kind == "level":
        if paging_mode_vs == RV.RiscvPagingModes.DISABLE:
            # VS-stage disabled: force on the g-stage leaf level directly (same as leaf branch).
            g_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_gleaf)
            attrs[f"{base_attr}_level{g_leaf_level}"] = attr_value
            return
        vslevel_to_randomize = vs_level_arg

    # vs_is_nonleaf drives the G-stage pagesize SELECTION below.
    # Use vs_kind directly rather than comparing levels: when available_pt_levels==1
    # the nonleaf fallback sets vslevel_to_randomize==vs_leaf_level, which would give
    # the wrong answer if we checked (vslevel_to_randomize != vs_leaf_level).
    vs_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs)
    vs_is_nonleaf = (vs_kind == "nonleaf") or (vs_kind == "level" and vslevel_to_randomize != vs_leaf_level)

    g_kind, g_level_arg = _g_selector(attr)

    # Determine which g-stage level the forcing applies to.
    if g_kind == "nonleaf":
        map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode_g)
        gnonleaf_pagesize = final_pagesize_gnonleaf if vs_is_nonleaf else final_pagesize_gleaf
        levels_this_page = RV.RiscvPageSizes.pt_leaf_level(gnonleaf_pagesize)
        available_pt_levels = map_max_levels - levels_this_page
        if available_pt_levels == 1:
            possible_pt_levels = map_max_levels - 1
            levels_range = list(range(possible_pt_levels - 1, possible_pt_levels))
        else:
            levels_range = list(range(levels_this_page + 1, map_max_levels))
        glevel_to_randomize = levels_range[0]

    elif g_kind == "leaf":
        pagesize = final_pagesize_gnonleaf if vs_is_nonleaf else final_pagesize_gleaf
        glevel_to_randomize = RV.RiscvPageSizes.pt_leaf_level(pagesize)

    elif g_kind == "level":
        glevel_to_randomize = g_level_arg

    # Construct the concrete level/glevel attribute consumed by the PT builder.
    attrs[f"{base_attr}_level{vslevel_to_randomize}_glevel{glevel_to_randomize}"] = attr_value


def apply_gstage_leaf_nonleaf_attrs(
    attrs: Dict[str, Any],
    config: PagingParams,
    paging_mode: RV.RiscvPagingModes,
    paging_g_mode: RV.RiscvPagingModes,
    final_pagesize_vs: RV.RiscvPageSizes,
    gstage_vs_leaf_pagesize: RV.RiscvPageSizes,
    gstage_vs_nonleaf_pagesize: RV.RiscvPageSizes,
) -> None:
    """Translate all g-stage leaf/non-leaf shorthand attrs in ``attrs`` in place.

    Mirrors the g-stage block of generator.py: randomize_pagetable_attributes. Materializes
    each forcing shorthand into its concrete ``{base}_level{vs}_glevel{g}`` key -- the in-place
    side effect is the whole point; nothing is returned.

    The ``gstage_vs_*_pagesize`` arguments are geometry supplied by the caller
    on the destination ``Page`` and non-leaf ``PTGPage`` declarations. Here
    they only select the level on which each shorthand force applies. Per-node
    ``pagesize`` is the authority on g-stage geometry, and the builder's
    attribute-based coloring isolates incompatible siblings.
    """
    # Most pages carry none of the ~240 shorthand forms. Walk only names that
    # are present to keep the common no-op path inexpensive.
    present = GSTAGE_FORCING_ATTRS_SET.intersection(attrs)
    if not present:
        return
    for attr in GSTAGE_FORCING_ATTRS:
        if attr not in present or attrs.get(attr) is None:
            continue
        randomize_gstage_pt_attrs(
            attr=attr,
            attrs=attrs,
            paging_mode_g=paging_g_mode,
            paging_mode_vs=paging_mode,
            priv_mode=config.priv_mode,
            final_pagesize_vs=final_pagesize_vs,
            final_pagesize_gleaf=gstage_vs_leaf_pagesize,
            final_pagesize_gnonleaf=gstage_vs_nonleaf_pagesize,
        )


# ----------------------------------------------------------------------------
# String-key -> pt_nodes level converters
#
# The expanders above resolve the user vocabulary into concrete string keys
# ({base}_level{n} single-stage, {base}_level{vs}_glevel{g} two-stage). Consumers
# using Mapping.pt_nodes need those regrouped as {level: {base: val}}.
# Priority (2-token over 1-token, insignificant-value materialization) is already
# baked into the resolved keys, so these are pure regroupings -- no re-ranking.
# ----------------------------------------------------------------------------

_SINGLE_LEVEL_RE = re.compile(r"^([a-z]+)_level(\d+)$")  # w_level0, a_level2 (NOT ..._glevel*)
_VS_GLEVEL_RE = re.compile(r"^([a-z]+)_level(\d+)_glevel(\d+)$")  # a_level0_glevel1


def attrs_to_pt_node_levels(attrs: Dict[str, Any]) -> Dict[int, Dict[str, int]]:
    """Group source-stage ``{base}_level{n}`` keys into ``{level: {base: value}}``.

    The feed for a source mapping's ``pt_nodes[level].attrs``. Two-stage
    ``{base}_level{vs}_glevel{g}`` keys are ignored here (they are the g-stage
    frame's own PTEs -- see :func:`gstage_frame_pt_node_levels`).
    """
    out: Dict[int, Dict[str, int]] = {}
    for key, val in attrs.items():
        m = _SINGLE_LEVEL_RE.match(key)
        if m:
            out.setdefault(int(m.group(2)), {})[m.group(1)] = val
    return out


# Plain base PTE bits that fold onto the leaf page-table node -- the user's leaf-PTE
# intent, folded onto the mapping's leaf level.
#
# Two consumer vocabularies meet here and they disagree on precedence, so the rule below
# is the one that suits the vocabulary that cannot express the other:
#
# - RiescueD always emits BOTH a bare base and a ``{base}_level{n}`` per page
#   (``ParsedPageMapping`` hard-defaults the whole matrix), so the per-level key is the
#   only way a test can say anything specific, and it must beat the always-present base.
#   That is the rule implemented here: a base bit fills the leaf only when no explicit
#   ``{base}_level{leaf}`` force already occupies it.
# - The ``gen_pages`` JSON vocabulary is the reverse: a bare ``a`` is the leaf PTE's
#   value and ``{base}_level{n}`` exists to reach the other levels, so the bare bit takes
#   precedence at the leaf. That is not expressed by changing this rule -- the JSON frontend rewrites each
#   bare bit into the concrete leaf key before anything downstream sees it
#   (``json_frontend._apply_leaf_base_precedence``), so both vocabularies arrive here already
#   unambiguous and this function stays single-meaning.
#
LEAF_FOLD_BASES = PTE_BASES


def pt_node_levels_with_leaf(attrs: Dict[str, Any], leaf_level: int) -> Dict[int, Dict[str, int]]:
    """``attrs_to_pt_node_levels`` plus the plain base bits folded onto the leaf.

    The full per-level PTE feed for a source mapping's ``pt_nodes``: every
    ``{base}_level{n}`` key grouped by level, with the plain base bits
    ({v,r,w,x,u,a,d,g,n,pbmt}) added at ``leaf_level`` when that level does not
    already carry an explicit force for the base.
    """
    levels = attrs_to_pt_node_levels(attrs)
    leaf = levels.setdefault(leaf_level, {})
    for base in LEAF_FOLD_BASES:
        val = attrs.get(base)
        if val is not None and base not in leaf:
            leaf[base] = val
    return levels


def gstage_frame_pt_node_levels(attrs: Dict[str, Any], vs_level: int) -> Dict[int, Dict[str, int]]:
    """Group one VS-frame's ``{base}_level{vs}_glevel{g}`` keys into ``{g_level: {base: value}}``.

    The per-g-level PTE bits for the G-space frame backing the VS-stage node at
    ``vs_level`` (that frame's own GPA -> HPA g-stage tree). Attaches to the frame
    Page's own ``Mapping.pt_nodes``.
    """
    out: Dict[int, Dict[str, int]] = {}
    for key, val in attrs.items():
        m = _VS_GLEVEL_RE.match(key)
        if m and int(m.group(2)) == vs_level:
            out.setdefault(int(m.group(3)), {})[m.group(1)] = val
    return out


def gstage_leaf_pt_node_levels(
    vs_attrs: Dict[str, Any],
    vs_paging_mode: RV.RiscvPagingModes,
    gstage_mode: RV.RiscvPagingModes,
    vs_pagesize: RV.RiscvPageSizes,
    gstage_vs_leaf_size: Optional[RV.RiscvPageSizes],
    gstage_vs_nonleaf_size: Optional[RV.RiscvPageSizes],
    secure: bool = False,
) -> Dict[int, Dict[str, int]]:
    """The data page's GPA -> HPA g-stage leaf frame as ``{g_level: {base: value}}``.

    Reuses the F4-stable :func:`gstage_leaf_attrs_for` (the ``{base}_level{g}``
    leaf dict) and regroups it. Any bare base bit (e.g. ``x``, the identity leaf's
    always-executable force) folds onto the g-stage leaf level, but only where that level
    carries no explicit ``{base}_level{g}`` force -- same precedence as
    :func:`pt_node_levels_with_leaf`. The bare bit is a default, so letting it overwrite
    would drop a consumer's ``{base}_leaf_gleaf`` force without notice (``x_leaf_gleaf=0`` on a
    page whose g-stage leaf must fault a fetch). ``secure`` is a mapping flag, not a PTE
    level bit, so it stays out of the level grouping.
    """
    leaf = gstage_leaf_attrs_for(vs_attrs, vs_paging_mode, gstage_mode, vs_pagesize, gstage_vs_leaf_size, gstage_vs_nonleaf_size, secure=secure)
    out = attrs_to_pt_node_levels(leaf)
    g_leaf_level = RV.RiscvPageSizes.pt_leaf_level(gstage_vs_leaf_size or RV.RiscvPageSizes.S4KB)
    for base in LEVEL_TYPES:
        if base in leaf:
            out.setdefault(g_leaf_level, {}).setdefault(base, leaf[base])
    return out


# ----------------------------------------------------------------------------
# pt_nodes assembly helpers
#
# The level groupings above are still plain dicts; these wrap them as the
# ``{level_or_LEAF: PTNode}`` a Mapping's ``pt_nodes`` carries, and hang the
# synthesized g-stage identity (a PTGPage per VS node) off them. Shared by the
# builder's two-stage pattern, the JSON frontend, and RiescueD's request
# builder -- one implementation, not a copy per consumer.
# ----------------------------------------------------------------------------


def pt_nodes_from_levels(levels: Dict[int, Dict[str, int]], leaf_level: int) -> Dict[Any, PTNode]:
    """Wrap ``{level: {base: val}}`` as ``{level_or_LEAF: PTNode(attrs)}``.

    The leaf level folds onto the :data:`LEAF` sentinel so callers need not know a
    mapping's own leaf level; other levels stay explicit ints. Module-level because both
    the builder's two-stage pattern and the JSON frontend feed a mapping's ``pt_nodes``
    from the same regrouping -- one implementation, not a copy per consumer."""
    out: Dict[Any, PTNode] = {}
    for level, attrs in levels.items():
        key = LEAF if level == leaf_level else level
        out[key] = PTNode(attrs=dict(attrs))
    return out


def attach_gstage_ptgpages(
    pt_nodes: Dict[Any, PTNode],
    attrs: Dict[str, Any],
    vs_leaf_level: int,
    gstage_nonleaf_size: Optional[RV.RiscvPageSizes],
    max_levels: int = 0,
) -> None:
    """Attach a :class:`PTGPage` to every VS node whose synthesized g-stage identity this
    mapping declares, so per-node ``pagesize`` is the only authority on that identity's geometry.

    Two things are attached here. First, each VS level that carries g-stage identity forcing (a
    ``{base}_level{vs}_glevel{g}`` subset of ``attrs``, regrouped by
    :func:`gstage_frame_pt_node_levels`) gets a PTGPage holding those per-g-level PTE
    bits (explicit int g-level keys, the grammar's own numbering -- no LEAF folding). Second,
    when ``max_levels`` is given (a two-stage VS mapping), every remaining table level below
    the frontend-owned root gets an attribute-free PTGPage carrying just
    ``gstage_nonleaf_size``: the frames those nodes draw need that geometry whether or not any
    bit is forced in them, and an empty PTGPage is inert everywhere else (it signs identically
    to an absent node in the coloring, overlays no PTE bits, and pins no frame). Pass
    ``max_levels=0`` for a single-stage or bare-g-stage mapping, which has no g-stage
    identity and so must declare nothing.

    A PTGPage coexists with any VS PTE bits already on the node (``attrs`` are kept). It never
    displaces a pinned frame :class:`Page`: at every call site the caller either overlays its
    explicit ``pt_nodes`` afterwards or drops the PTGPage from pinned levels."""
    size = gstage_nonleaf_size or RV.RiscvPageSizes.S4KB
    preferred_size = size.preferred if isinstance(size, Choice) else size
    preferred_g_leaf = RV.RiscvPageSizes.pt_leaf_level(preferred_size)
    # Iterate every architectural VS level (5 covers sv32..sv57 and the VS-DISABLE bare-VS
    # case, where the force's vs index is nominal); an absent level yields an empty matrix.
    forced = set()
    for vs in range(5):
        matrix = gstage_frame_pt_node_levels(attrs, vs)
        if not matrix:
            continue
        # A bare G-stage source has no VS table frame at this level; its
        # two-index force applies directly to the one-stage leaf.
        if max_levels == 0 and vs == vs_leaf_level:
            leaf_bits = matrix.get(preferred_g_leaf)
            if leaf_bits:
                existing = pt_nodes.get(LEAF) or pt_nodes.get(vs_leaf_level)
                attrs_at_leaf = dict(existing.attrs) if existing is not None else {}
                attrs_at_leaf.update(leaf_bits)
                if "u" in leaf_bits:
                    # Preserve that U came from the explicit two-index
                    # g-stage force, not from the VS single-index U field.
                    attrs_at_leaf["_gstage_leaf_u"] = leaf_bits["u"]
                pt_nodes[LEAF] = PTNode(
                    page=(existing.page if existing is not None else None),
                    attrs=attrs_at_leaf,
                    choice=(existing.choice if existing is not None else None),
                )
            continue
        # Consumer ``*_levelN_glevelM`` names the pointer level N
        # whose target frame is being translated. Core PTNode keys name
        # the level stored in that frame, so this frontend-only adapter
        # converts N to N-1. No core API level is shifted.
        if vs <= vs_leaf_level:
            continue
        key = vs - 1
        existing = pt_nodes.get(key)
        vs_attrs = dict(existing.attrs) if existing is not None else {}
        g_nodes = {(LEAF if g_level == preferred_g_leaf and any(isinstance(value, Choice) for value in bits.values()) else g_level): PTNode(attrs=dict(bits)) for g_level, bits in matrix.items()}
        pt_nodes[key] = PTNode(attrs=vs_attrs, page=PTGPage(pt_nodes=g_nodes, pagesize=size, identity=True))
        forced.add(key)

    # Every table below the root needs a G-stage translation.  PTNode keys name
    # the level whose PTEs live in that frame, so a 4 KiB Sv39 walk declares
    # generated frames at levels 0 and 1; the level-2 root is frontend-owned.
    for vs in range(vs_leaf_level, max_levels - 1):
        if vs in forced:
            continue
        existing = pt_nodes.get(vs)
        if existing is not None and existing.page is not None:
            continue  # the caller already placed a frame / PTGPage here; it defines the geometry
        vs_attrs = dict(existing.attrs) if existing is not None else {}
        pt_nodes[vs] = PTNode(attrs=vs_attrs, page=PTGPage(pagesize=size, identity=True))
