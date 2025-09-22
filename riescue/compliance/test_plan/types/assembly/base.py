# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod


class AssemblyBase(ABC):
    """
    Base class for all assembly elements. Used to construct and assembly files.

    """

    @abstractmethod
    def emit(self) -> str:
        """
        Emit the assembly element as a string.
        """
        pass
