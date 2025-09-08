# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
from typing import Generic, Iterator, TypeVar, Optional, Type
from enum import Enum

from riescue.lib.rand import RandNum

T = TypeVar("T")
E = TypeVar("E", bound=Enum)


class Candidate(Generic[T]):
    """
    Mutable pool of values that resolves to a single choice. Used to select possible list of values

    :param pool: Initial candidates

    :examples:

        >>> from random import Random
        >>> c = Candidate("a", "b", "c")
        >>> c.choose(RandNum(seed=0))  # picks one value, deterministic with seed
        'b'

        >>> c2 = Candidate(1, 2, 3)
        >>> c2.discard(2)        # remove a value before resolving
        >>> c2.choose(RandNum(seed=0))
        1
    """

    __slots__ = ("_pool", "_chosen")

    def __init__(self, *pool: T, allow_duplicates: bool = False):
        if allow_duplicates:
            self._pool: list[T] = list(pool)
        else:
            seen = set()
            self._pool: list[T] = [x for x in pool if not (x in seen or seen.add(x))]
        self._chosen: Optional[T] = None

    def __len__(self) -> int:
        return len(self._pool)

    def __eq__(self, other) -> bool:
        """
        Check equality - if other is not a Candidate, check if we contain only that value
        If other is a non-Candidate, check that both pools are identical
        """
        if isinstance(other, Candidate):
            return self._pool == other._pool
        elif isinstance(other, list):
            return set(self._pool) == set(other)
        else:
            return len(self._pool) == 1 and self._pool[0] == other

    def discard(self, v: T) -> None:
        self._pool.remove(v)

    def __iter__(self) -> Iterator[T]:
        return iter(self._pool)

    def choose(self, rng: RandNum) -> T:
        if self._chosen is None:
            if len(self._pool) == 0:
                raise ValueError("No candidates to choose from")
            self._chosen = rng.choice(tuple(self._pool))
        return self._chosen

    def copy(self) -> Candidate[T]:
        "returns a new copy of the candidate"
        return Candidate(*self._pool)

    def __repr__(self) -> str:
        return f"Candidate({', '.join(str(x) for x in self._pool)})"

    def only_contains(self, v: T) -> bool:
        "returns True if the candidate only has one value, False otherwise"
        return len(self._pool) == 1 and self._pool[0] == v

    @classmethod
    def from_enum(cls: type[Candidate[E]], enum: Type[E]) -> Candidate[E]:
        """
        Create a candidate from an enum. Equivalend to ``Candidate(enum for enum in RV.FooEnum)``

        :param enum: The enum to create a candidate from.
        :return: A candidate from the enum.
        """
        return cls(*tuple(enum))

    @classmethod
    def with_weights(cls: type[Candidate[T]], choices: list[tuple[T, float]]) -> Candidate[T]:
        """
        Create a candidate with weighted probabilities.

        :param choices: List of (value, weight) tuples where weight determines probability
        :return: A candidate that respects the given weights
        """
        weighted_pool = []
        weight_sum = sum(weight for _, weight in choices)
        if weight_sum != 1:
            weight_math = " + ".join(str(weight) for _, weight in choices)
            raise ValueError(f"Total weight sum must equal 1.0, got {weight_math} = {weight_sum} ")

        for value, weight in choices:
            if weight <= 0 or weight > 1:
                raise ValueError("Weights must be between 0 and 1")
            # could find least common denominator of weights and use that to scale to integers, but this works
            count = max(1, int(weight * 100))
            weighted_pool.extend([value] * count)

        return cls(*weighted_pool, allow_duplicates=True)
