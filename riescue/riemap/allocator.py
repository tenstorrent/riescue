# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Allocation: turning constraints into concrete, non-overlapping addresses.

:class:`BatchAllocationStrategy` is the order-independent **solver**. Given the whole
set of :class:`AllocRequest` s (one per declaration :class:`~riescue.riemap.request.Page`,
constructed by the builder after its geometry pass) and any
:class:`~riescue.riemap.request.MemoryRegion` s, :meth:`~BatchAllocationStrategy.solve`
places every region, resolves every relation (targets before dependents), and draws
every free address, guaranteeing all constraints hold. The order requests were added
is *not* significant: any order yields a valid result (the concrete addresses for a
given seed may differ between orders -- that is not part of the contract). Draw order
among ready candidates is instead driven by each request's ``seq`` (construction order,
assigned by the builder when a page is added) -- the only thing that makes the pick
order deterministic without names or ids.

``AllocRequest`` is internal engine plumbing, one per declaration ``Page``. It is
never exposed to a consumer; consumers read through ``AllocationResult``, keyed by
the declaration ``Page`` object itself.

The strategy is purely geometric -- it knows nothing about satp modes,
canonicalization, or g-stage. Callers (the builder, RiescueD, the JSON frontend)
prepare geometric constraints and post-process the result.
"""

import bisect
import dataclasses
import heapq
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Hashable, List, Optional, Tuple

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum

from riescue.riemap.addrgen import AddrGen
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.addrgen.types import AddressConstraint
from riescue.riemap.masked_bases import MaskedBases, choose_in_windows, free_windows
from riescue.riemap.request import AddrSpec, DerivedFrom, MemoryRegion, OffsetFrom, Page, SameAs, Space

log = logging.getLogger(__name__)

_DEFAULT_ALIGN_MASK = 0xFFFFFFFFFFFFF000
_FULL_MASK = 0xFFFFFFFFFFFFFFFF
_RESERVED_SPAN_OWNER = "reserved span"


class _SearchBudgetExhausted(Exception):
    pass


class ClaimKind(Enum):
    """Why a resolved base occupies an address-domain interval."""

    BACKING = "backing"
    ADDRESS = "address"
    COVERAGE = "coverage"
    STRUCTURAL = "structural"
    RESERVATION = "reservation"


@dataclass(frozen=True)
class SpanClaim:
    """One half-open span based at a placement's resolved address.

    A backing claim consumes bytes in the physical allocator. A coverage claim
    occupies a VA/GPA interval but consumes no physical memory. ``share_key`` is
    the only overlap authorization; relation ancestry alone grants none.
    """

    addr_type: RV.AddressType
    space: Optional[Space]
    size: int
    kind: ClaimKind
    share_key: Optional[Hashable] = None
    all_spaces: bool = False
    allow_partial_share: bool = False
    # Fixed leaf aliases may name a subrange of one declared physical/GPA
    # mapping.  This is narrower than ``allow_partial_share`` (used by
    # relation families): only containment is authorized.
    allow_contained_share: bool = False
    #: Signed distance from the resolved address to the START of the span. Negative when the
    #: span extends BELOW the placement -- an aligned window the placement sits
    #: at an offset inside, e.g. a frame slotted into a shared g-stage leaf table covers the
    #: whole GPA window that table spans, not just the bytes above its own slot.
    offset: int = 0
    #: When set, expand ``[value, value + size)`` to every aligned granule it touches:
    #: ``align_down(value, granule)`` .. ``align_up(value + size, granule)``. ``offset`` is
    #: ignored in that mode -- the containing span is computed from the placement itself.
    #: Must be a power of two.
    granule: Optional[int] = None

    def __post_init__(self) -> None:
        if self.granule is not None:
            if self.granule <= 0 or self.granule & (self.granule - 1):
                raise ValueError(f"SpanClaim.granule must be a positive power of two, got 0x{self.granule:x}")

    def span(self, value: int) -> Tuple[int, int]:
        """The half-open ``[start, end)`` this claim occupies for a placement at ``value``."""
        if self.granule is not None:
            start = value & ~(self.granule - 1)
            end_addr = value + self.size
            end = (end_addr + self.granule - 1) & ~(self.granule - 1)
            if end <= start:
                end = start + self.granule
            return start, end
        start = value + self.offset
        return start, start + self.size


@dataclass
class PlacementRequest:
    """One base address plus every domain span that base claims.

    Built by the builder after its geometry pass folds pagesize alignment / bit width
    into ``addr``. ``size`` is strictly the backing footprint; translated
    leaf/exclusive spans belong in ``claims``.
    """

    page: Page
    addr_type: RV.AddressType
    size: int
    addr: AddrSpec
    validation_size: Optional[int] = None
    # Byte alignment this placement must satisfy beyond its own pagesize. A destination
    # reached through a superpage leaf PTE is the case: its PPN has to be aligned to the
    # SOURCE page's size, which its own (possibly 4 KiB) pagesize cannot express. None
    # keeps plain pagesize alignment.
    align: Optional[int] = None
    # Per-space linear/GPA pool this request's linear draw comes from (None -> the
    # global linear pool). A PHYSICAL request carrying a space_key is an identity page:
    # it draws in the global physical pool but its value must also be reserved in that
    # linear space (dual reserve), so a free VA draw there cannot collide.
    space_key: Optional[Space] = None
    # Further linear spaces this PHYSICAL value occupies, beyond ``space_key``. A
    # consumer-pinned page-table ROOT frame is the case: it is declared in the
    # physical (or GPA) domain, but its span must also stay free of conflicting
    # coverage in the space it roots. Same treatment as ``space_key``: checked
    # before the draw commits and reserved after.
    extra_linear_spaces: Tuple[Space, ...] = ()
    # Construction order (assigned when the page was added to the builder), used as
    # the stable tiebreaker among equally-ready candidates.
    seq: int = 0
    place_first: bool = False
    allocated: Optional[int] = None
    claims: Tuple[SpanClaim, ...] = ()

    def linear_pools(self) -> Tuple[Space, ...]:
        """Every linear pool this request's value occupies *besides* the one it draws from.

        Empty for a LINEAR request -- that draw already reserves itself in its own pool. A
        PHYSICAL request draws in the global physical pool, so each pool listed here has to be
        cleared before the value is committed and reserved once it is (the identity contract)."""
        if self.addr_type != RV.AddressType.PHYSICAL:
            return ()
        pools = [] if self.space_key is None else [self.space_key]
        pools.extend(space for space in self.extra_linear_spaces if space not in pools)
        return tuple(pools)

    def span_claims(self) -> Tuple[SpanClaim, ...]:
        """Return explicit claims or derive the request's standard claims."""
        if self.claims:
            return self.claims
        coverage_size = max(
            self.size,
            self.validation_size or 0,
            RV.RiscvPageSizes.memory(self.page.pagesize),
        )
        relation = self.addr.relation
        default_share = relation.target if relation is not None else self.page
        own_kind = ClaimKind.BACKING if self.addr_type == RV.AddressType.PHYSICAL else ClaimKind.COVERAGE
        own_size = self.size if own_kind is ClaimKind.BACKING else coverage_size
        claims = [
            SpanClaim(
                addr_type=self.addr_type,
                space=self.space_key,
                size=own_size,
                kind=own_kind,
                share_key=default_share,
                allow_partial_share=relation is not None,
            )
        ]
        if self.addr_type == RV.AddressType.PHYSICAL and coverage_size > self.size:
            claims.append(
                SpanClaim(
                    addr_type=RV.AddressType.PHYSICAL,
                    space=None,
                    size=coverage_size,
                    kind=ClaimKind.COVERAGE,
                    share_key=default_share,
                    allow_partial_share=relation is not None,
                )
            )
        claims.extend(
            SpanClaim(
                addr_type=RV.AddressType.LINEAR,
                space=space,
                size=coverage_size,
                kind=ClaimKind.COVERAGE,
                share_key=default_share,
                allow_partial_share=relation is not None,
            )
            for space in self.linear_pools()
        )
        return tuple(claims)


# Concise name used by the builder and direct solver tests.
AllocRequest = PlacementRequest


def _effective_and_mask(spec: AddrSpec) -> int:
    # Callers write alignment masks as ``~(size - 1)``, which is negative in Python
    # because there is no width to truncate against. Fold into the 64-bit address
    # domain here so downstream constraint validation sees a real address mask.
    mask = spec.and_mask if spec.and_mask is not None else _DEFAULT_ALIGN_MASK
    return mask & _FULL_MASK


def _has_sparse_domain(spec: AddrSpec) -> bool:
    """Whether ``spec`` admits only a scattered subset of its address width.

    An ordinary alignment mask clears a contiguous run of low bits, so its legal bases
    are every aligned address -- densely reachable, and a greedy neighbour that takes
    one leaves the rest. A mask that clears bits *inside* the width (index-field pinning,
    a consumer's hand-written mask) instead admits a handful of scattered bases, any one
    of which an unrelated greedy placement can be the only one to take.
    """
    bits = spec.bits if spec.bits is not None else 64
    width_mask = (1 << bits) - 1
    cleared = (_effective_and_mask(spec) & width_mask) ^ width_mask
    # A contiguous low run satisfies cleared & (cleared + 1) == 0.
    return bool(cleared & (cleared + 1))


@dataclass
class SolveResult:
    """Addresses chosen by :meth:`BatchAllocationStrategy.solve`."""

    addresses: Dict[Page, int] = field(default_factory=dict)
    region_bases: Dict[MemoryRegion, int] = field(default_factory=dict)

    def address(self, page: Page) -> int:
        return self.addresses[page]


@dataclass
class _Reservation:
    """One span the solver has placed, tagged with the request/region that produced it.

    Recorded for everything the solver reserves so a relation-derived span can be
    checked for collision against unrelated owners.
    ``space`` is the conflict key: the per-space pool for a linear span, ``None`` for a
    physical one (physical is always the single global pool). ``owner`` is the
    declaration object (a ``Page`` or ``MemoryRegion``) that placed this span, or the
    ``_RESERVED_SPAN_OWNER`` sentinel for a caller-reserved span.
    """

    owner: Any
    addr_type: RV.AddressType
    space: Optional[Space]
    start: int
    end: int  # exclusive
    kind: ClaimKind = ClaimKind.RESERVATION
    share_key: Optional[Hashable] = None
    allow_contained_share: bool = False
    # Caller-reserved spans live in the global pool, which every per-space pool
    # inherits, so they conflict in any space of their addr_type.
    all_spaces: bool = False


class _SpanIndex:
    """Spans of one conflict key, bucketed on a fixed address grid.

    A query walks only the cells its own span crosses. This bounds the candidate set for
    common page-sized queries even when a solve contains thousands of reservations.

    A span wider than :attr:`_WIDE_CELLS` cells (a region, a caller-reserved DRAM span)
    is kept in ``_wide`` and returned to every query instead -- registering a 1 GiB span
    in 512 cells costs more than scanning the few spans that big. Callers still apply the
    exact ``lo < end and start < hi`` test, so this is a filter, not a promise: a cell may
    hand back a neighbour that does not actually overlap.
    """

    _CELL_BITS = 16  # 64 KiB -- dense 4 KiB placements fill a 2 MiB cell with hundreds of neighbours
    _WIDE_CELLS = 8

    def __init__(self) -> None:
        self._cells: Dict[int, List[_Reservation]] = {}
        self._wide: List[_Reservation] = []

    def add(self, reservation: _Reservation) -> None:
        first = reservation.start >> self._CELL_BITS
        last = (max(reservation.end, reservation.start + 1) - 1) >> self._CELL_BITS
        if last - first >= self._WIDE_CELLS:
            self._wide.append(reservation)
            return
        cells = self._cells
        for cell in range(first, last + 1):
            cells.setdefault(cell, []).append(reservation)

    def overlapping(self, lo: int, hi: int) -> List[_Reservation]:
        """Every span that may overlap ``[lo, hi)``."""
        found = list(self._wide)
        first = lo >> self._CELL_BITS
        last = (max(hi, lo + 1) - 1) >> self._CELL_BITS
        cells = self._cells
        if last - first >= self._WIDE_CELLS:
            # Iterating every cell in a root-slot-sized query is proportional
            # to the address-space width (SV57 can cover 2**32 cells). Scan
            # the populated cells instead; there are at most as many as the
            # placed reservations.
            seen = {id(reservation) for reservation in found}
            for spans in cells.values():
                for reservation in spans:
                    marker = id(reservation)
                    if marker not in seen:
                        seen.add(marker)
                        found.append(reservation)
            return found
        if first == last:
            found.extend(cells.get(first, ()))
            return found
        # A span registered in several of the cells this query crosses would be returned
        # once per cell; callers tolerate duplicates but there is no reason to pay for them.
        seen = set()
        for cell in range(first, last + 1):
            for reservation in cells.get(cell, ()):
                marker = id(reservation)
                if marker not in seen:
                    seen.add(marker)
                    found.append(reservation)
        return found

    def clone(self) -> "_SpanIndex":
        cloned = _SpanIndex()
        cloned._cells = {cell: list(spans) for cell, spans in self._cells.items()}
        cloned._wide = list(self._wide)
        return cloned


class _ReservationLedger:
    """Owner-tagged placed spans, indexed by their conflict key so a relation-derived
    value is checked only against reservations that can actually collide with it.

    A span conflicts with a query ``(addr_type, space)`` iff it has the same ``addr_type``
    and either the same conflict ``space`` or ``all_spaces`` (a caller-reserved span, which
    lives in the global pool every per-space pool inherits). The flat scan this replaces
    was O(reservations) per check -- quadratic once PT-node pinning grows the ledger.
    ``all_spaces`` implies ``space is None`` (only the reserved-span seed sets it), so the
    two buckets never overlap. Within a key, :class:`_SpanIndex` narrows the scan by address.
    """

    def __init__(self) -> None:
        self._by_type_space: Dict[Tuple[RV.AddressType, Optional[Space]], _SpanIndex] = {}
        self._all_spaces_by_type: Dict[RV.AddressType, _SpanIndex] = {}

    def add(self, reservation: _Reservation) -> None:
        if reservation.all_spaces:
            index = self._all_spaces_by_type.setdefault(reservation.addr_type, _SpanIndex())
        else:
            index = self._by_type_space.setdefault((reservation.addr_type, reservation.space), _SpanIndex())
        index.add(reservation)

    def overlapping(self, addr_type: RV.AddressType, space: Optional[Space], lo: int, hi: int) -> List[_Reservation]:
        """Every reservation that may collide with ``[lo, hi)`` in ``(addr_type, space)``."""
        same_space = self._by_type_space.get((addr_type, space))
        all_spaces = self._all_spaces_by_type.get(addr_type)
        if all_spaces is None:
            return same_space.overlapping(lo, hi) if same_space is not None else []
        if same_space is None:
            return all_spaces.overlapping(lo, hi)
        return same_space.overlapping(lo, hi) + all_spaces.overlapping(lo, hi)

    def clone(self) -> "_ReservationLedger":
        cloned = _ReservationLedger()
        cloned._by_type_space = {key: index.clone() for key, index in self._by_type_space.items()}
        cloned._all_spaces_by_type = {key: index.clone() for key, index in self._all_spaces_by_type.items()}
        return cloned


# -- strategy -------------------------------------------------------------


class AllocationStrategy(ABC):
    """Pluggable placement policy: resolve a whole batch of requests at once."""

    @abstractmethod
    def solve(
        self,
        requests: List[AllocRequest],
        regions: List[MemoryRegion],
        addrgen: AddrGen,
        rng: RandNum,
        reserved_spans: Optional[List[Tuple[RV.AddressType, int, int]]] = None,
    ) -> "SolveResult":
        """Place every region/request, satisfying all constraints; order-independent.

        ``reserved_spans`` are (addr_type, start, size) spans the caller already
        reserved on the pools; they are off-limits to relation-derived values.
        """

    def solve_in_place(
        self,
        requests: List[AllocRequest],
        regions: List[MemoryRegion],
        addrgen: AddrGen,
        rng: RandNum,
        reserved_spans: Optional[List[Tuple[RV.AddressType, int, int]]] = None,
    ) -> "SolveResult":
        """Solve against caller-owned disposable state.

        Strategies without an optimized implementation retain transactional behavior.
        """
        return self.solve(requests, regions, addrgen, rng, reserved_spans)


class BatchAllocationStrategy(AllocationStrategy):
    """The collect-everything-then-place-at-once policy and order-independent solver."""

    _BACKTRACK_BUDGET = 8192
    # A region candidate is drawn from the exact set of free masked bases, so a
    # _claims_fit rejection means an external (structural/backing) conflict rather
    # than a bad guess. A handful is expected; a flood is a real conflict, and the
    # legal set can be large enough that "retry until it runs out" is no bound.
    _REGION_REJECTION_BUDGET = 64

    @staticmethod
    def _clone_addrgen(addrgen: AddrGen) -> AddrGen:
        """Clone mutable pools while preserving identity-based Space keys."""
        return addrgen.clone()

    # ---- order-independent solver --------------------------

    def solve(
        self,
        requests: List[AllocRequest],
        regions: List[MemoryRegion],
        addrgen: AddrGen,
        rng: RandNum,
        reserved_spans: Optional[List[Tuple[RV.AddressType, int, int]]] = None,
    ) -> SolveResult:
        """Place all regions and requests, satisfying every constraint.

        Independent of the order ``requests`` were supplied in.
        """
        # AddrGen has no public unreserve operation.  Solve against a copy and replay the
        # complete answer only after search succeeds; neither a rejected branch nor a
        # failed solve can leak reservations (or partially update AllocRequest.allocated).
        # Space declarations are identity-based public keys.  Preserve those keys while
        # cloning the mutable pools; a plain deepcopy invents new Space objects and makes
        # every request's original ``space_key`` miss the cloned ``_space_pools`` dict.
        working_addrgen = self._clone_addrgen(addrgen)
        # AddrGen and the allocator intentionally share one RandNum stream.
        working_rng = working_addrgen._rng
        result = self._solve_transaction(requests, regions, working_addrgen, working_rng, reserved_spans)
        self._commit_solution(requests, regions, result, addrgen)
        rng.rand.setstate(working_rng.rand.getstate())
        for req in requests:
            req.allocated = result.addresses[req.page]
        return result

    def solve_in_place(
        self,
        requests: List[AllocRequest],
        regions: List[MemoryRegion],
        addrgen: AddrGen,
        rng: RandNum,
        reserved_spans: Optional[List[Tuple[RV.AddressType, int, int]]] = None,
    ) -> SolveResult:
        """Solve directly on state already protected by an outer transaction."""
        result = self._solve_transaction(
            requests,
            regions,
            addrgen,
            rng,
            reserved_spans,
        )
        # Search branches use cloned AddrGen pools. Replay the winning answer onto
        # the caller-owned transaction so subsequent planning sees every reservation,
        # not only pins made before the first branch.
        self._commit_solution(requests, regions, result, addrgen)
        for req in requests:
            req.allocated = result.addresses[req.page]
        return result

    def _solve_transaction(
        self,
        requests: List[AllocRequest],
        regions: List[MemoryRegion],
        addrgen: AddrGen,
        rng: RandNum,
        reserved_spans: Optional[List[Tuple[RV.AddressType, int, int]]],
    ) -> SolveResult:
        result = SolveResult()
        self._last_claim_conflict = ""
        # A region listed twice is one declaration: MemoryRegion compares by identity, so
        # placing it once per listing would consume its window twice.
        regions = self._unique_regions(regions)
        region_taken: Dict[MemoryRegion, List[Tuple[int, int]]] = {r: [] for r in regions}
        # Every span placed so far, owner-tagged, so a relation-derived value can be
        # rejected when it is placed on an unrelated allocation (Phase 2). Caller-reserved
        # spans seed the ledger so derived values cannot be placed on them either. The ledger
        # indexes by conflict key so an overlap check scans only collidable spans, and
        reservations = _ReservationLedger()
        for addr_type, start, size in reserved_spans or []:
            reservations.add(_Reservation(owner=_RESERVED_SPAN_OWNER, addr_type=addr_type, space=None, start=start, end=start + size, all_spaces=True))
        log.info("Solving %d page allocations across %d region(s)", len(requests), len(regions))

        by_page: Dict[Page, AllocRequest] = {r.page: r for r in requests}
        resolved: Dict[Page, int] = result.addresses

        # Phase 1: reserve every pinned (exact, non-relation) address before any
        # free draw -- including a floating region -- can steal a pinned slot.
        for req in requests:
            if req.addr.exact is not None and req.addr.relation is None:
                if not self._claims_fit(
                    req,
                    req.addr.exact,
                    addrgen,
                    reservations,
                    allow_reserved=True,
                ):
                    raise AddrGenError(
                        f"exact placement 0x{req.addr.exact:x} has an " f"incompatible backing or coverage claim for " f"{req.page!r}: {req.span_claims()!r}; " f"{self._last_claim_conflict}"
                    )
                self._commit_claims(
                    req,
                    req.addr.exact,
                    addrgen,
                    reservations,
                )
                resolved[req.page] = req.addr.exact

        result.region_bases = self._place_regions(regions, addrgen, reservations)

        # Region members are selected from the free bases of an already-known window
        # (floating after ``_place_regions``, or fixed/adopted at a pinned base). They
        # do not re-draw through AddrGen's DRAM/MMIO segments -- that would reject a
        # legal member whose region sits outside those segments, the same way an
        # explicit in-window placement is never overridden.
        region_members = [r for r in requests if r.page not in resolved and r.addr.region is not None]
        region_members.sort(key=lambda r: (-r.size, r.seq))
        for req in region_members:
            region = req.addr.region
            if region not in result.region_bases:
                raise AddrGenError("request targets unknown region")
            taken = region_taken[region]
            # A rejected base is unusable for this member only; it is not occupied
            # memory, so it must not cost the region the span it would have filled.
            # Granule conflicts retire the whole containing span so choose_in_windows
            # cannot resample dozens of offsets inside one already-conflicting granule.
            rejected: List[int] = []
            blocked: List[Tuple[int, int]] = []
            rejected_sigs: set = set()
            attempts = 0
            while True:
                occupied = list(taken)
                for span in blocked:
                    bisect.insort(occupied, span)
                value = self._place_in_region(
                    req,
                    result.region_bases[region],
                    region,
                    occupied,
                    rejected,
                    rng,
                )
                if self._claims_fit(
                    req,
                    value,
                    addrgen,
                    reservations,
                    allowed_region=region,
                ):
                    break
                attempts += 1
                signatures = self._granule_claim_signatures({req.page: value}, {req.page: req})
                if signatures:
                    new_sigs = [sig for sig in signatures if sig not in rejected_sigs]
                    if not new_sigs:
                        raise AddrGenError(
                            f"region granule search made no progress for member " f"(size 0x{req.size:x}) at 0x{value:x}: repeated claim " f"signatures {signatures!r}; {self._last_claim_conflict}"
                        )
                    rejected_sigs.update(new_sigs)
                    for claim in req.span_claims():
                        if claim.granule is None:
                            continue
                        start, end = claim.span(value)
                        bisect.insort(blocked, (start, end))
                else:
                    rejected.append(value)
                if attempts > self._REGION_REJECTION_BUDGET:
                    conflict = f": {self._last_claim_conflict}" if self._last_claim_conflict else ""
                    raise AddrGenError(
                        f"region member (size 0x{req.size:x}, pagesize={req.page.pagesize.name}) had "
                        f"{attempts} candidates rejected in region (base 0x{result.region_bases[region]:x}, "
                        f"size 0x{region.size:x}){conflict}"
                    )
            bisect.insort(taken, (value, value + req.size))
            resolved[req.page] = value
            self._commit_region_claims(req, value, region, addrgen, reservations)

        # Resolve every forced chain whose root is already pinned (or region-placed)
        # before any free variable gets a chance to shadow it.
        self._resolve_ready_forced(requests, resolved, by_page, addrgen, reservations)

        children: Dict[Page, List[AllocRequest]] = {}
        for r in requests:
            if r.addr.relation is not None:
                children.setdefault(r.addr.relation.target, []).append(r)
        variable_keys = {req.page: self._variable_key(req, children) for req in requests}
        heap_order = {req.page: index for index, req in enumerate(requests)}
        # A sparse domain has few reachable bases and no way to signal that to the MRV
        # estimate, which cannot see memory-segment bounds. While one is unresolved, every
        # other root stays a rollback point instead of committing greedily on top of the
        # only base the sparse request could have used.
        sparse_pages = {req.page for req in requests if _has_sparse_domain(req.addr)}
        rollback_roots: set[Page] = set()
        for req in requests:
            if not (isinstance(req.addr.relation, DerivedFrom) and req.addr.relation.random_mask):
                continue
            page = req.addr.relation.target
            seen: set[Page] = set()
            while page not in seen:
                seen.add(page)
                rollback_roots.add(page)
                parent = by_page[page].addr.relation
                if parent is None:
                    break
                page = parent.target
        waiting: Dict[Page, List[AllocRequest]] = {}
        ready = []
        for req in requests:
            relation = req.addr.relation
            if relation is None and req.addr.exact is None and req.addr.region is None:
                ready.append((variable_keys[req.page], heap_order[req.page], req))
            elif isinstance(relation, DerivedFrom) and relation.random_mask:
                if relation.target in resolved:
                    ready.append((variable_keys[req.page], heap_order[req.page], req))
                else:
                    waiting.setdefault(relation.target, []).append(req)
        heapq.heapify(ready)

        budget = {"backtracks": self._BACKTRACK_BUDGET}
        self._last_dead_end = None
        previous_recursion_limit = sys.getrecursionlimit()
        required_recursion_limit = len(requests) + 512
        try:
            if required_recursion_limit > previous_recursion_limit:
                sys.setrecursionlimit(required_recursion_limit)
            solved = self._search(requests, by_page, children, variable_keys, heap_order, rollback_roots, sparse_pages, waiting, ready, resolved, addrgen, reservations, budget)
        except _SearchBudgetExhausted as exc:
            detail = f": {self._last_dead_end}" if self._last_dead_end else ""
            raise AddrGenError(f"allocation search budget exhausted after " f"{self._BACKTRACK_BUDGET} backtracks{detail}") from exc
        finally:
            if required_recursion_limit > previous_recursion_limit:
                sys.setrecursionlimit(previous_recursion_limit)
        if solved is None:
            unresolved = len(requests) - len(resolved)
            detail = f": {self._last_dead_end}" if self._last_dead_end else ""
            raise AddrGenError(f"unsatisfiable allocation constraints ({unresolved} unresolved page(s)){detail}")
        resolved, winning_addrgen, _reservations = solved
        rng.rand.setstate(winning_addrgen._rng.rand.getstate())
        result.addresses.update(resolved)
        return result

    @staticmethod
    def _unique_regions(regions: List[MemoryRegion]) -> List[MemoryRegion]:
        """``regions`` with repeated listings of the same declaration collapsed."""
        return list(dict.fromkeys(regions))

    def _commit_solution(self, requests: List[AllocRequest], regions: List[MemoryRegion], result: SolveResult, addrgen: AddrGen) -> None:
        """Replay a complete solution through AddrGen's public reservation API."""
        for region in self._unique_regions(regions):
            if region.base is None:
                addrgen.reserve_memory(
                    RV.AddressType.PHYSICAL,
                    result.region_bases[region],
                    region.size,
                )
        region_pages = {r.page for r in requests if r.addr.region is not None and r.addr.region.base is None}
        for req in requests:
            value = result.addresses[req.page]
            for claim in req.span_claims():
                if (req.page in region_pages and claim.kind is ClaimKind.BACKING) or (claim.kind is ClaimKind.COVERAGE and claim.addr_type == RV.AddressType.PHYSICAL):
                    continue
                start, end = claim.span(value)
                addrgen.reserve_memory(
                    claim.addr_type,
                    start,
                    end - start,
                    space_key=claim.space,
                )

    @staticmethod
    def _is_fixed_relation(req: AllocRequest) -> bool:
        rel = req.addr.relation
        return rel is not None and not (isinstance(rel, DerivedFrom) and rel.random_mask)

    def _relation_value(self, req: AllocRequest, base: int) -> int:
        rel = req.addr.relation
        if isinstance(rel, SameAs):
            return base
        if isinstance(rel, OffsetFrom):
            return base + rel.delta
        if isinstance(rel, DerivedFrom) and not rel.random_mask:
            return ((base & rel.and_mask) ^ rel.not_mask) | rel.or_mask
        raise AddrGenError(f"relation on page is not fixed ({type(rel).__name__})")

    @staticmethod
    def _is_exact_alias(req: AllocRequest, by_page: Dict[Page, AllocRequest]) -> bool:
        """Return whether ``req`` is a SameAs chain rooted at an exact page."""
        current = req
        seen: set[Page] = set()
        while current.page not in seen:
            seen.add(current.page)
            if current.addr.exact is not None:
                return True
            if not isinstance(current.addr.relation, SameAs):
                return False
            current = by_page.get(current.addr.relation.target)
            if current is None:
                return False
        return False

    def _resolve_ready_forced(self, requests, resolved, by_page, addrgen, reservations) -> None:
        """Commit forced descendants of roots which were placed before search."""
        progress = True
        while progress:
            progress = False
            for req in requests:
                if req.page in resolved or not self._is_fixed_relation(req):
                    continue
                target = req.addr.relation.target
                if target not in resolved:
                    continue
                value = self._relation_value(req, resolved[target])
                if not self._claims_fit(
                    req,
                    value,
                    addrgen,
                    reservations,
                    allow_reserved=self._is_exact_alias(req, by_page),
                ):
                    raise AddrGenError(f"relation placement 0x{value:x} overlaps already-reserved " f"backing or coverage: " f"{self._last_claim_conflict}")
                self._commit_claims(req, value, addrgen, reservations)
                resolved[req.page] = value
                progress = True

    def _variable_key(self, req: AllocRequest, children: Dict[Page, List[AllocRequest]]) -> Tuple[int, int, int, int, int]:
        """MRV approximation: few free bits, then larger fixed bundles/spans first."""
        bits = req.addr.bits if req.addr.bits is not None else 64
        free_bits = bin(_effective_and_mask(req.addr) & ((1 << bits) - 1)).count("1")
        if req.addr.pinned:
            free_bits -= bits

        fixed_descendants = 0
        validation_span = self._validation_span(req)
        stack = [req.page]
        seen = set()
        while stack:
            page = stack.pop()
            if page in seen:
                continue
            seen.add(page)
            for child in children.get(page, ()):
                if self._is_fixed_relation(child):
                    fixed_descendants += 1
                    validation_span = max(
                        validation_span,
                        self._validation_span(child),
                    )
                    stack.append(child.page)
        return (
            0 if req.place_first else 1,
            -validation_span,
            free_bits,
            -fixed_descendants,
            req.seq,
        )

    def _bundle_values(self, root: AllocRequest, root_value: int, children) -> Dict[Page, int]:
        values = {root.page: root_value}
        queue = [root.page]
        while queue:
            page = queue.pop(0)
            for child in children.get(page, ()):
                if not self._is_fixed_relation(child):
                    continue
                if child.page in values:
                    raise AddrGenError("unresolvable relations (cycle in fixed bundle)")
                values[child.page] = self._relation_value(child, values[page])
                queue.append(child.page)
        return values

    def _validation_span(self, req: AllocRequest) -> int:
        """Largest declared footprint used only for MRV ordering.

        A granule affects fragmentation priority, but not the size requested from
        AddrGen: :meth:`_reservation_span` remains the ordinary contiguous draw.
        """
        return max(max(claim.size, claim.granule or 0) for claim in req.span_claims())

    def _reservation_span(self, req: AllocRequest) -> int:
        """Ordinary placement-domain footprint used to draw a candidate.

        Granule claims expand after the address is chosen. Inflating this draw size
        with their extent would force AddrGen to find a contiguous free window the
        size of a root slot (1 GiB / 512 GiB), which is unnecessary and can stall.
        """
        matching = [
            claim.size
            for claim in req.span_claims()
            if claim.granule is None and claim.addr_type == req.addr_type and self._conflict_space(claim.addr_type, claim.space) == self._conflict_space(req.addr_type, req.space_key)
        ]
        return max(matching, default=req.size)

    def _lineage_has_granule(self, root: AllocRequest, children: Dict[Page, List[AllocRequest]]) -> bool:
        """Whether ``root`` or any fixed descendant declares a granule claim."""
        stack = [root]
        seen: set[Page] = set()
        while stack:
            req = stack.pop()
            if req.page in seen:
                continue
            seen.add(req.page)
            if any(claim.granule is not None for claim in req.span_claims()):
                return True
            for child in children.get(req.page, ()):
                if self._is_fixed_relation(child):
                    stack.append(child)
        return False

    @staticmethod
    def _granule_claim_signatures(bundle: Dict[Page, int], by_page: Dict[Page, AllocRequest]) -> List[Tuple[Any, ...]]:
        """Resolved ``(domain, granule, start, end)`` signatures produced by ``bundle``."""
        signatures: List[Tuple[Any, ...]] = []
        for page, value in bundle.items():
            for claim in by_page[page].span_claims():
                if claim.granule is None:
                    continue
                start, end = claim.span(value)
                signatures.append((claim.addr_type, claim.space, claim.granule, start, end))
        return signatures

    def _invert_relation_interval(
        self,
        child: AllocRequest,
        parent_value: int,
        child_lo: int,
        child_hi: int,
    ) -> Optional[Tuple[int, int]]:
        """Map a child claim interval back onto the parent's address domain.

        Returns ``None`` when the relation is not invertible over the interval.
        """
        rel = child.addr.relation
        if isinstance(rel, SameAs):
            return child_lo, child_hi
        if isinstance(rel, OffsetFrom):
            return child_lo - rel.delta, child_hi - rel.delta
        if isinstance(rel, DerivedFrom) and not rel.random_mask:
            # value = ((base & and_mask) ^ not_mask) | or_mask
            # Invertible on bits where and_mask is set and or_mask does not force them.
            free = rel.and_mask & ~rel.or_mask
            sample = ((parent_value & rel.and_mask) ^ rel.not_mask) | rel.or_mask
            span = child_hi - child_lo
            power_of_two = span > 0 and (span & (span - 1)) == 0
            # A power-of-two granule interval legitimately varies in its low bits, including
            # bits forced by or_mask. Only non-free bits at/above the span must stay fixed;
            # those are the high granule bits the plan requires us to translate back.
            high_mask = ~(span - 1) if power_of_two else ~0
            if power_of_two and (high_mask & free) == 0:
                # Parent free bits cannot select among granules -- every parent maps into
                # the same low subspace. Refusing avoids a useless giant retirement.
                return None
            if (sample & ~free & high_mask) != (child_lo & ~free & high_mask):
                return None
            if ((child_hi - 1) & ~free & high_mask) != (child_lo & ~free & high_mask):
                return None
            # Parent bits in ``free`` track child bits after undoing not_mask.
            # child_bit = (parent_bit & and) ^ not  (for bits in free; or bits are forced)
            # parent_bit = child_bit ^ not   (on free bits)
            xor = rel.not_mask & free
            parent_lo = (parent_value & ~free) | ((child_lo ^ xor) & free)
            parent_hi = (parent_value & ~free) | ((child_hi ^ xor) & free)
            if parent_hi <= parent_lo and span > 0:
                # Forced low bits can make (child_hi ^ xor) & free collapse; preserve span.
                parent_hi = parent_lo + span
            if parent_hi <= parent_lo:
                return None
            return parent_lo, parent_hi
        return None

    def _retire_granule_preimages(
        self,
        probe: AddrGen,
        root: AllocRequest,
        root_value: int,
        bundle: Dict[Page, int],
        by_page: Dict[Page, AllocRequest],
        children: Dict[Page, List[AllocRequest]],
    ) -> None:
        """Exclude root candidates that would reproduce the bundle's granule claims.

        Claim spans are reserved in their own domains. Root-domain preimages are
        reserved in the draw pool; an identity PHYSICAL root with a linear space_key
        also dual-reserves through ``AddressType.MEMORY`` so the GPA pool and DRAM
        stay aligned without thrashing inside one granule.
        """
        for page, value in bundle.items():
            for claim in by_page[page].span_claims():
                if claim.granule is None:
                    continue
                start, end = claim.span(value)
                probe.reserve_memory(
                    claim.addr_type,
                    start,
                    end - start,
                    space_key=claim.space,
                )
        retire_type = RV.AddressType.MEMORY if root.addr_type == RV.AddressType.PHYSICAL and root.space_key is not None else root.addr_type
        for retire_start, retire_size in self._root_retire_intervals(root, root_value, bundle, by_page, children):
            probe.reserve_memory(
                retire_type,
                retire_start,
                retire_size,
                space_key=root.space_key,
            )

    def _root_retire_intervals(
        self,
        root: AllocRequest,
        root_value: int,
        bundle: Dict[Page, int],
        by_page: Dict[Page, AllocRequest],
        children: Dict[Page, List[AllocRequest]],
    ) -> List[Tuple[int, int]]:
        """Root-domain intervals that reproduce the bundle's granule claims.

        SameAs / OffsetFrom preimages are exact interval translations. Deterministic
        DerivedFrom preimages are retired when the granule bits remain invertible;
        otherwise raise an explicit unsupported-preimage error instead of probing
        thousands of root offsets that cannot clear the same child claim.
        """
        parent_of: Dict[Page, Page] = {}
        for parent, kids in children.items():
            for child in kids:
                if child.page in bundle and self._is_fixed_relation(child):
                    parent_of[child.page] = parent

        intervals: List[Tuple[int, int]] = []
        for page, value in bundle.items():
            for claim in by_page[page].span_claims():
                if claim.granule is None:
                    continue
                child_lo, child_hi = claim.span(value)
                lo, hi = child_lo, child_hi
                current = page
                while current != root.page:
                    parent = parent_of.get(current)
                    if parent is None:
                        raise AddrGenError(f"unsupported-preimage: cannot map granule claim " f"[{child_lo:#x}, {child_hi:#x}) for {page!r} back to " f"search root {root.page!r}")
                    child_req = by_page[current]
                    mapped = self._invert_relation_interval(child_req, bundle[parent], lo, hi)
                    if mapped is None:
                        rel = child_req.addr.relation
                        raise AddrGenError(
                            f"unsupported-preimage: granule claim "
                            f"[{child_lo:#x}, {child_hi:#x}) via {type(rel).__name__} "
                            f"on {current!r} is not invertible over a single root "
                            f"interval; refusing unbounded offset probing"
                        )
                    lo, hi = mapped
                    current = parent
                if hi > lo:
                    intervals.append((lo, hi - lo))
                else:
                    intervals.append((root_value, max(root.size, 1)))
        return intervals

    @staticmethod
    def _alignment(req: AllocRequest) -> int:
        """Byte alignment every placement of ``req`` must satisfy.

        A page is at least aligned to its own size. ``align`` adds geometry the page's own
        pagesize cannot express -- the destination of a superpage leaf PTE has to be aligned
        to the SOURCE page's size however small its own pagesize is.
        """
        pagesize_align = RV.RiscvPageSizes.memory(req.page.pagesize)
        if req.align is None:
            return pagesize_align
        return max(pagesize_align, req.align)

    def _claim_overlaps_addrgen(
        self,
        claim: SpanClaim,
        value: int,
        addrgen: AddrGen,
    ) -> bool:
        start, end = claim.span(value)
        size = end - start
        if claim.addr_type == RV.AddressType.PHYSICAL:
            return addrgen.physical_overlap(start, size)
        return addrgen.linear_overlap(start, size, claim.space)

    @staticmethod
    def _pinned_exact(page: Page) -> Optional[int]:
        """Exact address this page is pinned to, following a SameAs chain if needed."""
        current: Optional[Page] = page
        seen: set = set()
        while current is not None and current not in seen:
            seen.add(current)
            if current.addr.exact is not None:
                return current.addr.exact
            rel = current.addr.relation
            current = rel.target if isinstance(rel, SameAs) else None
        return None

    @staticmethod
    def _is_exact_duplicate(page: Page, owner: Any, claim: Optional["SpanClaim"] = None) -> bool:
        """Whether ``owner`` is another Page pinned to the same exact address.

        Same-space exact pins are duplicate declarations of one translation.
        Physical claims also treat cross-space exact pins as duplicates: MMIO is
        often declared both as a DISABLE/phys leaf and as an identity VA==PA
        mapping in a paging space, and the physical pool is global -- allocation
        cannot separate two pinned PAs. ``SameAs`` followers of an exact identity
        source inherit that pin. ``plan_topology`` remains the authority on
        whether their destinations agree.
        """
        if not isinstance(owner, Page) or owner is page:
            return False
        page_exact = BatchAllocationStrategy._pinned_exact(page)
        owner_exact = BatchAllocationStrategy._pinned_exact(owner)
        if page_exact is None or owner_exact != page_exact:
            return False
        if owner.space is page.space:
            return True
        return claim is not None and claim.addr_type == RV.AddressType.PHYSICAL

    def _claims_fit(
        self,
        req: AllocRequest,
        value: int,
        addrgen: AddrGen,
        reservations: _ReservationLedger,
        check_addrgen: bool = True,
        allow_reserved: bool = False,
        allowed_region: Optional[MemoryRegion] = None,
    ) -> bool:
        """Check every backing/coverage claim at one candidate base."""
        bits = req.addr.bits if req.addr.bits is not None else 64
        # The whole footprint has to fit, not only its base: a span running off the top of
        # the domain would be truncated by canonicalization into a different address.
        span = self._reservation_span(req)
        if value < 0 or value + span > (1 << bits):
            self._last_claim_conflict = f"value 0x{value:x} (span 0x{span:x}) does not fit the declared {bits}-bit address domain"
            return False
        alignment = self._alignment(req)
        if value & (alignment - 1):
            self._last_claim_conflict = f"value 0x{value:x} does not satisfy the required 0x{alignment:x} " f"alignment (pagesize {req.page.pagesize.name})"
            return False
        for claim in req.span_claims():
            space = self._conflict_space(claim.addr_type, claim.space)
            lo, hi = claim.span(value)
            explained_overlap = False
            for reserved in reservations.overlapping(claim.addr_type, space, lo, hi):
                if not (lo < reserved.end and reserved.start < hi):
                    continue
                if allow_reserved and reserved.owner is _RESERVED_SPAN_OWNER:
                    explained_overlap = True
                    continue
                if allowed_region is not None and reserved.owner is allowed_region and claim.addr_type is RV.AddressType.PHYSICAL and reserved.start <= lo and hi <= reserved.end:
                    explained_overlap = True
                    continue
                if self._is_exact_duplicate(req.page, reserved.owner, claim):
                    explained_overlap = True
                    continue
                same_share_key = claim.share_key is not None and reserved.share_key is not None and claim.share_key == reserved.share_key
                contained = (lo <= reserved.start and reserved.end <= hi) or (reserved.start <= lo and hi <= reserved.end)
                conditional_structural_share = (
                    {claim.kind, reserved.kind} == {ClaimKind.COVERAGE, ClaimKind.STRUCTURAL} and contained and (claim.allow_contained_share or reserved.allow_contained_share)
                )
                shared = (
                    conditional_structural_share
                    or same_share_key
                    and (
                        (claim.kind is ClaimKind.COVERAGE and reserved.kind is ClaimKind.COVERAGE and lo == reserved.start and hi == reserved.end or claim.allow_partial_share)
                        or ((claim.allow_contained_share or reserved.allow_contained_share) and contained)
                        or (claim.kind is not reserved.kind and ClaimKind.STRUCTURAL in {claim.kind, reserved.kind})
                        or (ClaimKind.STRUCTURAL not in {claim.kind, reserved.kind} and ClaimKind.COVERAGE not in {claim.kind, reserved.kind})
                    )
                )
                physical_coverage_vs_backing = claim.addr_type == RV.AddressType.PHYSICAL and {
                    claim.kind,
                    reserved.kind,
                } == {
                    ClaimKind.BACKING,
                    ClaimKind.COVERAGE,
                }
                linear_coverage_vs_address = claim.addr_type == RV.AddressType.LINEAR and {
                    claim.kind,
                    reserved.kind,
                } == {
                    ClaimKind.ADDRESS,
                    ClaimKind.COVERAGE,
                }
                if not shared and not physical_coverage_vs_backing and not linear_coverage_vs_address:
                    self._last_claim_conflict = f"{claim!r} overlaps {reserved!r}"
                    return False
                explained_overlap = True
            if check_addrgen and self._claim_overlaps_addrgen(claim, value, addrgen) and not explained_overlap:
                self._last_claim_conflict = f"{claim!r} at 0x{value:x} overlaps the address " "generator without a matching reservation-ledger owner"
                return False
        return True

    def _reserve_claim(
        self,
        owner: Any,
        claim: SpanClaim,
        value: int,
        addrgen: AddrGen,
        reservations: _ReservationLedger,
    ) -> None:
        start, end = claim.span(value)
        size = end - start
        if not (claim.kind is ClaimKind.COVERAGE and claim.addr_type == RV.AddressType.PHYSICAL):
            addrgen.reserve_memory(
                claim.addr_type,
                start,
                size,
                space_key=claim.space,
            )
        reservations.add(
            _Reservation(
                owner=owner,
                addr_type=claim.addr_type,
                space=self._conflict_space(claim.addr_type, claim.space),
                start=start,
                end=end,
                kind=claim.kind,
                share_key=claim.share_key,
                allow_contained_share=claim.allow_contained_share,
                all_spaces=claim.all_spaces,
            )
        )

    def _commit_claims(
        self,
        req: AllocRequest,
        value: int,
        addrgen: AddrGen,
        reservations: _ReservationLedger,
    ) -> None:
        for claim in req.span_claims():
            self._reserve_claim(req.page, claim, value, addrgen, reservations)

    def _commit_region_claims(
        self,
        req: AllocRequest,
        value: int,
        region: MemoryRegion,
        addrgen: AddrGen,
        reservations: _ReservationLedger,
    ) -> None:
        """Commit a region member the way :meth:`_commit_solution` will replay it.

        A floating region has already claimed its whole window through AddrGen
        (:meth:`_place_regions`), so a member's physical span is recorded in the
        ledger only -- reserving it again would book the same bytes twice. A fixed
        region reserves nothing, so its members must reserve their own spans or
        AddrGen would keep offering those bytes to free draws for the rest of the
        solve, leaving the ledger to reject each one at the cost of a backtrack.
        """
        for claim in req.span_claims():
            if region.base is None and claim.addr_type is RV.AddressType.PHYSICAL:
                start, end = claim.span(value)
                reservations.add(
                    _Reservation(
                        owner=req.page,
                        addr_type=claim.addr_type,
                        space=self._conflict_space(claim.addr_type, claim.space),
                        start=start,
                        end=end,
                        kind=claim.kind,
                        share_key=claim.share_key,
                        allow_contained_share=claim.allow_contained_share,
                        all_spaces=claim.all_spaces,
                    )
                )
                continue
            self._reserve_claim(req.page, claim, value, addrgen, reservations)

    def _free_constraint(self, req: AllocRequest, resolved: Dict[Page, int], size: int) -> AddressConstraint:
        spec = req.addr
        rel = spec.relation
        mask = _effective_and_mask(spec)
        or_mask = spec.or_mask or 0
        partial_relation = isinstance(rel, DerivedFrom) and rel.random_mask != 0
        if partial_relation:
            base = resolved[rel.target]
            bits = spec.bits if spec.bits is not None else 64
            width_mask = (1 << bits) - 1
            requested_random_bits = rel.random_mask & width_mask
            random_bits = requested_random_bits & ~rel.or_mask
            fixed_value = ((((base & rel.and_mask) ^ rel.not_mask) & ~requested_random_bits) | rel.or_mask) & width_mask
            # Only random_mask bits remain drawable. Every other bit is fixed to
            # the deterministic derivation, with AddrSpec.or_mask applied last.
            or_mask = (fixed_value | or_mask) & width_mask
            mask = mask & random_bits & ~or_mask
            if mask == 0:
                # AddressConstraint rejects an empty mask. A forced-one bit is a
                # harmless carrier because pinned allocation removes it from the
                # free-bit set.
                mask = or_mask
            if mask == 0:
                raise AddrGenError("derived relation has no drawable bits after applying " "random_mask and alignment")
        region = spec.region
        # An unbound region leaves the window as None; 0 is a real bound, not "unset".
        fixed_region = region is not None and region.base is not None
        region_start = region.base if fixed_region else None
        region_end = region.base + region.size - 1 if fixed_region else None
        qualifiers = set(region.qualifiers) if region is not None and region.qualifiers else set(spec.qualifiers)
        return AddressConstraint(
            type=req.addr_type,
            bits=spec.bits if spec.bits is not None else 64,
            size=size,
            mask=mask,
            or_mask=or_mask,
            start=region_start,
            end=region_end,
            qualifiers=qualifiers,
            pinned=spec.pinned or partial_relation,
            exclude=spec.exclude,
        )

    def _bundle_fits(self, bundle, by_page, addrgen, reservations) -> bool:
        """Validate all externally-conflicting spans before reserving any bundle member."""
        pending = reservations.clone()
        for page, value in bundle.items():
            req = by_page[page]
            if not self._claims_fit(req, value, addrgen, pending):
                return False
            for claim in req.span_claims():
                start, end = claim.span(value)
                pending.add(
                    _Reservation(
                        owner=page,
                        addr_type=claim.addr_type,
                        space=self._conflict_space(
                            claim.addr_type,
                            claim.space,
                        ),
                        start=start,
                        end=end,
                        kind=claim.kind,
                        share_key=claim.share_key,
                        allow_contained_share=claim.allow_contained_share,
                        all_spaces=claim.all_spaces,
                    )
                )
        return True

    def _commit_bundle(self, bundle, by_page, addrgen, reservations) -> None:
        for page, value in bundle.items():
            req = by_page[page]
            self._commit_claims(req, value, addrgen, reservations)

    @staticmethod
    def _activate_waiting(bundle, waiting, ready, variable_keys, heap_order) -> None:
        for page in bundle:
            for req in waiting.get(page, ()):
                heapq.heappush(ready, (variable_keys[req.page], heap_order[req.page], req))

    def _search(self, requests, by_page, children, variable_keys, heap_order, rollback_roots, sparse_pages, waiting, ready, resolved, addrgen, reservations, budget):
        while True:
            if len(resolved) == len(requests):
                return resolved, addrgen, reservations

            while ready and ready[0][2].page in resolved:
                heapq.heappop(ready)
            if not ready:
                return None
            _key, _seq, root = heapq.heappop(ready)
            span = self._reservation_span(root)
            constraint = self._free_constraint(root, resolved, span)
            # A pinned mask can expose only a sparse set of candidates. The
            # free-bit MRV estimate cannot account for memory-segment bounds,
            # so an earlier pinned root may consume the only reachable slot of
            # a later one and must remain a rollback point. Granule claims also
            # force the speculative path: rejected candidates must retire whole
            # containing spans without leaking those exclusions into committed pools.
            has_granule = self._lineage_has_granule(root, children)
            sparse_pending = any(page is not root.page and page not in resolved for page in sparse_pages)
            greedy = root.page not in rollback_roots and not root.addr.pinned and root.addr.region is None and not has_granule and not sparse_pending
            # While any sparse free root is still pending, rejected free-root candidates
            # retire only ``_alignment(root)`` bytes so overlapping legal bases survive.
            # Allocating the full span would erase neighbours the sparse request still needs.
            retire_base_only = sparse_pending and not has_granule and root.addr.region is None

            # Ordinary roots cannot make a later request unsatisfiable: pins were placed
            # first and fixed descendants are validated as one bundle. Draw those roots
            # without allocating, validate, then commit directly into this branch. Only
            # roots feeding a random relation retain the cloned recursive rollback path.
            probe = addrgen if greedy else self._clone_addrgen(addrgen)
            draw_constraint = dataclasses.replace(constraint, dont_allocate=True) if greedy or retire_base_only else constraint
            candidate_index = 0
            rejected_sigs: set = set()
            while True:
                try:
                    value = probe.generate_address(constraint=draw_constraint, space_key=root.space_key)
                except AddrGenError as exc:
                    self._last_dead_end = f"request seq={root.seq}, page={root.page!r}, " f"size=0x{root.size:x}, type={root.addr_type.name}: " f"{exc}"
                    return None
                bundle = self._bundle_values(root, value, children)
                if not self._bundle_fits(bundle, by_page, addrgen, reservations):
                    self._last_dead_end = f"request seq={root.seq} candidate 0x{value:x}: " f"{self._last_claim_conflict or 'claim conflict'}"
                    if budget["backtracks"] == 0:
                        raise _SearchBudgetExhausted()
                    budget["backtracks"] -= 1
                    if has_granule:
                        signatures = self._granule_claim_signatures(bundle, by_page)
                        if signatures:
                            new_sigs = [sig for sig in signatures if sig not in rejected_sigs]
                            if not new_sigs:
                                raise AddrGenError(
                                    f"granule search made no progress for request seq={root.seq} " f"at 0x{value:x}: repeated claim signatures {signatures!r}; " f"{self._last_claim_conflict}"
                                )
                            rejected_sigs.update(new_sigs)
                            self._retire_granule_preimages(
                                probe,
                                root,
                                value,
                                bundle,
                                by_page,
                                children,
                            )
                        else:
                            probe.reserve_memory(root.addr_type, value, span, space_key=root.space_key)
                    elif retire_base_only:
                        probe.reserve_memory(root.addr_type, value, self._alignment(root), space_key=root.space_key)
                    elif greedy:
                        probe.reserve_memory(root.addr_type, value, span, space_key=root.space_key)
                    candidate_index += 1
                    continue

                if greedy:
                    self._commit_bundle(bundle, by_page, addrgen, reservations)
                    resolved.update(bundle)
                    self._activate_waiting(bundle, waiting, ready, variable_keys, heap_order)
                    break

                # The probe is an isolated rollback branch. Reuse it when it contains
                # only the winning reservation; otherwise replay the winner from the
                # pristine parent state without carrying rejected candidates forward.
                # A base-retiring probe is never reused: it has to survive this branch
                # unpolluted so the next candidate is drawn against the retirements only.
                if candidate_index == 0 and span == root.size and not has_granule and not retire_base_only:
                    branch_addrgen = probe
                else:
                    branch_addrgen = self._clone_addrgen(addrgen)
                    branch_addrgen.adopt_rng_stream(probe)
                branch_reservations = reservations.clone()
                branch_resolved = dict(resolved)
                branch_ready = list(ready)
                self._commit_bundle(bundle, by_page, branch_addrgen, branch_reservations)
                branch_resolved.update(bundle)
                self._activate_waiting(bundle, waiting, branch_ready, variable_keys, heap_order)
                answer = self._search(
                    requests,
                    by_page,
                    children,
                    variable_keys,
                    heap_order,
                    rollback_roots,
                    sparse_pages,
                    waiting,
                    branch_ready,
                    branch_resolved,
                    branch_addrgen,
                    branch_reservations,
                    budget,
                )
                if answer is not None:
                    return answer
                if budget["backtracks"] == 0:
                    raise _SearchBudgetExhausted()
                budget["backtracks"] -= 1
                # A later recursive failure means this candidate's whole branch is dead.
                # Retire its granule ownership from the probe so the next root draw cannot
                # reproduce the same claim.
                if has_granule:
                    signatures = self._granule_claim_signatures(bundle, by_page)
                    new_sigs = [sig for sig in signatures if sig not in rejected_sigs]
                    if signatures and not new_sigs:
                        raise AddrGenError(f"granule search made no progress after branch failure for " f"request seq={root.seq} at 0x{value:x}: repeated claim " f"signatures {signatures!r}")
                    rejected_sigs.update(signatures)
                    self._retire_granule_preimages(
                        probe,
                        root,
                        value,
                        bundle,
                        by_page,
                        children,
                    )
                elif retire_base_only:
                    probe.reserve_memory(root.addr_type, value, self._alignment(root), space_key=root.space_key)
                candidate_index += 1

    def _place_regions(self, regions: List[MemoryRegion], addrgen: AddrGen, reservations: _ReservationLedger) -> Dict[MemoryRegion, int]:
        """Reserve fixed regions and place floating ones; return each region's base.

        Placed in construction order (the order they were added to the builder) --
        deterministic given a fixed construction sequence; a region has no id to sort
        by. A region's whole span is reserved (so nothing else is placed in it); members are
        sub-placed within it by :meth:`_place_in_region`.
        """
        bases: Dict[MemoryRegion, int] = {}
        for region in regions:
            if region.base is not None:
                bases[region] = region.base
            else:
                align_mask = (~(region.align - 1)) & _FULL_MASK
                bases[region] = addrgen.generate_address(
                    constraint=AddressConstraint(
                        type=RV.AddressType.PHYSICAL,
                        bits=region.bits or 64,
                        size=region.size,
                        mask=align_mask,
                        qualifiers=set(region.qualifiers),
                    )
                )
                # A floating region allocates new backing memory. A
                # fixed region instead describes an existing address range;
                # only its placed members reserve bytes within that range.
                self._record(
                    reservations,
                    region,
                    RV.AddressType.PHYSICAL,
                    None,
                    bases[region],
                    region.size,
                )
        return bases

    def _place_in_region(
        self,
        req: AllocRequest,
        base: int,
        region: MemoryRegion,
        taken: List[Tuple[int, int]],
        rejected: List[int],
        rng: RandNum,
    ) -> int:
        """Choose uniformly among the free bases a region member's masks allow.

        Inside a region window, only the request's ``and_mask``, ``or_mask``, ``bits``,
        and the pagesize alignment :meth:`_claims_fit` requires apply -- not platform
        qualifiers or builder exclusions, because the window is the consumer's placement
        domain. Alignment is folded in here rather than trusted from the caller's mask,
        so a request that reaches the solver without the builder's geometry pass still
        cannot produce a misaligned leaf.

        Counting the legal bases per free window and un-ranking one draw keeps the
        cost proportional to the occupancy, not to the size of the region: walking
        aligned slots instead costs one step per slot (262144 for a 1 GiB region of
        4 KiB pages) and pays it again on every retry.
        """
        spec = req.addr
        domain = MaskedBases.for_span(
            and_mask=_effective_and_mask(spec),
            or_mask=spec.or_mask or 0,
            alignment=self._alignment(req),
            bits=spec.bits,
        )
        if domain is not None:
            value = choose_in_windows(domain, free_windows(base, base + region.size, taken), req.size, rng, rejected)
            if value is not None:
                return value
        raise AddrGenError(
            f"region (base 0x{base:x}, size 0x{region.size:x}) has no free base for member "
            f"(size 0x{req.size:x}, pagesize={req.page.pagesize.name}, reserve_size={req.page.reserve_size}, "
            f"space={req.page.space}, and_mask=0x{_effective_and_mask(spec):x}, or_mask=0x{spec.or_mask or 0:x}, "
            f"bits={spec.bits}); {len(taken)} span(s) placed, {len(rejected)} base(s) rejected"
        )

    @staticmethod
    def _conflict_space(addr_type: RV.AddressType, space_key: Optional[Space]) -> Optional[Space]:
        """The pool key a span contends in: per-space for linear, global (None) for physical."""
        return space_key if addr_type == RV.AddressType.LINEAR else None

    def _record(self, reservations: _ReservationLedger, owner: Any, addr_type: RV.AddressType, space_key: Optional[Space], start: int, size: int) -> None:
        reservations.add(_Reservation(owner=owner, addr_type=addr_type, space=self._conflict_space(addr_type, space_key), start=start, end=start + size))
