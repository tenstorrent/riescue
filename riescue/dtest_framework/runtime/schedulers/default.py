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
        "No additional scheduler init code is needed by default"
        return ""

    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading a0 with the next test.
        """
        code = ""

        # insert CSR read randomization logic here if allowed
        if not self.featmgr.no_random_csr_reads:
            code += "scheduler__csr_read_randomization:\n"
            code += self.csr_read_randomization() + "\n"

        # check if this is the first time scheduler is being ran
        # if so, do test_setup and skip to the next test.
        # too bad there's no way to execute code in a while loop only once
        # instead we need to load and store a variable to check if we are running the loop for the first time :)

        # FIXME: should the scheduler really be loading the pass value? or should this be in opsys?
        code += f"""
            la t0, scheduler__setup
            ld t1, 0(t0)
            sd x0, 0(t0)
            mv t0, x0
            bnez t1, scheduler__start_next_test

        scheduler__load_num_runs:
            la t0, num_runs
            lw t1, 0(t0)
            li gp, 0x{self.featmgr.eot_pass_value:x}
            beqz t1, os_end_test # end program, if zero

        scheduler__decrement_num_runs:
            addi t2, t1, -1
            sw t2, 0(t0)

        scheduler__calc_test_pointer:
            mv t0, t1
            slli t0, t0, 3

        # Get the pointer to the next test label and jump / sret to it
        scheduler__start_next_test:
            la t1, os_test_sequence
            add t0, t0, t1    # t0 = current os_test_sequence pointer
            ld t1, 0(t0)      # t1 = [os_test_sequence] (actual test label)
        """

        return code

    def scheduler_variables(self) -> str:
        """
        Generates scheduler-local variables.
        """
        code = ""
        code += f"""
        # Scheduler local variables
        .align 3
        scheduler__seed:
            .dword {self.rng.get_seed()}
        scheduler__setup:
        """

        code += ".dword 1 # Hart 0 scheduler setup\n"
        code += f"""
        num_runs:
            # We need +1 below since we have cleanup as the last entry in the dtests_seq
            .dword {len(self.dtests_sequence)+1}
        """

        code += """
        .align 3
        os_test_sequence:
            .dword test_setup
            .dword test_cleanup
        """
        for test in self.dtests_sequence:
            code += f"    .dword {test}\n"
        return code

    def execute_test(self) -> str:
        """
        Logic responsible for executing a test.

        FIXME: real execute test logic should be common to base scheduler.
        If this is needed, it should be in scheduler dispatch or in base scheduler.
        """
        code = ""

        if self.test_priv == RV.RiscvPrivileges.MACHINE:
            code += f"""
            # Schedule next test, t1 has the test_label
            # priv_mode: {self.test_priv}

            # Need barrier here so tests don't read num_runs after hart 0 updated it
            """
            code += "jr t1   # jump to t1\n"
        else:
            # For user mode use sret to jump to test
            code += self.switch_test_privilege(
                from_priv=RV.RiscvPrivileges.SUPER,
                to_priv=self.test_priv,
                jump_register="t1",
            )
        return code
