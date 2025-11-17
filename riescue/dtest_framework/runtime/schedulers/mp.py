# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Tuple, Any

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.schedulers.scheduler import Scheduler


class MpScheduler(Scheduler):
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
        self.force_alignment = self.featmgr.force_alignment  # Align all instructions on 8 byte boundary
        self.mhartid_offset = self.variable_manager.get_variable("mhartid").offset

    def scheduler_init(self) -> str:
        "No additional scheduler init code is needed by default"
        return ""

    def scheduler_routines(self) -> str:
        """
        Additional scheduler code that is called by the scheduler
        """
        code = ""
        code += "\n# Scheduler functions\n"
        code += self.os_barrier_amo()
        if self.mp_parallel:
            code += "os_parallel_get_next_exclusive_test:"
            code += self.place_get_next_exclusive_test(
                name="opgnet",
                fallback_routine_label="eot__skip_lock_release",  # Don't need to release lock if done with tests
                test_locks_label="test_mutexes",
                top_seed_label="scheduler__seeds",
                remaining_balance_array_label="num_runs",
                array_length=len(self.dtests),
                seed_offset_scale=8,
                test_labels_offset_scale=8,
                num_test_labels_ignore=1,
            )
            code += "\tret"

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

        code += "la t0, scheduler__setup\n"

        if self.mp_parallel:
            code += f"""
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}
            # Get
            li t2, 8 # While the harts are allowed to run test_setup at the same time, they also each must.
            mul t2, {self.hartid_reg}, t2
            add t0, t0, t2
            """

        code += "ld t1, 0(t0)\n"
        if self.mp_simultaneous:
            code += f"""
            {self.os_barrier_with_saves(regs_to_save=['t0', 't1'], scratch_regs=['s8', 's9'])}
            bnez {self.hartid_reg}, scheduler__not_allowed_to_write
            """

        code += "sd x0, 0(t0)\n"
        if self.mp_simultaneous:
            code += "scheduler__not_allowed_to_write:\n"

        code += """
            mv t0, x0
            bnez t1, scheduler__start_next_test
        """

        if self.featmgr.repeat_times != -1 and not self.mp_parallel:
            code += self.endless()
        code += self.scheduler()

        code += """
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
        if self.featmgr.force_alignment:
            code += ".align 3\n"
        code += f"""
        # Scheduler local variables
        scheduler__seed:
            .dword {self.rng.get_seed()}
        scheduler__setup:
        """

        for hart in range(self.featmgr.num_cpus):
            code += f".dword 1 # Hart {hart} scheduler setup\n"

        if not self.mp_parallel:
            code += self.simultaneous_scheduler_variables()
        else:
            code += self.parallel_scheduler_variables()

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
            {self.os_barrier_with_saves(regs_to_save=['t1'], scratch_regs=['s9'])  if self.mp_simultaneous else ""}
            """
            if self.mp_simultaneous:
                code += self.os_barrier_with_saves(regs_to_save=["t1"], scratch_regs=["s9"]) + "\n"
            code += "jr t1   # jump to t1\n"
        else:
            # For user mode use sret to jump to test
            if self.mp_simultaneous:
                pre_xret_code = self.os_barrier()
            else:
                pre_xret_code = ""
            code += self.switch_test_privilege(
                from_priv=RV.RiscvPrivileges.SUPER,
                to_priv=self.test_priv,
                jump_register="t1",
                pre_xret=pre_xret_code,
            )
        return code

    def endless(self) -> str:
        """
        Generate assembly code for finite test repetition loop.

        Generates code that decrements num_runs counter and reloads the test pointer until the counter reaches zero.
        Only generates code when repeat_times is not -1 and MP parallel mode is disabled.

        :return: Assembly code string for the repeat loop or empty string
        """

        if self.featmgr.repeat_times != -1 and not self.mp_parallel:
            code = "scheduler__endless_loop:\n"
            if self.mp_simultaneous:
                code += self.os_barrier_with_saves(regs_to_save=["t0", "t1", "t2"], scratch_regs=["s7", "s8", "s9"], num_tabs=4) + "\n"

            code += """# Load test pointer (all harts need to do this)
            la t0, num_runs

            scheduler__load_test_pointer:\n"""
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
            decrement_num_runs:
            addi t2, t1, -1
            """

            if self.mp_simultaneous:
                code += f"""{self.os_barrier_with_saves(regs_to_save=["t0", "t1", "t2"], scratch_regs=["s7", "s8", "s9"], num_tabs=4)}
                {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}
                bnez {self.hartid_reg}, scheduler__dont_write
                """

            if self.featmgr.big_endian:
                code += "sd t2, 0(t0)\n"
            else:
                code += "sw t2, 0(t0)\n"

            if self.mp_simultaneous:
                code += "scheduler__dont_write:\n"
        else:
            code = ""
        return code

    def scheduler(self) -> str:
        """
        Generates main scheduler logic.
        Assumes t1 has the previous number of runs left.

        Single hart: Multiples number of runs by 8 to return the os_test_sequence offset

        Parallel mode: Uses os_parallel_get_next_exclusive_test to get a random offset into the os_test_sequence array
        """
        # TODO for linux + parallel, just dont decrement num runs, write it, check it etc
        # TODO for linux + simultaneous, need to randomize next test pointers in a thread safe way and also don't decrement num runs

        scheduler = "scheduler:\n"
        if not self.mp_parallel:
            scheduler += """
            mv t0, t1
            slli t0, t0, 3
            """
        else:
            retrieve_selected_test_label_offset = self.place_retrieve_memory_indexed_address(
                index_array_name="selected_test_label_offset", target_array_name="os_test_sequence", index_element_size=8, hartid_reg=self.hartid_reg, work_reg_1="a0", dest_reg="t0"
            )
            scheduler = f"""
            # How parallel mp mode works:
            # 1) Hart has an array of counters for the test labels
            # 2) Hart randomly picks a test label from that list where the counter is not zero
            # 3) Hart tries to get the mutex for that test label
            # 4) If it gets the mutex, it decrements the counter and eventually runs the test
            # 5) If it doesn't get the mutex, it goes back to step 2
            # 6) If it runs the test, it releases the mutex next time after scheduler is entered
            # 7) If no tests to run end hart attempts to end program successfully.
            #
            # Here in scheduler we release the old test lock, obtain and activate the next test we have reserved.
            #
                # Get hartid
                {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}

                # Check if we are storing nonzero in held_locks for this hart, we hold it to this point for readability sake.
                la a0, held_locks
                {Routines.place_offset_address_by_scaled_hartid(address_reg='a0', dest_reg='a0', hartid_reg=self.hartid_reg, work_reg='t1', scale=8)}
                ld t1, 0(a0) # Load the lock address
                beqz t1, continue_scheduling # If nonzero, we are holding a lock, so continue scheduling
                {Routines.place_release_lock(name="scheduler", lock_addr_reg='t1')}

                continue_scheduling:
                    la a0, os_parallel_get_next_exclusive_test # This is a big routine so we call it instead of inlining it.
                    jalr ra, a0
                    mv t2, a0 # Lock address we have not released, need to store it so we can release it later.

                    # Retrieve test labels offset
                    retrieve_test_label_offset:
                    {retrieve_selected_test_label_offset}
                    # Turn back into an offset now that our check is done
                    la a0, os_test_sequence
                    sub t0, t0, a0 # t0 = offset from os_test_sequence

                    # Get hartid
                    {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}

                    la a0, held_locks
                    {Routines.place_offset_address_by_scaled_hartid(address_reg='a0', dest_reg='a0', hartid_reg=self.hartid_reg, work_reg='t1', scale=8)}
                    update_lock_status:
                    sd t2, 0(a0) # Store the lock address we have not released

            """
        return scheduler

    def simultaneous_scheduler_variables(self) -> str:
        """
        Place test labels for the non-MP parallel scheduler.
        """
        code = f"""
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

    def parallel_scheduler_variables(self) -> str:
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

        routine = """
        num_runs:
            # Need a count per hart per test
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "hart_" + str(hart) + "_num_runs:\n"
            for test in self.dtests:
                routine += f"hart_{str(hart)}_{test}:\n"
                if test in harts_to_tests[hart]:
                    routine += ".dword " + hex(self.featmgr.repeat_times) + "\n"
                else:
                    routine += ".dword 0x0\n"

        routine += """
        .align 8
        pad_mtx:
        .dword 0x0
        test_mutexes:
            # One mutex per test label
        """
        for test in self.dtests:
            # routine += ".align 8\n"
            routine += "" + test + "_mutex:\n"
            routine += ".dword 0x0\n"

        routine += """
        # These are now just a list of the available test label names to go with positions in num_runs under each harts section
        os_test_sequence:
            test_setup_addr:
                .dword test_setup
        """
        for test in self.dtests:
            routine += "" + test + "_addr:\n"
            routine += ".dword " + test + "\n"

        routine += """
        scheduler__seeds:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "hart_" + str(hart) + "_seed:\n"
            routine += ".dword " + hex(self.rng.random_nbit(64)) + "\n"

        routine += """
        held_locks: # Holds the address of the test lock the hart holds so it knows what it can / should release."
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "hart_" + str(hart) + "_held_lock:\n"
            routine += ".dword 0x0\n"

        routine += """
        selected_num_runs_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "hart_" + str(hart) + "_selected_num_runs_offset:\n"
            routine += ".dword 0x0\n"

        routine += """
        selected_mutex_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "hart_" + str(hart) + "_selected_mutex_offset:\n"
            routine += ".dword 0x0\n"

        routine += """
        selected_test_label_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "hart_" + str(hart) + "_selected_test_label_offset:\n"
            routine += ".dword 0x0\n"

        return routine

    # Barrier methods
    def os_barrier_amo(self) -> str:
        """
        OS Barrier implementation.
        """
        code = ["os_barrier_amo:"]
        barrier_code = Routines.place_barrier(
            name="osb",
            lock_addr_reg="a0",
            arrive_counter_addr_reg="a1",
            depart_counter_addr_reg="a2",
            flag_addr_reg="a3",
            swap_val_reg="t0",
            work_reg_1="t1",
            work_reg_2="t2",
            num_cpus=self.featmgr.num_cpus,
            end_test_label="os_end_test_addr",
            max_tries=self.MAX_OS_BARRIER_TRIES,
            disable_wfi_wait=self.featmgr.disable_wfi_wait,  # RVTOOLS-4204
        )
        code.append(barrier_code)
        code.append(
            """
            ret
        op_nop:
            nop
            ret
        # a0 will hold the lock address
        # a1 holds subroutine address
        os_critical_section_amo:
            mv s0, ra # Preserve return address
            li a0, barrier_lock
            li t0, 1        # Initialize swap value.
            os_cs_again:
                lw           t1, (a0)     # Check if lock is held.
                bnez         t1,  os_cs_again    # Retry if held.
                amoswap.w.aq t1, t0, (a0) # Attempt to acquire lock.
                bnez         t1,  os_cs_again    # Retry if held.

                # critical section
                jalr ra, a1

                amoswap.w.rl x0, x0, (a0) # Release lock by storing 0.

            #restore callers return address
            mv ra, s0
        """
        )
        return "\n".join(code)

    def os_barrier(self) -> str:
        """
        Synchronization barrier for multiple harts.
        This label doesn't exist if not in mp mode, so throwing a ValueError if it's called

        All harts wait at this point until every hart reaches the barrier, then all proceed together. The barrier resets automatically for reuse.
        """
        return """
            la a0, os_barrier_amo
            jalr ra, a0
        """

    def os_barrier_with_saves(self, regs_to_save: list[str], scratch_regs: list[str], num_tabs: int = 4) -> str:
        assert len(regs_to_save) == len(scratch_regs), "regs_to_save and scratch_regs must be the same length"
        routine = ""
        for src, dst in zip(regs_to_save, scratch_regs):
            routine += "\t".join("" for _ in range(num_tabs + 1)) + f"mv {dst}, {src}\n"
        routine += self.os_barrier()
        for src, dst in zip(scratch_regs, regs_to_save):
            routine += "\t".join("" for _ in range(num_tabs + 1)) + f"mv {dst}, {src}\n"

        return routine

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

    def place_get_next_exclusive_test(
        self,
        name: str,
        fallback_routine_label: str,
        test_locks_label: str,
        top_seed_label: str,
        remaining_balance_array_label: str,
        array_length: int,
        seed_offset_scale: int,
        test_labels_offset_scale: int,
        num_test_labels_ignore: int,
        seed_addr_reg: str = "a1",
        test_labels_offset_scale_reg: str = "s5",
        seed_offset_scale_reg: str = "s4",
        array_length_reg: str = "s6",
        num_ignore_reg: str = "s7",
        remaining_balance_addr_reg: str = "s8",
        work_reg_1: str = "s9",
        work_reg_2: str = "a2",
    ) -> str:
        """Generate assembly for getting next exclusive test in parallel MP mode.

        :param name: Name prefix for generated labels
        :param fallback_routine_label: Label to jump to when no tests available
        :param test_locks_label: Label of test mutex array
        :param top_seed_label: Label of seed array for randomization
        :param remaining_balance_array_label: Label of test count array
        :param array_length: Number of test elements
        :param seed_offset_scale: Scale factor for seed array indexing
        :param test_labels_offset_scale: Scale factor for test label indexing
        :param num_test_labels_ignore: Number of test labels to skip
        """

        semi_random_nonzero_offset = self.place_get_offset_for_semirandom_nonzero_in_array(
            name=name + "_get_new_lock_wish",
            seed_addr_reg=seed_addr_reg,
            seed_offset_scale_reg=seed_offset_scale_reg,
            remaining_balance_addr_reg=remaining_balance_addr_reg,
            array_length_reg=array_length_reg,
            test_labels_offset_scale_reg=test_labels_offset_scale_reg,
            num_ignore_reg=num_ignore_reg,
            fallback_label=fallback_routine_label,
            work_reg_1=work_reg_1,
            work_reg_2=work_reg_2,
        )
        retrieve_selected_mutex_offset = self.place_retrieve_memory_indexed_address(
            index_array_name="selected_mutex_offset", target_array_name="test_mutexes", index_element_size=8, hartid_reg=self.hartid_reg, work_reg_1=work_reg_1, dest_reg="a0"
        )
        retrieve_selected_num_runs_offset = self.place_retrieve_memory_indexed_address(
            index_array_name="selected_num_runs_offset", target_array_name="num_runs", index_element_size=8, hartid_reg=self.hartid_reg, work_reg_1=work_reg_1, dest_reg="t1"
        )
        return f"""
            # load address for seeds array, the rng routine offsets to the correct hart's element
            la {seed_addr_reg}, {top_seed_label}

            # load constants
            li {test_labels_offset_scale_reg}, {test_labels_offset_scale}
            li {seed_offset_scale_reg}, {seed_offset_scale}
            li {array_length_reg}, {array_length}
            li {num_ignore_reg}, {num_test_labels_ignore}

            # get hartid
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}

            # load address for remaining balance array and offset to the first element for this hart
            la {remaining_balance_addr_reg}, {remaining_balance_array_label}
            mul {work_reg_1}, {array_length_reg}, {self.hartid_reg} # Number of elements in each sub array is the number of test labels.
            mul {work_reg_1}, {work_reg_1}, {test_labels_offset_scale_reg} # Size of the elements is the same as for the test labels.
            add {remaining_balance_addr_reg}, {remaining_balance_addr_reg}, {work_reg_1} # Offset to the first element for this hart.

            # Changes a0 to have new lock address, a2 will have the address of the resource being protected by the lock.
            # load address for test locks array
            la a2, {test_locks_label}
            {name}_get_new_lock_wish:
                # load constants
                li {test_labels_offset_scale_reg}, {test_labels_offset_scale}
                li {seed_offset_scale_reg}, {seed_offset_scale}
                li {array_length_reg}, {array_length}
                li {num_ignore_reg}, {num_test_labels_ignore}
                {semi_random_nonzero_offset}

                # Retrieve lock address
                retrieve_lock_address:
                # Get hartid
                {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}
                {retrieve_selected_mutex_offset}

            {name}_try_lock:
                li t0, 1        # Initialize swap value.
                ld           t1, (a0)     # Check if lock is held.
                bnez         t1,  {name}_get_new_lock_wish # Retry if held.
                amoswap.d.aq t1, t0, (a0) # Attempt to acquire lock.
                bnez         t1,  {name}_get_new_lock_wish # Retry if held.

                # Critical section technically continues well past here through the execution of the test.
                # Retrieve the remaining balance address for this hart and particular test
                retrieve_remaining_balance_address:
                # Get hartid
                {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}
                {retrieve_selected_num_runs_offset}

                ld t0, (t1)
                beqz t0, test_failed # shouldnt be zero here
                addi t0, t0, -1

                decrement_num_runs:
                sd t0, (t1)

                # No release here because we do not want to have to run the critical routine immediately without considering OS mechanisms.

            """

    def place_get_offset_for_semirandom_nonzero_in_array(
        self,
        name: str,
        seed_addr_reg: str,
        seed_offset_scale_reg: str,
        remaining_balance_addr_reg: str,
        array_length_reg: str,
        test_labels_offset_scale_reg: str,
        num_ignore_reg: str,
        fallback_label: str,
        work_reg_1: str,
        work_reg_2: str,
    ) -> str:
        hartid_reg = "s1"
        retrieve_memory_indexed_address = self.place_retrieve_memory_indexed_address(
            index_array_name="selected_num_runs_offset", target_array_name="num_runs", index_element_size=8, hartid_reg=hartid_reg, work_reg_1=work_reg_1, dest_reg="t1"
        )
        return f"""
            reset_offset:
            li a0, 0x0
            # save current a0 into s3
            mv s3, a0

            mv t0, {array_length_reg} # Counter to detect cycle
            mv t3, {array_length_reg} # Upper bound for the loop
            mv {work_reg_2}, zero # Additional offset
            {name}_find_first_nonzero_in_remaining_balance_array:
                addi t0, t0, -1 # Decrement num attempts
                mv {work_reg_1}, {work_reg_2} # Copy additional offset
                add {work_reg_1}, {work_reg_1}, s3 # relative offset from RNG
                remu {work_reg_1}, {work_reg_1}, t3 # Wrap around if we go past the end of the array
                slli {work_reg_1}, {work_reg_1}, 3 # Scale by 8 for dword sized elements
                # Work reg 1 holds relative offset, in bytes, from the start of this hart's section
                add t2, {work_reg_1}, {remaining_balance_addr_reg} # Calculate address of element in remaining_balance_array

                ld t1, (t2)
                blt zero, t1, {name}_success

                # Update additional offset
                addi {work_reg_2}, {work_reg_2}, 1

                blt zero, t0, {name}_find_first_nonzero_in_remaining_balance_array

            {name}_no_tests_left:
                li gp, 0x{self.featmgr.eot_pass_value:x} # Tests done
                la a0, {fallback_label}
                # save ra in s3 in case we need to return to this routine, s3 is okay because the offset isn't useful
                mv s3, ra
                jalr ra, a0
                # restore ra from s3
                mv ra, s3

            {name}_success:
                # Return relative offset
                mv a3, {work_reg_1} # Return relative offset, no consideration for test_setup or hart*tests offset

                # Store mutex offset
                store_mutex_offset:
                    # Get hartid
                    {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}
                    la t0, selected_mutex_offset
                    slli t1, {hartid_reg}, 3 # 8 bytes per entry
                    add t0, t0, t1 # Offset to the hart's entry
                    sd a3, (t0) # Store the offset to the mutex for this hart

                # Store test label offset
                store_test_label_offset:
                    la t0, selected_test_label_offset
                    add t0, t0, t1 # Offset to the hart's entry
                    addi {work_reg_1}, a3, 8 # Ignore test_setup
                    sd {work_reg_1}, (t0) # Store the offset to the test label for this hart

                # Store num runs offset
                store_num_runs_offset:
                    la t0, selected_num_runs_offset
                    add t0, t0, t1 # Offset to the hart's entry
                    mul {work_reg_1}, {array_length_reg}, t1 # Number of elements in each sub array is the number of test labels, scale that by hartid times element data size.
                    add {work_reg_1}, {work_reg_1}, a3 # Offset to the selected element for this hart and test in num_runs.
                    sd {work_reg_1}, (t0) # Store the offset to the num runs for this hart

                    # get hartid
                    {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}

                    # Check if this num runs entry is actually zero.
                    {retrieve_memory_indexed_address}
                    ld t0, (t1)
                    beqz t0, test_failed # shouldnt be zero here

            """
