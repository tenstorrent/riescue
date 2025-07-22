# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging

from .riescued import RiescueD

logging.getLogger("riescue").addHandler(logging.NullHandler())

__all__ = ["RiescueD"]
