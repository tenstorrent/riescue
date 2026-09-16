# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

import riescue.lib.common as common
from riescue.dtest_framework.lib.pma import PmaInfo, no_routing_on_pma, allow_amos_in_pma_ncio, set_allow_amos_in_pma_ncio
from riescue.dtest_framework.lib.pma_generator import PmaRandomizer
from riescue.lib.rand import RandNum

PHYS_ADDR_BITS = 44


def is_legal_pmacfg(value: int) -> bool:
    """Standalone re-implementation of the pmacfg legality rules for verification.

    Bits 6:5 are the 2-bit amo type: pinned to 0b11 on cacheable main memory, free over 0b00-0b11
    everywhere else (the whisper configs shipped here set allow_amo_in_non_cacheable_regions and
    allow_amo_in_io_regions). Bit 8 is routing: required on cacheable main memory, rejected on io/ch.
    Off a legacy_pma target bit 8 is reserved instead and must always be 0 --
    see PMACFG_ROUTING_BIT and docs/source/user_guides/pma.rst.
    """
    n = value >> 58
    if n == 0 or n < 12:
        return False
    read = value & 1
    write = (value >> 1) & 1
    execute = (value >> 2) & 1
    memtype = (value >> 3) & 3
    amo = (value >> 5) & 3
    cacheable = (value >> 7) & 1
    coherent = (value >> 8) & 1
    if (value >> 9) & 7:
        return False  # bits 11:9 reserved
    if no_routing_on_pma():
        if coherent:
            return False  # bit 8 is reserved off a legacy_pma target and must never be written
    elif memtype != 0 and coherent:
        return False  # whisper rejects a coherent io/ch region
    if memtype != 0:  # io/ch0/ch1
        if write and not read:
            return False
    else:  # memory
        if read + write + execute not in (0, 3):
            return False
        if cacheable:
            if amo != 3:
                return False  # cacheable main memory is the one shape pinned to amo type 0b11
            if not no_routing_on_pma() and not coherent:
                return False  # while bit 8 is live, whisper requires cacheable main memory to be coherent
    return True


class PmaRandomizerTest(unittest.TestCase):
    """
    Tests for PmaRandomizer legality, placement, masking, and determinism.
    """

    def setUp(self):
        # Decoys roll the full NC/IO amo range only on a target that permits it; the legality
        # oracle above assumes that target, so pin it rather than inherit the process default.
        self.addCleanup(set_allow_amos_in_pma_ncio, allow_amos_in_pma_ncio())
        set_allow_amos_in_pma_ncio(True)

    def make_randomizer(self, seed=42, mask_pct=25):
        return PmaRandomizer(RandNum(seed=seed), mask_pct=mask_pct, phys_addr_bits=PHYS_ADDR_BITS)

    def test_deterministic_per_seed(self):
        regions_a = self.make_randomizer(seed=7).generate(10, [])
        regions_b = self.make_randomizer(seed=7).generate(10, [])
        self.assertEqual([(r.pma_address, r.pma_size, r.pma_mask, repr(r)) for r in regions_a], [(r.pma_address, r.pma_size, r.pma_mask, repr(r)) for r in regions_b])

    def test_different_seeds_differ(self):
        regions_a = self.make_randomizer(seed=1).generate(10, [])
        regions_b = self.make_randomizer(seed=2).generate(10, [])
        self.assertNotEqual([(r.pma_address, r.pma_mask, repr(r)) for r in regions_a], [(r.pma_address, r.pma_mask, repr(r)) for r in regions_b])

    def test_napot_alignment_and_bounds(self):
        for seed in range(5):
            for region in self.make_randomizer(seed=seed).generate(12, []):
                self.assertGreaterEqual(region.pma_size, 1 << PmaRandomizer.MIN_SIZE_LOG2)
                self.assertLessEqual(region.pma_size, 1 << PmaRandomizer.MAX_SIZE_LOG2)
                self.assertEqual(region.pma_size & (region.pma_size - 1), 0, "size must be power of two")
                self.assertEqual(region.pma_address % region.pma_size, 0, "base must be size-aligned")
                self.assertLessEqual(region.pma_address + region.pma_size, 1 << PHYS_ADDR_BITS)

    def test_all_generated_values_legal(self):
        for seed in range(10):
            for region in self.make_randomizer(seed=seed, mask_pct=50).generate(16, []):
                value = region.generate_pma_value(force=True)
                self.assertTrue(is_legal_pmacfg(value), f"illegal pmacfg 0x{value:x} from {region}")

    def test_blocked_intervals_avoided(self):
        blocked = [(0x0, 0x8000_0000), (0x1_0000_0000, 0x8_0000_0000)]
        for region in self.make_randomizer(seed=3).generate(12, blocked):
            for start, end in blocked:
                self.assertFalse(region.pma_address < end and start < region.pma_address + region.pma_size, f"region {region} overlaps blocked [{start:#x}, {end:#x})")

    def test_generated_regions_mutually_disjoint(self):
        regions = self.make_randomizer(seed=4).generate(16, [])
        for i, a in enumerate(regions):
            for b in regions[i + 1 :]:
                self.assertFalse(a.pma_address < b.pma_address + b.pma_size and b.pma_address < a.pma_address + a.pma_size, f"{a} overlaps {b}")

    def test_mask_pct_zero_no_masks(self):
        for region in self.make_randomizer(seed=5, mask_pct=0).generate(16, []):
            self.assertEqual(region.pma_mask, 0)

    def test_mask_pct_hundred_masks_where_possible(self):
        max_maskable = min(PHYS_ADDR_BITS - PmaRandomizer.MASKED_MIN_COMPARE_BITS, 52)
        for region in self.make_randomizer(seed=6, mask_pct=100).generate(16, []):
            if common.msb(region.pma_size) < max_maskable:
                self.assertNotEqual(region.pma_mask, 0, f"expected mask on {region}")

    def test_masked_regions_keep_min_compare_bits(self):
        for seed in range(5):
            for region in self.make_randomizer(seed=seed, mask_pct=100).generate(16, []):
                if region.pma_mask == 0:
                    continue
                self.assertNotEqual(region.effective_match_mask(), 0)
                size_bits = common.msb(region.pma_size)
                compare_span = region.effective_match_mask() & ((1 << PHYS_ADDR_BITS) - 1)
                compare_bits = bin(compare_span >> size_bits).count("1")
                self.assertGreaterEqual(compare_bits, PmaRandomizer.MASKED_MIN_COMPARE_BITS, f"mask 0x{region.pma_mask:x} on {region} leaves too few compare bits")
                self.assertEqual(region.pma_mask & ((1 << size_bits) - 1), 0, "mask bits below region size are meaningless")

    def test_truncation_on_exhaustion(self):
        blocked = [(0x0, 1 << PHYS_ADDR_BITS)]
        randomizer = self.make_randomizer(seed=8)
        with self.assertLogs("riescue.dtest_framework.lib.pma_generator", level="WARNING"):
            regions = randomizer.generate(4, blocked)
        self.assertEqual(regions, [])

    def test_region_names_and_flags(self):
        regions = self.make_randomizer(seed=9).generate(3, [])
        for idx, region in enumerate(regions):
            self.assertEqual(region.pma_name, f"pma_rand_{idx}")
            self.assertTrue(region.pma_randomized)
            self.assertTrue(region.pma_valid)

    def test_memory_type_bias_favors_cacheable(self):
        """Decoy attributes follow the fixed bias: ~78% cacheable / ~10% noncacheable / 10% io / 2% ch0+ch1."""
        counts = {"cacheable": 0, "noncacheable": 0, "io": 0, "ch0": 0, "ch1": 0}
        total = 0
        for seed in range(20):
            for region in self.make_randomizer(seed=seed, mask_pct=0).generate(50, []):
                key = region.pma_cacheability if region.pma_memory_type == "memory" else region.pma_memory_type
                counts[key] += 1
                total += 1
        self.assertGreater(counts["cacheable"] / total, 0.72)
        self.assertLess(counts["cacheable"] / total, 0.84)
        self.assertGreater(counts["noncacheable"] / total, 0.05)
        self.assertLess(counts["noncacheable"] / total, 0.15)
        self.assertGreater(counts["io"] / total, 0.05)
        self.assertLess(counts["io"] / total, 0.15)
        # ch0/ch1 keep a small slice so memory-type encodings 2/3 stay exercised
        self.assertGreater(counts["ch0"] + counts["ch1"], 0)
        self.assertLess((counts["ch0"] + counts["ch1"]) / total, 0.06)

    def test_all_strategies_produce_legal_masks(self):
        """Every mask shape strategy yields a nonzero mask using only the candidate bits."""
        for seed in range(5):
            randomizer = self.make_randomizer(seed=seed)
            for span in (1, 2, 3, 8, 24):
                candidate_bits = list(range(12, 12 + span))
                allowed = sum(1 << bit for bit in candidate_bits)
                for strategy in PmaRandomizer.MASK_STRATEGY_WEIGHTS:
                    mask = randomizer._build_mask(strategy, candidate_bits)
                    self.assertNotEqual(mask, 0, f"strategy {strategy} produced empty mask for span {span}")
                    self.assertEqual(mask & ~allowed, 0, f"strategy {strategy} set bits outside candidates for span {span}")

    def test_apply_random_mask_force_ignores_pct(self):
        """force=True masks a maskable region even with mask_pct=0."""
        randomizer = self.make_randomizer(seed=10, mask_pct=0)
        region = PmaInfo(pma_name="pma_force", pma_address=0x8000_0000, pma_size=0x1000, pma_valid=True)
        self.assertTrue(randomizer.apply_random_mask(region, [], force=True))
        self.assertNotEqual(region.pma_mask, 0)

    def test_apply_random_mask_respects_blocked(self):
        """No safe mask exists when everything but the region itself is blocked; it stays unmasked."""
        # Small phys space keeps the page-walking congruence check cheap while blocking every window
        randomizer = PmaRandomizer(RandNum(seed=11), mask_pct=100, phys_addr_bits=24)
        region = PmaInfo(pma_name="pma_blocked", pma_address=0x80_0000, pma_size=0x1000, pma_valid=True)
        blocked = [(0x0, 0x80_0000), (0x80_1000, 1 << 24)]
        self.assertFalse(randomizer.apply_random_mask(region, blocked, force=True))
        self.assertEqual(region.pma_mask, 0)

    def test_forced_mask_single_bit_sweep_fallback(self):
        """force=True finds the lone safe single-bit mask on every seed via the fallback sweep."""
        # candidate bits are 12..15; only bit 15's window (0x80_8000) is unblocked, so any
        # multi-bit or lower-bit mask collides and the sweep is the only reliable path
        blocked = [(0x0, 0x80_0000), (0x80_1000, 0x80_8000), (0x80_9000, 1 << 24)]
        for seed in range(10):
            randomizer = PmaRandomizer(RandNum(seed=seed), mask_pct=100, phys_addr_bits=24)
            region = PmaInfo(pma_name="pma_forced_sweep", pma_address=0x80_0000, pma_size=0x1000, pma_valid=True)
            self.assertTrue(randomizer.apply_random_mask(region, blocked, force=True), f"seed {seed} found no mask")
            self.assertEqual(region.pma_mask, 1 << 15, f"seed {seed} picked unsafe mask 0x{region.pma_mask:x}")


class NcioAmoClampRandomizerTest(unittest.TestCase):
    """With allow_amos_in_pma_ncio off, no decoy may land a non-AMONone pmacfg on NC/IO space."""

    def setUp(self):
        self.addCleanup(set_allow_amos_in_pma_ncio, allow_amos_in_pma_ncio())

    def regions(self, seed, allow):
        set_allow_amos_in_pma_ncio(allow)
        return PmaRandomizer(RandNum(seed=seed), mask_pct=25, phys_addr_bits=PHYS_ADDR_BITS).generate(16, [])

    def test_no_decoy_carries_ncio_atomicity(self):
        for seed in range(10):
            for region in self.regions(seed, allow=False):
                if region.is_cacheable_memory():
                    continue
                bits = (region.generate_pma_value(force=True) >> 5) & 3
                self.assertEqual(bits, 0, f"decoy {region} programmed pmacfg[6:5]=0b{bits:02b}")

    def test_knob_on_still_rolls_the_full_range(self):
        seen = set()
        for seed in range(10):
            for region in self.regions(seed, allow=True):
                if not region.is_cacheable_memory():
                    seen.add((region.generate_pma_value(force=True) >> 5) & 3)
        self.assertEqual(seen, {0, 1, 2, 3})

    def test_placement_is_identical_across_the_knob(self):
        """The clamp replaces a drawn value, it does not skip the draw, so nothing else may shift"""
        for seed in range(5):
            off = [(r.pma_address, r.pma_size, r.pma_mask, r.pma_memory_type, r.pma_cacheability) for r in self.regions(seed, allow=False)]
            on = [(r.pma_address, r.pma_size, r.pma_mask, r.pma_memory_type, r.pma_cacheability) for r in self.regions(seed, allow=True)]
            self.assertEqual(off, on, f"seed {seed}: decoy placement shifted with the knob")


if __name__ == "__main__":
    unittest.main(verbosity=2)
