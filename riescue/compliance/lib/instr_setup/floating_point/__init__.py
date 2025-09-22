# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# Floating point instruction setup and base methods


from .base import FpSetup, FloatComponent
from .arithmetic import FpRegRegSetup, FpRegRegRegSetup, FpSqrtSetup
from .classification import FpClassifySetup
from .compare import FpRegRegCompareSetup
from .conversion import FpConvertMove
from .load import FpLoadSetup
from .store import FloatStoreRegBasedSetup

__all__ = [
    "FpSetup",
    "FloatComponent",
    "FpRegRegSetup",
    "FpRegRegRegSetup",
    "FpSqrtSetup",
    "FpClassifySetup",
    "FpRegRegCompareSetup",
    "FpConvertMove",
    "FpLoadSetup",
    "FloatStoreRegBasedSetup",
]
