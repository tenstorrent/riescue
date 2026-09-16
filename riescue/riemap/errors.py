# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Structured, human-readable failures produced by RieMap.

The public fields are deliberately simple values so callers can inspect a
failure without parsing its text.  ``str(error)`` remains useful in an uncaught
traceback: it preserves the legacy message first, then adds a compact
explanation, participants, context, and possible remedies.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Tuple


class FailurePhase(Enum):
    """The page-table generation phase that rejected a request."""

    DECLARATION = "declaration"
    COLORING = "coloring"
    ALLOCATION = "allocation"
    TOPOLOGY = "topology"
    PLANNING = "planning"
    EMISSION = "emission"


class FailureKind(Enum):
    """Stable machine-readable categories for expected RieMap failures."""

    CONSTRAINT = "constraint"
    DUPLICATE_SOURCE = "constraint.duplicate_source"
    LEAF_LEAF = "topology.leaf_leaf"
    POINTER_POINTER = "topology.pointer_pointer"
    LEAF_POINTER = "topology.leaf_pointer"
    REQUIRED_TRANSLATION = "topology.required_translation"
    MISSING_GSTAGE_TRANSLATION = "topology.missing_gstage_translation"
    CLAIM_OVERLAP = "allocation.claim_overlap"
    UNSATISFIABLE = "allocation.unsatisfiable_constraints"
    RELATION_CYCLE = "allocation.relation_cycle"
    ADDRESS_SPACE_EXHAUSTED = "allocation.address_space_exhausted"
    REGION_EXHAUSTED = "allocation.region_exhausted"
    COLORING_EXHAUSTED = "coloring.address_space_exhausted"
    SEARCH_EXHAUSTED = "planning.search_exhausted"
    JOINT_PLANNING = "planning.no_feasible_assignment"
    PTE_SLOT = "emission.pte_slot"
    LEAF_DEEPER = "emission.leaf_deeper"
    FRAME_OVERPACKED = "emission.frame_overpacked"
    GSTAGE_IDENTITY = "emission.gstage_identity"


@dataclass(frozen=True)
class FailureParticipant:
    """One declaration or internal object involved in a failure."""

    subject: Any
    role: str = ""
    description: str = ""


@dataclass(frozen=True)
class FailureSite:
    """Architectural location of a page-table conflict."""

    space: Any = None
    level: Optional[int] = None
    slot: Optional[int] = None
    table_prefix: Optional[int] = None
    frame: Optional[int] = None
    address: Optional[int] = None
    span: Optional[Tuple[int, int]] = None


def _format_value(name: str, value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        hex_fields = ("addr", "address", "base", "end", "frame", "mask", "size", "span", "start", "target", "value")
        if any(field in name.lower() for field in hex_fields):
            return f"0x{value:x}"
    return str(value)


class RieMapError(Exception):
    """Base class for expected, structured RieMap failures."""

    def __init__(
        self,
        message: str,
        *,
        kind: FailureKind = FailureKind.CONSTRAINT,
        phase: FailurePhase = FailurePhase.DECLARATION,
        summary: Optional[str] = None,
        reason: Optional[str] = None,
        participants: Iterable[FailureParticipant] = (),
        site: Optional[FailureSite] = None,
        context: Optional[Mapping[str, Any]] = None,
        hints: Iterable[str] = (),
    ):
        super().__init__(message)
        self.legacy_message = message
        self.kind = kind
        self.phase = phase
        self.summary = summary or message
        self.reason = reason
        self.participants = tuple(participants)
        self.site = site
        self.context = dict(context or {})
        self.hints = tuple(hints)
        self.labels: dict[int, str] = {}

    def add_labels(self, labels: Mapping[Any, str]) -> "RieMapError":
        """Attach consumer names to otherwise anonymous declaration objects."""

        self.labels.update({id(subject): label for subject, label in labels.items()})
        cause = self.__cause__
        if isinstance(cause, RieMapError):
            cause.add_labels(labels)
        return self

    def _subject_name(self, subject: Any) -> str:
        label = self.labels.get(id(subject))
        if label is not None:
            return label
        src = getattr(subject, "src", None)
        dst = getattr(subject, "dst", None)
        if src is not None and dst is not None:
            return f"{self._subject_name(src)} -> {self._subject_name(dst)}"
        return repr(subject)

    def format_diagnostic(self) -> str:
        """Render a concise report suitable for a traceback or CLI."""

        lines = [
            f"RieMap failure [{self.kind.value}]",
            f"  Phase: {self.phase.value}",
            f"  Summary: {self.summary}",
        ]
        if self.reason and self.reason != self.summary:
            lines.append(f"  Why: {self.reason}")

        site = self.site
        if site is not None:
            values = []
            if site.space is not None:
                values.append(f"space={site.space!r}")
            if site.level is not None:
                values.append(f"level={site.level}")
            if site.slot is not None:
                values.append(f"slot=0x{site.slot:x}")
            if site.table_prefix is not None:
                values.append(f"table_prefix=0x{site.table_prefix:x}")
            if site.frame is not None:
                values.append(f"frame=0x{site.frame:x}")
            if site.address is not None:
                values.append(f"address=0x{site.address:x}")
            if site.span is not None:
                values.append(f"span=[0x{site.span[0]:x}, 0x{site.span[1]:x})")
            if values:
                lines.append("  Site: " + ", ".join(values))

        if self.participants:
            lines.append("  Participants:")
            for participant in self.participants:
                role = f"{participant.role}: " if participant.role else ""
                description = f" — {participant.description}" if participant.description else ""
                lines.append(f"    - {role}{self._subject_name(participant.subject)}{description}")

        if self.context:
            lines.append("  Context:")
            for name, value in self.context.items():
                lines.append(f"    {name}: {_format_value(name, value)}")

        if self.hints:
            lines.append("  Possible fixes:")
            lines.extend(f"    - {hint}" for hint in self.hints)
        return "\n".join(lines)

    def __str__(self) -> str:
        diagnostic = self.format_diagnostic()
        if diagnostic == self.legacy_message:
            return self.legacy_message
        return f"{self.legacy_message}\n{diagnostic}"


class ConstraintConflict(RieMapError, ValueError):
    """Declarations that cannot all be true in one page-table layout."""


class AddrGenError(RieMapError):
    """Base class for address generation and allocation failures."""


class AllocationConflict(AddrGenError, ValueError):
    """Address constraints contradict an existing placement or each other."""


class AddressSpaceExhausted(AddrGenError, ValueError):
    """A placement domain has insufficient compatible address capacity."""


class PlanningExhausted(AddrGenError):
    """Bounded search or all declared planning policies were exhausted."""
