# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Conf file for default_combined_handler_override.s.

Registers BOTH:
  * an interrupt handler for SSI (vec 1) via register_default_handler()
  * an exception handler for ILLEGAL_INSTRUCTION (cause 2) via
    register_default_exception_handler()

in a single Conf.add_hooks() call, to demonstrate that the two override
mechanisms compose cleanly.  Each handler writes a distinctive sentinel to
its own marker so the test can check that both handlers actually ran.

Usage:
    riescued.py -t .../default_combined_handler_override.s \\
                --conf .../default_combined_handler_override_conf.py \\
                --seed 1 --run_iss --deleg_excp_to=machine
"""

from riescue import Conf, FeatMgr, TrapContext


def combined_ssi_handler(ctx: TrapContext) -> str:
    """Interrupt handler for SSI (vec 1): clear SSIP, write 0xCAFE to test_marker_intr, xret."""
    return f"""
    csrr  t0, {ctx.xip}
    li    t1, ~(1 << 1)
    and   t0, t0, t1
    csrw  {ctx.xip}, t0   # clear SSIP (bit 1)
    li    t0, test_marker_intr
    li    t1, 0xCAFE
    sd    t1, 0(t0)
    {ctx.xret}
"""


def combined_illegal_handler(ctx: TrapContext) -> str:
    """Exception handler for ILLEGAL_INSTRUCTION (cause 2):
    write 0xBEEF to test_marker_excp, skip the faulting insn, xret."""
    return f"""
    li    t0, test_marker_excp
    li    t1, 0xBEEF
    sd    t1, 0(t0)
    csrr  t0, {ctx.xepc}
    addi  t0, t0, 4        # skip the faulting 32-bit illegal instruction
    csrw  {ctx.xepc}, t0
    {ctx.xret}
"""


class DefaultCombinedHandlerOverrideConf(Conf):
    def add_hooks(self, featmgr: FeatMgr) -> None:
        # Interrupt override (mirrors the standalone default_handler_override flow).
        featmgr.register_default_handler(
            vec=1,
            label="combined_ssi_handler",
            assembly=combined_ssi_handler,
        )
        # Exception override registered in the same add_hooks() call.
        featmgr.register_default_exception_handler(
            cause=2,
            label="combined_illegal_handler",
            assembly=combined_illegal_handler,
        )


def setup() -> Conf:
    return DefaultCombinedHandlerOverrideConf()
