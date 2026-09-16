# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Transactional coordination for address placement and symbolic topology.

The allocator generates candidates and geometric reservations.
This module coordinates the transaction over those candidates, structural
closure, policy choices, and the resulting :class:`TopologyPlan`.  Nothing is
published until one complete branch satisfies every layer.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Generic, Tuple, TypeVar

from riescue.riemap.addrgen import AddrGen
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.errors import FailureKind, FailurePhase, PlanningExhausted
from riescue.riemap.layout import TopologyConflict, TopologyPlan


T = TypeVar("T")


class ChoicePolicy(Enum):
    """Ordering policy for legal declaration choices in one branch."""

    PREFERRED = "preferred"
    MINIMUM_GEOMETRY = "minimum-geometry"


@dataclass
class PlanBranch(Generic[T]):
    """One complete, unpublished planner branch."""

    addrgen: AddrGen
    allocation: T
    topology: TopologyPlan
    payload: Any = None


class JointPlanningError(PlanningExhausted):
    """No address/topology/choice branch satisfies all declarations."""

    def __init__(self, failures: Tuple[Tuple[ChoicePolicy, BaseException], ...]):
        self.failures = failures
        legacy_markers = []
        for _policy, failure in failures:
            text = getattr(failure, "legacy_message", str(failure))
            for marker in (
                "incompatible backing or coverage claim",
                "no free base for member",
                "search budget exhausted",
                "unsatisfiable allocation constraints",
                "leaf/pointer conflict",
                "conflicting leaves",
                "conflicting pointers",
            ):
                if marker in text and marker not in legacy_markers:
                    legacy_markers.append(marker)
        message = "joint address/topology planning has no feasible assignment"
        if legacy_markers:
            message += "; " + "; ".join(legacy_markers)
        super().__init__(
            message,
            kind=FailureKind.JOINT_PLANNING,
            phase=FailurePhase.PLANNING,
            summary="No declared page-size policy produced both a valid address assignment and a valid page-table topology.",
            reason="Every attempted policy failed for the reasons listed below.",
            context={"policies_tried": ", ".join(policy.value for policy, _error in failures)},
            hints=(
                "Resolve the per-policy failures shown below; changing the seed helps only when a failure is marked movable or search-limited.",
                "Prefer smaller page sizes when coarse leaves conflict with finer mappings.",
            ),
        )

    def add_labels(self, labels):
        for _policy, failure in self.failures:
            if isinstance(failure, (AddrGenError, TopologyConflict)):
                failure.add_labels(labels)
        return super().add_labels(labels)

    def format_diagnostic(self) -> str:
        lines = [super().format_diagnostic(), "  Policy failures:"]
        for policy, error in self.failures:
            if hasattr(error, "kind") and hasattr(error, "summary"):
                lines.append(f"    - {policy.value}: [{error.kind.value}] {error.summary}")
                if error.reason:
                    lines.append(f"      Why: {error.reason}")
                for participant in error.participants:
                    lines.append(f"      {participant.role or 'participant'}: {error._subject_name(participant.subject)}")
            else:
                lines.append(f"    - {policy.value}: {error}")
        return "\n".join(lines)


class JointPlanner(Generic[T]):
    """Run complete plan attempts against isolated address-pool clones."""

    def __init__(self, addrgen: AddrGen):
        self._base = addrgen

    def solve(
        self,
        attempt: Callable[[AddrGen, ChoicePolicy], PlanBranch[T]],
        *,
        policies: Tuple[ChoicePolicy, ...] = (
            ChoicePolicy.PREFERRED,
            ChoicePolicy.MINIMUM_GEOMETRY,
        ),
    ) -> PlanBranch[T]:
        """Return the first complete branch, preferred policy first."""

        failures = []
        for policy in policies:
            branch_addrgen = self._base.clone()
            try:
                branch = attempt(branch_addrgen, policy)
                branch.topology.validate_required_coverage()
                return branch
            except (AddrGenError, TopologyConflict) as exc:
                failures.append((policy, exc))
                if isinstance(exc, TopologyConflict) and not exc.retriable:
                    raise
        error = JointPlanningError(tuple(failures))
        if failures:
            raise error from failures[-1][1]
        raise error
