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

    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
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

        # FIXME Does this need to call place_retrieve_hartid so many times?

        retrieve_selected_test_label_offset = self.place_retrieve_memory_indexed_address(
            index_array_name="selected_test_label_offset", target_array_name="os_test_sequence", index_element_size=8, hartid_reg=self.hartid_reg, work_reg_1="a0", dest_reg="t0"
        )
        # How parallel dispatch works:
        # 1. Hart enters dispatch, releases any previously-held mutex
        # 2. calls scheduler__next_test to get next test label
        #   Probes its section of num_runs to find a test with count > 0
        #   Tries to acquire that test's mutex
        #   If acquired, decrements num_runs, stores offsets into selected_*_offset arrays
        #   If not acquired (another hart holds it), picks a different test
        # 3. Scheduler loads the selected test label from os_test_sequence into t1

        code += f"csrr {self.hartid_reg}, mhartid\n"
        code += f"""
                # Check if we are storing nonzero in held_locks for this hart, we hold it to this point for readability sake.
                la a0, held_locks
                {Routines.place_offset_address_by_scaled_hartid(address_reg='a0', dest_reg='a0', hartid_reg=self.hartid_reg, work_reg='t1', scale=8)}
                ld t1, (a0)
                beqz t1, scheduler__continue_scheduling # If nonzero, we are holding a lock, so continue scheduling


            scheduler__release_lock:
                amoswap.d.rl x0, x0, (t1)
                sd zero, 0(a0) # clear the held_lock variable, so eot doesn't release it.

            # This is a big routine so we call it instead of inlining it.
            scheduler__continue_scheduling:
                call scheduler__next_test
                mv t2, a0 # Lock address we have not released, need to store it so we can release it later.

                # Retrieve test labels offset
                retrieve_test_label_offset:
                {retrieve_selected_test_label_offset}

                # Turn back into an offset now that our check is done
                la a0, os_test_sequence
                sub t0, t0, a0 # t0 = offset from os_test_sequence
                la a0, held_locks
                {Routines.place_offset_address_by_scaled_hartid(address_reg='a0', dest_reg='a0', hartid_reg=self.hartid_reg, work_reg='t1', scale=8)}
            scheduler__update_lock_status:
                # Store the lock address we have not released
                sd t2, 0(a0)

        # t1 = os_test_sequence + ((--num_runs) * 8])
        # or os_test_sequence[--num_runs]
        scheduler__load_test_addr:
            la t1, os_test_sequence
            add t0, t0, t1
            ld t1, 0(t0)
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
        # Scheduler local variables
        scheduler__seed:
            .dword {self.rng.get_seed()}
        """
        num_harts = self.featmgr.num_cpus
        harts_to_tests: dict[int, list[str]] = {}

        for hart in range(num_harts):
            if hart not in harts_to_tests:
                harts_to_tests[hart] = []

        if self.featmgr.parallel_scheduling_mode == RV.RiscvParallelSchedulingMode.ROUND_ROBIN:
            # Assign the tests round robin to the harts, with no harts sharing tests.
            for i, test in enumerate(self.dtests):
                hart = i % num_harts
                harts_to_tests[hart].append(test)
        elif self.featmgr.parallel_scheduling_mode == RV.RiscvParallelSchedulingMode.EXHAUSTIVE:
            # Exhaustive scheduling mode assigns all tests to all harts.
            for i, test in enumerate(self.dtests):
                for hart in range(num_harts):
                    harts_to_tests[hart].append(test)
        else:
            raise Exception(f"Unknown parallel scheduling mode: {self.featmgr.parallel_scheduling_mode}")

        # NOTE: other code is written assuming each hart has a count for each test, so for those tests
        #       a hart doesn't do, we set the count to 0.
        code += """

        # remaining tests to run per hart.
        num_runs:
        """
        for hart in range(self.featmgr.num_cpus):
            code += "hart_" + str(hart) + "_num_runs:\n"
            for test in self.dtests:
                code += f"hart_{str(hart)}_{test}:\n"
                if test in harts_to_tests[hart]:
                    code += ".dword " + hex(self.featmgr.repeat_times) + "\n"
                else:
                    code += ".dword 0x0\n"

        code += self._fs_vs_rr_tables_section()
        code += """
        # One lock per test.
        # A hart must hold the mutex before running that test.
        # Prevents two harts from running the same test concurrently.
        .align 3
        test_mutexes:
        """
        for test in self.dtests:
            code += "" + test + "_mutex:\n"
            code += ".dword 0x0\n"

        code += """\n
        # These are now just a list of the available test label names to go with positions in num_runs under each harts section
        # Array of test label addresses: [test1_addr, test2_addr, ...]
        os_test_sequence:
        """
        for test in self.dtests:
            code += "" + test + "_addr:\n"
            code += ".dword " + test + "\n"
        code += ".dword test_cleanup\n"

        code += """\n
        # Array of seeds: [seed1, seed2, ...]
        # unused
        scheduler__seeds:
        """
        for hart in range(self.featmgr.num_cpus):
            code += "hart_" + str(hart) + "_seed:\n"
            code += ".dword " + hex(self.rng.random_nbit(64)) + "\n"

        code += """\n
        # Tracks which mutex each hart currently holds so it can release it on next scheduler entry
        held_locks:
        """
        for hart in range(self.featmgr.num_cpus):
            code += "hart_" + str(hart) + "_held_lock:\n"
            code += ".dword 0x0\n"

        code += """\n
        # per-hart storage for the num runs offset
        selected_num_runs_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            code += "hart_" + str(hart) + "_selected_num_runs_offset:\n"
            code += ".dword 0x0\n"

        code += """\n
        # per-hart storage for the mutex offset
        selected_mutex_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            code += "hart_" + str(hart) + "_selected_mutex_offset:\n"
            code += ".dword 0x0\n"

        code += """
        # per-hart storage for the test label offset
        selected_test_label_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            code += "hart_" + str(hart) + "_selected_test_label_offset:\n"
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

    # if not set, set it and run test_cleanup
    li t1, 1
    sd t1, 0(t0)

    ld t1, (scheduler__test_cleanup_ptr)
    j scheduler__execute_test




scheduler__cleanup_was_ran:
    li gp, 1
    j eot__end_test

    .align 3
    scheduler__test_cleanup_ran:
    """
        for hart in range(self.featmgr.num_cpus):
            code += f"test_cleanup_hart_{hart}_ran:\n"
            code += ".dword 0x0\n"

        return code

    def place_retrieve_memory_indexed_address(
        self,
        index_array_name: str,
        target_array_name: str,
        index_element_size: int,
        hartid_reg: str,
        work_reg_1: str,
        dest_reg: str,
    ) -> str:
        """
        Use a precalculated and scaled offset stored in an num_harts elements long array to access data from an array with a more exotic layout.
        """

        return f"""
            la {work_reg_1}, {index_array_name}
            {Routines.place_offset_address_by_scaled_hartid(address_reg=work_reg_1, dest_reg=work_reg_1, hartid_reg=hartid_reg, work_reg=dest_reg, scale=index_element_size)}
            ld {dest_reg}, 0({work_reg_1}) # Load the index
            la {work_reg_1}, {target_array_name}
            add {dest_reg}, {work_reg_1}, {dest_reg} # Offset to the correct element
        """

    def get_next_test(self) -> str:
        """
        Get the next test to run. Loads the next test's address into a0

        Assumes ``s1`` contains the hartid.

        iterates through hart_n_num_runs array and finds first non-zero entry.
        If none found, jumps to eot__end_test

        Searches the num_runs array for a test with non-zero runs remaining, attempts atomic lock acquisition, and decrements the run counter.
        Jumps to scheduler__finished when no tests remain

        FIXME: Shouldn't use mul here. Use shift if possible. (Or just don't use the hartid at all?)
        FIXME: Document the calling convention of this routine, since it's meant to be returned. Alternatively, don't call it? Just inline in?


        :param name: Name prefix for generated labels
        :param array_length: Number of test elements
        :param num_test_labels_ignore: Number of test labels to skip
        """

        # this should be swapped for hart-local storage instead
        seed_offset_scale = 8  # size of each seed in the seeds array
        test_labels_offset_scale = 8  # size of each test label

        # these are up here because they are too long to inline with black settings
        retrieve_memory_indexed_address = self.place_retrieve_memory_indexed_address(
            index_array_name="selected_num_runs_offset", target_array_name="num_runs", index_element_size=8, hartid_reg=self.hartid_reg, work_reg_1="s9", dest_reg="t1"
        )
        retrieve_selected_mutex_offset = self.place_retrieve_memory_indexed_address(
            index_array_name="selected_mutex_offset", target_array_name="test_mutexes", index_element_size=8, hartid_reg=self.hartid_reg, work_reg_1="s9", dest_reg="a0"
        )
        retrieve_selected_num_runs_offset = self.place_retrieve_memory_indexed_address(
            index_array_name="selected_num_runs_offset", target_array_name="num_runs", index_element_size=8, hartid_reg=self.hartid_reg, work_reg_1="s9", dest_reg="t1"
        )

        return f"""
        scheduler__next_test:
            # load address for seeds array, the rng routine offsets to the correct hart's element
            # Unused. No runtime randomization is done here.
            # la a1, scheduler__seeds

            # load constants
            li s5, {test_labels_offset_scale}
            li s4, {seed_offset_scale}
            li s6, {len(self.dtests)}

            # load address for remaining num_runs array and offset to the first element for this hart
            la s8, num_runs
            mul s9, s6, {self.hartid_reg} # Number of elements in each sub array is the number of test labels.
            mul s9, s9, s5 # Size of the elements is the same as for the test labels.
            add s8, s8, s9 # Offset to the first element for this hart.

            # Changes a0 to have new lock address, a2 will have the address of the resource being protected by the lock.
            # load address for test locks array
            la a2, test_mutexes

        scheduler__next_test__get_new_lock_wish:
            # load constants
            li s5, {test_labels_offset_scale}
            li s4, {seed_offset_scale}
            li s6, {len(self.dtests)}

        scheduler__next_test__reset_offset:
            mv t0, s6 # Counter to detect cycle
            mv t3, s6 # Upper bound for the loop
            mv a2, zero # Additional offset

        # this entire routine is confusing and ineffecient.
        # This iterates through the num_runs array ( while t0>0), so there's no way we would ever reach the end of the array.
        # The work_reg_2 isn't necessary. this can be done with a single work_reg_1.

        # The end result of it is that s9 = first nonzero value in num_runs.
        # if there are none (end of array length), goto scheduler__finished.
        scheduler__next_test__find_first_nonzero_in_num_runs:
            addi t0, t0, -1 # Decrement num attempts
            mv s9, a2 # Copy additional offset

            # remu is useless since a2 starts at 0 and this loop only runs until t0>0
            remu s9, s9, t3 # Wrap around if we go past the end of the array
            slli s9, s9, 3 # Scale by 8 for dword sized elements

            # Work reg 1 holds relative offset, in bytes, from the start of this hart's section
            add t2, s9, s8 # Calculate address of element in num_runs
            ld t1, (t2)
            blt zero, t1, scheduler__next_test__success

            # Update additional offset
            addi a2, a2, 1
            blt zero, t0, scheduler__next_test__find_first_nonzero_in_num_runs

        scheduler__next_test__no_tests_left:
            j scheduler__finished

            # FIXME: this stores the values to an array for per-hart storage. This effectively stores an offset, then immediately loads it, then loads again from that offset.
            # this could just be a single load?
            # or at least re-use the stored value in the next loop?

        scheduler__next_test__success:
            # Return relative offset
            # work_reg_1, a3 = test_offset
            mv a3, s9 # work_reg_1 = test_offset

        # Store mutex offset
        scheduler__next_test__store_mutex_offset:
            la t0, selected_mutex_offset
            slli t1, {self.hartid_reg}, 3 # 8 bytes per entry
            add t0, t0, t1 # Offset to the hart's entry
            sd s9, (t0) # Store the offset to the mutex for this hart

        # Store test label offset; selected_num_runs_offset[hartid] = (hartid * num_tests * 8) + test_offset
        scheduler__next_test__store_test_label_offset:
            la t0, selected_test_label_offset
            add t0, t0, t1 # Offset to the hart's entry
            sd s9, (t0) # Store the offset to the test label for this hart

        # Store num runs offset
        scheduler__next_test__store_num_runs_offset:
            la t0, selected_num_runs_offset
            add t0, t0, t1 # Offset to the hart's entry
            mul a3, s6, t1 # Number of elements in each sub array is the number of test labels, scale that by hartid times element data size.
            add s9, s9, a3 # Offset to the selected element for this hart and test in num_runs.
            sd s9, (t0) # Store the offset to the num runs for this hart

            # Check if this num runs entry is actually zero.
            {retrieve_memory_indexed_address}
            ld t0, (t1)
            beqz t0, test_failed # shouldnt be zero here


            # Retrieve lock address
            retrieve_lock_address:
            {retrieve_selected_mutex_offset}

        scheduler__next_test__try_lock:
            li t0, 1        # Initialize swap value.
            ld           t1, (a0)     # Check if lock is held.
            bnez         t1,  scheduler__next_test__get_new_lock_wish # Retry if held.
            amoswap.d.aq t1, t0, (a0) # Attempt to acquire lock.
            bnez         t1,  scheduler__next_test__get_new_lock_wish # Retry if held.

            # Critical section technically continues well past here through the execution of the test.
            # Retrieve the remaining number of runs address for this hart and particular test
        scheduler__next_test__retrieve_remaining_num_runs_address:
            {retrieve_selected_num_runs_offset}

            ld t0, (t1)
            beqz t0, test_failed # shouldnt be zero here
            addi t0, t0, -1

            decrement_num_runs:
                sd t0, (t1)
                # No release here because we do not want to have to run the critical routine immediately without considering OS mechanisms.

            ret
            """
