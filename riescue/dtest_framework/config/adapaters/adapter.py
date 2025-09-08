# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, TypeVar, Generic

if TYPE_CHECKING:
    from ..builder import FeatMgrBuilder

log = logging.getLogger(__name__)

T = TypeVar("T")


class Adapter(ABC, Generic[T]):
    """
    Base for translating an input object to a ``FeatMgrBuilder``.
    This lets logic for each configuration option to be
    encapsulated in a single class, and allows for easy testing of each configuration option, along with easy override of configuration
    options

    .. note:

        randomization should not occur in this class

    :param src: input object to translate
    :param builder: builder to mutate
    :returns: the same builder, mutated in-place
    """

    @abstractmethod
    def apply(self, builder: FeatMgrBuilder, src: T) -> FeatMgrBuilder: ...
