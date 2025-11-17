# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

from riescue.dtest_framework.runtime.schedulers.scheduler import Scheduler


class LinuxModeScheduler(Scheduler):
    """
    Test scheduler logic for linux mode / application code:

    - Runs tests in Machine mode without any loader or paging support.
    - Currently only supports single hart tests.


    TODO for linux + parallel, just dont decrement num runs, write it, check it etc
    TODO for linux + simultaneous, need to randomize next test pointers in a thread safe way and also don't decrement num runs
    """

    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.force_alignment = self.featmgr.force_alignment  # Align all instructions on 8 byte boundary

        if self.mp_active:
            raise ValueError("MP mode is not supported with linux mode")

        self.dtests: list[str] = list(self.pool.discrete_tests.keys())  #: All Discrete Tests in .rasm file
        self.dtests_sequence: list[str]  #: Sequence of tests to run. Each Discrete Test is repeated ``repeat_times``
        if self.featmgr.repeat_times == -1:
            self.dtests_sequence = [test for test in self.dtests]
        else:
            self.dtests_sequence = [test for test in self.dtests for _ in range(self.featmgr.repeat_times)]
        self.rng.shuffle(self.dtests_sequence)

    def scheduler_init(self) -> str:
        "Run the test_setup code here. This only gets executed once at the start of the test"
        code = [
            "ld t0, scheduler__test_setup_ptr",
            "jr t0",
        ]
        return "\n\t".join(code) + "\n"

    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading a0 with the next test.

        # TODO for linux + parallel, just dont decrement num runs (write it, check it etc)
        # TODO for linux + simultaneous, need to randomize next test pointers in a thread safe way and also don't decrement num runs
        """
        code = ""
        # insert CSR read randomization logic here if allowed
        if not self.featmgr.no_random_csr_reads:
            code += "scheduler__csr_read_randomization:\n"
            code += self.csr_read_randomization() + "\n"

        if self.featmgr.repeat_times != -1:
            code += self.endless_loop()

        code += f"""
scheduler__get_test_offset:
    la a1, scheduler__seed    # seed array
    li a2, {len(self.dtests)}    # target array length
    li a3, 8                # seed offset scale
    li a4, 8                # target offset scale
    li a5, 1                # num_ignore
    call os_rng_orig

    addi a0, a0, 1 # add num_ignore
    li t0, 8
    mul t0, t0, a0 # Scale by pointer size

# Get the pointer to the next test label and jump / sret to it
scheduler__start_next_test:
la t1, os_test_sequence
add t0, t0, t1    # t0 = os_test_sequence + offset
ld a0, 0(t0)      # a0 = [os_test_sequence]
        """

        return code

    def scheduler_variables(self) -> str:
        """
        Generates scheduler-local variables.
        """
        code = ""
        if self.featmgr.force_alignment:
            code += ".align 3\n"
        code += f"""


        scheduler__seed:
            .dword {self.rng.get_seed()}
        """
        code += f"""
        num_runs:
            # We need +1 below since we have cleanup as the last entry in the dtests_seq
            .dword {len(self.dtests_sequence)+1}
        """

        if self.featmgr.force_alignment:
            code += ".align 3\n"
        os_test_sequence = "    .dword test_setup\n"
        for test in self.dtests_sequence:
            os_test_sequence += f"    .dword {test}\n"
        os_test_sequence += "    .dword test_cleanup\n"

        code += f"""
        os_test_sequence:
        {os_test_sequence}
        """

        # previously test setup and cleanup were in the test sequence
        code += """
# Test pointers
scheduler__test_setup_ptr:
    .dword test_setup               # pointer to test's test_setup code, ran exactly once at start of test
scheduler__test_cleanup_ptr:
    .dword test_cleanup             # pointer to test's test_cleanup code, ran exactly once at end of test

        """

        return code

    def execute_test(self) -> str:
        """
        Jumps to test label in a0
        """
        code = ""
        code += "jr a0\n"
        return code

    def endless_loop(self) -> str:
        """
        Scheduler endless loop logic. Used to run tests endlessly
        """

        code = """
scheduler__endless_loop:
    # Load test pointer (all harts need to do this)
    la t0, num_runs

scheduler__load_test_pointer:
    """
        if self.featmgr.big_endian:
            code += """ld t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)
            srli t1, t1, 32
            bnez t1, scheduler__skip_reload_test_pointer
            ld t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)
            scheduler__skip_reload_test_pointer:
            """
        else:
            code += "lw t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)\n"
        code += f"""li gp, 0x{self.featmgr.eot_pass_value:x}
beqz t1, eot__end_test # end program, if zero

# Decrement num_runs and store it back
scheduler__decrement_num_runs:
addi t2, t1, -1
        """
        if self.featmgr.big_endian:
            code += "sd t2, 0(t0)\n"
        else:
            code += "sw t2, 0(t0)\n"
        return code
