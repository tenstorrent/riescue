# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Tuple, Any

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.schedulers.scheduler import Scheduler


class DefaultScheduler(Scheduler):
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
        self.mhartid_offset = self.variable_manager.get_variable("mhartid").offset

    def scheduler_init(self) -> str:
        "Runs test_setup code. Loads test_setup into a0 and launches scheduler__execute_test"
        return """
    la a0, scheduler__test_setup_ptr
    ld a0, (a0)
    j scheduler__execute_test
"""

    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading a0 with the next test.
        """
        code = ""

        # insert CSR read randomization logic here if allowed
        if not self.featmgr.no_random_csr_reads:
            code += "scheduler__csr_read_randomization:\n"
            code += self.csr_read_randomization() + "\n"

        code += f"""
        # (num_runs == 0) goto scheduler__finished
        scheduler__load_num_runs:
            la t0, num_runs
            lw t1, 0(t0)
            beqz t1, {self.scheduler_finished_label}

        scheduler__decrement_num_runs:
            addi t2, t1, -1
            sw t2, 0(t0)

        scheduler__calc_test_pointer:
            slli t0, t2, 3

        # a0 = os_test_sequence[(--num_runs) * 8]
        scheduler__start_next_test:
            la t1, os_test_sequence
            add t0, t0, t1
            ld a0, 0(t0)
        """

        return code
