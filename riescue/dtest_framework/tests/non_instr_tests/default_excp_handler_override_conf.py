# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Conf file for default_excp_handler_override.s and default_excp_handler_override_s.s.

Registers a custom default handler for ILLEGAL_INSTRUCTION (cause 2) via
FeatMgr.register_default_exception_handler().  The handler writes 0xCAFE to
test_marker and advances xepc by 4 so the faulting 32-bit instruction is
skipped and the test can verify the handler ran.

The handler uses TrapContext so the same callable works regardless of whether
cause 2 is delegated to S-mode (scause/sepc/sret) or kept in M-mode
(mcause/mepc/mret).

Usage (M-mode, not delegated):
    riescued.py -t .../default_excp_handler_override.s \\
                --conf .../default_excp_handler_override_conf.py \\
                --seed 1 --run_iss --deleg_excp_to=machine

Usage (S-mode, delegated):
    riescued.py -t .../default_excp_handler_override_s.s \\
                --conf .../default_excp_handler_override_conf.py \\
                --seed 1 --run_iss --deleg_excp_to=super
"""

from riescue import Conf, FeatMgr, TrapContext


def my_illegal_handler(ctx: TrapContext) -> str:
    """Handler body for ILLEGAL_INSTRUCTION (cause 2).

    Writes 0xCAFE to test_marker, skips the faulting 32-bit instruction by
    advancing ctx.xepc by 4, and returns via ctx.xret.  test_marker is a
    random_addr equate; use li (not la) to load its address.
    """
    return f"""
    li    t0, test_marker
    li    t1, 0xCAFE
    sd    t1, 0(t0)
    csrr  t0, {ctx.xepc}
    addi  t0, t0, 4        # skip the faulting 32-bit illegal instruction
    csrw  {ctx.xepc}, t0
    {ctx.xret}
"""


class DefaultExcpHandlerOverrideConf(Conf):
    def add_hooks(self, featmgr: FeatMgr) -> None:
        # Override the default exception path for ILLEGAL_INSTRUCTION (cause 2).
        featmgr.register_default_exception_handler(
            cause=2,
            label="my_illegal_handler",
            assembly=my_illegal_handler,
        )


def setup() -> Conf:
    return DefaultExcpHandlerOverrideConf()
