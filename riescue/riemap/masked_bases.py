# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Masked bases: counting and picking the addresses a mask constraint allows.

A geometric address constraint is a pair of masks: a base ``b`` is legal iff
``b == (b & and_mask) | or_mask``. Bit by bit that says ``b`` is 1 wherever
``or_mask`` is, 0 wherever neither mask covers the position, and free exactly where
``free = and_mask & ~or_mask``. So the legal bases are

    b(k) = or_mask | deposit(k, free)      k in [0, 2**popcount(free))

where :func:`deposit` writes the bits of ``k`` into the set positions of ``free``.
That map is a strictly increasing bijection, so counting the legal bases in an
interval and fetching the n-th of them cost ``O(address bits)`` and never
enumerate candidates. Placement becomes "count the legal bases in each free
window, draw one ordinal, un-rank it" -- as opposed to walking every aligned
address, which costs one step per slot (262144 of them for a 1 GiB window at
4 KiB alignment) and repays that cost on every retry.

Ordering the legal set is not the same as ranking it, and naive bit extraction
does not rank it: for ``and_mask=0b01, or_mask=0`` the largest legal base ``<= 2``
is 1, while depositing the extracted bits of 2 gives 0. :meth:`MaskedBases.count_le`
therefore walks the address from the top, crediting whole subtrees as it goes.

A mask pair on its own never rules everything out -- ``or_mask`` is always legal.
Emptiness comes only from the extra constraints a caller folds in (architectural
alignment, address width), which is why :meth:`MaskedBases.for_span` answers with
``None`` instead of raising: "nothing can satisfy this" is an ordinary result that
callers recover from by trying another window, cluster, or region. Malformed
inputs (a negative mask, an alignment that is not a power of two) do raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Iterable, Iterator, List, Optional, Tuple

from riescue.lib.rand import RandNum


_FULL_MASK = (1 << 64) - 1


def _popcount(value: int) -> int:
    return bin(value).count("1")


def deposit(value: int, mask: int) -> int:
    """Write the low bits of ``value`` into the set positions of ``mask``, lowest first.

    Bits of ``value`` above ``popcount(mask)`` have nowhere to go and are dropped;
    :meth:`MaskedBases.nth` rejects out-of-range indices before they get here.
    """
    out = 0
    remaining = mask
    while remaining and value:
        low = remaining & -remaining
        if value & 1:
            out |= low
        value >>= 1
        remaining ^= low
    return out


@dataclass(frozen=True)
class MaskedBases:
    """The ascending set of bases ``b`` satisfying ``b == (b & and_mask) | or_mask``."""

    and_mask: int
    or_mask: int

    def __post_init__(self) -> None:
        # Callers often write ``~(pagesize - 1)``, which is negative in Python. Fold both
        # masks into the 64-bit address domain so counting walks a finite bit width.
        object.__setattr__(self, "and_mask", self.and_mask & _FULL_MASK)
        object.__setattr__(self, "or_mask", self.or_mask & _FULL_MASK)

    @classmethod
    def for_span(cls, and_mask: int, or_mask: int, alignment: int = 1, bits: Optional[int] = None) -> Optional["MaskedBases"]:
        """The legal bases for a span, with architectural alignment and address width folded in.

        Alignment becomes a mask restriction, so callers never need a separate
        placement step. ``None`` means the folded constraints admit no address at
        all: ``or_mask`` forces a bit below ``alignment``, or forces a bit at or
        above ``bits``.
        """
        if alignment < 1 or alignment & (alignment - 1):
            raise ValueError(f"alignment must be a positive power of two, got {alignment}")
        if bits is not None and bits <= 0:
            raise ValueError(f"bits must be positive, got {bits}")
        if or_mask & (alignment - 1):
            return None
        folded = and_mask & ~(alignment - 1)
        if bits is not None:
            width_mask = (1 << bits) - 1
            if or_mask > width_mask:
                return None
            folded &= width_mask
        return cls(and_mask=folded, or_mask=or_mask)

    @property
    def free(self) -> int:
        """The positions a legal base may vary in."""
        return self.and_mask & ~self.or_mask

    @property
    def total(self) -> int:
        """How many legal bases exist in total (may exceed a machine word)."""
        return 1 << _popcount(self.free)

    def contains(self, base: int) -> bool:
        return base >= 0 and base == (base & self.and_mask) | self.or_mask

    def count_le(self, x: int) -> int:
        """How many legal bases are ``<= x``.

        Walks positions from the top. At a free position where ``x`` has a 1,
        choosing 0 puts the base below ``x`` whatever the lower bits do, so every
        completion counts and the walk continues along ``x``. At a forced position
        that disagrees with ``x`` the walk ends: below ``x`` credits all remaining
        completions, above it credits none.
        """
        if x < 0:
            return 0
        free, or_mask = self.free, self.or_mask
        width = max(x.bit_length(), or_mask.bit_length(), free.bit_length())
        if width == 0:
            return 1  # the only legal base is 0, and x >= 0
        count = 0
        free_below = _popcount(free & ((1 << (width - 1)) - 1))
        for pos in range(width - 1, -1, -1):
            x_bit = (x >> pos) & 1
            if (free >> pos) & 1:
                if x_bit:
                    count += 1 << free_below
            else:
                forced = (or_mask >> pos) & 1
                if forced < x_bit:
                    return count + (1 << free_below)
                if forced > x_bit:
                    return count
            if pos:
                free_below -= (free >> (pos - 1)) & 1
        return count + 1  # the walk stayed on x the whole way, so x itself is legal

    def count_in(self, lo: int, hi: int) -> int:
        """How many legal bases lie in the inclusive interval ``[lo, hi]``."""
        if hi < lo:
            return 0
        return self.count_le(hi) - self.count_le(lo - 1)

    def nth(self, index: int) -> int:
        """The ``index``-th smallest legal base."""
        if not 0 <= index < self.total:
            raise IndexError(f"index {index} out of range for {self.total} masked bases")
        return self.or_mask | deposit(index, self.free)

    def nth_in(self, lo: int, hi: int, index: int) -> int:
        """The ``index``-th smallest legal base in the inclusive interval ``[lo, hi]``."""
        first = self.count_le(lo - 1)
        if not 0 <= index < self.count_le(hi) - first:
            raise IndexError(f"index {index} out of range for {self.count_in(lo, hi)} masked bases in [0x{lo:x}, 0x{hi:x}]")
        return self.nth(first + index)

    def ordinal(self, base: int) -> int:
        """Where a legal ``base`` sits in the ascending set."""
        if not self.contains(base):
            raise ValueError(f"0x{base:x} is not a legal base for and_mask=0x{self.and_mask:x}, or_mask=0x{self.or_mask:x}")
        return self.count_le(base) - 1


def free_windows(start: int, end: int, occupied: Iterable[Tuple[int, int]]) -> Iterator[Tuple[int, int]]:
    """The half-open gaps in ``[start, end)`` that ``occupied`` leaves behind.

    ``occupied`` is half-open spans sorted by start; they may overlap or extend past
    either end of the window.
    """
    cursor = start
    for span_start, span_end in occupied:
        if span_start >= end:
            break
        if span_end <= cursor:
            continue
        if span_start > cursor:
            yield cursor, span_start
        cursor = span_end
        if cursor >= end:
            return
    if cursor < end:
        yield cursor, end


def choose_in_windows(
    domain: MaskedBases,
    windows: Iterable[Tuple[int, int]],
    size: int,
    rng: RandNum,
    excluded: Collection[int] = (),
) -> Optional[int]:
    """Pick uniformly among the legal bases whose ``size``-byte span fits a free window.

    ``windows`` are half-open free spans, so a window ``[start, end)`` admits the
    inclusive base interval ``[start, end - size]``. ``excluded`` bases are treated
    as if they were not legal -- that is a base-by-base exclusion, not an occupancy
    span, so it never rules out a neighbour whose span merely covers one of them.
    Returns ``None`` when no base fits anywhere.

    Uniformity comes from counting first and drawing one ordinal, which needs an
    exact integer draw: ``RandNum.random_in_range`` scales a float and would bias
    or unreach ordinals once the count passes a mantissa.
    """
    if size < 1:
        raise ValueError(f"size must be positive, got {size}")
    candidates: List[Tuple[int, int, List[int]]] = []
    total = 0
    for window_start, window_end in windows:
        hi = window_end - size
        if hi < window_start:
            continue
        first = domain.count_le(window_start - 1)
        count = domain.count_le(hi) - first
        if count <= 0:
            continue
        # A set: the same base named twice is still one base, and subtracting it twice
        # would undercount the window and leave a legal base unreachable.
        skipped = sorted({domain.ordinal(base) - first for base in excluded if window_start <= base <= hi and domain.contains(base)})
        count -= len(skipped)
        if count <= 0:
            continue
        candidates.append((first, count, skipped))
        total += count
    if total == 0:
        return None
    rank = rng.randrange(0, total)
    for first, count, skipped in candidates:
        if rank < count:
            for skip in skipped:
                if skip > rank:
                    break
                rank += 1
            return domain.nth(first + rank)
        rank -= count
    raise AssertionError(f"rank {rank} escaped {total} counted masked bases")  # pragma: no cover
