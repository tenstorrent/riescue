# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.schedulers import DefaultScheduler, LinuxModeScheduler, MpScheduler


class TestScheduler(AssemblyGenerator):
    """
    Generates test scheduler assembly code for coordinating test execution.

    Manages test sequencing, randomization, and synchronization across single or multiple harts.
    Handles barriers, mutexes, and test cleanup coordination.
    Supports both sequential and parallel test execution modes.

    Hands control to individual tests and manages test completion flow.


    Scheduler supports different modes for scheduling tests:
    - single hart (default): tests are scheduled for reapeat_times number of times
    - Parallel MP: All harts run different tests in parallel with no sync before running
    - Simulataneous MP: All harts run the same test in parallel, with a sync barrier before starting the test
    - linux mode: tests are scheduled at runtime using a randomization algorithm. Can be ran endlessly if repeat_times=-1

    It seems like this would benefit greatly from not re-using the same code for all modes, and separating the logic out.
    The logic for a single hart, default mode isn't that complex. But reusing the same code for all modes means there's
    undocumented dependencies (expecting the t1 to be an offset, or the number of harts, etc.)

    Need to have some scheduler interface that Runtime code understands, i.e.
    - scheduler__init
    - scheduler__dispatch
    - scheduler__finished
    - scheduler__panic

    This would allow other code to just know how to start and continue the scheduler.
    Having a panic routine would make it a lot easier to debug errors. But this requires the scheduler to be running in a privileged mode (M or S)
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
            self.scheduler = MpScheduler(dtests=dtests, **kwargs)
        else:
            self.scheduler = DefaultScheduler(dtests=dtests, **kwargs)

    def generate(self) -> str:
        """
        Generates scheduler assembly code for scheduling tests.
        """

        return self.scheduler.generate()
