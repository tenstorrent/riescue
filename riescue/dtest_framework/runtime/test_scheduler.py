# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Tuple, List
import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class TestScheduler(AssemblyGenerator):
    """
    Scheduler for tests.
    """

    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.force_alignment = self.featmgr.force_alignment  # Align all instructions on 8 byte boundary

    def generate(self) -> str:
        code = ".section .text\n"
        dtests, dtests_ntimes, dtests_seq = self.set_dtests()

        if self.mp_active:
            code += self.os_barrier_amo()

        if self.mp_parallel:
            code += "os_parallel_get_next_exclusive_test:"
            code += self.place_get_next_exclusive_test(
                name="opgnet",
                fallback_routine_label="os_end_test",
                test_locks_label="test_mutexes",
                top_seed_label="schedule_seeds",
                remaining_balance_array_label="num_runs",
                array_length=len(dtests),
                seed_offset_scale=8,
                test_labels_offset_scale=8,
                num_test_labels_ignore=1,
            )
            code += "\tret"

        if self.featmgr.force_alignment:
            code += ".align 8\n"
        code += f"""
        schedule_seed:
            .dword {self.rng.get_seed()}
        schedule_setup:
        """
        if self.mp_parallel:
            code += "\n".join("\t\t\t\t.dword 1" for hart in range(self.featmgr.num_cpus)) + "\n"
        else:
            code += "\t\t\t\t.dword 1\n"

        code += (
            f"""
        schedule_tests:
            # Insert CSR read randomization logic here if allowed
            {self.csr_read_randomization()}

            la t0, schedule_setup

            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode) if self.mp_parallel else ""}
            {"li t2, 8 # While the harts are allowed to run test_setup at the same time, they also each must." if self.mp_parallel else ""}
            {"mul t2, " + self.hartid_reg + ", t2" if self.mp_parallel else ""}
            {"add t0, t0, t2" if self.mp_parallel else ""}

            ld t1, 0(t0)

            {self.os_barrier_with_saves(regs_to_save=['t0', 't1'], scratch_regs=['s8', 's9']) if self.mp_simultanous else ""}

            {"bnez " + self.hartid_reg + ", not_allowed_to_write" if self.mp_simultanous else ""}
            sd x0, 0(t0)
            {"not_allowed_to_write:" if self.mp_simultanous else ""}

            mv t0, x0
            bnez t1, schedule_next_test
            """
            + self.endless()
            + self.scheduler(dtests)
            + f"""
        schedule_next_test:
            # Get the pointer to the next test label
            la t1, os_test_sequence
            add t0, t0, t1 # t0 = current os_test_sequence pointer
            {"ld t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)" if self.featmgr.big_endian else "ld t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)"}

        """
        )
        # if (self.featmgr.priv_mode != RV.RiscvPrivileges.USER):
        if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
            code += f"""
            # Schedule next test, t1 has the test_label
            # priv_mode: {self.featmgr.priv_mode}

            # Need barrier here so tests don't read num_runs after hart 0 updated it
            {self.os_barrier_with_saves(regs_to_save=['t1'], scratch_regs=['s9'])  if self.mp_simultanous else ""}

            jr t1   # jump to t1
            # For user mode use sret to jump to test

        """
        else:

            if self.mp_simultanous:
                pre_xret_code = self.os_barrier()
            else:
                pre_xret_code = ""
            code += self.switch_test_privilege(
                from_priv=RV.RiscvPrivileges.SUPER,
                to_priv=self.featmgr.priv_mode,
                jump_register="t1",
                pre_xret=pre_xret_code,
            )

        if not self.mp_parallel:
            code += self.place_non_mp_par_scheduler_variables(dtests_ntimes=dtests_ntimes, dtests_seq=dtests_seq)
        else:
            code += self.place_mp_par_scheduler_variables(dtests=dtests)

        return code

    def endless(self) -> str:
        """Routine to generate endless loop for non-MP parallel mode."""

        code = (
            f"""
            endless:

            {self.os_barrier_with_saves(regs_to_save = ["t0", "t1", "t2"], scratch_regs = ["s7", "s8", "s9"], num_tabs = 4) if self.mp_simultanous else ""}

            # Load test pointer (all harts need to do this)
            la t0, num_runs
            load_test_pointer:
            {"ld t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)" if self.featmgr.big_endian else "lw t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)"}
            {"srli t1, t1, 32" if self.featmgr.big_endian else ""}
            {"bnez t1, skip_reload_test_pointer" if self.featmgr.big_endian else ""}
            {"ld t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)" if self.featmgr.big_endian else "lw t1, 0(t0)  # t1 = [os_test_sequence] (actual test label)"}
            {"skip_reload_test_pointer:"if self.featmgr.big_endian else ""}
            li gp, 0x{self.featmgr.eot_pass_value:x}
            beqz t1, os_end_test # end program, if zero
            # Decrement num_runs and store it back
            decrement_num_runs:
            addi t2, t1, -1

            {self.os_barrier_with_saves(regs_to_save = ["t0", "t1", "t2"], scratch_regs = ["s7", "s8", "s9"], num_tabs = 4) if self.mp_simultanous else ""}

            # Get hartid
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode) if self.mp_simultanous else ""}
            {"bnez " + self.hartid_reg +", dont_write" if self.mp_simultanous else ""}
            {"sd t2, 0(t0)" if self.featmgr.big_endian else "sw t2, 0(t0)"}
            {"dont_write:" if self.mp_simultanous else ""}

            """
            if self.featmgr.repeat_times != -1 and not self.mp_parallel
            else """
            endless:
            """
        )
        return code

    def os_get_random_offset(
        self,
        seed_array_label,
        target_array_length,
        seed_offset_scale,
        target_offset_scale,
        num_ignore,
    ) -> str:
        return f"""
            la a1, {seed_array_label}
            li a2, {target_array_length}
            li a3, {seed_offset_scale}
            li a4, {target_offset_scale}
            li a5, {num_ignore}

            la a0, os_rng_orig
            jalr ra, a0

        """

    def scheduler(self, dtests) -> str:
        """Routine to generate scheduler for non-MP parallel mode."""
        # TODO for linux + parallel, just dont decrement num runs, write it, check it etc
        # TODO for linux + simultaneous, need to randomize next test pointers in a thread safe way and also don't decrement num runs
        scheduler = ""
        if not self.mp_parallel:
            scheduler = (
                f"""
                scheduler:
                    {self.os_get_random_offset(seed_array_label = "schedule_seed", target_array_length = len(dtests), seed_offset_scale = 8, target_offset_scale = 8, num_ignore = 1)}

                    la a1, schedule_seed    # seed array
                    li a2, {len(dtests)}    # target array length
                    li a3, 8                # seed offset scale
                    li a4, 8                # target offset scale
                    li a5, 1                # num_ignore

                    la a0, os_rng_orig
                    jalr ra, a0


                    addi a0, a0, 1 # add num_ignore
                    li t0, 8
                    set_test_pointer:
                    mul t0, t0, a0 # Scale by pointer size

                """
                if self.featmgr.linux_mode
                else """
                scheduler:
                mv t0, t1
                slli t0, t0, 3

                """
            )
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
            scheduler:
                # Get hartid
                {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}

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
                    {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}

                    la a0, held_locks
                    {Routines.place_offset_address_by_scaled_hartid(address_reg='a0', dest_reg='a0', hartid_reg=self.hartid_reg, work_reg='t1', scale=8)}
                    update_lock_status:
                    sd t2, 0(a0) # Store the lock address we have not released

            """
        return scheduler

    def set_dtests(self) -> Tuple[List[str], List, str]:
        dtests = list(self.pool.discrete_tests.keys())
        dtests_ntimes = []
        dtests_seq = ""
        if self.featmgr.repeat_times == -1 and not self.featmgr.linux_mode:
            raise ValueError("Can only use repeat_times == -1 with linux mode")
        if self.featmgr.repeat_times == -1 or self.mp_parallel:
            # Schedule is randomized at runtime for linux mode and for parallel mp mode.
            dtests_ntimes = [test for test in dtests]
        else:
            dtests_ntimes = [test for test in dtests for _ in range(self.featmgr.repeat_times)]
        self.rng.shuffle(dtests_ntimes)
        dtests_seq = "    .dword test_setup\n"
        # The linux mode scheduler works a bit differently, where it expects the test_cleanup label at the end
        if not self.featmgr.linux_mode:
            dtests_seq += "    .dword test_cleanup\n"
        for test in dtests_ntimes:
            dtests_seq += f"    .dword {test}\n"
        if self.featmgr.linux_mode:
            dtests_seq += "    .dword test_cleanup\n"

        return dtests, dtests_ntimes, dtests_seq

    def place_non_mp_par_scheduler_variables(self, dtests_ntimes: list, dtests_seq: str) -> str:
        """
        Place test labels for the non-MP parallel scheduler.
        """
        code = f"""
        num_runs:
            # We need +1 below since we have cleanup as the last entry in the dtests_seq
            .dword {len(dtests_ntimes)+1}
        """
        if self.featmgr.force_alignment:
            code += ".align 8\n"
        code += f"""
        os_test_sequence:
        {dtests_seq}
        """
        return code

    def place_mp_par_scheduler_variables(self, dtests: list) -> str:
        num_harts = self.featmgr.num_cpus
        harts_to_tests = dict()

        for hart in range(num_harts):
            if hart not in harts_to_tests:
                harts_to_tests[hart] = []

        if self.featmgr.parallel_scheduling_mode == RV.RiscvParallelSchedulingMode.ROUND_ROBIN:
            # Assign the tests round robin to the harts, with no harts sharing tests.
            for i, test in enumerate(dtests):
                hart = i % num_harts
                harts_to_tests[hart].append(test)
        elif self.featmgr.parallel_scheduling_mode == RV.RiscvParallelSchedulingMode.EXHAUSTIVE:
            # Exhaustive scheduling mode assigns all tests to all harts.
            for i, test in enumerate(dtests):
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
            routine += "\t\t\t\t\thart_" + str(hart) + "_num_runs:\n"
            for test in dtests:
                routine += f"\t\t\t\t\t\thart_{str(hart)}_{test}:\n"
                if test in harts_to_tests[hart]:
                    routine += "\t\t\t\t\t\t\t.dword " + hex(self.featmgr.repeat_times) + "\n"
                else:
                    routine += "\t\t\t\t\t\t\t.dword 0x0\n"

        routine += """
        .align 8
        pad_mtx:
        .dword 0x0
        test_mutexes:
            # One mutex per test label
        """
        for test in dtests:
            # routine += ".align 8\n"
            routine += "\t\t\t\t\t" + test + "_mutex:\n"
            routine += "\t\t\t\t\t\t.dword 0x0\n"

        routine += """
        # These are now just a list of the available test label names to go with positions in num_runs under each harts section
        os_test_sequence:
            test_setup_addr:
                .dword test_setup
        """
        for test in dtests:
            routine += "\t\t\t\t\t" + test + "_addr:\n"
            routine += "\t\t\t\t\t\t.dword " + test + "\n"

        routine += """
        schedule_seeds:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "\t\t\t\t\thart_" + str(hart) + "_seed:\n"
            routine += "\t\t\t\t\t\t.dword " + hex(self.rng.random_nbit(64)) + "\n"

        routine += """
        held_locks: # Holds the address of the test lock the hart holds so it knows what it can / should release."
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "\t\t\t\t\thart_" + str(hart) + "_held_lock:\n"
            routine += "\t\t\t\t\t\t.dword 0x0\n"

        routine += """
        selected_num_runs_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "\t\t\t\t\thart_" + str(hart) + "_selected_num_runs_offset:\n"
            routine += "\t\t\t\t\t\t.dword 0x0\n"

        routine += """
        selected_mutex_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "\t\t\t\t\thart_" + str(hart) + "_selected_mutex_offset:\n"
            routine += "\t\t\t\t\t\t.dword 0x0\n"

        routine += """
        selected_test_label_offset:
        """
        for hart in range(self.featmgr.num_cpus):
            routine += "\t\t\t\t\thart_" + str(hart) + "_selected_test_label_offset:\n"
            routine += "\t\t\t\t\t\t.dword 0x0\n"

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
        if not self.mp_active:
            raise ValueError("os_barrier called but not in MP mode")
        return """
            la a0, os_barrier_amo
            jalr ra, a0
        """

    def os_barrier_with_saves(self, regs_to_save: list, scratch_regs: list, num_tabs: int = 4) -> str:
        assert len(regs_to_save) == len(scratch_regs), "regs_to_save and scratch_regs must be the same length"
        routine = ""
        for src, dst in zip(regs_to_save, scratch_regs):
            routine += "\t".join("" for tab in range(num_tabs + 1)) + f"mv {dst}, {src}\n"
        routine += self.os_barrier()
        for src, dst in zip(scratch_regs, regs_to_save):
            routine += "\t".join("" for tab in range(num_tabs + 1)) + f"mv {dst}, {src}\n"

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
        seed_addr_reg="a1",
        test_labels_offset_scale_reg="s5",
        seed_offset_scale_reg="s4",
        array_length_reg="s6",
        num_ignore_reg="s7",
        remaining_balance_addr_reg="s8",
        work_reg_1="s9",
        work_reg_2="a2",
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
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}

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
                {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}
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
                {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}
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
                    {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv_mode)}
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
                    {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv_mode)}

                    # Check if this num runs entry is actually zero.
                    {retrieve_memory_indexed_address}
                    ld t0, (t1)
                    beqz t0, test_failed # shouldnt be zero here

            """
