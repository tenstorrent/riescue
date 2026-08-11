# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Paging configuration seam for the page table generator.

:class:`PagingParams` holds allocation policy the walker and resolvers read:
physical address space width, privilege, and ``secure_pt_probability`` for
auto-allocated page-table frames. It does not choose PTE bits or paging modes --
those come from ``Mapping.pt_nodes`` and each ``PageMap``. There is no
``secure_mode`` (pass probability 0 when not in secure mode) and no ``svadu``
(declare the A/D bits wanted). Resolvers take paging modes as explicit arguments.
The only value shared across spaces is ``physical_addr_bits``; the builder builds
one :class:`PagingParams` per space.
"""

from dataclasses import dataclass

import riescue.lib.enums as RV


@dataclass
class PagingParams:
    """The per-space paging policy the page table generator reads.

    Everything mode-related lives on the mapping spaces / :class:`PageMap`, not here.
    """

    physical_addr_bits: int = 56
    priv_mode: RV.RiscvPrivileges = RV.RiscvPrivileges.SUPER
    secure_pt_probability: int = 0

    def __post_init__(self) -> None:
        # ``bool`` is an ``int`` subclass, so it is excluded explicitly: ``True`` would
        # otherwise read as a 1-bit physical address space / a 1% probability.
        width = self.physical_addr_bits
        if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 64:
            raise ValueError(f"PagingParams.physical_addr_bits must be an integer bit width in [1, 64], got {width!r}")
        probability = self.secure_pt_probability
        if isinstance(probability, bool) or not isinstance(probability, int) or not 0 <= probability <= 100:
            raise ValueError(f"PagingParams.secure_pt_probability must be an integer percentage in [0, 100], got {probability!r}")
