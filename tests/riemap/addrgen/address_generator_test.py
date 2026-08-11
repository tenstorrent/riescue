# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""AddrGen pool routing: per-space VA/GPA pools and global-pool mirroring.

The global linear pool means "unmapped in every space": a VA placed in any
mirrored space pool is off-limits to bare (space-less) draws and vice versa,
while distinct spaces may still deliberately share a VA. G-stage (GPA) pools
are a separate address universe and stay isolated from the mirroring.
"""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.address_generator import AddrGen
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.addrgen.types import AddressConstraint, ExcludedRegion
from riescue.riemap.memory import CustomRange, DramRange, IoRange, Memory


def _addrgen(seed=1):
    return AddrGen(rng=RandNum(seed=seed), mem=Memory(dram_ranges=[DramRange(start=0x80000000, size=0x100000000)]))


def _lin(size=0x1000, **kw):
    return AddressConstraint(type=RV.AddressType.LINEAR, size=size, mask=0xFFFFFFFFFFFFF000, bits=39, **kw)


def _window(addr, size=0x1000):
    """A probe constraint whose only possible draw is [addr, addr+size)."""
    return AddressConstraint(type=RV.AddressType.LINEAR, size=size, mask=0xFFFFFFFFFFFFF000, bits=57, start=addr, end=addr + size - 1)


class TestPoolMirroring(unittest.TestCase):
    def test_space_draw_is_reserved_globally(self):
        # A VA placed in a space pool must be unavailable to bare (global) draws.
        ag = _addrgen()
        ag.make_space_pool("a")
        addr = ag.generate_address(constraint=_lin(), space_key="a")
        with self.assertRaises(AddrGenError):
            ag.generate_address(constraint=_window(addr))

    def test_global_draw_is_reserved_in_space_pools(self):
        # A bare VA must be unavailable to space-pool draws.
        ag = _addrgen()
        ag.make_space_pool("a")
        addr = ag.generate_address(constraint=_lin())
        with self.assertRaises(AddrGenError):
            ag.generate_address(constraint=_window(addr), space_key="a")

    def test_space_reserve_is_mirrored_globally(self):
        ag = _addrgen()
        ag.make_space_pool("a")
        ag.reserve_memory(address_type=RV.AddressType.LINEAR, start_address=0x1_0000_0000, size=0x1000, space_key="a")
        with self.assertRaises(AddrGenError):
            ag.generate_address(constraint=_window(0x1_0000_0000))

    def test_spaces_may_still_share_a_va(self):
        # Mirroring must not stop two spaces from deliberately holding the same VA.
        ag = _addrgen()
        ag.make_space_pool("a")
        ag.make_space_pool("b")
        ag.reserve_memory(address_type=RV.AddressType.LINEAR, start_address=0x1_0000_0000, size=0x1000, space_key="a")
        ag.reserve_memory(address_type=RV.AddressType.LINEAR, start_address=0x1_0000_0000, size=0x1000, space_key="b")

    def test_gstage_pool_is_isolated(self):
        # A GPA is not a VA: a g-stage pool draw must not consume global VA space,
        # and a bare VA must stay drawable at the same value a GPA holds.
        ag = _addrgen()
        ag.make_space_pool("g", mirror_global=False)
        gpa = ag.generate_address(constraint=_lin(), space_key="g")
        addr = ag.generate_address(constraint=_window(gpa))
        self.assertEqual(addr, gpa)

    def test_global_draw_not_mirrored_into_gstage_pool(self):
        ag = _addrgen()
        ag.make_space_pool("g", mirror_global=False)
        addr = ag.generate_address(constraint=_lin())
        gpa = ag.generate_address(constraint=_window(addr), space_key="g")
        self.assertEqual(gpa, addr)

    def test_replacing_space_pool_updates_mirror_semantics(self):
        ag = _addrgen()
        ag.make_space_pool("g", mirror_global=True)
        ag.make_space_pool("g", mirror_global=False)
        address = 0x1_0000_0000

        ag.reserve_memory(
            RV.AddressType.LINEAR,
            address,
            0x1000,
            space_key="g",
        )

        self.assertEqual(ag.generate_address(_window(address)), address)


class TestMemoryAddressType(unittest.TestCase):
    @staticmethod
    def _memory_constraint(start, exclude=None):
        return AddressConstraint(
            type=RV.AddressType.MEMORY,
            bits=32,
            size=0x1000,
            mask=0xFFFF_F000,
            start=start,
            end=start + 0xFFF,
            exclude=exclude,
        )

    def test_memory_draw_must_belong_to_physical_memory(self):
        ag = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=0x8000_0000, size=0x1000)]),
        )

        with self.assertRaises(AddrGenError):
            ag.generate_address(self._memory_constraint(0x2000))

    def test_memory_draw_uses_selected_space_pool(self):
        address = 0x8000_0000
        ag = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=address, size=0x1000)]),
        )
        ag.make_space_pool("g", mirror_global=False)
        ag.reserve_memory(
            RV.AddressType.LINEAR,
            address,
            0x1000,
            space_key="g",
        )

        with self.assertRaises(AddrGenError):
            ag.generate_address(
                self._memory_constraint(address),
                space_key="g",
            )

    def test_memory_draw_honors_per_request_exclusion(self):
        address = 0x8000_0000
        ag = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=address, size=0x1000)]),
        )
        excluded = ExcludedRegion.from_interval(address, address + 0x1000)

        with self.assertRaises(AddrGenError):
            ag.generate_address(self._memory_constraint(address, exclude=(excluded,)))


class TestRestrictionTransactions(unittest.TestCase):
    @staticmethod
    def _same_index_window(address, dont_allocate=False):
        return AddressConstraint(
            type=RV.AddressType.LINEAR,
            bits=57,
            size=0x1000,
            mask=0xFFFF_FFFF_FFFF_F000,
            start=address,
            end=address + 0xFFF,
            dont_allocate=dont_allocate,
        )

    def test_restriction_rejection_does_not_reserve_candidate(self):
        ag = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=0x8000_0000, size=0x100000)]),
            limit_indices=True,
        )
        for index in range(4):
            ag.generate_address(self._same_index_window(0x1000 + index * 0x10000))
        rejected = 0x41000

        with self.assertRaisesRegex(AddrGenError, "Restricted address limit"):
            ag.generate_address(self._same_index_window(rejected))

        self.assertFalse(ag.linear_overlap(rejected, 0x1000))

    def test_dont_allocate_probe_does_not_consume_restriction_quota(self):
        ag = AddrGen(
            RandNum(seed=1),
            Memory(dram_ranges=[DramRange(start=0x8000_0000, size=0x100000)]),
            limit_indices=True,
        )
        probe = self._same_index_window(0x1000, dont_allocate=True)

        for _ in range(5):
            self.assertEqual(ag.generate_address(probe), 0x1000)

        self.assertEqual(dict(ag.restricted_indices), {})


class TestExclusionCompleteness(unittest.TestCase):
    def test_more_than_32_excluded_candidates_reaches_legal_tail(self):
        base = 0x8000_0000
        page_size = 0x1000
        excluded = [
            ExcludedRegion.from_interval(
                base + index * page_size,
                base + (index + 1) * page_size,
            )
            for index in range(33)
        ]
        ag = AddrGen(
            RandNum(seed=1),
            Memory(
                dram_ranges=[
                    DramRange(start=base, size=34 * page_size),
                ]
            ),
            excluded_regions=excluded,
        )
        ag._rng.percent = lambda: 100
        ag._rng.random_in_range = lambda start, end: start
        constraint = AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,
            size=page_size,
            mask=0xFFFF_F000,
            start=base,
            end=base + 34 * page_size - 1,
        )

        self.assertEqual(
            ag.generate_address(constraint),
            base + 33 * page_size,
        )


class TestConstraintValidation(unittest.TestCase):
    def test_rejects_negative_size(self):
        constraint = AddressConstraint(
            type=RV.AddressType.LINEAR,
            size=-1,
        )
        with self.assertRaisesRegex(AddrGenError, "positive"):
            constraint.validate_constraints()

    def test_rejects_invalid_address_widths(self):
        for bits in (0, 65):
            with self.subTest(bits=bits):
                constraint = AddressConstraint(
                    type=RV.AddressType.LINEAR,
                    bits=bits,
                )
                with self.assertRaisesRegex(AddrGenError, "bits"):
                    constraint.validate_constraints()

    def test_rejects_negative_mask(self):
        constraint = AddressConstraint(
            type=RV.AddressType.LINEAR,
            mask=-1,
        )
        with self.assertRaisesRegex(AddrGenError, "mask"):
            constraint.validate_constraints()

    def test_zero_mask_is_valid_fixed_address(self):
        constraint = AddressConstraint(
            type=RV.AddressType.LINEAR,
            bits=16,
            size=1,
            mask=0,
            or_mask=0,
            start=0,
            end=0,
        )
        constraint.validate_constraints()

    def test_reservation_requires_positive_size(self):
        ag = _addrgen()
        with self.assertRaisesRegex(AddrGenError, "positive"):
            ag.reserve_memory(
                RV.AddressType.PHYSICAL,
                0x8000_0000,
                0,
            )


class TestCallerState(unittest.TestCase):
    def test_custom_region_resolution_does_not_mutate_constraint(self):
        region = CustomRange(name="device", start=0x8000_0000, size=0x1000)
        ag = AddrGen(
            RandNum(seed=1),
            Memory(custom_ranges=[region]),
        )
        constraint = AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            bits=32,
            size=0x1000,
            mask=0xFFFF_F000,
            custom_region="device",
        )
        before = (
            constraint.start,
            constraint.end,
            constraint.qualifiers,
        )

        self.assertEqual(ag.generate_address(constraint), region.start)
        self.assertEqual(
            (constraint.start, constraint.end, constraint.qualifiers),
            before,
        )


class TestReservedRanges(unittest.TestCase):
    def test_inclusive_final_byte_is_reserved(self):
        reserved = IoRange(start=0x80001000, size=0x1000, test_access=False)
        ag = AddrGen(
            rng=RandNum(seed=1),
            mem=Memory(
                dram_ranges=[DramRange(start=0x80000000, size=0x100000)],
                reserved_ranges=[reserved],
            ),
        )
        final_byte = reserved.end
        only_final_byte = AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,
            size=1,
            mask=0xFFFFFFFFFFFFFFFF,
            start=final_byte,
            end=final_byte,
        )

        with self.assertRaises(AddrGenError):
            ag.generate_address(only_final_byte)


class TestLinearIndexLimits(unittest.TestCase):
    def test_fifth_address_with_same_index_is_rejected(self):
        ag = AddrGen(
            rng=RandNum(seed=1),
            mem=Memory(
                dram_ranges=[
                    DramRange(start=0x80000000, size=0x100000),
                ]
            ),
            limit_indices=True,
        )
        addresses = [0x1000 + i * 0x10000 for i in range(5)]
        for address in addresses[:4]:
            self.assertEqual(ag.generate_address(_window(address)), address)

        with self.assertRaisesRegex(AddrGenError, "Restricted address limit"):
            ag.generate_address(_window(addresses[4]))


class TestAdoptRngStream(unittest.TestCase):
    """The allocator replays a probe's winning draw onto a fresh branch (see ``_search``)."""

    def test_branch_continues_the_probe_stream(self):
        probe = _addrgen()
        probe.generate_address(constraint=_lin())
        expected_next = probe.clone().generate_address(constraint=_lin())

        branch = probe.clone()
        branch.bind_rng(RandNum(seed=999))
        branch.adopt_rng_stream(probe)

        # Adoption replaces the branch's unrelated stream with the probe's position, so the
        # branch picks up where the probe left off instead of repeating its draw.
        self.assertEqual(branch.generate_address(constraint=_lin()), expected_next)

    def test_branch_draws_do_not_disturb_the_probe(self):
        probe = _addrgen()
        reference = _addrgen()
        branch = probe.clone()
        branch.adopt_rng_stream(probe)

        branch.generate_address(constraint=_lin())

        # An abandoned branch must leave the probe's own stream untouched.
        self.assertEqual(probe.generate_address(constraint=_lin()), reference.generate_address(constraint=_lin()))

    def test_pools_follow_the_adopted_rng(self):
        probe = _addrgen()
        probe.make_space_pool("a")
        branch = probe.clone()

        branch.adopt_rng_stream(probe)

        self.assertIsNot(branch._rng, probe._rng)
        for pool in (branch._linear_addr_space, branch._physical_addr_space, *branch._space_pools.values()):
            self.assertIs(pool.rng, branch._rng)


if __name__ == "__main__":
    unittest.main()
