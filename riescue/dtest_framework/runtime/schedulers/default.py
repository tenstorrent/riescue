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

        self.variable_manager.register_hart_variable(name="num_runs", value=0)
        self.num_runs = self.variable_manager.get_variable("num_runs")

    def scheduler_init(self) -> str:
        """
        Assumes hart context is already in tp. Sets up num_runs for run.
        This runs number of tests + test_cleanup
        """
        code = ""

        num_runs_init_value = len(self.dtests_sequence) + 1
        code += f"li t0, {num_runs_init_value} # setting num_runs to {num_runs_init_value}\n "
        code += self.num_runs.store(src_reg="t0")
        code += super().scheduler_init()
        return code

    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading t1 with the next test.
        """
        code = ""

        # insert CSR read randomization logic here if allowed
        if not self.featmgr.no_random_csr_reads:
            code += "scheduler__csr_read_randomization:\n"
            code += self.csr_read_randomization() + "\n"

        code += f"""
        # (num_runs == 0) goto scheduler__finished
        scheduler__load_num_runs:
            {self.num_runs.load(dest_reg="t1")}
            beqz t1, {self.scheduler_finished_label}

        scheduler__decrement_num_runs:
            addi t2, t1, -1
            {self.num_runs.store(src_reg="t2")}

        scheduler__calc_test_pointer:
            slli t0, t2, 3

        # t1 = os_test_sequence[(--num_runs) * 8]
        scheduler__load_test_addr:
            la t1, os_test_sequence
            add t0, t0, t1
            ld t1, 0(t0)
        """

        return code
