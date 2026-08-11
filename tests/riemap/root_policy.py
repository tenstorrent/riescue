# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Explicit frontend root declarations for two-stage RieMap tests."""

import riescue.lib.enums as RV
from riescue.riemap.request import (
    LEAF,
    AddrSpec,
    Mapping,
    Page,
    PTNode,
    SameAs,
    Space,
    Stage,
)


def declare_vs_root_identities(builder, paging_mode, *g_spaces, **space_kwargs):
    """Declare a VS space's root frame on the space itself and return the space.

    Creates the shared root HPA page (physical, DRAM) plus, per G space, a root GPA
    page pinned SameAs that HPA and its ordinary GPA -> HPA identity
    mapping, then mints the VS :class:`Space` with ``root_frame`` set to the first GPA
    page. Extra keyword arguments are forwarded to the VS space (e.g.
    ``secure_pt_probability``).
    """
    root_hpa = builder.add_page(
        Page(
            space=builder.phys,
            addr=AddrSpec(
                qualifiers={
                    RV.AddressQualifiers.ADDRESS_DRAM,
                }
            ),
        )
    )
    root_gpas = []
    for g_space in g_spaces:
        root_gpa = builder.add_page(
            Page(
                space=g_space,
                addr=AddrSpec(relation=SameAs(root_hpa)),
            )
        )
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
        root_gpas.append(root_gpa)
    return builder.add_space(Space(paging_mode=paging_mode, stage=Stage.VS, root_frame=root_gpas[0], **space_kwargs))


def declare_vs_root_identity(builder, paging_mode, g_space, **space_kwargs):
    """The single-G-space form of :func:`declare_vs_root_identities`."""
    return declare_vs_root_identities(builder, paging_mode, g_space, **space_kwargs)
