# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import copy
import dataclasses
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Optional, Sequence

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.addrgen.address_space import AddressSpace
from riescue.riemap.addrgen.types import AddressConstraint, ExcludedRegion
from riescue.riemap.memory import Memory

if TYPE_CHECKING:
    from riescue.riemap.request import Space

log = logging.getLogger(__name__)

_EXCLUSION_RETRY_LIMIT = 32


def _subtract_inclusive_span(lo: int, hi: int, hole_lo: int, hole_hi: int) -> list[tuple[int, int]]:
    """
    Return disjoint inclusive spans covering [lo, hi] minus [hole_lo, hole_hi].
    Spans that do not overlap the hole are returned unchanged.
    """
    if hi < lo:
        return []
    hs = max(lo, hole_lo)
    he = min(hi, hole_hi)
    if hs > he:
        return [(lo, hi)]
    out: list[tuple[int, int]] = []
    if lo <= hs - 1:
        out.append((lo, hs - 1))
    if he + 1 <= hi:
        out.append((he + 1, hi))
    return out


class AddrGen:
    """
    Address Generator, Facade interface for generating and managing AddressSpace objects.
    Handles physical and linear address spaces, validates constraints, manages memory reservations

    `generate_address` is the main method to generate an address based on the constraints.
    `reserve_memory` can also be used to reserve memory for fixed addresses.

    :param rng: Random number generator
    :param mem: Memory object
    :param limit_indices: Whether to limit the number of addresses with the same index
    :param limit_way_predictor_multihit: Whether to limit the number of addresses with the same way predictor multihit
    :param excluded_regions: physical windows no drawn address may land in; held by reference
    """

    def __init__(self, rng: RandNum, mem: Memory, limit_indices: bool = False, limit_way_predictor_multihit: bool = False, excluded_regions=None):
        self._mem = mem
        self._rng = rng
        self._linear_addr_space = AddressSpace(rng, RV.AddressType.LINEAR)
        self._physical_addr_space = AddressSpace(rng, RV.AddressType.PHYSICAL)
        # Per-space VA/GPA pools keyed by the Space object itself, created by the page
        # table builder (make_space_pool). A linear/GPA allocation that names a space
        # key is routed to that space's own pool, so distinct pages in different
        # spaces may share a VA; the physical pool above stays global. Callers that
        # pass no space key use the global linear pool.
        self._space_pools: "dict[Space, AddressSpace]" = {}
        # Space pools that mirror reservations with the global linear pool (VA spaces).
        # G-stage (GPA) pools are a separate address universe and stay isolated.
        self._mirrored_pools: "set[Space]" = set()
        # Constructor exclusions are held by reference so a consumer that truncates or
        # appends later still has the exclusion honored. Late carve-outs registered via
        # exclude_region() stay in a separate list that clone() copies.
        self._excluded_regions = excluded_regions if excluded_regions is not None else []
        self._extra_excluded_regions: list = []

        self.limit_indices = limit_indices
        self.limit_way_predictor_multihit = limit_way_predictor_multihit

        self.restricted_indices = defaultdict(int)  # ; tracks number of times address with same index was generated

        # Fixed windows (pma_randomization: false) are punched out of the general DRAM pool. A memory map
        # normally declares one large DRAM range with these windows inside it, so without the subtraction
        # the window stays ADDRESS_DRAM and an ordinary random_addr can land in it by chance.
        fixed_ranges = tuple(self._mem.pma_fixed_ranges)
        carveout_holes = [(cr.start, cr.end) for cr in fixed_ranges]

        # Setting up DRAM, IO, Secure, and Reserved ranges
        for range in self._mem.dram_ranges:
            log.debug(f"Adding DRAM range: 0x{range.start:016x} - 0x{range.end:016x}")
            dram_spans: list[tuple[int, int]] = [(range.start, range.end)]
            for hole_lo, hole_hi in carveout_holes:
                next_spans: list[tuple[int, int]] = []
                for lo, hi in dram_spans:
                    next_spans.extend(_subtract_inclusive_span(lo, hi, hole_lo, hole_hi))
                dram_spans = next_spans
            for lo, hi in dram_spans:
                self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, lo, hi)

        for range in self._mem.io_ranges:
            log.debug(f"Adding IO range: 0x{range.start:016x} - 0x{range.end:016x}")
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_MMIO, range.start, range.end)

        for range in self._mem.secure_ranges:
            log.debug(f"Adding Secure range: 0x{range.start:016x} - 0x{range.end:016x}")
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_SECURE, range.start, range.end)

        custom_holes = [(cr.start, cr.end) for cr in self._mem.custom_ranges]
        for range in self._mem.reserved_ranges:
            # BaseMem.end is inclusive; reserve the complete declared range.
            # Exclude custom regions: they are allocatable as ADDRESS_CUSTOM and must not be pre-marked allocated
            # because a reserved IO window (e.g. preload) can overlap a custom probe buffer.
            span_lo, span_hi = range.start, range.end
            segments: list[tuple[int, int]] = [(span_lo, span_hi)] if span_lo <= span_hi else []
            for hole_lo, hole_hi in custom_holes:
                next_segments: list[tuple[int, int]] = []
                for lo, hi in segments:
                    next_segments.extend(_subtract_inclusive_span(lo, hi, hole_lo, hole_hi))
                segments = next_segments
            for lo, hi in segments:
                if lo <= hi:
                    self._physical_addr_space.reserve_memory(lo, hi)
                    self._linear_addr_space.reserve_memory(lo, hi)
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_RESERVED, range.start, range.end)

        self._custom_regions: dict[str, tuple[int, int]] = {}
        for region in self._mem.custom_ranges:
            log.debug(f"Adding Custom range '{region.name}': 0x{region.start:016x} - 0x{region.end:016x}")
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_CUSTOM, region.start, region.end)
            self._custom_regions[region.name] = (region.start, region.end)

        # Fixed windows are targetable by name but never part of the general DRAM pool (subtracted
        # above), so only an explicit custom_region= request places an address in one. Tag lookup lives
        # in PageTableRequestBuilder; the name map here backs AddressConstraint.custom_region.
        for region in fixed_ranges:
            log.debug(f"Adding fixed PMA window '{region.name}' (tags={list(region.tags)}): 0x{region.start:016x} - 0x{region.end:016x}")
            self._physical_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_CUSTOM, region.start, region.end)
            self._custom_regions[region.name] = (region.start, region.end)

        # Setting up Linear address space
        self._linear_addr_space.define_segment(RV.AddressQualifiers.ADDRESS_LINEAR, 0, pow(2, 57) - 1)

    def clone(self) -> "AddrGen":
        """Copy mutable allocation pools while sharing immutable declarations."""
        result = copy.copy(self)
        result._rng = self._rng.clone()
        result._linear_addr_space = self._linear_addr_space.clone(result._rng)
        result._physical_addr_space = self._physical_addr_space.clone(result._rng)
        result._space_pools = {space: pool.clone(result._rng) for space, pool in self._space_pools.items()}
        result._mirrored_pools = set(self._mirrored_pools)
        result._extra_excluded_regions = list(self._extra_excluded_regions)
        result.restricted_indices = defaultdict(int, self.restricted_indices)
        return result

    def bind_rng(self, rng: RandNum) -> None:
        """Point this generator and every pool at ``rng``."""
        self._rng = rng
        self._physical_addr_space.rng = rng
        self._linear_addr_space.rng = rng
        for pool in self._space_pools.values():
            pool.rng = rng

    def adopt_rng_stream(self, other: "AddrGen") -> None:
        """Resume draws from ``other``'s stream position on an independent RNG.

        Used when a speculative probe's draw is replayed onto a fresh branch: the branch
        continues the stream rather than repeating it, while ``other`` keeps its own RNG so
        an abandoned branch does not disturb the probe's subsequent draws.
        """
        self.bind_rng(other._rng.clone())

    def generate_address(self, constraint: AddressConstraint, space_key: "Optional[Space]" = None) -> int:
        """Generate a random address based on provided constraints and feature manager settings.

        :param constraint: AddressConstraint object specifying address requirements
        :type constraint: AddressConstraint
        :param space_key: optional per-space linear/GPA pool selector (None -> global)
        :return: Generated address value
        :rtype: int
        :raises AddrGenError: If address generation fails or restrictions are violated
        """

        # Resolve a named custom region into bounds + qualifier before validation.
        # This keeps the caller (generator.py) free from knowing about region bounds.
        # The resolution applies to a copy: the caller's constraint is its own, and a
        # retry loop that reuses it must not see bounds narrowed by an earlier draw.
        if constraint.custom_region is not None:
            if constraint.custom_region not in self._custom_regions:
                known = list(self._custom_regions.keys())
                raise AddrGenError(f"Custom region {constraint.custom_region!r} not found. Defined regions: {known}")
            start, end = self._custom_regions[constraint.custom_region]
            constraint = dataclasses.replace(
                constraint,
                start=start,
                end=end,
                qualifiers={RV.AddressQualifiers.ADDRESS_CUSTOM},
            )
            log.debug("Resolved custom region %r: [%#x, %#x]", constraint.custom_region, start, end)

        constraint.validate_constraints()
        log.debug("Generating address with constraints: %s", constraint)

        # Generate address
        address = None
        if constraint.type == RV.AddressType.MEMORY:
            address = self._draw_memory(constraint, space_key)
        elif constraint.type == RV.AddressType.PHYSICAL:
            excluded = constraint.exclude if constraint.exclude is not None else self._effective_excluded_regions()
            if not excluded:
                address = self._physical_addr_space.generate_address(constraint)
            else:
                # Candidates almost always clear the exclusions, and a ``dont_allocate``
                # draw mutates nothing, so probe the real pool until one does NOT: only
                # then is a private pool worth its copy-on-write clone of the touched
                # cluster's allocated intervals.  Rejected candidates are reserved in that
                # private pool -- never in the real allocator -- which makes the retry
                # exhaustive rather than a finite number of random probes.
                probe_space = self._physical_addr_space
                candidate_constraint = dataclasses.replace(constraint, dont_allocate=True)
                # Retiring a contiguous region removes it for good, so those retries are
                # bounded by the number of excluded regions and need no cap -- capping
                # them would report exhaustion while legal addresses remain. Only the
                # masked path, which retires one candidate at a time, needs a budget.
                masked_retries = 0
                while True:
                    try:
                        address = probe_space.generate_address(candidate_constraint)
                    except AddrGenError:
                        raise AddrGenError(f"No physical address avoids the excluded regions: {constraint}")
                    hit = self._hits_excluded_region(address, constraint.size, excluded)
                    if hit is None:
                        break
                    log.debug(f"Address 0x{address:x} matches excluded region {hit}, retrying")
                    if probe_space is self._physical_addr_space:
                        probe_space = self._physical_addr_space.clone(self._rng)
                    span = hit.interval()
                    if span is not None:
                        # Contiguous: the whole region is off limits, so retire it in one go.
                        # interval() is half-open; reserve_memory takes an inclusive end.
                        probe_space.reserve_memory(span[0], span[1] - 1)
                    else:
                        # Masked: the region matches windows scattered across the address
                        # space and cannot be reserved as an interval. Retire just this
                        # candidate so the next draw makes progress.
                        masked_retries += 1
                        if masked_retries >= _EXCLUSION_RETRY_LIMIT:
                            raise AddrGenError(f"No physical address avoids the excluded regions after {_EXCLUSION_RETRY_LIMIT} tries: {constraint}")
                        probe_space.reserve_memory(address, address + constraint.size - 1)
                    address = None
                if not constraint.dont_allocate:
                    self._physical_addr_space.reserve_memory(address, address + constraint.size - 1)
        else:
            # Probe before committing: a restriction rejection below must not leave the
            # candidate reserved, so draw without allocating and reserve once it passes.
            pool = self._address_space(constraint.type, space_key)[0]
            address = pool.generate_address(dataclasses.replace(constraint, dont_allocate=True))
        if address is None:
            raise AddrGenError(f"No address generated for constraints: {constraint}")

        # Check restrictions ; physical addresses are not restricted
        # If linear address, check restriction. If not restricted, address is generated and count is incremented in restricted_indices
        if constraint.type != RV.AddressType.PHYSICAL:
            index = common.bits(address, 15, 6)
            if self._check_linear_addr_restrictions(address):
                raise AddrGenError(f"Restricted address limit reached: {address:x}, index: {self.restricted_indices.get(index, 0):x}")
            log.debug(f"unique address: {address:x}, index: {index}")
            # A dont_allocate draw is a probe that reserves nothing, so it must not
            # spend quota either -- otherwise probing a layout exhausts the index.
            if self.limit_indices and not constraint.dont_allocate:
                self.restricted_indices[index] += 1
                log.warning(f"restricted_indices: {self.restricted_indices}")

        if constraint.type != RV.AddressType.PHYSICAL and not constraint.dont_allocate:
            self._commit_draw(constraint, address, space_key)
            self._mirror_linear_span(space_key if constraint.type == RV.AddressType.LINEAR else None, address, constraint.size)

        log.debug(f"Generated address: {address:x}")
        return address

    def _commit_draw(self, constraint: AddressConstraint, address: int, space_key: "Optional[Space]") -> None:
        """Reserve a validated non-physical draw in every pool it occupies."""
        end_address = address + constraint.size - 1
        if constraint.type == RV.AddressType.MEMORY:
            # A MEMORY draw is one address in two universes: it must hold in the
            # selected linear pool and in the global physical pool.
            self._address_space(RV.AddressType.LINEAR, space_key)[0].reserve_memory(address, end_address)
            self._physical_addr_space.reserve_memory(address, end_address)
        else:
            self._address_space(constraint.type, space_key)[0].reserve_memory(address, end_address)

    def physical_overlap(self, start_address: int, size: int) -> bool:
        """Return True if [start_address, start_address+size) overlaps an allocated physical span."""
        return self._physical_addr_space.check_overlap(start_address, start_address + size - 1)

    def linear_overlap(self, start_address: int, size: int, space_key: "Optional[Space]" = None) -> bool:
        """Return True if [start_address, start_address+size) is already taken in a LINEAR pool.

        ``space_key`` selects that space's own VA/GPA pool (None -> the global linear pool), the
        same way :meth:`reserve_memory` does. An identity (VA == PA) draw needs this: it draws in
        the PHYSICAL pool, which knows nothing of any space's VA reservations, so the value has to
        be checked against the pool it will also occupy before it is committed."""
        return self._address_space(RV.AddressType.LINEAR, space_key)[0].check_overlap(start_address, start_address + size - 1)

    def allocated_physical_intervals(self) -> list:
        """Return every allocated physical [start, end) interval (cluster-boundary splits included)."""
        intervals = []
        for cluster in self._physical_addr_space.clusters.values():
            for start, end in cluster.allocated_addresses:
                intervals.append((start, end + 1))  # allocated spans store inclusive ends
        return intervals

    def allocated_linear_intervals(self) -> list:
        """Return every allocated linear [start, end) interval, across the global linear
        pool and every per-space VA/GPA pool (cluster-boundary splits included).

        Page VAs now live in per-space pools, so a caller reserving these back into its
        own space (RiescueD's read-back) must see the union; overlapping spans from
        different spaces are harmless (reserve_memory is idempotent on overlap)."""
        intervals = []
        for pool in [self._linear_addr_space, *self._space_pools.values()]:
            for cluster in pool.clusters.values():
                for start, end in cluster.allocated_addresses:
                    intervals.append((start, end + 1))  # allocated spans store inclusive ends
        return intervals

    def exclude_region(self, region: ExcludedRegion) -> None:
        """Exclude a (typically masked) region from future physical address generation."""
        self._extra_excluded_regions.append(region)

    def _effective_excluded_regions(self) -> list:
        """Constructor exclusions plus late-registered carve-outs."""
        if not self._extra_excluded_regions:
            return self._excluded_regions
        return list(self._excluded_regions) + self._extra_excluded_regions

    def _hits_excluded_region(self, address: int, size: int, excluded: Optional[Sequence[ExcludedRegion]] = None):
        """Return the first excluded region matching [address, address+size), or None."""
        regions = self._effective_excluded_regions() if excluded is None else excluded
        for region in regions:
            if region.overlaps(address, size):
                return region
        return None

    def make_space_pool(self, space_key: "Space", mirror_global: bool = True) -> AddressSpace:
        """Create, register, and return a per-space VA/GPA pool.

        The pool is a copy of the global linear pool taken at call time, so it
        inherits the same linear segment plus every reservation made so far
        (memory reserved ranges, page-table roots, declared reserved spans); it
        then diverges as this space's own pages are placed. The copy shares the one
        real RNG (a deep copy would otherwise clone RNG state and make every space
        draw the same sequence).

        ``mirror_global`` keeps "unmapped" meaningful across pools (see
        :meth:`_mirror_linear_span`); a G-stage (GPA) pool passes False -- GPAs are
        not VAs and its pool stays isolated.
        """
        pool = self._linear_addr_space.clone(self._rng)
        self._space_pools[space_key] = pool
        if mirror_global:
            self._mirrored_pools.add(space_key)
        else:
            # Replacing a pool replaces its mirroring policy too; a stale flag would
            # keep mirroring a pool the caller just declared isolated.
            self._mirrored_pools.discard(space_key)
        return pool

    def _mirror_linear_span(self, space_key: "Optional[Space]", start_address: int, size: int) -> None:
        """Mirror a placed linear span so the global pool means "unmapped anywhere".

        A span placed in a mirrored space pool is reserved in the global linear pool
        too (a bare draw must avoid every mapped VA); a span placed globally is
        reserved in every mirrored space pool (a space draw must avoid every bare VA).
        Mirroring tolerates overlap -- distinct spaces may share a VA.
        """
        end_address = start_address + size - 1
        if space_key is None:
            for key in self._mirrored_pools:
                self._space_pools[key].reserve_memory(start_address, end_address)
        elif space_key in self._mirrored_pools:
            self._linear_addr_space.reserve_memory(start_address, end_address)

    def reserve_memory(self, address_type: RV.AddressType, start_address: int, size: int, interesting_address: bool = False, space_key=None):
        """
        reserve_memory() is used to allocate memory for fixed addresses into a given
        address space. ``space_key`` selects a per-space linear/GPA pool; None uses the
        global linear pool (physical is always global).
        """
        if size <= 0:
            raise AddrGenError(f"reserve_memory requires a positive size, got {size}")
        end_address = start_address + size - 1
        addr_space = self._address_space(address_type, space_key)

        log.debug(f"Call to reserving memory: {start_address:x} - {end_address:x}")

        for _ in addr_space:
            _.reserve_memory(start_address, end_address, interesting_address)

        if address_type != RV.AddressType.PHYSICAL:
            self._mirror_linear_span(space_key, start_address, size)

    def _address_space(self, address_type: RV.AddressType, space_key=None) -> list[AddressSpace]:
        """
        Help method to decide which address space(s) to call. Returns a list.

        ``space_key``, when set, selects that space's own linear/GPA pool for the
        linear side; the physical side is always the single global pool.
        """
        linear = self._space_pools[space_key] if space_key is not None else self._linear_addr_space
        addr_space = []
        if address_type == RV.AddressType.LINEAR:
            addr_space = [linear]
        elif address_type == RV.AddressType.PHYSICAL:
            addr_space = [self._physical_addr_space]
        elif address_type == RV.AddressType.MEMORY:
            addr_space = [linear]
            addr_space.append(self._physical_addr_space)
        else:
            raise TypeError(f"Unknown address type {address_type}")

        return addr_space

    def _draw_memory(self, constraint: AddressConstraint, space_key: "Optional[Space]" = None) -> int:
        """
        Find an address that is compatible with both linear and physical address spaces.
        Reserves nothing; the caller commits once restrictions have been checked.

        A MEMORY address is drawn from the linear pool -- the selected space's own pool
        when ``space_key`` names one -- and must then hold in the physical pool too: it
        has to name real physical memory, be free there, and clear the exclusions this
        request asks for.

        Assumes that constraint.type is MEMORY

        :param constraint: AddressConstraint object specifying address requirements
        :type constraint: AddressConstraint
        :return: Generated address value
        :rtype: int
        :raises AddrGenError: If address generation fails or restrictions are violated
        """
        size = constraint.size
        try_times = 32
        linear = self._address_space(RV.AddressType.LINEAR, space_key)[0]
        excluded = constraint.exclude if constraint.exclude is not None else self._effective_excluded_regions()
        # The linear pool knows only ADDRESS_LINEAR, so the qualifier that decides
        # physical membership is the request's own -- defaulting, as a PHYSICAL draw
        # would, to DRAM: a MEMORY address names memory, not a device window.
        backing = constraint.qualifiers or {RV.AddressQualifiers.ADDRESS_DRAM}
        candidate_constraint = dataclasses.replace(constraint, dont_allocate=True)
        for _ in range(try_times):
            address = linear.generate_address(constraint=candidate_constraint)

            if not self._physical_addr_space.declares_span(address, address + size - 1, backing):
                log.debug(f"MEMORY candidate 0x{address:x} is not backed by declared physical memory")
                continue
            if not self._physical_addr_space.check_overlap(address, address + size - 1) and self._hits_excluded_region(address, size, excluded) is None:
                return address
        raise AddrGenError(f"Failed to generate physical address with constraints: {constraint}")

    def _check_linear_addr_restrictions(self, address: int) -> bool:
        """Return whether an enabled linear-address restriction rejects ``address``."""
        if self.limit_indices:
            index = common.bits(address, 15, 6)
            # .get, not [], so a probe does not leave a zero behind in the tally.
            if self.restricted_indices.get(index, 0) >= 4:
                return True
        if self.limit_way_predictor_multihit:
            # va[26:16] ^ va[37:27] ^ va[48:38] ^ {va[18:16], va[56], va[19], va[54], va[21], va[52:49]
            way_p_hash = (
                common.bits(address, 26, 16)
                ^ common.bits(address, 37, 27)
                ^ common.bits(address, 48, 38)
                ^ (
                    (common.bits(address, 18, 16) << 8)
                    | (common.bitn(address, 56) << 7)
                    | (common.bitn(address, 19) << 6)
                    | (common.bitn(address, 54) << 5)
                    | (common.bitn(address, 21) << 4)
                    | common.bits(address, 52, 49)
                )
            )
            if way_p_hash == 0:
                return True

        return False
