from enum import Enum, IntEnum, auto

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

lmul_map = {"m1": 1, "m2": 2, "m4": 4, "m8": 8, "mf2": 0.5, "mf4": 0.25, "mf8": 0.125, 1: "m1", 2: "m2", 4: "m4", 8: "m8", 0.5: "mf2", 0.25: "mf4", 0.125: "mf8"}


class format_e(IntEnum):
    R_FORMAT = 0
    R4_FORMAT = auto()
    I_FORMAT = auto()
    S_FORMAT = auto()
    B_FORMAT = auto()
    U_FORMAT = auto()
    J_FORMAT = auto()
    F_FORMAT = auto()
    E_FORMAT = auto()


class extension_e(IntEnum):
    I_EXTENSION = 0
    M_EXTENSION = auto()
    A_EXTENSION = auto()
    F_EXTENSION = auto()
    D_EXTENSION = auto()
    B_EXTENSION = auto()
    V_EXTENSION = auto()
    C_EXTENSION = auto()


class RoundingMode(IntEnum):
    RNE = 0
    RTZ = 1
    RDN = 2
    RUP = 3
    RMM = 4
    RSVD1 = 5
    RSVD2 = 6
    DYN = 7
