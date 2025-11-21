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

    All harts run the same test in parallel, with a sync barrier before starting the test.
    """

    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
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

        code += f"""
            la t0, scheduler__setup
            ld t1, 0(t0)
            {self.os_barrier_with_saves(regs_to_save=['t0', 't1'], scratch_regs=['s8', 's9'])}
            bnez {self.hartid_reg}, scheduler__not_allowed_to_write
            sd x0, 0(t0)
        scheduler__not_allowed_to_write:
            mv t0, x0
            bnez t1, scheduler__start_next_test
        """

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

        code += self.simultaneous_scheduler_variables()

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

            {self.os_barrier_with_saves(regs_to_save=['t1'], scratch_regs=['s9'])}
            """
            code += self.os_barrier_with_saves(regs_to_save=["t1"], scratch_regs=["s9"]) + "\n"
            code += "jr t1   # jump to t1\n"
        else:
            # For user mode use sret to jump to test
            code += self.switch_test_privilege(
                from_priv=RV.RiscvPrivileges.SUPER,
                to_priv=self.test_priv,
                jump_register="t1",
                pre_xret=self.os_barrier(),
            )
        return code

    def scheduler(self) -> str:
        """
        Generates main scheduler logic.
        Assumes t1 has the previous number of runs left.

        Runs all tests through a barrier, loads test pointer, decrements num_runs, and gets index to test label array.
        """
        code = "scheduler__endless_loop:\n"
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
        beqz t1, os_end_test # end program, if zero

        # Decrement num_runs and store it back
        decrement_num_runs:
        addi t2, t1, -1
        {self.os_barrier_with_saves(regs_to_save=["t0", "t1", "t2"], scratch_regs=["s7", "s8", "s9"], num_tabs=4)}
        {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv, mhartid_offset=self.mhartid_offset)}
        bnez {self.hartid_reg}, scheduler__dont_write
        sw t2, 0(t0)

        scheduler__dont_write:

        scheduler:
        mv t0, t1
        slli t0, t0, 3
        """
        return code

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
