# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable

from coretp import TestPlan, TestEnv, TestEnvSolver
from coretp.rv_enums import PrivilegeMode

from riescue.compliance.test_plan.actions import ActionRegistry
from riescue.compliance.test_plan.factory import TestPlanFactory
from riescue.compliance.test_plan.types import DiscreteTest, AssemblyFile, Header, TestCase, TextBlock
from riescue.compliance.test_plan.transformer import Transformer
from riescue.compliance.test_plan.memory import MemoryRegistry
from riescue.compliance.test_plan.actions import CsrReadAction, CsrWriteAction
from riescue.compliance.config import TpCfg
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

    def __init__(self, cfg: TpCfg, rng: RandNum, action_registry: Optional[ActionRegistry] = None, env_constraints: Predicates = None):
        self.rng = rng
        self.mem_reg = MemoryRegistry(cfg)
        self.featmgr = cfg.featmgr  # kept for top-of-test directive emission (e.g. IMSIC page mapping)

        if env_constraints is None:
            self.env_solver = TestEnvSolver()
        else:
            self.env_solver = TestEnvSolver(env_constraints)

        # Create factory once since it only depends on action_registry
        self.test_plan_factory = TestPlanFactory(action_registry=action_registry)
        self.transformer = Transformer(rng=self.rng, mem_reg=self.mem_reg, featmgr=cfg.featmgr, isa=cfg.isa)

    def generate_test_plan(self, test_plan: TestPlan) -> str:
        """
        Generaete a finished assembly test file from a ``TestPlan`` object.

        :param test_plan: ``coretp.TestPlan`` object containing scenarios and test environments.
        """
        discrete_tests = self.build(test_plan)
        env = self.solve(discrete_tests)
        return self.generate(
            discrete_tests,
            env,
            test_plan.name,
            excp_handler_pre=test_plan.excp_handler_pre,
            excp_handler_post=test_plan.excp_handler_post,
        )

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

    def generate(
        self,
        discrete_tests: list[DiscreteTest],
        env: TestEnv,
        test_plan_name: str = "generated_test_plan",
        excp_handler_pre: Optional[str] = None,
        excp_handler_post: Optional[str] = None,
    ) -> str:
        """
        Generate a string of assembly code for the given :class:`DiscreteTest` objects and ``TestEnv``.

        .. warning::

            This function should only be called once per list of ``DiscreteTests``.
            This modifies the ``DiscreteTest`` objects in place and re-running shouldn't be done

        :param discrete_tests: List of :class:`DiscreteTest` objects to generate.
        :param env: ``TestEnv`` object to generate.
        :param test_plan_name: Name of the test plan to use in the generated test case. - FIXME: In the future, maybe have discrete tests store TestPlan name as metadata?
        :param excp_handler_pre: Optional plan-wide assembly body to emit inside an
            ``excp_handler_pre:`` label (terminated with ``ret``). When only one
            of pre/post is set, the missing label is emitted with a ``nop`` body
            so the runtime hook pointer pair always resolves. The test must be
            run with ``--excp_hooks`` for the runtime trap handler to call it.
        :param excp_handler_post: Optional plan-wide assembly body to emit inside an
            ``excp_handler_post:`` label (terminated with ``ret``). The test must be
            run with ``--excp_hooks`` for the runtime trap handler to call it.
        """

        # filtered discrete tests to only include tests that match the environment
        filtered_discrete_tests = []
        for test in discrete_tests:
            # FIXME: NO support on multiple harts yet, we can only do tests that cater to single hart environments
            if self._test_for_filtering(test, env):
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

        # Plan-wide ``excp_handler_pre`` / ``excp_handler_post`` labels.
        #
        # Emitted in .code so ``--excp_hooks`` resolves the function pointer to a
        # user-section symbol (subject to the .code VMA→LMA relocation that the
        # M-mode trap handler applies in _call_excp_hook).
        #
        # ``--excp_hooks`` registers runtime pointers for BOTH labels (see
        # opsys.OpSys runtime_pointers), so when the plan sets either of pre/post
        # we must define both to keep the link resolving — fill the absent body
        # with a ``nop``.
        if excp_handler_pre or excp_handler_post:
            pre_body = excp_handler_pre if excp_handler_pre else "    nop"
            post_body = excp_handler_post if excp_handler_post else "    nop"
            text.blocks.append(TestCase([TextBlock(label="excp_handler_pre", text=[pre_body, "ret"])]))
            text.blocks.append(TestCase([TextBlock(label="excp_handler_post", text=[post_body, "ret"])]))

        # When --map_imsic_pages is set, identity-map both the M-IMSIC
        # (PA 0x40000000) and S-IMSIC (PA 0x44000000) pages at the top of the
        # test so HS/S/U scenarios can store to RVMODEL_SET_M/SEXT_INT's
        # target addresses without page-faulting. Emitted unconditionally
        # (no MEI/SEI presence check) per design: the flag is the opt-in,
        # and scenarios that never touch the IMSIC just carry harmless
        # extra PTEs.
        #
        # parser.py:96 matches ``;#page_mapping`` via ``line.startswith()`` so
        # the directives must land at column 0 — Header.emit() already emits
        # at column 0, so passing them via ``extra_directives`` keeps them
        # there. generator.py:629-652 handles ``phys_addr=`` alone as
        # identity-mapping VA→PA and auto-creates the named Address entries,
        # so no companion ``;#random_addr`` is needed.
        #
        # Multi-hart stride (PA + hartid * 0x40000) is intentionally not
        # emitted here — mirrors the ``hart=0`` hard-code in the sibling
        # ``;#enable_ext_intr_id`` line elsewhere. MP support is a follow-up.
        extra_directives: list[str] = []
        if self.featmgr.map_imsic_pages:
            extra_directives.append(";#page_mapping(lin_name=mimsic_m_lin_h0, phys_name=mimsic_m_phys_h0, phys_addr=0x40000000, pagesize=['4kb'], v=1, r=1, w=1, a=1, d=1)")
            extra_directives.append(";#page_mapping(lin_name=simsic_s_lin_h0, phys_name=simsic_s_phys_h0, phys_addr=0x44000000, pagesize=['4kb'], v=1, r=1, w=1, a=1, d=1)")
            # Guest interrupt file 1 for hart 0 (sbase + 1*4KB = 0x44001000).
            # Not 2MB-aligned so pagesize=['4kb'] is mandatory to avoid a
            # misaligned-superpage fault (same constraint as ACLINT pages).
            extra_directives.append(";#page_mapping(lin_name=gimsic_guest1_lin_h0, phys_name=gimsic_guest1_phys_h0, phys_addr=0x44001000, pagesize=['4kb'], v=1, r=1, w=1, a=1, d=1)")
            # Guest interrupt file 2 for hart 0 (sbase + 2*4KB = 0x44002000),
            # poked by RVMODEL_SET_HGEI_INT (guest file index 2) to drive SGEI
            # independently of VSEI.
            extra_directives.append(";#page_mapping(lin_name=gimsic_guest2_lin_h0, phys_name=gimsic_guest2_phys_h0, phys_addr=0x44002000, pagesize=['4kb'], v=1, r=1, w=1, a=1, d=1)")

        # When --map_aclint_pages is set, identity-map the ACLINT peripheral
        # pages so HS/S/U scenarios can store to RVMODEL_SET/CLR_MSW_INT and
        # RVMODEL_SET/CLR_MTIMER_INT target addresses without page-faulting.
        # ACLINT layout (per whisper_config):
        #   0x42180000 .. 0x42183FFF : MSIP    (hart 0 @ +0x0)
        #   0x42184000 .. 0x4218BFF7 : MTIMECMP (hart 0 @ +0x4000)
        #   0x4218BFF8 .. 0x4218BFFF : MTIME   (shared, at +0xBFF8)
        # The MSIP, MTIMECMP, and MTIME registers each land on a distinct 4KB
        # page; emit one identity mapping per page. Hart=0 hard-coded for now,
        # mirroring the same constraint in the IMSIC block above; MP support
        # is a follow-up.
        # pagesize=['4kb'] is mandatory here: the ACLINT register pages
        # (0x42180000/0x42184000/0x4218B000) are 4KB-aligned but NOT 2MB-aligned,
        # so if the allocator selects a 2MB leaf the resulting PTE has PPN[0]!=0
        # and the walk faults with "misaligned superpage" — seen empirically as
        # a Store/AMO page fault at 0x42184000 with the leaf PTE PPN=0x42180.
        if self.featmgr.map_aclint_pages:
            extra_directives.append(";#page_mapping(lin_name=aclint_msip_lin_h0, phys_name=aclint_msip_phys_h0, phys_addr=0x42180000, pagesize=['4kb'], v=1, r=1, w=1, a=1, d=1)")
            extra_directives.append(";#page_mapping(lin_name=aclint_mtimecmp_lin_h0, phys_name=aclint_mtimecmp_phys_h0, phys_addr=0x42184000, pagesize=['4kb'], v=1, r=1, w=1, a=1, d=1)")
            extra_directives.append(";#page_mapping(lin_name=aclint_mtime_lin, phys_name=aclint_mtime_phys, phys_addr=0x4218B000, pagesize=['4kb'], v=1, r=1, w=1, a=1, d=1)")

        header = Header.from_env(env=env, plan_name=test_plan_name, extra_directives=extra_directives)
        assembly_file = AssemblyFile(header=header, code=text, data=data)

        # generate text
        return assembly_file.emit()

    # internal methods #

    def _test_for_filtering(self, test: DiscreteTest, env: TestEnv) -> bool:
        """
        Check if a test should be filtered out based on the environment.
        """

        skip_test = False
        # FIXME: NO support on multiple harts yet, we can only do tests that cater to single hart environments
        return (
            env.paging_mode in test.env.paging_modes
            and env.g_paging_mode in test.env.g_paging_modes
            and env.priv in test.env.priv_modes
            and test.env.min_num_harts == 1
            and env.virtualized in test.env.virtualized
            and not skip_test
        )

    def _check_for_conflicting_labels(self, discrete_tests: list[DiscreteTest]) -> None:
        """Checks for duplicate labels in test plan, if multiple tests raises ValueError"""

        test_case_names = [t.name for t in discrete_tests]
        if len(test_case_names) != len(set(test_case_names)):
            raise ValueError("Found duplicate test label in TestPlan - Test case names must be unique")
