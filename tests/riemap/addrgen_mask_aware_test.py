# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Tests for exact mask-aware address allocation.

Both pinned and ordinary masked requests select from the exact reachable slot
set (bases b with ``b == (b & mask) | or_mask``), so sparse legal slots cannot
be missed by random probing.
"""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.address_space import AddressSpace
from riescue.riemap.addrgen.types import AddressConstraint
from riescue.riemap.addrgen.exceptions import AddrGenError

# Coloring pin: clear the low 20 bits in the and_mask (so bits 0..19 are forced),
# and force bit 12 high via or_mask -> reachable bases are k*0x100000 + 0x1000.
_STRIDE = 0x100000
_OFFSET = 0x1000
_MASK = ~(_STRIDE - 1) & 0xFFFFFFFFFFFFFFFF  # clears bits 0..19
_OR_MASK = _OFFSET
_SIZE = 0x1000


def _space(seed):
    return AddressSpace(RandNum(seed=seed), RV.AddressType.PHYSICAL)


def _pinned(pinned=True, size=_SIZE):
    return AddressConstraint(
        type=RV.AddressType.PHYSICAL,
        bits=64,
        size=size,
        mask=_MASK,
        or_mask=_OR_MASK,
        qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
        pinned=pinned,
    )


class TestPinnedReachableSlot(unittest.TestCase):
    # A near-empty pool whose single free window straddles a stride boundary so that
    # exactly ONE reachable base fits, and it sits at the very tail of the window.
    # Probe-and-mask (unpinned) almost always masks its random probe onto the
    # out-of-window base below the window start; the reachable-slot path lands on the
    # in-window base every time.
    _LO = 0x80001001  # just past the excluded base 0x80001000
    _HI = 0x80101FFF  # last byte of the only fitting base 0x80101000
    _ONLY_BASE = 0x80101000

    def _defined_space(self, seed):
        space = _space(seed)
        space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, self._LO, self._HI)
        return space

    def test_pinned_draw_is_reachable_aligned_in_segment(self):
        addr = self._defined_space(seed=1).generate_address(_pinned())
        self.assertEqual(addr, (addr & _MASK) | _OR_MASK, "address is not on the pinned congruence class")
        self.assertEqual(addr % _SIZE, 0, "address is not size-aligned")
        self.assertTrue(self._LO <= addr and addr + _SIZE - 1 <= self._HI, "address is outside the defined segment")
        self.assertEqual(addr, self._ONLY_BASE)

    def test_sparse_masked_slot_is_found_for_every_seed(self):
        seeds = range(80)
        for seed in seeds:
            for pinned in (False, True):
                addr = self._defined_space(seed).generate_address(_pinned(pinned=pinned))
                self.assertEqual(addr, self._ONLY_BASE, f"failed for seed {seed}, pinned={pinned}")


class TestPinnedPacking(unittest.TestCase):
    # A fully-free window spanning four stride blocks -> exactly four reachable bases.
    _LO = 0x80100000
    _HI = 0x804FFFFF
    _BASES = {0x80101000, 0x80201000, 0x80301000, 0x80401000}

    def _defined_space(self, seed):
        space = _space(seed)
        space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, self._LO, self._HI)
        return space

    def test_repeated_pinned_draws_pack_then_exhaust(self):
        space = self._defined_space(seed=5)
        drawn = []
        for _ in range(len(self._BASES)):
            addr = space.generate_address(_pinned())
            drawn.append(addr)

        self.assertEqual(set(drawn), self._BASES, "pinned draws did not cover exactly the reachable congruence class")
        self.assertEqual(len(set(drawn)), len(drawn), "pinned draws collided")
        # No two drawn spans overlap.
        ordered = sorted(drawn)
        for a, b in zip(ordered, ordered[1:]):
            self.assertGreaterEqual(b, a + _SIZE, "drawn pinned spans overlap")
        # The congruence class is now exhausted -> the next pinned draw raises cleanly.
        with self.assertRaises(AddrGenError):
            space.generate_address(_pinned())


class TestPinnedDeterminism(unittest.TestCase):
    _LO = 0x80100000
    _HI = 0x804FFFFF

    def _defined_space(self, seed):
        space = _space(seed)
        space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, self._LO, self._HI)
        return space

    def _sequence(self, seed, n=4):
        space = self._defined_space(seed)
        return [space.generate_address(_pinned()) for _ in range(n)]

    def test_same_seed_same_sequence(self):
        self.assertEqual(self._sequence(seed=7), self._sequence(seed=7))

    def test_distinct_seeds_may_differ_but_are_reproducible(self):
        first = self._sequence(seed=11)
        second = self._sequence(seed=11)
        self.assertEqual(first, second)
        self.assertGreater(len({self._sequence(seed)[0] for seed in range(12)}), 1)


class TestForcedHighPrefix(unittest.TestCase):
    def test_or_mask_selects_cluster_when_free_mask_only_covers_low_bits(self):
        space = AddressSpace(RandNum(seed=1), RV.AddressType.LINEAR)
        space.define_segment(
            RV.AddressQualifiers.ADDRESS_LINEAR,
            0,
            (1 << 39) - 1,
        )
        constraint = AddressConstraint(
            type=RV.AddressType.LINEAR,
            bits=39,
            size=0x1000,
            mask=0x1FF000,
            or_mask=0x3677000000,
            qualifiers={RV.AddressQualifiers.ADDRESS_LINEAR},
            pinned=True,
        )

        address = space.generate_address(constraint)

        self.assertEqual(address, (address & constraint.mask) | constraint.or_mask)


if __name__ == "__main__":
    unittest.main()
