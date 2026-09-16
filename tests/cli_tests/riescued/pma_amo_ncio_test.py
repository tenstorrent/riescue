# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class PmaAmoNcioTest(BaseRiescuedTest):
    """
    Both sides of mmap.pma.allow_amos_in_pma_ncio, run through the ISS.

    The two cpuconfigs differ in that one key. pma_amo_ncio.s asks for
    pma_amo_type=arithmetic on a noncacheable and an io region either way, and branches on
    the PMA_ALLOW_AMOS_IN_NCIO equate: with the knob on the atomics retire, with it off the
    clamp makes them access-fault and OS_SETUP_CHECK_EXCP catches causes 7 and 5.
    """

    CPUCONFIG_ON = "riescue/dtest_framework/tests/cpu_config_pma_amo_ncio.json"
    CPUCONFIG_OFF = "riescue/dtest_framework/tests/cpu_config_pma_amo_ncio_off.json"

    def setUp(self):
        self.testname = "riescue/dtest_framework/tests/pma_amo_ncio.s"
        super().setUp()

    def test_amos_allowed_in_ncio(self):
        "allow_amos_in_pma_ncio on: NC/IO keep pmacfg[6:5] and the atomics succeed"
        cli_args = ["--run_iss", "--needs_pma", "--cpuconfig", self.CPUCONFIG_ON]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_amos_clamped_in_ncio(self):
        "allow_amos_in_pma_ncio off: NC/IO clamp to AMONone and the atomics access-fault"
        cli_args = ["--run_iss", "--needs_pma", "--cpuconfig", self.CPUCONFIG_OFF]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
