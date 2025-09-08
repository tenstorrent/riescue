# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Strategies for translating data structures into ``FeatMgr`` object. Called by ``FeatMgrBuilder``.

"""

from .test_config import TestConfigAdapter
from .cpu_config_adapater import CpuConfigAdapter
from .cli_adapter import CliAdapter

__all__ = ["TestConfigAdapter", "CpuConfigAdapter", "CliAdapter"]
