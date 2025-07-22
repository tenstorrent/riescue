# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
OpSys takes care of all the operating system related code including Scheduler
"""

from types import MappingProxyType
import riescue.lib.enums as RV
from riescue.lib.csr_manager.csr_manager_interface import CsrManagerInterface
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class OpSys(AssemblyGenerator):
    """
    Generates OS code for test. Includes end test jump table, end of test routines, and utility routines.

    Produces scheduler, end-test routines, barrier synchronization, and
    utility functions for discrete test execution and coordination.
    """

    PER_HART_OS_VARIABLES = MappingProxyType(
        {
            "check_excp": "0x1",
            "check_excp_expected_pc": "-1",
            "check_excp_actual_pc": "-1",
            "check_excp_return_pc": "-1",
            "check_excp_expected_tval": "-1",
            "check_excp_expected_cause": "0xff",
            "check_excp_actual_cause": "0xff",
            "os_save_ecall_fn_epc": "-1",
        }
    )

    SHARED_OS_VARIABLES = MappingProxyType(
        {
            "passed_addr": "passed",
            "failed_addr": "failed",
            "machine_flags": "0x0",
            "user_flags": "0x0",
            "super_flags": "0x0",
            "machine_area": "0x0",
            "user_area": "0x0",
            "super_area": "0x0",
            "os_passed_addr": "test_passed",
            "os_failed_addr": "test_failed",
            "os_end_test_addr": "os_end_test",
            "end_test_addr": "end_test",
            "num_harts_ended": "0x0",
            "num_hard_fails": "0x0",
            "excp_ignored_count": "0x0",
        }
    )

    MP_SHARED_OS_VARIABLES = MappingProxyType(
        {
            "num_harts": None,
            "barrier_arrive_counter": "0x0",
            "barrier_depart_counter": None,
            "barrier_flag": "0x0",
            "barrier_lock": "0x0",
            "hartid_counter": "0x0",
        }
    )

    OS_VARIABLE_SIZE = 8
    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        assert not (self.featmgr.linux_mode and self.mp_active), "Linux mode and not MP mode are not supported together currently"

        if not self.featmgr.cmdline.no_random_csr_reads:
            self.csr_manager = CsrManagerInterface(self.rng)
        # Build OS Data variables
        self.per_hart_variables = dict(OpSys.PER_HART_OS_VARIABLES)
        self.shared_variables = dict(OpSys.SHARED_OS_VARIABLES)

        if self.featmgr.user_interrupt_table:
            self.shared_variables["user_interrupt_table_addr"] = "USER_INTERRUPT_TABLE"
        if self.featmgr.excp_hooks:
            self.shared_variables["excp_handler_pre_addr"] = "excp_handler_pre"
            self.shared_variables["excp_handler_post_addr"] = "excp_handler_post"
        if self.featmgr.vmm_hooks:
            self.shared_variables["vmm_handler_pre_addr"] = "vmm_handler_pre"
            self.shared_variables["vmm_handler_post_addr"] = "vmm_handler_post"
        if self.mp_active:
            self.shared_variables.update(OpSys.MP_SHARED_OS_VARIABLES)
            self.shared_variables["num_harts"] = str(self.featmgr.num_cpus)
            self.shared_variables["barrier_depart_counter"] = str(self.featmgr.num_cpus)

    def generate(self) -> str:
        code = ""

        # If WYSIWYG mode, then we only need to add to_from_host addresses
        if self.featmgr.wysiwyg:
            code += self.append_end_test()
            code += self.append_os_data()
            code += self.append_to_from_host()
            return code

        code += self.generate_os()

        return code

    def generate_os(self):
        hartid_reg = "s1"

        dtests, dtests_ntimes, dtests_seq = self.set_dtests()

        code = ""
        code += self.test_passed_failed_labels()

        code += """
        .section .text

        enter_scheduler:
            # Check if t0 has a pass or fail condition
            li t1, 0xbaadc0de
            beq t0, t1, test_failed

        """
        if self.featmgr.fe_tb:
            code += """
            # With FE testbench, we can't jump to failed even in the bad path since they can take that
            # path and end the test prematurely. So, make failed==passed label
            test_failed:
            """
        code += """
        test_passed:
            j schedule_tests

    """
        if not self.featmgr.fe_tb:
            code += f"""
        test_failed:
            li a0, num_hard_fails
            li t0, 1
            amoadd.w x0, t0, (a0)
            li gp, 0x{self.featmgr.eot_fail_val:x}
            j os_end_test

        """

        code += "os_rng_orig:\n"
        code += Routines.place_rng_unsafe_reg(
            seed_addr_reg="a1", modulus_reg="a2", seed_offset_scale_reg="a3", target_offset_scale_reg="a4", num_ignore_reg="a5", handler_priv_mode=self.handler_priv_mode
        )
        code += "\tret\n"

        if self.mp_active:
            code += "\nos_barrier_amo:"
            code += Routines.place_barrier(
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
                max_tries=OpSys.MAX_OS_BARRIER_TRIES,
                disable_wfi_wait=self.featmgr.cmdline.disable_wfi_wait,  # RVTOOLS-4204
            )
            code += "\n\tret"
            code += """
        os_nop:
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

        endless = (
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
            li gp, 0x{self.featmgr.eot_pass_val:x}
            beqz t1, os_end_test # end program, if zero
            # Decrement num_runs and store it back
            decrement_num_runs:
            addi t2, t1, -1

            {self.os_barrier_with_saves(regs_to_save = ["t0", "t1", "t2"], scratch_regs = ["s7", "s8", "s9"], num_tabs = 4) if self.mp_simultanous else ""}

            # Get hartid
            {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv_mode) if self.mp_simultanous else ""}
            {"bnez " + hartid_reg +", dont_write" if self.mp_simultanous else ""}
            {"sd t2, 0(t0)" if self.featmgr.big_endian else "sw t2, 0(t0)"}
            {"dont_write:" if self.mp_simultanous else ""}

            """
            if self.featmgr.repeat_times != -1 and not self.mp_parallel
            else """
            endless:
            """
        )

        # TODO for linux + parallel, just dont decrement num runs, write it, check it etc
        # TODO for linux + simultaneous, need to randomize next test pointers in a thread safe way and also don't decrement num runs
        scheduler = ""
        if not self.mp_parallel:
            scheduler = (
                f"""
                scheduler:
                    {self.os_get_random_offset(seed_array_label = "schedule_seed", target_array_length = len(dtests), seed_offset_scale = 8, target_offset_scale = 8, num_ignore = 1)}
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
                index_array_name="selected_test_label_offset", target_array_name="os_test_sequence", index_element_size=8, hartid_reg=hartid_reg, work_reg_1="a0", dest_reg="t0"
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
                    {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv_mode)}

                    # Check if we are storing nonzero in held_locks for this hart, we hold it to this point for readability sake.
                    la a0, held_locks
                    {Routines.place_offset_address_by_scaled_hartid(address_reg='a0', dest_reg='a0', hartid_reg=hartid_reg, work_reg='t1', scale=8)}
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
                        {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv_mode)}

                        la a0, held_locks
                        {Routines.place_offset_address_by_scaled_hartid(address_reg='a0', dest_reg='a0', hartid_reg=hartid_reg, work_reg='t1', scale=8)}
                        update_lock_status:
                        sd t2, 0(a0) # Store the lock address we have not released

                """
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

            {Routines.place_retrieve_hartid(dest_reg=hartid_reg, priv_mode=self.handler_priv_mode) if self.mp_parallel else ""}
            {"li t2, 8 # While the harts are allowed to run test_setup at the same time, they also each must." if self.mp_parallel else ""}
            {"mul t2, " + hartid_reg + ", t2" if self.mp_parallel else ""}
            {"add t0, t0, t2" if self.mp_parallel else ""}

            ld t1, 0(t0)

            {self.os_barrier_with_saves(regs_to_save=['t0', 't1'], scratch_regs=['s8', 's9']) if self.mp_simultanous else ""}

            {"bnez " + hartid_reg + ", not_allowed_to_write" if self.mp_simultanous else ""}
            sd x0, 0(t0)
            {"not_allowed_to_write:" if self.mp_simultanous else ""}

            mv t0, x0
            bnez t1, schedule_next_test
            """
            + endless
            + scheduler
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
        else:  # self.featmgr.priv_mode != RV.RiscvPrivileges.MACHINE:

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

        code += self.append_end_test()

        # FE_TB needs to have extra instructions after the end oqf this segment
        if self.featmgr.fe_tb:
            code += """
            ## FE_TB needs to have extra instructions after the end of this segment
            .nop(0x100)
            """

        if not self.mp_parallel:
            code += self.place_non_mp_par_scheduler_variables(dtests_ntimes=dtests_ntimes, dtests_seq=dtests_seq)
        else:
            code += self.place_mp_par_scheduler_variables(dtests=dtests)

        # Append all the functions
        code += self.fn_rand()

        # Append the os_data
        code += self.append_os_data()

        # Append the to_from_host addresses
        code += self.append_to_from_host()

        return code

    def test_passed_failed_labels(self) -> str:
        """Generate passed/failed/end_test labels for test code.

        User mode uses ECALL while other modes load addresses from .text section.
        """

        # Add end of the test passed and failed routines
        # USER mode always needs to do ecall to exit from the discrete_test and muist be placed in user accessible pages
        if self.featmgr.priv_mode == RV.RiscvPrivileges.USER:
            # TODO: Move this from .code to  .section .text_user, "ax"
            # When this change happens, existing tests which jump to passed/failed labels will fail to link since this jump will
            # be beyond the 2GB limit addressable by auipc instructions. Those jumps have to be replaced by a directive which adds
            # "li a0, passed_addr; ld a1, 0(a0); jalr ra, 0(a1);"
            return """
                .section .code
                passed:
                    li x31, 0xf0000001  # Schedule test
                    ecall

                failed:
                    li x31, 0xf0000002  # End test with fail
                    ecall

                end_test:
                    li x31, 0xf0000003 # End test with a pass, in case test is overlong but this was somewhat expected
                    ecall
            """
        else:
            # TODO: Move this from .code to  .section .text
            # When this change happens, existing tests which jump to passed/failed labels will fail to link since this jump will
            # be beyond the 2GB limit addressable by auipc instructions. Those jumps have to be replaced by a directive which adds
            # "li a0, passed_addr; ld a1, 0(a0); jalr ra, 0(a1);"
            return """
                .section .code
                passed:
                    li t0, os_passed_addr
                    ld t1, 0(t0)
                    jr t1

                failed:
                    li t0, os_failed_addr
                    ld t1, 0(t0)
                    jr t1

                end_test:
                    li t0, os_end_test_addr
                    ld t1, 0(t0)
                    jr t1
            """

    def csr_read_randomization(self):
        """Generate random CSR reads based on current OS privilege mode.

        Helps trigger CSR value comparison checks in testbenches.
        Can be disabled with --no-random-csr-reads flag.
        """
        if self.featmgr.cmdline.no_random_csr_reads:
            return ""

        # Find the current privilege mode of OS
        # Machine mode is default
        instrs = ""
        priv_mode = RV.RiscvPrivileges.MACHINE
        csr_list = []
        # If test mode is in supervisor or user, the OS is in Supervisor always
        if self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER or self.featmgr.priv_mode == RV.RiscvPrivileges.USER:
            priv_mode = "Supervisor"
            # Also, we need to include supervisor and user CSR list provided in the commandline
            if self.featmgr.cmdline.random_supervisor_csr_list:
                csr_list += self.featmgr.cmdline.random_supervisor_csr_list.split(",")
            if self.featmgr.cmdline.random_user_csr_list:
                csr_list += self.featmgr.cmdline.random_user_csr_list.split(",")
        if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
            # Also, we need to include machine, supervisor and user CSR list provided in the commandline
            if self.featmgr.cmdline.random_machine_csr_list:
                csr_list += self.featmgr.cmdline.random_machine_csr_list.split(",")
            if self.featmgr.cmdline.random_supervisor_csr_list:
                csr_list += self.featmgr.cmdline.random_supervisor_csr_list.split(",")
            if self.featmgr.cmdline.random_user_csr_list:
                csr_list += self.featmgr.cmdline.random_user_csr_list.split(",")

        # Get up to max_random_csr_reads random CSR to read
        for i in range(self.rng.randint(3, self.featmgr.cmdline.max_random_csr_reads)):
            if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
                priv_mode = self.rng.choice(["Machine", "Supervisor"])
            csr_config = self.csr_manager.get_random_csr(match={"Accessibility": priv_mode, "ISS_Support": "Yes"})
            csr_name = list(csr_config.keys())[0]

            instrs += f"csrr t0, {csr_name}\n"

        for csr in csr_list:
            instrs += f"csrr t0, {csr}\n"

        return instrs

    def set_dtests(self) -> tuple:
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

    def append_end_test(self):
        code = f"""
        .align 6; .global tohost_mutex; tohost_mutex: .dword 0; # Used to protect access to tohost

        tohost_addr_mem:
            .dword tohost

        os_end_test:
        os_write_tohost:

        # each hart increments num_harts_ended so that the first one waits till all harts have finished before writing to tohost
        mark_done:
            li t0, 1
            li t3, num_harts_ended
            amoadd.w x0, t0, (t3)

        # MP Specific, check if gp[31] == 1
        # If so, then we detected a core bailed early. We should not write to tohost
        li t0, 0x80000000
        beq t0, gp, wait_for_dismissal

        # Try to obtain tohost_mutex
        la a0, tohost_mutex
        j tohost_try_lock

        wait_for_dismissal:
            wfi
            j wait_for_dismissal

        tohost_try_lock:
            li t0, 1                    # Initialize swap value.
            ld           t1, (a0)       # Check if lock is held.
            bnez         t1,  wait_for_dismissal        # fail if held.
            amoswap.d.aq t1, t0, (a0)   # Attempt to acquire lock.
            bnez         t1,  wait_for_dismissal        # fail if held

            # obtained lock, no need to release this one since we are ending the simulation.
            li t2, {self.featmgr.num_cpus}
            li t1, num_hard_fails
            li t4, {OpSys.EOT_WAIT_FOR_OTHERS_TIMEOUT} # Timeout for eot waiting

        wait_for_others:
            bltz t4, mark_fail # This is a timeout, other harts didn't finish
            addi t4, t4, -1
            lw t0, (t1)
            bnez t0, mark_fail # Write immediately if there was a hard fail
            lw t0, (t3)
            bne t0, t2, wait_for_others

        j load_tohost_addr

        mark_fail:
            li gp, 0x{self.featmgr.eot_fail_val:x}

        load_tohost_addr:
            ld t0, tohost_addr_mem


        write_to_tohost:
            fence iorw, iorw
            {"sd gp, 0(t0)" if self.featmgr.big_endian else "sw gp, 0(t0)"}
        """

        # If in linux mode then add following
        if self.featmgr.linux_mode:
            code += """
            _exit:
                li a7, 93  # __NR_exit
                li a0, 0   # exit code
                ecall

                j wait_for_dismissal
            """

        if not self.featmgr.fe_tb and not self.featmgr.linux_mode:
            code += """
        _exit:
           j wait_for_dismissal

        """

        return code

    # TODO make this able to be used more than once and be dependent on test seed.
    def fn_rand(self):
        code = """
        # Pseudorandom number generator between 0 and 10 using LCG algorithm
        # Seed value
        li a0, 42       # Set initial seed value (can be any value)

        # LCG parameters
        li a1, 1664525  # Multiplier
        li a2, 1013904223  # Increment
        li a3, 2^32     # Modulus (2^32 for a 32-bit pseudorandom number)

        # Generate pseudorandom number
        mul a0, a0, a1   # a0 = a0 * multiplier
        add a0, a0, a2  # a0 = a0 + increment
        rem a0, a0, a3   # a0 = a0 % modulus (remainder)

        # Calculate pseudorandom number between 0 and 10
        li a1, 11        # Maximum value (10 + 1)
        rem a0, a0, a1   # a0 = a0 % maximum value

        ret

        # The pseudorandom number between 0 and 10 will be stored in a0

        """
        return code

    def append_to_from_host(self):
        code = """
        # HTIF is defeined at 0x7000_0000, can be used as a character device so should be read/writeable.
        .section .io_htif, "aw"
        .align 6; .global tohost; tohost: .dword 0;
        .align 6; .global fromhost; fromhost: .dword 0;

        """
        return code

    def append_os_data(self):
        # This function creates a jump table to ensure we can jump from .code to .text regions even if they span > 2GB
        # We use defines in the equates file to provide an immediate value to be used as an address to the required fn ptr
        # caller can do
        #   li t0, passed_addr # where passed_addr is in the equates file pointing to the memory location of the fn ptr
        #   ld t1, 0(t0)
        #   jalr t1

        os_data_section = """
        .section .os_data, "aw"
        # OS data
        """

        for var in self.per_hart_variables:
            os_data_section += f"{var}_mem:\n"
            os_data_section += "\n".join([f"    .dword {self.per_hart_variables[var]}" for _ in range(self.featmgr.num_cpus)]) + "\n"

        for var in self.shared_variables:
            os_data_section += f"{var}_mem:\n"
            os_data_section += f"    .dword {self.shared_variables[var]}\n"

        return os_data_section

    # A barrier, doesn't guarantee the harts are entering the barrier for the same purpose from the same code path.
    # Resets itself after all harts have entered the barrier so it can be used again.
    def os_barrier(self) -> str:
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

    # Use a precalculated and scaled offset stored in an num_harts elements long array to access data from an array with a more exotic layout.
    def place_retrieve_memory_indexed_address(
        self,
        index_array_name: str,
        target_array_name: str,
        index_element_size: int,
        hartid_reg: str,
        work_reg_1: str,
        dest_reg: str,
    ) -> str:
        return f"""
            la {work_reg_1}, {index_array_name}
            {Routines.place_offset_address_by_scaled_hartid(address_reg=work_reg_1, dest_reg=work_reg_1, hartid_reg=hartid_reg, work_reg=dest_reg, scale=index_element_size)}
            ld {dest_reg}, 0({work_reg_1}) # Load the index
            la {work_reg_1}, {target_array_name}
            add {dest_reg}, {work_reg_1}, {dest_reg} # Offset to the correct element
        """

    # Too specific to this OS code to be in the routines library
    #
    # Instead of repeatedly trying to get the same lock, the retry path routine generates a guess for an
    # available lock.
    #
    #   Do pair with a call to code from self.place_release_lock
    #
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
                li gp, 0x{self.featmgr.eot_pass_val:x} # Tests done
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

    def place_non_mp_par_scheduler_variables(self, dtests_ntimes: list, dtests_seq: str) -> str:
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
            assert False, "Unknown parallel scheduling mode"

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

    def generate_equates(self) -> str:
        equates = ""
        # Some OS data hack
        equates += """
            # Test OS data hack:")
            # These symbols contain the addresses of OS variables that can't be LAd directly"
        """
        offset_adder = 0

        def build_text(variable_name, offset):
            return f".equ {variable_name:35}, os_data + {offset}\n"

        for variable_name in self.per_hart_variables:
            equates += build_text(variable_name, offset_adder)
            offset_adder += self.featmgr.num_cpus * OpSys.OS_VARIABLE_SIZE

        for variable_name in self.shared_variables:
            equates += build_text(variable_name, offset_adder)
            offset_adder += OpSys.OS_VARIABLE_SIZE

        equates += "\n"
        return equates
