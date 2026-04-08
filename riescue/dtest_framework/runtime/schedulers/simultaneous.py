# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Tuple, Any

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.schedulers.scheduler import Scheduler


class SimultaneousScheduler(Scheduler):
    """
    Generates test scheduler assembly code for MP simultaneous mode.

    All harts run the same test at the same time.
    Each ``scheduler__execute_test`` call includes a sync barrier.

    test_setup and test_cleanup also include a barrier before executing.
    """

    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def scheduler_init(self) -> str:
        "Launches test_setup for all harts. Since this is a simultaneous scheduler, need a barrier before running test_setup"
        return "call os_barrier_amo\n" + super().scheduler_init()

    def scheduler_routines(self) -> str:
        """
        Simultaneous scheduler logic.
        Gets number of runs ``num_runs``, uses a barrier to ensure all harts have read it, and uses it to index into the test label array ``os_test_sequence``.
        After barrier, Hart 0 decrements the number of runs and stores it back to ``num_runs``, then jumps to ``scheduler__execute_test``.

        Assumes t1 has the previous number of runs left.

        """
        code = ""
        code += "\n# Scheduler functions\n"
        code += self.os_barrier_amo()
        return code

    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading a0 with the next test.
        """
        code = ""

        if not self.featmgr.no_random_csr_reads:
            code += "scheduler__csr_read_randomization:\n"
            code += self.csr_read_randomization() + "\n"

        code += f"""
        # iff (num_runs == 0) goto scheduler__finished
        scheduler__load_num_runs:
            la s11, num_runs
            lw s10, 0(s11)
            beqz s10, {self.scheduler_finished_label}

        # Decrement num_runs and store it back
        scheduler__decrement_num_runs:
            addi s10, s10, -1
        """
        # barrier after all harts have read num_runs but before decrementing and storing it to shared memory.
        # using s11, s10 bc a* and t* registers clobbered by barrier
        code += "call os_barrier_amo\n"

        # writing num_runs back to shared memory
        # only hart 0 needs to store s10
        # FIXME: Use variable manager to make this more readable.
        code += f"csrr {self.hartid_reg}, mhartid\n"
        code += f"""
            bnez {self.hartid_reg}, scheduler__calc_test_pointer
            sw s10, 0(s11)


        scheduler__calc_test_pointer:
            slli s11, s10, 3     # s11 = (--num_runs) * 8
        """

        # Store current test index for RVCP pass/fail messages
        # s10 contains the decremented num_runs - look up discrete test index from dtest_index_map
        if self.featmgr.rvcp_print_enabled():
            code += f"""
        # Store current test index for RVCP messages
        # Look up discrete test index from dtest_index_map[s10]
        la t3, dtest_index_map
        slli t4, s10, 2  # t4 = s10 * 4 (word size)
        add t3, t3, t4
        lw t3, 0(t3)   # t3 = dtest_index_map[s10]
        {self.current_test_index.store(src_reg="t3")}
        """

        code += """
        # s11 = os_test_sequence + ((--num_runs) * 8])
        # or os_test_sequence[--num_runs]
        scheduler__load_test_addr:
            la t0, os_test_sequence
            add s11, s11, t0
            ld t1, 0(s11)
        """

        return code

    def execute_test(self) -> str:
        """
        Execute test address loaded into s11.
        Barrier clobbers a0, so need to save to a different register (doesn't follow ABI so can't use s0, s1)
        """
        code = "call os_barrier_amo\n"
        code += super().execute_test()
        return code

    # Barrier methods
    def os_barrier_amo(self) -> str:
        """
        OS Barrier implementation.
        All harts wait at this point until every hart reaches the barrier, then all proceed together.
        Barrier is reusable after all harts have passed it


        .. note::
            In the future this should just use the ABI calling convention (s0-s11 are preserved, sp/tp preserved. all others can be overwritten).
            Not changing this just yet, becasue Routines



        """
        code = "os_barrier_amo:\n"
        code += Routines.place_barrier(
            name="osb",
            lock_addr_reg="a0",
            arrive_counter_addr_reg="a1",
            depart_counter_addr_reg="a2",
            flag_addr_reg="a3",
            swap_val_reg="t0",
            work_reg_1="s1",
            work_reg_2="t2",
            num_cpus=self.featmgr.num_cpus,
            end_test_label="os_end_test_addr_pa",
            max_tries=self.MAX_OS_BARRIER_TRIES,
            use_zawrs=self.featmgr.is_feature_enabled("zawrs"),
            bare=True,
        )
        code += """
            ret
        op_nop:
            nop
            ret
        # a0 will hold the lock address
        # a1 holds subroutine address
        os_critical_section_amo:
            mv s0, ra # Preserve return address
            li a0, barrier_lock_pa
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
        return code

    def os_barrier_with_saves(self, regs_to_save: list[str], scratch_regs: list[str], num_tabs: int = 4) -> str:
        """
        This calls os_barrier_amo, but does some moves.
        Callers could just not use the registers that the os_barrier clobbers and call os_barrier_amo directly.
        This would save a few instructions.
        """
        if len(regs_to_save) != len(scratch_regs):
            raise ValueError("regs_to_save and scratch_regs must be the same length")
        routine = ""
        for src, dst in zip(regs_to_save, scratch_regs):
            routine += "\t".join("" for _ in range(num_tabs + 1)) + f"mv {dst}, {src}\n"
        routine += "call os_barrier_amo\n"
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
