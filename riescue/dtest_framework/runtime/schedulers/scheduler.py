# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Tuple, Any
from abc import ABC, abstractmethod

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class Scheduler(AssemblyGenerator, ABC):
    """
    Base scheduler, provides the scheduler interface for runtime.
    This includes scheduler assembly routines that are called by other runtime modules:

    - scheduler__init
    - scheduler__dispatch
    - scheduler__finished
    - scheduler__panic

    This class contains scaffolding for the scheduler interface, including jumping to test, switching privilege for test, scheduler error handling, etc.
    Actual scheduling logic is implemented in subclasses.

    Runs in ``handler_priv`` mode.
    """

    def __init__(self, dtests: list[str], **kwargs: Any):
        super().__init__(**kwargs)
        self.dtests = dtests
        self.force_alignment = self.featmgr.force_alignment  # Align all instructions on 8 byte boundary

        self.dtests_sequence: list[str]
        if self.featmgr.repeat_times == -1 or self.mp_parallel:
            self.dtests_sequence = [test for test in dtests]  # Schedule is randomized at runtime for linux mode and for parallel mp mode.
        else:
            self.dtests_sequence = [test for test in dtests for _ in range(self.featmgr.repeat_times)]
        self.rng.shuffle(self.dtests_sequence)

    @abstractmethod
    def scheduler_init(self) -> str:
        return ""

    @abstractmethod
    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading a0 with the next test.
        """
        return ""

    @abstractmethod
    def scheduler_variables(self) -> str:
        """
        Generates scheduler-local variables.
        """
        return ""

    def generate(self) -> str:
        """
        Generates scheduler assmebly code for scheduler. Subclasses should really only implement the abstract methods, scheduler_init, scheduler_dispatch, and scheduler_finished.
        """
        return f"""
.section .text

# Scheduler initialization; only ran once
{self.scheduler_init_label}:
    {self.scheduler_init()}

# Entry point for scheduling next test
{self.scheduler_dispatch_label}:
    {self._dispatch_setup()}
    {self.pre_dispatch_hooks()}
    {self.scheduler_dispatch()}
    {self.post_dispatch_hooks()}

# Jump to test. Test address should be loaded into a0 before running
scheduler__execute_test:
    {self.execute_test()}

# Exit point for scheduler; only ran when all tests are finished
{self.scheduler_finished_label}:
    {self.scheduler_finished()}

# Panic point for scheduler; If an exception occurs during scheduling, it causes the test to end immediately. Only internal errors should happen here.
# Interrupts are disabled during scheduling and resumed when the scheduler is fininished.
{self.kernel_panic(self.scheduler_panic_label)}

# Additional scheduler routines
{self.scheduler_routines()}

# scheduler-local variables
{self.scheduler_variables()}
"""

    def pre_dispatch_hooks(self) -> str:
        """
        Pre-dispatch hooks. Add additional code here to be executed before test is executed. Ensure a0 contains the test to jump to at the end
        """
        return ""

    def post_dispatch_hooks(self) -> str:
        """
        Post-dispatch hooks. Add additional code here to be executed after test is executed. Ensure a0 contains the test to jump to at the end
        """
        return ""

    def scheduler_finished(self) -> str:
        """
        Subclasses can override this to run additional cleanup code.
        """
        return ""

    def scheduler_routines(self) -> str:
        """
        Additional scheduler routines, called by scheduler
        """
        return ""

    def _dispatch_setup(self) -> str:
        """
        Setup for scheduler dispatch.
        Disables interrupts, sets up kernel panic handler to catch internal errors.
        """

        return ""

    def execute_test(self) -> str:
        """
        Handles jumping to test. Test address should be loaded into ``a0``

        If test is in M/S mode, handler should already be in correct privilege mode, and can jump to test.
        """

        # set tvec
        # enable interrupts

        if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
            code = "jr a0\n"
        else:
            # For user mode use sret to jump to test
            code = self.switch_test_privilege(
                from_priv=RV.RiscvPrivileges.SUPER,
                to_priv=self.featmgr.priv_mode,
                jump_register="a0",
            )
        return code
