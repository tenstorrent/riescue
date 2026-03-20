# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Conf file for default_handler_override.s.

Registers a custom default handler for Supervisor Software Interrupt (vec 1) via
FeatMgr.register_default_handler().  The handler clears SSIP and writes 0xCAFE
to test_marker so the test body can verify it ran.

Usage:
    riescued.py -t riescue/dtest_framework/tests/non_instr_tests/default_handler_override.s \\
                --conf riescue/dtest_framework/tests/non_instr_tests/default_handler_override_conf.py \\
                --seed 1 --run_iss
"""

from riescue import Conf, FeatMgr


def my_ssi_handler(featmgr: FeatMgr) -> str:
    """Handler body for Supervisor Software Interrupt (vec 1).

    Clears SSIP, writes 0xCAFE to test_marker, then returns with mret.
    test_marker is a random_addr equate; use li (not la) to load its address.
    """
    return """
    csrci mip, 2          # clear SSIP (bit 1)
    li    t0, test_marker
    li    t1, 0xCAFE
    sd    t1, 0(t0)
    mret
"""


class DefaultHandlerOverrideConf(Conf):
    def add_hooks(self, featmgr: FeatMgr) -> None:
        # Override the framework's built-in clear-and-return for SSI (vec 1)
        # with a handler that writes a recognisable value the test can check.
        featmgr.register_default_handler(
            vec=1,
            label="my_ssi_handler",
            assembly=my_ssi_handler,
        )


def setup() -> Conf:
    return DefaultHandlerOverrideConf()
