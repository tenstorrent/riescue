import enum

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


class RegisterFormat(enum.Enum):
    pass


class eMisa(RegisterFormat):
    WARL_BASE = (31, 30)
    WIRI = (29, 26)
    WARL_EXT = (25, 0)
