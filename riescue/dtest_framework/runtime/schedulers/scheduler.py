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
    {self.featmgr.call_hook(RV.HookPoint.PRE_DISPATCH)}
    {self.scheduler_dispatch()}
    {self.featmgr.call_hook(RV.HookPoint.POST_DISPATCH)}

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

# test pointers
.align 3
scheduler__test_setup_ptr:
    .dword test_setup
scheduler__test_cleanup_ptr:
    .dword test_cleanup

"""

    def scheduler_finished(self) -> str:
        """
        Done with scheduler, proceed to EOT. Override if any additional cleanup is needed
        """
        return """
    li gp, 1
    j eot__end_test
"""

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

    def scheduler_variables(self) -> str:
        """
        Default scheduler-local variables. Uses an array of test labels to store the test sequence.
        Essentially the array is``os_test_sequence = ["test_cleanup", "test01", "test02", ...]``,
        which can be indexed by the ``num_runs`` variable to get the next test to run

        .. note:: Not scalable for large repeat_times values
            This stores the self.dtests_sequence list into memory.
            Since self.dtest_sequences is the tests repeated repeat_times times, it has a size of O(MxN) for M tests and N ``repeat_times``.

            This can be optimized in the future using a static array of M tests and iterating through the array N times at runtime.

        Since test_cleanup is always ran last, it is inlcuded in ``os_test_sequence``.
        This is a bit faster than setting a flag in scheduler__finished to check if test_cleanup has been ran.
        """
        code = f"""
        .align 3
        num_runs:
            .dword {len(self.dtests_sequence)+1}

        .align 3
        os_test_sequence:
            .dword test_cleanup
        """
        for test in self.dtests_sequence:
            code += f"    .dword {test}\n"
        return code
