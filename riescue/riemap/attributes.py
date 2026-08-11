# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Declarative schema for page-table entry attributes.

The walker attribute model and page defaults consume the same generated level
matrices. Keeping field policy here avoids hundreds of hand-maintained keys.
"""

from typing import Optional

# Every software-controlled field in an Sv39/Sv48/Sv57 PTE. PPN is address
# geometry, not an attribute; ``secure`` and ``modify_pt`` are generator policy.
# Keep this as the one vocabulary from which parsers, resolvers, builders and the
# walker derive their accepted PTE names.
PTE_BASES = ("v", "r", "w", "x", "u", "g", "a", "d", "rsw", "reserved", "pbmt", "n")
PTE_FIELD_WIDTHS = {"rsw": 2, "reserved": 7, "pbmt": 2}
PTE_LEVELS = range(5)
_VS_G_LEVELS = tuple((vs, g) for vs in PTE_LEVELS for g in PTE_LEVELS)


def _level_values(base: str, leaf_value: int, pointer_value: int = 0) -> dict[str, int]:
    """Return ``base_levelN`` defaults, treating level zero as the default leaf."""
    return {f"{base}_level{level}": leaf_value if level == 0 else pointer_value for level in PTE_LEVELS}


def _gstage_values(base: str, leaf_value: int, pointer_value: int = 0) -> dict[str, int]:
    """Return the VS-level by G-level default matrix for ``base``."""
    return {f"{base}_level{vs}_glevel{g}": leaf_value if g == 0 else pointer_value for vs, g in _VS_G_LEVELS}


def _zero_levels(base: str) -> dict[str, int]:
    return {f"{base}_level{level}": 0 for level in PTE_LEVELS}


def _zero_gstage(base: str) -> dict[str, int]:
    return {f"{base}_level{vs}_glevel{g}": 0 for vs in PTE_LEVELS for g in PTE_LEVELS}


_ROLE_SPECIFIC_KEYS = {"v", "a_level0", "d_level0", "n", "u", "u_level0"}

COMMON_PTE_ATTRS: dict[str, Optional[int]] = {
    key: value
    for key, value in {
        "v": None,
        **_level_values("v", 1, 1),
        **_gstage_values("v", 1, 1),
        "a": None,
        **_zero_levels("a"),
        **_gstage_values("a", 1),
        "d": None,
        **_zero_levels("d"),
        **_gstage_values("d", 1),
        "r": 1,
        **_level_values("r", 1),
        **_gstage_values("r", 1),
        "w": 1,
        **_level_values("w", 1),
        **_gstage_values("w", 1),
        "x": 1,
        **_level_values("x", 1),
        **_gstage_values("x", 1),
        "u": 0,
        **_zero_levels("u"),
        **_gstage_values("u", 1),
        "g": 0,
        **_zero_levels("g"),
        **_zero_gstage("g"),
        "rsw": 0,
        **_zero_levels("rsw"),
        **_zero_gstage("rsw"),
        "reserved": 0,
        **_zero_levels("reserved"),
        **_zero_gstage("reserved"),
        "pbmt": 0,
        **_zero_levels("pbmt"),
        **_zero_gstage("pbmt"),
        "n": 0,
        **_zero_levels("n"),
        **{f"n_level{vs}_glevel{g}": None for vs, g in _VS_G_LEVELS},
        "secure": 0,
    }.items()
    if key not in _ROLE_SPECIFIC_KEYS
}

# The generic PTE model starts with invalid/accessed/dirty level-zero defaults.
_PTATTRS_OVERRIDES: dict[str, Optional[int]] = {
    "v": None,
    "a_level0": 0,
    "d_level0": 0,
    "n": 0,
    "u": 0,
    "u_level0": 0,
}

# A page declaration starts with a valid, accessed, dirty leaf. U is supplied
# separately because it depends on the page's privilege context.
_PAGE_STATIC_OVERRIDES: dict[str, Optional[int]] = {
    "v": 1,
    "a_level0": 1,
    "d_level0": 1,
    "n": None,
}


def pt_attrs_schema() -> dict[str, Optional[int]]:
    """Return an independent valid-attribute schema for ``PTAttrs``."""
    return {**COMMON_PTE_ATTRS, **_PTATTRS_OVERRIDES}


def page_default_attrs(ubit: int) -> dict[str, Optional[int]]:
    """Return an independent page-default mapping for ``ubit``."""
    template = _PAGE_DEFAULT_TEMPLATES.get(ubit)
    if template is None:
        template = {
            **COMMON_PTE_ATTRS,
            **_PAGE_STATIC_OVERRIDES,
            "u": ubit,
            "u_level0": ubit,
        }
        _PAGE_DEFAULT_TEMPLATES[ubit] = template
    return dict(template)


_PAGE_DEFAULT_TEMPLATES: dict[int, dict[str, Optional[int]]] = {}
