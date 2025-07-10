# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from abc import ABC, abstractmethod
from typing import Tuple, Iterator, Sequence

from intervaltree import IntervalTree, Interval

log = logging.getLogger(__name__)


AddressRange = Tuple[int, int]
"""
Address range type. Used for type hinting, can be swapped later for more complex types.

e.g. NamedTuple which would function as drop-in replacement for Tuple, but adds methods

class AddressRange(NamedTuple):
    start: int
    end: int
"""


def _addr_range_str(address_range: AddressRange) -> str:
    "Helper to print AddressRange objects"
    return f"(0x{address_range[0]:X}, 0x{address_range[1]:X})"


class AddressRangeSet(ABC):
    """
    Set of AddressRange objects.
    """

    @abstractmethod
    def __iter__(self) -> Iterator[AddressRange]:
        pass

    @abstractmethod
    def __len__(self) -> int:
        pass

    @abstractmethod
    def __str__(self) -> str:
        """Return a string representation of the set, must be formatted as a sequence of hex addresses. E.g. "(0x1000, 0x2000), (0x3000, 0x4000)" """
        pass

    @abstractmethod
    def add(self, interval: AddressRange) -> None:
        """Add an address range to the set."""
        pass

    @abstractmethod
    def remove(self, item: AddressRange) -> None:
        """
        Remove item from set, raise Keyerror to support legacy behavior
        :param item: AddressRange
        :return: None
        :raises KeyError: If item is not found in set
        """
        pass

    @abstractmethod
    def overlap(self, interval: AddressRange) -> Sequence[AddressRange]:
        """Return the overlap of the address range with the set."""
        pass


class _IntervalAddressRangeSet(AddressRangeSet):
    """
    Represents a set of address ranges. Abstraction to test IntervalTree() implementation.

    IntervalTree() holds a collection of Interval(begin, end, data=None) objects. These can be stand-ins for address ranges.
    "This library was designed to allow tagging text and time intervals, where the intervals include the lower bound but not the upper bound."
    https://github.com/ilanschnell/intervaltree

    AddressRangeSet uses closed-bound intervals, but IntervalTree() uses open-bound intervals. Need to add 1 to the end address to include the end address.
    Queries also used open-bound intervals so query arguments need to be incremented to make them closed-bound.
    """

    def __init__(self):
        self._container = IntervalTree()

    def __str__(self) -> str:
        """Return a string representation of the set."""
        intervals = ", ".join(f"(0x{interval.begin:X}, 0x{interval.end-1:X})" for interval in sorted(self._container))
        return f"AddressRangeSet([{intervals}])"

    def __iter__(self) -> Iterator[AddressRange]:
        """Return an iterator over the ranges. Sorted iterator for backward compatibility"""
        for interval in sorted(self._container):
            yield (interval.begin, interval.end - 1)

    def __len__(self) -> int:
        """Return the number of ranges in the set."""
        return len(self._container)

    def add(self, item: AddressRange):
        start, end = item
        self._container.add(Interval(start, end + 1))

    def remove(self, item: AddressRange):
        start, end = item
        if Interval(start, end + 1) not in self._container:
            raise KeyError(f"Interval {_addr_range_str(item)} not found in set")
        self._container.remove(Interval(start, end + 1))

    def overlap(self, other: AddressRange) -> Sequence[AddressRange]:
        # queries are just sets. need to sort to mirror banyan.SortedSet behavior
        start, end = other
        overlap_set = set()
        for interval in self._container.overlap(start, end + 1):
            overlap_set.add((interval.begin, interval.end - 1))
        overlap_set = sorted(overlap_set)
        return overlap_set


def address_range_set() -> AddressRangeSet:
    """
    Factory function to create a new AddressRangeSet.

    :return: AddressRangeSet
    """
    return _IntervalAddressRangeSet()
