# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Tuple, Any

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.schedulers.scheduler import Scheduler


class ParallelScheduler(Scheduler):
    """
    Generates test scheduler assembly code for MP parallel mode.

    All harts run different tests in parallel with no sync before running.
    Tests are selected randomly, no two harts run the same test at the same time.
    """

    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def scheduler_routines(self) -> str:
        """
        Additional scheduler code that is called by the scheduler
        """
        code = ""
        code += self.get_next_test()

        return code

    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading a0 with the next test.
        """
        code = ""

        # insert CSR read randomization logic here if allowed
        if not self.featmgr.no_random_csr_reads:
            code += "scheduler__csr_read_randomization:\n"
            code += self.csr_read_randomization() + "\n"

        # Generates main scheduler logic.
        # Assumes t1 has the previous number of runs left.
        # TODO for linux + parallel, just dont decrement num runs, write it, check it etc
        # TODO for linux + simultaneous, need to randomize next test pointers in a thread safe way and also don't decrement num runs

        # How parallel dispatch works:
        # 1. Hart enters dispatch, releases any previously-held mutex
        # 2. calls scheduler__next_test to get next test label
        #   Selects a random test and probes its section of num_runs to find a test with count > 0
        #   Tries to acquire that test's mutex
        #   If acquired, decrements num_runs
        #   If not acquired (another hart holds it), picks a different test
        # 3. Scheduler loads the selected test label from os_test_sequence into t1 and jumps to execute_test

        code += f"""
        scheduler__dispatch_post_csr_read_randomization:
                csrr {self.hartid_reg}, mhartid
                slli s11, {self.hartid_reg}, 3

                # Check if we are storing nonzero in held_locks for this hart, we hold it to this point for readability sake.
                la t2, held_locks
                add t2, t2, s11
                ld t1, 0(t2)
                beqz t1, scheduler__continue_scheduling # If nonzero, we are holding a lock, so continue scheduling

            scheduler__release_lock:
                amoswap.d.rl x0, x0, (t1)
                sd zero, 0(t2) # clear the held_lock variable, so eot doesn't release it.

            # This is a big routine so we call it instead of inlining it.
            scheduler__continue_scheduling:
                call scheduler__next_test
                # a0 contains the offset into the test related arrays for the selected test.
                # a1 contains the locked test mutex address

                la t2, held_locks
                add t2, t2, s11
                # Store the lock test_mutex address we have not released
                sd a1, 0(t2)
        """

        # Store current test index for RVCP pass/fail messages
        # t0 = offset from os_test_sequence in bytes (index * 8)
        # Look up the discrete test index from dtest_index_map
        if self.featmgr.rvcp_print_enabled():
            code += f"""
            # Store current test index for RVCP messages
            # a0 = offset in bytes, convert to word offset for dtest_index_map lookup
            srli t3, a0, 1  # t3 = t0 / 2 (convert byte offset to word offset: t0/8 * 4)
            la t4, dtest_index_map
            add t3, t4, t3
            lw t3, 0(t3)   # t3 = dtest_index_map[position]
            {self.current_test_index.store(src_reg="t3")}
        """

        code += """
        # t1 = os_test_sequence + ((--num_runs) * 8])
        # or os_test_sequence[--num_runs]
        scheduler__load_test_addr:
            la t1, os_test_sequence
            add t0, a0, t1
            ld t1, 0(t0)
        """

        return code

    def scheduler_variables(self) -> str:
        """
        Generates scheduler-local variables.
        Tests x repeat_times run across all cores from a shared pool.
        Global num_runs (one per test), test_select lock, LCG random selection.
        """
        code = ""
        if self.featmgr.force_alignment:
            code += ".balign 8, 0\n"
        code += f"""
        # Scheduler local variables
        scheduler__seed:
            .dword {self.rng.get_seed()}
        """
        code += (
            """
        # Global lock for test selection - must be held when picking a test
        .align 3
        test_select_lock:
            .dword 0x0

        # Total pending runs (avoids summing num_runs each dispatch)
        .align 3
        pending_num_runs:
            .dword """
            + str(len(self.dtests) * self.featmgr.repeat_times)
            + """

        # Remaining runs per test (shared across all cores). Each test runs repeat_times total.
        num_runs:
        """
        )
        for test in self.dtests:
            code += f"{test}_num_runs:\n"
            code += ".dword " + hex(self.featmgr.repeat_times) + "\n"

        code += self._fs_vs_rr_tables_section()
        code += """
        # One lock per test.
        .align 3
        test_mutexes:
        """
        for test in self.dtests:
            code += f"{test}_mutex:\n"
            code += ".dword 0x0\n"

        code += """
        # Array of test label addresses: [test1_addr, test2_addr, ..., test_cleanup]
        os_test_sequence:
        """
        for test in self.dtests:
            code += f"{test}_addr:\n"
            code += f".dword {test}\n"
        code += ".dword test_cleanup\n"

        code += """
        # Per-hart LCG seeds for random test selection
        scheduler__seeds:
        """
        for hart in range(self.featmgr.num_cpus):
            code += f"hart_{hart}_seed:\n"
            code += ".dword " + hex(self.rng.random_nbit(64)) + "\n"

        code += """
        # Tracks which mutex each hart currently holds
        held_locks:
        """
        for hart in range(self.featmgr.num_cpus):
            code += f"hart_{hart}_held_lock:\n"
            code += ".dword 0x0\n"

        return code

    def execute_test(self) -> str:
        """
        Logic responsible for executing a test.
        Executes test loaded into t1
        """
        code = ""

        if self.test_priv == RV.RiscvPrivileges.MACHINE:
            code += "jr t1\n"
        else:
            # For user mode use sret to jump to test
            code += self.switch_test_privilege(
                from_priv=RV.RiscvPrivileges.SUPER,
                to_priv=self.test_priv,
                jump_register="t1",
            )
        return code

    def scheduler_finished(self) -> str:
        """
        Parallel scheduler doesn't account for test_cleanup, so this needs to run it.

        Since trap handler jumps to scheduler__dispatch, after running test_cleanup, it will jump to scheduler__dispatch again.
        So this needs a global flag to check if test_cleanup has been ran. Assumes hartid is in s1.

        This has a runtime penalty of running dispatch again (iterating through array and checking all num_runs are 0)

        FIXME: Use the hartid from the hart-local context instead.
        """

        code = """
    # check if scheduler__cleanup_was_ran flag has been set for this hart
    la t0, scheduler__test_cleanup_ran
    slli t1, s1, 3
    add t0, t0, t1
    ld t1, 0(t0)
    bnez t1, scheduler__cleanup_was_ran

    # when not set, set it and run test_cleanup
    li t1, 1
    sd t1, 0(t0)
        """

        # Set current test index to test_cleanup special value
        if self.featmgr.rvcp_print_enabled():
            code += f"""
    # Set test_cleanup index for RVCP messages
    li t3, 0xFFFFFFFF  # test_cleanup special index
    {self.current_test_index.store(src_reg="t3")}
        """

        code += """
    ld t1, (scheduler__test_cleanup_ptr)
    j scheduler__execute_test




scheduler__cleanup_was_ran:
    li gp, 1
    j eot__end_test

    .balign 8, 0
    scheduler__test_cleanup_ran:
    """
        for hart in range(self.featmgr.num_cpus):
            code += f"test_cleanup_hart_{hart}_ran:\n"
            code += ".dword 0x0\n"

        return code

    def get_next_test(self) -> str:
        """
        Get the next test to run. Loads the next test's address into a0

        Assumes ``s1`` contains the hartid and ``s11`` contains the hartid scaled by 8.

        CORE_BALANCED: Acquire test_select lock, check if tests remain, LCG random select,
        walk test list until % satisfied. If test locked, pick another. Lock test, decrement
        num_runs, release test_select. If no tests left, goto scheduler__finished.
        """

        num_tests = len(self.dtests)
        return f"""
        scheduler__next_test:
            # Setup before test_select critical section (addresses, LCG constants, walk bound)
            la s6, test_mutexes
            la s7, test_select_lock
            la s8, pending_num_runs
            la s9, num_runs
            la s10, scheduler__seeds
            add s10, s10, s11
            li t4, 1664525
            li t5, 1013904223
            li t3, 1

            scheduler__next_test__test_select_lock:
            {Routines.place_acquire_lock(
                name="scheduler__next_test__test_select_lock",
                lock_addr_reg="s7",
                swap_val_reg="t3",
                work_reg="t1",
                end_test_label="os_end_test_addr_pa",
                max_tries=self.MAX_OS_BARRIER_TRIES,
                use_zawrs=self.featmgr.is_feature_enabled("zawrs"),
                bare=True,
                lock_reg_prelaoded=True,
                )}

            ld a0, 0(s8)
            beqz a0, scheduler__next_test__no_tests_release_lock

            # Generate new seed for random test selection
            ld a3, 0(s10)
            mul a3, a3, t4
            add a3, a3, t5
            sd a3, 0(s10)
            remu a4, a3, a0

            li a5, 0 # cumulative count of num_runs
            li a0, 0 # index/offset = 0
            li t0, {num_tests}
        scheduler__next_test__walk:
            add t2, s9, a0
            ld t3, 0(t2)
            add a5, a5, t3
            bgtu a5, a4, scheduler__next_test__selected
            addi a0, a0, 8
            addi t0, t0, -1
            bnez t0, scheduler__next_test__walk
            j scheduler__next_test__release_and_retry

        scheduler__next_test__selected:
            # a0 contains the offset into the test related arrays for the selected test.

            # Try and lock the test
            add a1, s6, a0
            ld t1, 0(a1)
            bnez t1, scheduler__next_test__release_and_retry
            amoswap.d.aq t1, t3, 0(a1)
            bnez t1, scheduler__next_test__release_and_retry

            # Successful test lock
            # decrement the num_runs for the selected test
            add t1, s9, a0
            ld t0, 0(t1)
            addi t0, t0, -1
            sd t0, 0(t1)

            # decrement the pending_num_runs
            ld t0, 0(s8)
            addi t0, t0, -1
            sd t0, 0(s8)

            amoswap.d.rl x0, x0, 0(s7) # release test selection lock
            # a0 contains the offset into the test related arrays for the selected test.
            # a1 contains the locked test mutex address
            ret

        scheduler__next_test__release_and_retry:
            {Routines.place_release_lock(name="scheduler__next_test__release_and_retry", lock_addr_reg="s7")}
            j scheduler__next_test__test_select_lock

        scheduler__next_test__no_tests_release_lock:
            {Routines.place_release_lock(name="scheduler__next_test__no_tests_release_lock", lock_addr_reg="s7")}
            j scheduler__finished
        """
