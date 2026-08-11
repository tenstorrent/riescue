# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Symbolic page-table topology planning.

This module contains no allocator or walker state.  It derives the
intermediate page-table nodes a resolved set of source addresses requires, so the
builder can turn those nodes into allocation demands before emitting any PTE.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.riemap import resolve
from riescue.riemap.request import Mapping, Page, Space, Stage


@dataclass(frozen=True)
class NodeKey:
    """Identity of one shared pointer PTE / child-table frame."""

    space: Space
    level: int
    prefix: int


@dataclass
class FrameDemand:
    """Every mapping whose walk reaches one intermediate node."""

    key: NodeKey
    mappings: List[Mapping]


class IntentProvenance(Enum):
    """Why a mapping participates in the topology."""

    EXPLICIT = "explicit"
    SYNTHETIC_FRAME = "synthetic-frame"
    SYNTHETIC_LEAF_TARGET = "synthetic-leaf-target"

    @property
    def conditional(self) -> bool:
        """Whether an explicit declaration may supersede this intent."""

        return self in {
            IntentProvenance.SYNTHETIC_FRAME,
            IntentProvenance.SYNTHETIC_LEAF_TARGET,
        }


@dataclass(frozen=True)
class LeafSpan:
    """One canonical, page-aligned architectural leaf interval."""

    space: Space
    start: int
    end: int
    pagesize: RV.RiscvPageSizes
    level: int

    @classmethod
    def from_address(
        cls,
        space: Space,
        address: int,
        pagesize: RV.RiscvPageSizes,
    ) -> "LeafSpan":
        normalized = _source_address(space, address)
        size = RV.RiscvPageSizes.memory(pagesize)
        start = normalized & ~(size - 1)
        return cls(
            space=space,
            start=start,
            end=start + size,
            pagesize=pagesize,
            level=RV.RiscvPageSizes.pt_leaf_level(pagesize),
        )

    def overlaps(self, other: "LeafSpan") -> bool:
        return self.space is other.space and self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class LeafContract:
    """Everything that must agree for two declarations to share one leaf PTE.

    ``target`` and ``pagesize`` decide whether two claims describe the same leaf, and
    :func:`plan_topology` derives them itself. The remaining fields are only compared when
    a caller supplies contracts explicitly, and they must describe *effective* PTE state:
    two declarations may spell the same leaf differently (one stating a default the other
    omits), so filling ``attrs`` from raw declared attrs would reject a legal alias.
    """

    target: Optional[int]
    pagesize: RV.RiscvPageSizes
    attrs: Tuple[Tuple[str, Any], ...] = ()
    pinned_frames: Tuple[Tuple[int, int], ...] = ()
    secure: Optional[bool] = None


@dataclass(frozen=True, eq=False)
class LeafIntent:
    """Normalized leaf declaration consumed by :class:`TopologyPlan`."""

    mapping: Mapping
    span: LeafSpan
    contract: LeafContract
    provenance: IntentProvenance = IntentProvenance.EXPLICIT


class TopologyConflict(ValueError):
    """A page-table contradiction with enough ownership data for search."""

    def __init__(
        self,
        message: str,
        *,
        mappings: Iterable[Mapping] = (),
        node_keys: Iterable[NodeKey] = (),
        retriable: bool = False,
    ):
        super().__init__(message)
        self.mappings = tuple(dict.fromkeys(mappings))
        self.node_keys = tuple(dict.fromkeys(node_keys))
        self.retriable = retriable


@dataclass(frozen=True)
class RootKey:
    """The one root table belonging to an address space."""

    space: Space


@dataclass(frozen=True)
class LogicalTable:
    """One logical table reached by an address-prefix walk."""

    space: Space
    level: int
    prefix: int


@dataclass(frozen=True)
class SlotKey:
    """One PTE slot in one logical table."""

    table: LogicalTable
    index: int


@dataclass
class LeafClaim:
    """Resolved leaf intent for one or more equivalent declarations."""

    mappings: List[Mapping]
    intent: LeafIntent

    @property
    def target(self) -> Optional[int]:
        return self.intent.contract.target

    @property
    def level(self) -> int:
        return self.intent.span.level

    @property
    def coverage(self) -> Tuple[int, int]:
        return (self.intent.span.start, self.intent.span.end)

    @property
    def identity(self) -> bool:
        return self.target == self.intent.span.start

    @property
    def provenance(self) -> IntentProvenance:
        return self.intent.provenance


@dataclass
class PointerClaim:
    """Resolved pointer intent leading to one child logical table."""

    mappings: List[Mapping]
    child: LogicalTable
    level: int
    identity: bool
    provenance: IntentProvenance = IntentProvenance.EXPLICIT


SlotClaim = Union[LeafClaim, PointerClaim]


def _source_address(space: Space, address: int) -> int:
    """Normalize an address before using it as a page-table prefix or slot index.

    Structural identities originate in the physical allocator but are walked in a
    source space.  The walker interprets a Stage.G source as a zero-extended GPA
    and every other paged source as a canonical VA; topology must make exactly the
    same interpretation.
    """
    if space.stage is Stage.G:
        return resolve.make_canonical_gpa(address, space.paging_mode)
    if space.paging_mode != RV.RiscvPagingModes.DISABLE:
        return resolve.make_canonical_va(address, space.paging_mode)
    return address


@dataclass
class TopologyPlan:
    """Complete logical PTE occupancy before any table is emitted."""

    roots: Dict[Space, RootKey] = field(default_factory=dict)
    slots: Dict[SlotKey, SlotClaim] = field(default_factory=dict)
    tables: Dict[LogicalTable, List[Mapping]] = field(default_factory=dict)
    suppressed_mappings: set[Mapping] = field(default_factory=set)
    intents: Dict[Mapping, LeafIntent] = field(default_factory=dict)
    required_translations: Dict[
        Space,
        Dict[int, Optional[int]],
    ] = field(default_factory=dict)

    def reachable_mappings(self) -> set[Mapping]:
        """Mappings whose leaf is reachable from a planned root."""
        reachable: set[Mapping] = set()
        visited: set[LogicalTable] = set()
        claims_by_table: Dict[LogicalTable, List[SlotClaim]] = {}
        for key, claim in self.slots.items():
            claims_by_table.setdefault(key.table, []).append(claim)

        def walk(table: LogicalTable) -> None:
            if table in visited:
                return
            visited.add(table)
            for claim in claims_by_table.get(table, ()):
                if isinstance(claim, LeafClaim):
                    reachable.update(claim.mappings)
                else:
                    walk(claim.child)

        for space in self.roots:
            top = RV.RiscvPagingModes.max_levels(space.paging_mode) - 1
            if top >= 0:
                walk(LogicalTable(space, top, 0))
        return reachable

    def frame_demands(self) -> List[FrameDemand]:
        """Every child-table frame required by the accepted pointer slots."""

        grouped: Dict[NodeKey, List[Mapping]] = {}
        space_order = {space: index for index, space in enumerate(self.roots)}
        for claim in self.slots.values():
            if not isinstance(claim, PointerClaim):
                continue
            key = NodeKey(
                space=claim.child.space,
                level=claim.level,
                prefix=claim.child.prefix,
            )
            grouped.setdefault(key, []).extend(claim.mappings)
        ordered = sorted(
            grouped,
            key=lambda key: (
                space_order.get(key.space, len(space_order)),
                key.level,
                key.prefix,
            ),
        )
        return [
            FrameDemand(
                key=key,
                mappings=list(dict.fromkeys(grouped[key])),
            )
            for key in ordered
        ]

    def prune_orphan_pointers(self) -> None:
        """Drop pointer paths whose conditional leaves were suppressed."""

        while True:
            occupied_tables = {key.table for key in self.slots}
            doomed = [key for key, claim in self.slots.items() if isinstance(claim, PointerClaim) and claim.child not in occupied_tables]
            if not doomed:
                return
            for key in doomed:
                del self.slots[key]

    def prune_suppressed_mappings(self) -> None:
        """Remove suppressed declarations from every descendant slot."""

        for key, claim in list(self.slots.items()):
            claim.mappings[:] = [mapping for mapping in claim.mappings if mapping not in self.suppressed_mappings]
            if not claim.mappings:
                del self.slots[key]
        self.prune_orphan_pointers()

    def _leaf_covers_pointer(
        self,
        leaf: LeafClaim,
        pointer: PointerClaim,
    ) -> bool:
        """Whether ``leaf`` makes every descendant translation redundant."""

        if leaf.target is None:
            return False
        for mapping in pointer.mappings:
            intent = self.intents.get(mapping)
            if intent is None or intent.contract.target is None:
                return False
            if not (leaf.intent.span.start <= intent.span.start and intent.span.end <= leaf.intent.span.end):
                return False
            expected = leaf.target + (intent.span.start - leaf.intent.span.start)
            if intent.contract.target != expected:
                return False
        return True

    def uncovered_required_addresses(self) -> Dict[Space, set[int]]:
        """Required structural addresses not translated by any surviving leaf."""

        uncovered: Dict[Space, set[int]] = {}
        for space, translations in self.required_translations.items():
            claims = [claim for claim in self.slots.values() if isinstance(claim, LeafClaim) and claim.intent.span.space is space]
            missing = {
                address
                for address, expected_target in translations.items()
                if not any(
                    claim.intent.span.start <= address < claim.intent.span.end
                    and claim.target is not None
                    and (expected_target is None or claim.target + (address - claim.intent.span.start) == expected_target)
                    for claim in claims
                )
            }
            if missing:
                uncovered[space] = missing
        return uncovered

    def validate_required_coverage(self) -> None:
        """Raise when explicit-declaration suppression made a frame unreachable."""

        uncovered = self.uncovered_required_addresses()
        if not uncovered:
            return
        details = ", ".join(f"{space!r}: {[hex(value) for value in sorted(values)]}" for space, values in uncovered.items())
        implicated = [mapping for mapping in self.suppressed_mappings if mapping.src.space in uncovered]
        raise TopologyConflict(
            f"required structural addresses are not translated: {details}",
            mappings=implicated,
            retriable=any(self.intents.get(mapping) is not None and self.intents[mapping].provenance.conditional for mapping in implicated)
            or any(mapping.src.addr.exact is None and mapping.src.addr.relation is None for mapping in implicated),
        )

    @staticmethod
    def _provenance(claim: SlotClaim) -> IntentProvenance:
        return claim.provenance

    @staticmethod
    def _is_movable(claim: SlotClaim) -> bool:
        return any(mapping.src.addr.exact is None and mapping.src.addr.relation is None for mapping in claim.mappings)

    def _suppress(self, claim: SlotClaim) -> None:
        """Remove every slot contributed by one conditional intent."""

        self.suppressed_mappings.update(claim.mappings)
        if isinstance(claim, LeafClaim):
            doomed = [key for key, existing in self.slots.items() if isinstance(existing, LeafClaim) and existing.intent is claim.intent]
        else:
            doomed = [key for key, existing in self.slots.items() if existing is claim]
        for doomed_key in doomed:
            del self.slots[doomed_key]

    def _prefer_explicit(
        self,
        key: SlotKey,
        prior: SlotClaim,
        claim: SlotClaim,
    ) -> bool:
        """Apply explicit-over-conditional precedence.

        Returns true when precedence completely handled the merge.
        """

        prior_conditional = self._provenance(prior).conditional
        claim_conditional = self._provenance(claim).conditional
        if prior_conditional == claim_conditional:
            return False
        if prior_conditional:
            self._suppress(prior)
            self.slots[key] = claim
        else:
            self._suppress(claim)
        return True

    def _conflict(
        self,
        message: str,
        key: SlotKey,
        prior: SlotClaim,
        claim: SlotClaim,
    ) -> TopologyConflict:
        mappings = [*prior.mappings, *claim.mappings]
        return TopologyConflict(
            message,
            mappings=mappings,
            node_keys=(
                NodeKey(
                    space=key.table.space,
                    level=key.table.level,
                    prefix=key.table.prefix,
                ),
            ),
            retriable=self._is_movable(prior) or self._is_movable(claim),
        )

    def merge_slot(self, key: SlotKey, claim: SlotClaim) -> None:
        prior = self.slots.get(key)
        if prior is None:
            self.slots[key] = claim
            return
        if isinstance(prior, LeafClaim) and isinstance(claim, LeafClaim):
            if prior.intent.span == claim.intent.span and prior.intent.contract == claim.intent.contract:
                prior.mappings.extend(claim.mappings)
                return
            if self._prefer_explicit(key, prior, claim):
                return
            raise self._conflict(
                f"two distinct pages resolve to VA in " f"{key.table.space!r}; conflicting leaves at " f"level-{key.table.level} slot 0x{key.index:x}",
                key,
                prior,
                claim,
            )
        if isinstance(prior, PointerClaim) and isinstance(
            claim,
            PointerClaim,
        ):
            if prior.child == claim.child:
                prior.mappings.extend(claim.mappings)
                prior.identity = prior.identity and claim.identity
                if not claim.provenance.conditional:
                    prior.provenance = claim.provenance
                return
            if self._prefer_explicit(key, prior, claim):
                return
            raise self._conflict(
                f"conflicting pointers in {key.table.space!r} " f"level-{key.table.level} slot 0x{key.index:x}",
                key,
                prior,
                claim,
            )
        if isinstance(prior, LeafClaim) != isinstance(claim, LeafClaim):
            leaf = prior if isinstance(prior, LeafClaim) else claim
            pointer = claim if isinstance(claim, PointerClaim) else prior
            assert isinstance(leaf, LeafClaim)
            assert isinstance(pointer, PointerClaim)
            if self._prefer_explicit(key, prior, claim):
                return
            pointer_is_conditional = all(self.intents[mapping].provenance.conditional for mapping in pointer.mappings)
            if pointer_is_conditional and self._leaf_covers_pointer(
                leaf,
                pointer,
            ):
                self.suppressed_mappings.update(pointer.mappings)
                self.slots[key] = leaf
                return
            raise self._conflict(
                f"leaf/pointer conflict in {key.table.space!r} " f"level-{key.table.level} slot 0x{key.index:x}: " f"{prior!r} vs {claim!r}",
                key,
                prior,
                claim,
            )
        raise AssertionError("unhandled topology claim combination")


def plan_topology(
    mappings: Iterable[Mapping],
    addresses: Dict[Page, int],
    target_addresses: Optional[Dict[Page, int]] = None,
    *,
    provenance: Optional[Dict[Mapping, IntentProvenance]] = None,
    contracts: Optional[Dict[Mapping, LeafContract]] = None,
    required_addresses: Optional[Dict[Space, Iterable[int]]] = None,
    required_translations: Optional[Dict[Space, Dict[int, Optional[int]]]] = None,
) -> TopologyPlan:
    """Build exact logical table/slot occupancy for resolved mappings."""

    targets = target_addresses or addresses
    plan = TopologyPlan()
    if required_addresses is not None:
        plan.required_translations = {space: {value: value for value in values} for space, values in required_addresses.items()}
    if required_translations is not None:
        for space, translations in required_translations.items():
            plan.required_translations.setdefault(
                space,
                {},
            ).update(translations)
    # A repeated source Page installs one leaf, so its repeats must agree on that leaf.
    # Without caller-supplied contracts this only compares destination and pagesize;
    # attribute agreement belongs to the layers that can see effective PTE state.
    seen_sources: Dict[Page, Tuple[Mapping, LeafContract]] = {}
    for mapping in mappings:
        src = mapping.src
        if src not in addresses:
            continue
        contract = (
            contracts[mapping]
            if contracts is not None and mapping in contracts
            else LeafContract(
                target=targets.get(mapping.dst),
                pagesize=src.pagesize,
            )
        )
        prior = seen_sources.get(src)
        if prior is not None:
            prior_mapping, prior_contract = prior
            if prior_contract.target != contract.target:
                raise TopologyConflict(
                    f"source page maps to a different destination: " f"{prior_contract.target!r} versus {contract.target!r}",
                    mappings=(prior_mapping, mapping),
                )
            if prior_contract != contract:
                raise TopologyConflict(
                    f"source page is declared twice in {src.space!r} with conflicting leaf " f"contracts: {prior_contract!r} versus {contract!r}",
                    mappings=(prior_mapping, mapping),
                )
            continue
        seen_sources[src] = (mapping, contract)
        mode = src.space.paging_mode
        levels = RV.RiscvPagingModes.max_levels(mode)
        if mode == RV.RiscvPagingModes.DISABLE or levels == 0:
            continue
        plan.roots.setdefault(src.space, RootKey(src.space))
        address = _source_address(src.space, addresses[src])
        top = levels - 1
        top_hi = RV.RiscvPagingModes.index_bits(mode, top)[0]
        leaf = RV.RiscvPageSizes.pt_leaf_level(src.pagesize)
        intent_provenance = provenance.get(mapping, IntentProvenance.EXPLICIT) if provenance is not None else IntentProvenance.EXPLICIT
        span = LeafSpan.from_address(
            src.space,
            address,
            src.pagesize,
        )
        intent = LeafIntent(
            mapping=mapping,
            span=span,
            contract=contract,
            provenance=intent_provenance,
        )
        plan.intents[mapping] = intent
        for level in range(top, leaf - 1, -1):
            hi, lo = RV.RiscvPagingModes.index_bits(mode, level)
            prefix_lo = hi + 1
            table_prefix = common.bits(address, top_hi, prefix_lo) if prefix_lo <= top_hi else 0
            table = LogicalTable(src.space, level, table_prefix)
            plan.tables.setdefault(table, []).append(mapping)
            slot = SlotKey(table, common.bits(address, hi, lo))
            if level == leaf:
                slot_indices = range(slot.index & ~0xF, (slot.index & ~0xF) + 16) if src.pagesize == RV.RiscvPageSizes.S64KB else (slot.index,)
                for slot_index in slot_indices:
                    plan.merge_slot(
                        SlotKey(table, slot_index),
                        LeafClaim(
                            mappings=[mapping],
                            intent=intent,
                        ),
                    )
                continue
            child_lo = RV.RiscvPagingModes.index_bits(mode, level)[1]
            child_prefix = common.bits(address, top_hi, child_lo)
            child = LogicalTable(src.space, level - 1, child_prefix)
            plan.merge_slot(
                slot,
                PointerClaim(
                    mappings=[mapping],
                    child=child,
                    level=level,
                    identity=targets.get(mapping.dst) == address,
                    provenance=intent_provenance,
                ),
            )
    plan.prune_suppressed_mappings()
    return plan
