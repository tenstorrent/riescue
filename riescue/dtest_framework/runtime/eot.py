# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator

# HTIF console putchar: packed 64-bit word per character (device 1, cmd 1) — Whisper handleStoreToHost / Spike-style.
# ACT4 Sail reference uses two 32-bit sw per character; not the same instruction pattern as this sd loop.
# dev=1, cmd=1 → bits [63:56] = 0x01, bits [55:48] = 0x01.
_HTIF_DEV_CMD_BITS: int = (1 << 56) | (1 << 48)


def rvcp_message(elf_basename: str, discrete: str, outcome: str) -> str:
    """Single-line RVCP status, newline-terminated. ASCII only (no ``TEST`` prefix before discrete)."""
    return f"RVCP: Test File {elf_basename} {discrete} {outcome}\n"


_RVCP_FAIL_BANNER_LINE = "============\n"

# ANSI: red background on ``<elf_basename> <discrete> FAILED``; reset after (Whisper / terminal must interpret SGR).
_RVCP_FAIL_BG_RED = "\x1b[41m"
_RVCP_FAIL_SGR_RESET = "\x1b[0m"


def rvcp_fail_message_with_banners(elf_basename: str, discrete: str) -> str:
    """FAIL line between banner lines; ``<elf> <discrete> FAILED`` uses red background SGR (ASCII + ESC)."""
    prefix = "RVCP: Test File "
    highlighted = f"{elf_basename} {discrete} FAILED"
    mid = f"{prefix}{_RVCP_FAIL_BG_RED}{highlighted}{_RVCP_FAIL_SGR_RESET}\n"
    return f"{_RVCP_FAIL_BANNER_LINE}{mid}{_RVCP_FAIL_BANNER_LINE}"


def _asm_escape_string(s: str) -> str:
    """Escape a Python string for use in a GAS ``.asciz`` directive (double-quoted)."""
    out: list[str] = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32 or ord(ch) > 126:
            out.append(f"\\{ord(ch):03o}")
        else:
            out.append(ch)
    return "".join(out)


def htif_rvcp_call_lines(label: str, message: str) -> list[str]:
    """
    Emit assembly lines to print ``message`` via ``htif_rvcp_print`` subroutine call.

    Stores the null-terminated string in ``.pushsection .runtime, "ax"`` so it lands in the
    already-mapped runtime section without requiring linker-script changes.  The call site uses
    ``la t4, <label>`` (PC-relative) + ``jal ra, htif_rvcp_print`` — two instructions instead of
    the previous O(len) inline sd sequence.  Clobbers: t4, ra (caller must not need ra preserved).
    """
    escaped = _asm_escape_string(message)
    return [
        '.pushsection .runtime, "ax"',
        f"{label}:",
        f'    .asciz "{escaped}"',
        ".popsection",
        ".align 2",  # Realign to 4-byte boundary: .popsection restores cursor to just after the string bytes
        f"la t4, {label}",
        "jal ra, htif_rvcp_print",
    ]


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

    def _htif_rvcp_print_subroutine(self) -> str:
        """
        ``htif_rvcp_print``: loop-based HTIF putchar subroutine.  Prints a null-terminated ASCII
        string pointed to by ``t4`` via HTIF tohost (one RV64 ``sd`` per byte, device 1 / cmd 1).
        Emitted only when ``--eot_print_htif_console`` or ``--print_rvcp_passes`` is active.
        Clobbers: t3, t4, t5, t6, ra (caller uses ``jal ra, htif_rvcp_print``).
        """
        if not (self.featmgr.eot_print_htif_console or self.featmgr.print_rvcp_passes):
            return ""
        dev_cmd = hex(_HTIF_DEV_CMD_BITS)
        return f"""
# HTIF RVCP console print subroutine: t4 = pointer to null-terminated message.
# norvc + balign: executable .runtime must stay 4-byte aligned for JAL/J relocations.
.option push
.option norvc
.balign 4
htif_rvcp_print:
    la   t3, tohost
    li   t6, {dev_cmd}
.Lhtif_rvcp_loop:
    lbu  t5, 0(t4)
    beqz t5, .Lhtif_rvcp_done
    or   t5, t5, t6
    sd   t5, 0(t3)
    addi t4, t4, 1
    j    .Lhtif_rvcp_loop
.Lhtif_rvcp_done:
    ret
.option pop
"""

    def _htif_rvcp_all_passed_string_data(self) -> str:
        """
        Static string for the ALL PASSED RVCP message in ``.runtime`` (near code for medany ``la``).
        ``.balign 4`` after ``.asciz`` keeps the following HTIF/EOT instructions 4-byte aligned so
        PC-relative ``j``/``jal`` relocs are not rejected (odd byte delta).
        """
        if not (self.featmgr.eot_print_htif_console or self.featmgr.print_rvcp_passes):
            return ""
        msg = rvcp_message(f"{self.pool.testname}.elf", "ALL", "PASSED")
        escaped = _asm_escape_string(msg)
        # .asciz adds trailing NUL; GAS may not pad before the next insn — explicit .space keeps
        # htif_rvcp_print on a 4-byte boundary (R_RISCV_JAL requires even PC-relative offset).
        n_with_nul = len(msg.encode("latin-1")) + 1
        pad = (4 - (n_with_nul % 4)) % 4
        pad_asm = f"    .space {pad}\n" if pad else ""
        return f"""
.align 3
.Lhtif_rvcp_msg_all_passed:
    .asciz "{escaped}"
{pad_asm}"""

    def generate(self) -> str:
        code = f"""
.section .runtime, "ax"
# End of test data
.align 3
tohost_mutex:
    .dword 0;
tohost_addr_mem:
    .dword tohost
{self._htif_rvcp_all_passed_string_data()}
{self._htif_rvcp_print_subroutine()}
# End of Test (4-byte align: PC-relative branches/jumps require even byte delta)
.balign 4
eot__end_test:
    {self.end_test()}

.balign 4
eot__passed:
    {self.passed()}

.balign 4
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

        Uses gp as a boolean for pass/fail. If gp is 1 it passed, otherwise failed.

        Since only one hart will write to tohost.
        """
        code = ""

        if self.mp_active:
            code += self._mp_end_test()
            return code

        code += """
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

        # nonzero gp means test failed, so increment number of hard fails
        code = f"""
    li t0, 0x1
    beq t0, gp, 1f
    {self.num_hard_fails.increment("t0", "a0")}
1:
"""
        # TODO: return to M mode, set Tvec to eot__failed (worst case panic will jump to this and fail instantly)
        # Can write the rest of the code assuming that we are in M mode, and panic will jump to eot__failed
        # TODO: FIX UP OS_END_TEST to use correct prefix and not use gp to tell if test passed or failed.

        if self.mp_parallel:
            # If parallel mode, holding a lock for other tests. Need to release lock

            code += "csrr s1, mhartid\n"

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
            li t1, num_hard_fails_pa
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

    def _eot_htif_console_rvcp_all_passed_asm(self) -> str:
        """
        Final EOT pass: whole test file succeeded (all discrete tests). Discrete tag \"ALL\".
        Emitted with --eot_print_htif_console or --print_rvcp_passes (HTIF tohost putchar).
        The string .Lhtif_rvcp_msg_all_passed is declared in the data area of generate() to
        avoid same-section .pushsection alignment issues when already in .runtime.
        """
        if not (self.featmgr.eot_print_htif_console or self.featmgr.print_rvcp_passes):
            return ""
        return "\n    la t4, .Lhtif_rvcp_msg_all_passed\n    jal ra, htif_rvcp_print\n"

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
        code += self._eot_htif_console_rvcp_all_passed_asm()
        code += f"""
    fence iorw, iorw
    ld t0, tohost_addr_mem
    li t1, 0x{self.featmgr.eot_pass_value:x}
    {"sw" if not self.featmgr.big_endian else "sd"} t1, 0(t0)
        """
        code += self.featmgr.call_hook(RV.HookPoint.POST_PASS)
        code += "\n    j eot__halt \n"
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
    {"sw" if not self.featmgr.big_endian else "sd"} t1, 0(t0)
        """
        code += self.featmgr.call_hook(RV.HookPoint.POST_FAIL)
        code += "\n    j eot__halt \n"
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
