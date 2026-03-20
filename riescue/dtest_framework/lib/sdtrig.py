# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Sdtrig (Trigger Module) helper for Riescue-D.

Encodes tdata1 values for mcontrol6 trigger type per RISC-V Debug Spec.
"""

from typing import Optional

# mcontrol6 type = 6
TDATA1_TYPE_MCONTROL6 = 6

# Size encoding: 0=1B, 1=2B, 2=4B, 3=8B
SIZE_1B = 0
SIZE_2B = 1
SIZE_4B = 2
SIZE_8B = 3


def size_to_encoding(size: int) -> int:
    """Map access size (1,2,4,8) to tdata1 size field."""
    if size == 1:
        return SIZE_1B
    if size == 2:
        return SIZE_2B
    if size == 4:
        return SIZE_4B
    if size == 8:
        return SIZE_8B
    return SIZE_4B  # default


def build_tdata1_mcontrol6(
    trigger_type: str,
    action: str = "breakpoint",
    size: int = 4,
    chain: int = 0,
) -> int:
    """
    Build tdata1 value for mcontrol6 trigger.

    :param trigger_type: execute, load, store, load_store
    :param action: breakpoint (0) or debug_mode (1)
    :param size: Access size 1, 2, 4, or 8 bytes
    :param chain: 0 or 1
    :returns: 64-bit tdata1 value
    """
    # type[63:60] = 6
    val = TDATA1_TYPE_MCONTROL6 << 60
    # dmode[59] = 0
    # match[10:7] = 0 (equal)
    # chain[11]
    val |= (chain & 1) << 11
    # action[15:12] = 0 for breakpoint
    if action == "debug_mode":
        val |= 1 << 12
    # size[18:16]
    val |= (size_to_encoding(size) & 7) << 16
    # m[6]=1, s[4]=1, u[3]=1 (enable in M, S, U)
    val |= (1 << 6) | (1 << 4) | (1 << 3)
    # load[0], store[1], execute[2]
    if trigger_type == "execute":
        val |= 1 << 2
    elif trigger_type == "load":
        val |= 1 << 0
    elif trigger_type == "store":
        val |= 1 << 1
    elif trigger_type == "load_store":
        val |= (1 << 0) | (1 << 1)
    else:
        raise ValueError(f"Unknown trigger_type: {trigger_type}")
    return val & 0xFFFFFFFFFFFFFFFF
