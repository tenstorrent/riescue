# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Controller-neutral allocation of guest-interrupt indices."""

from typing import Dict

from coretp.rv_enums import InterruptCause


class GuestInterruptAllocator:
    """Allocate indices uniquely within a scenario and reuse them across scenarios."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._next_index = 1
        self._indices: Dict[InterruptCause, int] = {}

    def index(self, cause: InterruptCause) -> int:
        if cause not in (InterruptCause.VSEI, InterruptCause.SGEI):
            raise ValueError(f"{cause.name} is not a guest external interrupt")
        if cause not in self._indices:
            self._indices[cause] = self._next_index
            self._next_index += 1
        return self._indices[cause]
