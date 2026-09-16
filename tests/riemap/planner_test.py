# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen import AddrGen
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.errors import FailureKind
from riescue.riemap.layout import TopologyConflict, TopologyPlan
from riescue.riemap.memory import Memory
from riescue.riemap.planner import (
    ChoicePolicy,
    JointPlanningError,
    JointPlanner,
    PlanBranch,
)


def _addrgen():
    memory = Memory.from_dict(
        {
            "dram": {
                "dram0": {
                    "address": "0x80000000",
                    "size": "0x100000",
                    "cacheable": True,
                    "configurable": True,
                }
            }
        }
    )
    return AddrGen(RandNum(seed=1), memory)


class TestJointPlanner(unittest.TestCase):
    def test_rejected_branch_does_not_leak_reservations(self):
        addrgen = _addrgen()
        before = addrgen.allocated_physical_intervals()
        attempts = []

        def attempt(branch_addrgen, policy):
            attempts.append(policy)
            branch_addrgen.reserve_memory(
                RV.AddressType.PHYSICAL,
                0x80000000,
                0x1000,
            )
            if policy is ChoicePolicy.PREFERRED:
                raise TopologyConflict(
                    "movable topology collision",
                    retriable=True,
                )
            return PlanBranch(
                addrgen=branch_addrgen,
                allocation="winner",
                topology=TopologyPlan(),
            )

        branch = JointPlanner(addrgen).solve(attempt)

        self.assertEqual(branch.allocation, "winner")
        self.assertEqual(
            attempts,
            [
                ChoicePolicy.PREFERRED,
                ChoicePolicy.MINIMUM_GEOMETRY,
            ],
        )
        self.assertEqual(addrgen.allocated_physical_intervals(), before)
        self.assertEqual(
            branch.addrgen.allocated_physical_intervals(),
            [(0x80000000, 0x80001000)],
        )

    def test_hard_topology_conflict_does_not_retry(self):
        attempts = 0

        def attempt(branch_addrgen, _policy):
            nonlocal attempts
            attempts += 1
            raise TopologyConflict(
                "pinned contradiction",
                retriable=False,
            )

        with self.assertRaisesRegex(
            TopologyConflict,
            "pinned contradiction",
        ):
            JointPlanner(_addrgen()).solve(attempt)
        self.assertEqual(attempts, 1)

    def test_all_policy_failures_are_preserved(self):
        def attempt(_branch_addrgen, policy):
            raise AddrGenError(f"{policy.value} failed")

        with self.assertRaises(JointPlanningError) as failed:
            JointPlanner(_addrgen()).solve(attempt)

        error = failed.exception
        self.assertEqual(error.kind, FailureKind.JOINT_PLANNING)
        self.assertEqual([policy for policy, _cause in error.failures], [ChoicePolicy.PREFERRED, ChoicePolicy.MINIMUM_GEOMETRY])
        self.assertIn("preferred failed", str(error))
        self.assertIn("minimum-geometry failed", str(error))


if __name__ == "__main__":
    unittest.main()
