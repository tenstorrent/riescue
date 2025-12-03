# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.schedulers import DefaultScheduler, LinuxModeScheduler, ParallelScheduler, SimultaneousScheduler


class TestScheduler(AssemblyGenerator):
    """
    Generates test scheduler assembly code for coordinating test execution.
    Hands control to individual tests and manages test completion flow.

    Scheduler supports different modes for scheduling tests:
    - single hart (default): tests are scheduled for reapeat_times number of times
    - Parallel MP: All harts run different tests in parallel with no sync before running
    - Simulataneous MP: All harts run the same test in parallel, with a sync barrier before starting the test
    - linux mode: tests are scheduled at runtime using a randomization algorithm. Can be ran endlessly if repeat_times=-1

    Scheduler runs in ``handler_mode`` which can be M, HS, or VS. Tests can run in ``user_mode`` or ``supervisor_mode``. The scheduler interface consists of

    scheduler__init - initializes the scheduler and runs test_setup
    scheduler__dispatch - load a0 with the next test's address
    scheduler__finished - called when the scheduler is finished with all tests OR when ``end_test`` is called
    scheduler__panic - trap vector for scheduler panic. Jumps to end of test immediately.
    """

    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        dtests: list[str] = list(self.pool.discrete_tests.keys())  #: all discrete tests

        if self.featmgr.repeat_times == -1 and not self.featmgr.linux_mode:
            raise ValueError("Can only use repeat_times == -1 with linux mode")

        self.scheduler: AssemblyGenerator
        if self.featmgr.linux_mode:
            self.scheduler = LinuxModeScheduler(dtests=dtests, **kwargs)
        elif self.mp_active:
            if self.mp_parallel:
                self.scheduler = ParallelScheduler(dtests=dtests, **kwargs)
            else:
                self.scheduler = SimultaneousScheduler(dtests=dtests, **kwargs)
        else:
            self.scheduler = DefaultScheduler(dtests=dtests, **kwargs)

    def generate(self) -> str:
        """
        Generates scheduler assembly code for scheduling tests.
        """

        return self.scheduler.generate()
