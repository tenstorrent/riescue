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
from typing import Any, Callable, Generic, Optional, Tuple, TypeVar

from riescue.riemap.addrgen import AddrGen
from riescue.riemap.addrgen.exceptions import AddrGenError
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


class JointPlanningError(AddrGenError):
    """No address/topology/choice branch satisfies all declarations."""


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

        last_error: Optional[BaseException] = None
        for policy in policies:
            branch_addrgen = self._base.clone()
            try:
                branch = attempt(branch_addrgen, policy)
                branch.topology.validate_required_coverage()
                return branch
            except (AddrGenError, TopologyConflict) as exc:
                last_error = exc
                if isinstance(exc, TopologyConflict) and not exc.retriable:
                    raise
        if isinstance(last_error, AddrGenError):
            raise last_error
        raise JointPlanningError("joint address/topology planning has no feasible assignment") from last_error
