# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the masked-base primitive (riescue.riemap.masked_bases).

The primitive is pure integer arithmetic, so most of it is pinned by exhaustive
comparison against brute-force enumeration rather than by sampling: for every
mask pair and every interval at a small width, ``count_le`` / ``count_in`` /
``nth_in`` must agree with "list the legal bases and slice the list".

The point of the primitive is that it never enumerates, so the counterexamples
that break the obvious-but-wrong implementations are pinned explicitly: naive bit
extraction for ranking, and occupancy spans for base exclusion.
"""

import unittest

from riescue.lib.rand import RandNum
from riescue.riemap.masked_bases import MaskedBases, choose_in_windows, deposit, free_windows


def _brute_force(and_mask, or_mask, width):
    """Every legal base below ``2**width``, ascending, by definition."""
    return [b for b in range(1 << width) if b == (b & and_mask) | or_mask]


def _oracle_count_le(and_mask, or_mask, width, x):
    return len([b for b in _brute_force(and_mask, or_mask, width) if b <= x])


class TestDeposit(unittest.TestCase):
    def test_writes_bits_into_set_positions_lowest_first(self):
        self.assertEqual(deposit(0b11, 0b10100), 0b10100)
        self.assertEqual(deposit(0b01, 0b10100), 0b00100)
        self.assertEqual(deposit(0b10, 0b10100), 0b10000)

    def test_is_strictly_increasing_in_the_index(self):
        mask = 0b1011001
        values = [deposit(k, mask) for k in range(1 << 4)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), len(values))


class TestExhaustiveAgainstBruteForce(unittest.TestCase):
    """Every mask pair and every interval at width 5, against enumeration."""

    WIDTH = 5

    def test_count_le_matches_enumeration(self):
        limit = 1 << self.WIDTH
        for and_mask in range(limit):
            for or_mask in range(limit):
                domain = MaskedBases(and_mask=and_mask, or_mask=or_mask)
                legal = _brute_force(and_mask, or_mask, self.WIDTH)
                for x in range(-1, limit):
                    self.assertEqual(
                        domain.count_le(x),
                        len([b for b in legal if b <= x]),
                        f"count_le(0x{x:x}) for and_mask=0b{and_mask:b}, or_mask=0b{or_mask:b}",
                    )

    def test_count_in_and_nth_in_match_enumeration(self):
        limit = 1 << self.WIDTH
        for and_mask in range(limit):
            for or_mask in range(limit):
                domain = MaskedBases(and_mask=and_mask, or_mask=or_mask)
                legal = _brute_force(and_mask, or_mask, self.WIDTH)
                for lo in range(limit):
                    for hi in range(lo, limit):
                        expected = [b for b in legal if lo <= b <= hi]
                        self.assertEqual(domain.count_in(lo, hi), len(expected))
                        for index, base in enumerate(expected):
                            self.assertEqual(domain.nth_in(lo, hi, index), base)

    def test_contains_ordinal_and_nth_round_trip(self):
        limit = 1 << self.WIDTH
        for and_mask in range(limit):
            for or_mask in range(limit):
                domain = MaskedBases(and_mask=and_mask, or_mask=or_mask)
                legal = _brute_force(and_mask, or_mask, self.WIDTH)
                for index, base in enumerate(legal):
                    self.assertTrue(domain.contains(base))
                    self.assertEqual(domain.ordinal(base), index)
                    self.assertEqual(domain.nth(index), base)
                for base in range(limit):
                    self.assertEqual(domain.contains(base), base in legal)


class TestWiderMasksAgainstBruteForce(unittest.TestCase):
    """A seeded sample at width 8, where exhausting every mask/interval triple is not affordable."""

    WIDTH = 8

    def test_sampled_mask_pairs_and_intervals(self):
        rng = RandNum(seed=17)
        limit = 1 << self.WIDTH
        for _ in range(200):
            and_mask = rng.random_in_range(0, limit)
            or_mask = rng.random_in_range(0, limit)
            domain = MaskedBases(and_mask=and_mask, or_mask=or_mask)
            legal = _brute_force(and_mask, or_mask, self.WIDTH)
            for _ in range(10):
                lo = rng.random_in_range(0, limit)
                hi = rng.random_in_range(lo, limit)
                expected = [b for b in legal if lo <= b <= hi]
                self.assertEqual(domain.count_in(lo, hi), len(expected))
                if expected:
                    self.assertEqual(domain.nth_in(lo, hi, 0), expected[0])
                    self.assertEqual(domain.nth_in(lo, hi, len(expected) - 1), expected[-1])


class TestRankingCounterexamples(unittest.TestCase):
    def test_naive_bit_extraction_would_rank_wrong(self):
        """and_mask=0b01, or_mask=0, x=2: the largest legal base <= x is 1, not 0.

        Depositing the extracted bits of x gives 0, which is why ranking needs a
        proper count_le walk rather than a mask round trip.
        """
        domain = MaskedBases(and_mask=0b01, or_mask=0)
        self.assertEqual(deposit(2 & domain.free, domain.free), 0)
        self.assertEqual(domain.count_le(2), 2)
        self.assertEqual(domain.nth_in(0, 2, domain.count_in(0, 2) - 1), 1)

    def test_forced_bit_above_the_interval_yields_nothing(self):
        domain = MaskedBases(and_mask=0b11, or_mask=0b10)
        self.assertEqual(domain.count_le(1), 0)
        self.assertEqual(domain.count_in(0, 1), 0)
        self.assertEqual(domain.count_in(0, 3), 2)

    def test_non_contiguous_mask(self):
        domain = MaskedBases(and_mask=0b10101, or_mask=0)
        self.assertEqual([domain.nth(k) for k in range(domain.total)], [0b00000, 0b00001, 0b00100, 0b00101, 0b10000, 0b10001, 0b10100, 0b10101])
        self.assertEqual(domain.count_in(0b00010, 0b10011), 4)  # 0b00100, 0b00101, 0b10000, 0b10001

    def test_or_mask_outside_and_mask_forces_bits(self):
        domain = MaskedBases(and_mask=0b0011, or_mask=0b1000)
        self.assertEqual([domain.nth(k) for k in range(domain.total)], [0b1000, 0b1001, 0b1010, 0b1011])
        self.assertFalse(domain.contains(0b0011))


class TestForSpan(unittest.TestCase):
    def test_folds_alignment_into_the_mask(self):
        domain = MaskedBases.for_span(and_mask=0xFFFFFFFFFFFFF000, or_mask=0, alignment=0x200000)
        assert domain is not None
        self.assertEqual(domain.and_mask & 0x1FFFFF, 0)
        self.assertEqual(domain.count_in(0, 0x7FFFFF), 4)

    def test_folds_address_width(self):
        domain = MaskedBases.for_span(and_mask=0xFFFFFFFFFFFFF000, or_mask=0, alignment=0x1000, bits=16)
        assert domain is not None
        self.assertEqual(domain.total, 1 << 4)
        self.assertEqual(domain.nth(domain.total - 1), 0xF000)

    def test_or_mask_below_alignment_admits_nothing(self):
        self.assertIsNone(MaskedBases.for_span(and_mask=0xFFFFFFFFFFFFF000, or_mask=0x1000, alignment=0x200000))

    def test_or_mask_above_width_admits_nothing(self):
        self.assertIsNone(MaskedBases.for_span(and_mask=0xFFFFFFFFFFFFF000, or_mask=0x10000, alignment=0x1000, bits=16))

    def test_malformed_inputs_raise(self):
        with self.assertRaisesRegex(ValueError, "power of two"):
            MaskedBases.for_span(and_mask=0, or_mask=0, alignment=3)
        with self.assertRaisesRegex(ValueError, "bits must be positive"):
            MaskedBases.for_span(and_mask=0, or_mask=0, bits=0)

    def test_python_inverted_mask_is_folded_to_64_bits(self):
        domain = MaskedBases(and_mask=~0xFFF, or_mask=0)
        self.assertEqual(domain.and_mask, 0xFFFFFFFFFFFFF000)
        self.assertTrue(domain.contains(0x1000))
        self.assertFalse(domain.contains(0x1001))


class TestFreeWindows(unittest.TestCase):
    def test_gaps_around_occupied_spans(self):
        self.assertEqual(list(free_windows(0, 100, [])), [(0, 100)])
        self.assertEqual(list(free_windows(0, 100, [(0, 10)])), [(10, 100)])
        self.assertEqual(list(free_windows(0, 100, [(90, 100)])), [(0, 90)])
        self.assertEqual(list(free_windows(0, 100, [(10, 20), (30, 40)])), [(0, 10), (20, 30), (40, 100)])

    def test_adjacent_and_overlapping_spans_collapse(self):
        self.assertEqual(list(free_windows(0, 100, [(10, 20), (20, 30)])), [(0, 10), (30, 100)])
        self.assertEqual(list(free_windows(0, 100, [(10, 40), (20, 30)])), [(0, 10), (40, 100)])

    def test_spans_outside_the_window_are_clipped(self):
        self.assertEqual(list(free_windows(50, 100, [(0, 60)])), [(60, 100)])
        self.assertEqual(list(free_windows(50, 100, [(0, 200)])), [])
        self.assertEqual(list(free_windows(50, 100, [(120, 130)])), [(50, 100)])

    def test_fully_occupied_window(self):
        self.assertEqual(list(free_windows(0, 100, [(0, 100)])), [])


class TestChooseInWindows(unittest.TestCase):
    def _domain(self):
        return MaskedBases(and_mask=0xFFFFFFFFFFFFFFF0, or_mask=0)

    def test_size_consumes_the_top_of_a_window(self):
        """A window [s, e) admits bases in [s, e - size], so a size-16 span cannot start at 0x30."""
        domain = self._domain()
        rng = RandNum(seed=1)
        seen = {choose_in_windows(domain, [(0x00, 0x40)], 16, rng) for _ in range(200)}
        self.assertEqual(seen, {0x00, 0x10, 0x20, 0x30})
        seen = {choose_in_windows(domain, [(0x00, 0x40)], 32, rng) for _ in range(200)}
        self.assertEqual(seen, {0x00, 0x10, 0x20})

    def test_covers_every_base_across_several_windows(self):
        domain = self._domain()
        rng = RandNum(seed=2)
        windows = [(0x00, 0x20), (0x100, 0x120)]
        seen = {choose_in_windows(domain, windows, 16, rng) for _ in range(400)}
        self.assertEqual(seen, {0x00, 0x10, 0x100, 0x110})

    def test_returns_none_when_nothing_fits(self):
        domain = self._domain()
        rng = RandNum(seed=3)
        self.assertIsNone(choose_in_windows(domain, [], 16, rng))
        self.assertIsNone(choose_in_windows(domain, [(0x00, 0x08)], 16, rng))
        self.assertIsNone(choose_in_windows(domain, [(0x01, 0x0F)], 1, rng))

    def test_excluded_bases_are_skipped_exactly(self):
        domain = self._domain()
        rng = RandNum(seed=4)
        for excluded in ([0x00], [0x10], [0x30], [0x00, 0x30], [0x10, 0x20]):
            seen = {choose_in_windows(domain, [(0x00, 0x40)], 16, rng, excluded=excluded) for _ in range(300)}
            self.assertEqual(seen, {0x00, 0x10, 0x20, 0x30} - set(excluded), f"excluded={excluded}")

    def test_all_bases_excluded_yields_none(self):
        domain = self._domain()
        rng = RandNum(seed=5)
        self.assertIsNone(choose_in_windows(domain, [(0x00, 0x40)], 16, rng, excluded=[0x00, 0x10, 0x20, 0x30]))

    def test_duplicate_exclusion_is_counted_once(self):
        domain = self._domain()
        self.assertEqual(
            choose_in_windows(
                domain,
                [(0x00, 0x20)],
                16,
                RandNum(seed=5),
                excluded=[0x00, 0x00],
            ),
            0x10,
        )

    def test_exclusion_does_not_remove_a_neighbour_whose_span_covers_it(self):
        """Excluding base 0x2000 must not remove base 0x1000 when the span is 0x2000 wide.

        A rejected candidate is one base, not occupied memory: modelling it as an
        occupied byte would also rule out every legal base whose span covers it.
        """
        domain = MaskedBases(and_mask=0xFFFFFFFFFFFFF000, or_mask=0)
        rng = RandNum(seed=6)
        seen = {choose_in_windows(domain, [(0x1000, 0x5000)], 0x2000, rng, excluded=[0x2000]) for _ in range(300)}
        self.assertEqual(seen, {0x1000, 0x3000})

    def test_selection_is_uniform_over_a_large_count(self):
        """One exact integer draw over a 2**52-element set, not a scaled float.

        ``random_in_range`` multiplies a 53-bit float, which starts skipping and
        double-counting ordinals at this size.
        """
        domain = MaskedBases(and_mask=0xFFFFFFFFFFFFF000, or_mask=0)
        rng = RandNum(seed=7)
        window = (0, 1 << 64)
        self.assertEqual(domain.count_in(0, (1 << 64) - 0x1000), 1 << 52)
        picks = [choose_in_windows(domain, [window], 0x1000, rng) for _ in range(200)]
        self.assertEqual(len(set(picks)), len(picks))
        for pick in picks:
            self.assertTrue(domain.contains(pick))
        low = len([p for p in picks if p < (1 << 63)])
        self.assertGreater(low, 60)
        self.assertLess(low, 140)

    def test_rejects_nonpositive_size(self):
        with self.assertRaisesRegex(ValueError, "size must be positive"):
            choose_in_windows(self._domain(), [(0, 0x40)], 0, RandNum(seed=8))


class TestBinarySearchOracle(unittest.TestCase):
    """A second, independent implementation of the bounds, via monotonicity alone.

    ``nth`` is strictly increasing, so bisecting it finds the same interval bounds
    that ``count_le`` computes directly. This shares the deposit premise (which the
    brute-force tests pin separately) but not the counting walk.
    """

    def _first_index_at_least(self, domain, lo):
        low, high = 0, domain.total
        while low < high:
            mid = (low + high) // 2
            if domain.nth(mid) < lo:
                low = mid + 1
            else:
                high = mid
        return low

    def test_matches_count_le_for_wide_masks(self):
        rng = RandNum(seed=11)
        for _ in range(50):
            and_mask = rng.random_in_range(0, 1 << 20) | 0xF
            domain = MaskedBases(and_mask=and_mask, or_mask=0)
            for _ in range(20):
                lo = rng.random_in_range(0, 1 << 20)
                self.assertEqual(domain.count_le(lo - 1), self._first_index_at_least(domain, lo))


if __name__ == "__main__":
    unittest.main()
