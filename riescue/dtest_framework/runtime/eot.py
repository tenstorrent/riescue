# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class Eot(AssemblyGenerator):
    """
    Handles end of test routines for the test.
    Tests either pass or fail, and this uses the default behavior of the tohost and fromhost registers.

    .. note::

        This code was written with the assumption that only one hart will write to tohost and writing to tohost will end execution.
        There's currently no mechanism to stop other tests from executing.
        This only waits for eot_wait_for_others_timeout loops before writing to tohost.


    By default the test will wait for all other harts to finish before writing to tohost.
    Only one hart will write to tohost. All other harts will wait for the first hart to write.

    .. note::

        Using seperate pass and fail labels so that custom code doesn't need to check if test passed or failed.

    """

    EOT_DEFAULT_WAIT_FOR_OTHERS_TIMEOUT = 5_000_000  # FIXME: RVTOOLS-4187 reduce to 500_000 once fixed

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.num_harts_ended = self.variable_manager.register_shared_variable("num_harts_ended", 0x0)
        self.num_hard_fails = self.variable_manager.register_shared_variable("num_hard_fails", 0x0)

        self.halt_label = "eot__halt"

        self.eot_wait_for_others_timeout = self.EOT_DEFAULT_WAIT_FOR_OTHERS_TIMEOUT  #: Number of loops to wait for other harts to finish before writing to tohost.

    def generate(self) -> str:
        code = f"""

.section .text
# End of test data
.align 3
tohost_mutex:
    .dword 0;
tohost_addr_mem:
    .dword tohost

# End of Test
eot__end_test:
    {self.end_test()}

eot__passed:
    {self.passed()}

eot__failed:
    {self.failed()}

# Halt the test, all harts enter here into a wfi j loop.
{self.halt_label}:
    {self.halt()}

.section .io_htif, "aw"
.align 6; .global tohost; tohost: .dword 0;
.align 6; .global fromhost; fromhost: .dword 0;
        """

        return code

    def end_test(self) -> str:
        """
        Generates end of test code.
        Code should be in handler_mode when jumping here.

        TODO:Returns to M mode if handler isn't in M mode.

        Uses gp as a boolean for pass/fail. If gp is 1 it passed, otherwise failed.

        Since only one hart will write to tohost.
        """
        code = ""

        # TODO: Return to M mode if not in M mode.
        # if self.handler_priv != RV.RiscvPrivileges.MACHINE:
        #     # jump to M mode
        #     # code += "csrr sp, sscratch\n"
        #     code += "ecall" + "\n"

        if self.mp_active:
            code += self._mp_end_test()
            return code

        code = """
        li t0, 0x1
        beq t0, gp, eot__passed
        j eot__failed
        """

        return code

    def _mp_end_test(self) -> str:
        """
        Generates code to end the test in multiprocessor mode.

        Called by syscalls, scheduler. If not in M mode, need to return to M mode.
        I think this means that any system calls will need to make sure we are in M mode?
        """
        code = ""
        # TODO: return to M mode, set Tvec to eot__failed (worst case panic will jump to this and fail instantly)
        # Can write the rest of the code assuming that we are in M mode, and panic will jump to eot__failed
        # TODO: FIX UP OS_END_TEST to use correct prefix and not use gp to tell if test passed or failed.

        if self.mp_parallel:
            # If parallel mode, holding a lock for other tests. Need to release lock

            # eot__end_test is running in handler privilege mode
            if self.handler_priv == RV.RiscvPrivileges.MACHINE:
                code += "csrr s1, mhartid\n"
            else:
                hartid_offset = self.variable_manager.get_variable("mhartid").offset
                code += "csrr tp, sscratch\n"
                code += f"ld s1, {hartid_offset}(tp)\n"

            # loading gp with 1, otherwise it will be garbage value. If it wins tohost_mutex it will write garbage value
            code += """
            li gp, 1

            # Check if we are storing nonzero in held_locks for this hart
            la a0, held_locks
            li t1, 8
            mul t1, s1, t1
            add a0, a0, t1

            ld t1, 0(a0) # Load the lock address
            beqz t1, eot__skip_lock_release # If zero, we don't hold a lock
            fence
            amoswap.w.rl x0, x0, (t1) # Release lock by storing 0.

            eot__skip_lock_release:
            """

        # num_harts_ended only matters for MP mode?  Should there be handlers for MP vs single hart?
        # if single hart I think we can just skip al lthis mutex and wait for others stuff, and just branch to eot__passed or eot__failed
        # fix all the labels first
        code += f"""

        # each hart increments num_harts_ended so that the first one waits till all harts have finished before writing to tohost
        eot__mark_done:
            {self.num_harts_ended.increment("t0", "t3")}

        # MP Specific, check if gp[31]==1
        # If so, then we detected a core bailed early. We should not write to tohost
        li t0, 0x80000000
        beq t0, gp, {self.halt_label}

        # Try to obtain tohost_mutex
        la a0, tohost_mutex
        j eot__tohost_try_lock


        eot__tohost_try_lock:
            li t0, 1                    # Initialize swap value.
            ld           t1, (a0)       # Check if lock is held.
            bnez         t1,  {self.halt_label}        # fail if held.
            amoswap.d.aq t1, t0, (a0)   # Attempt to acquire lock.
            bnez         t1,  {self.halt_label}        # fail if held

            # obtained lock, no need to release this one since we are ending the simulation.
            li t2, {self.featmgr.num_cpus}
            li t1, num_hard_fails
            li t4, {self.eot_wait_for_others_timeout} # Timeout for eot waiting

        eot__wait_for_others:
            bltz t4, eot__failed # This is a timeout, other harts didn't finish
            addi t4, t4, -1
            lw t0, (t1)
            bnez t0, eot__failed # Write immediately if there was a hard fail
            lw t0, (t3)
            bne t0, t2, eot__wait_for_others

        """
        return code

    def passed(self) -> str:
        """
        Generate code to indicate that test has passed.
        Spike/Whisper use tohost with a value of 1 to indicate a pass.
        Only one hart will write to tohost in multiprocessor mode.

        This loads value into register and writes to host

        Platforms may have other ways to indicate a pass, so additional code can be appeneded here.
        """

        code = ""
        code += self.featmgr.call_hook(RV.HookPoint.PRE_PASS)
        code += f"""
    fence iorw, iorw
    ld t0, tohost_addr_mem
    li t1, 0x{self.featmgr.eot_pass_value:x}
    sw t1, 0(t0)
    j eot__halt
        """
        code += self.featmgr.call_hook(RV.HookPoint.POST_PASS)
        return code

    def failed(self) -> str:
        """
        Generate code to indicate that test has failed.
        Spike/Whisper use tohost with a value of 1 to indicate a fail.
        Only one hart will write to tohost in multiprocessor mode.

        This loads value into register and writes to host

        Platforms may have other ways to indicate a fail, so additional code can be appeneded here.
        """

        code = ""
        code += self.featmgr.call_hook(RV.HookPoint.PRE_FAIL)
        code += f"""
    fence iorw, iorw
    ld t0, tohost_addr_mem
    li t1, 0x{self.featmgr.eot_fail_value:x}
    sw t1, 0(t0)
    j eot__halt
        """
        code += self.featmgr.call_hook(RV.HookPoint.POST_FAIL)
        return code

    def halt(self) -> str:
        """
        Generates halt code.
        Harts enter here if they aren't writing to tohost.

        If linux mode, do exit syscall.

        Additional code can be appended here if needed using hooks.
        """
        code = ""
        if self.featmgr.linux_mode:
            code = """
    li a7, 93  # __NR_exit
    li a0, 0   # exit code
    ecall \n"""

        # if hooks enabled, add them here.
        code += self.featmgr.call_hook(RV.HookPoint.PRE_HALT)
        code += """
eot__halt_loop:
    wfi
    j eot__halt_loop
        """
        return code
