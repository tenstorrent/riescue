# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable

from coretp import TestPlan, TestEnv, TestEnvSolver

from riescue.compliance.test_plan.actions import ActionRegistry
from riescue.compliance.test_plan.factory import TestPlanFactory
from riescue.compliance.test_plan.types import DiscreteTest, AssemblyFile, Header
from riescue.compliance.test_plan.transformer import Transformer
from riescue.compliance.test_plan.memory import MemoryRegistry
from riescue.lib.rand import RandNum

# Shortcut for optional list of predicates (callable functions that return bools) used to pass in more constraints
Predicates = Optional[list[Callable[[TestEnv], bool]]]


class TestPlanGenerator:
    """
    Generate a RiescueD assembly .s file for a given test plan

    Responsible for generating a series of :class:`DiscreteTest` objects from a ``coretp.TestPlan`` object.
    Chooses a ``coretp.TestEnv`` to select, generates a series of ``DiscreteTest`` cases.
    Optionally returns all :class:`DiscreteTest` objects or returns a string of a ``.s`` file

    :param action_registry: Optional :class:`ActionRegistry` to use for generating actions. If not provided, the default action registry will be used.
    :param env_constraints: Optional list of constraints to use for solving the test environment. If not provided, the default constraints will be used.
    """

    def __init__(self, isa: str, rng: RandNum, action_registry: Optional[ActionRegistry] = None, env_constraints: Predicates = None):
        self.rng = rng
        self.mem_reg = MemoryRegistry()

        if env_constraints is None:
            self.env_solver = TestEnvSolver()
        else:
            self.env_solver = TestEnvSolver(env_constraints)

        # Create factory once since it only depends on action_registry
        self.test_plan_factory = TestPlanFactory(action_registry=action_registry)
        self.transformer = Transformer(rng=self.rng, mem_reg=self.mem_reg, isa=isa)

    def generate_test_plan(self, test_plan: TestPlan) -> str:
        """
        Generaete a finished assembly test file from a ``TestPlan`` object.

        :param test_plan: ``coretp.TestPlan`` object containing scenarios and test environments.
        """
        discrete_tests = self.build(test_plan)
        env = self.solve(discrete_tests)
        return self.generate(discrete_tests, env, test_plan.name)

    def build(self, test_plan: TestPlan) -> list[DiscreteTest]:
        """
        Generate a list of :class:`DiscreteTest` objects from a ``TestPlan`` object.

        :param test_plan: ``coretp.TestPlan`` object containing scenarios and test environments.
        """

        discrete_tests = []
        for scenario in test_plan.scenarios:
            discrete_test = self.test_plan_factory.build(scenario)
            if discrete_test is not None:
                discrete_tests.append(discrete_test)
        return discrete_tests

    def solve(self, discrete_tests: list[DiscreteTest], env_constraints: Predicates = None) -> TestEnv:
        """
        For a given set of :class:`DiscreteTest` objects, solve for a ``TestEnv`` that satisfies all constraints.

        :param discrete_tests: List of :class:`DiscreteTest` objects to solve.
        :param env_constraints: Optional list of predicates to use for solving the test environment. Adds to ``env_solver`` before solving.
        """
        if env_constraints is not None:
            for constraint in env_constraints:
                self.env_solver.add_predicate(constraint)
        envs = self.env_solver.solve([t.env for t in discrete_tests])
        if len(envs) == 0:
            raise ValueError("No valid TestEnv objects found for given tests. TestPlan may be too narrow with TestEnvCfgs or constraints are too strict")
        return self.rng.random_entry_in(envs)

    def generate(self, discrete_tests: list[DiscreteTest], env: TestEnv, test_plan_name: str = "generated_test_plan") -> str:
        """
        Generate a string of assembly code for the given :class:`DiscreteTest` objects and ``TestEnv``.

        .. warning::

            This function should only be called once per list of ``DiscreteTests``.
            This modifies the ``DiscreteTest`` objects in place and re-running shouldn't be done

        :param discrete_tests: List of :class:`DiscreteTest` objects to generate.
        :param env: ``TestEnv`` object to generate.
        :param test_plan_name: Name of the test plan to use in the generated test case. - FIXME: In the future, maybe have discrete tests store TestPlan name as metadata?
        """

        # filtered discrete tests to only include tests that match the environment
        filtered_discrete_tests = []
        for test in discrete_tests:
            # FIXME: NO support on multiple harts yet, we can only do tests that cater to single hart environments
            if env.paging_mode in test.env.paging_modes and env.priv in test.env.priv_modes and test.env.min_num_harts == 1:
                filtered_discrete_tests.append(test)
        discrete_tests = filtered_discrete_tests

        # Check for conflicting labels
        test_case_names = set()
        for test in discrete_tests:
            if test.name in test_case_names:
                raise ValueError(f"Test case name {test.name} is already used in the test plan")
            test_case_names.add(test.name)

        # generate test segments
        text, data = self.transformer.transform_tests(discrete_tests, env)
        header = Header.from_env(env=env, plan_name=test_plan_name)
        assembly_file = AssemblyFile(header=header, code=text, data=data)

        # generate text
        return assembly_file.emit()

    # internal methods #

    def _check_for_conflicting_labels(self, discrete_tests: list[DiscreteTest]) -> None:
        """Checks for duplicate labels in test plan, if multiple tests raises ValueError"""

        test_case_names = [t.name for t in discrete_tests]
        if len(test_case_names) != len(set(test_case_names)):
            raise ValueError("Found duplicate test label in TestPlan - Test case names must be unique")
