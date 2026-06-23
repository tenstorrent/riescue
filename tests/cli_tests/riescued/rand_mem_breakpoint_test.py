# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""CLI tests for the random memory breakpoint feature
(``--rand_mem_breakpoint_pct`` / ``--rand_mem_n_triggers`` /
``--rand_mem_max_fires``).

Targets ``riescue/dtest_framework/tests/sdtrig/rand_mem_breakpoint.s``,
which emits two ``;#rand_mem_breakpoint_pool`` directive instances and
exercises a long mix of loads/stores against the pooled memories.
"""

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


_SDTRIG_CONFIG = "dtest_framework/lib/config_sdtrig.json"


class TestRandMemBreakpointDefaultOff(BaseRiescuedTest):
    """No flags → feature stays off; the pool directive is parsed but not consumed.
    Test still passes because every load/store completes normally."""

    def setUp(self):
        self.testname = "dtest_framework/tests/sdtrig/rand_mem_breakpoint.s"
        super().setUp()

    def test_cli(self):
        args = ["--run_iss", "--cpuconfig", _SDTRIG_CONFIG]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class TestRandMemBreakpointSingle(BaseRiescuedTest):
    """pct=100, n=1, max_fires=0 — single trigger, single shot, handler disables on first fire."""

    def setUp(self):
        self.testname = "dtest_framework/tests/sdtrig/rand_mem_breakpoint.s"
        super().setUp()

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            _SDTRIG_CONFIG,
            "--rand_mem_breakpoint_pct",
            "100",
            "--rand_mem_n_triggers",
            "1",
            "--rand_mem_max_fires",
            "0",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class TestRandMemBreakpointMultiTrigger(BaseRiescuedTest):
    """pct=100, n=4 (max — caps at the load/store-trigger budget),
    max_fires=20 — multi-trigger with re-arm via the pool."""

    def setUp(self):
        self.testname = "dtest_framework/tests/sdtrig/rand_mem_breakpoint.s"
        super().setUp()

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            _SDTRIG_CONFIG,
            "--rand_mem_breakpoint_pct",
            "100",
            "--rand_mem_n_triggers",
            "4",
            "--rand_mem_max_fires",
            "20",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class TestRandMemBreakpointClampN(BaseRiescuedTest):
    """n=10 should be clamped (with a log warning) to the load/store-capable trigger
    cap. The test should still pass — clamping is non-fatal."""

    def setUp(self):
        self.testname = "dtest_framework/tests/sdtrig/rand_mem_breakpoint.s"
        super().setUp()

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            _SDTRIG_CONFIG,
            "--rand_mem_breakpoint_pct",
            "100",
            "--rand_mem_n_triggers",
            "10",
            "--rand_mem_max_fires",
            "5",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class TestRandMemBreakpointPctZero(BaseRiescuedTest):
    """pct=0 → feature off even with non-default n / max_fires set."""

    def setUp(self):
        self.testname = "dtest_framework/tests/sdtrig/rand_mem_breakpoint.s"
        super().setUp()

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            _SDTRIG_CONFIG,
            "--rand_mem_breakpoint_pct",
            "0",
            "--rand_mem_n_triggers",
            "4",
            "--rand_mem_max_fires",
            "20",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class TestRandMemBreakpointWithIcountInjection(BaseRiescuedTest):
    """Master + inner icount gate both at pct=100: arms 4 mcontrol6 watchpoints AND
    an icount trigger on slot 8 with random count drawn from the 'often' range
    [1,100]. Both fire types share the max_fires=20 re-arm budget."""

    def setUp(self):
        self.testname = "dtest_framework/tests/sdtrig/rand_mem_breakpoint.s"
        super().setUp()

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            _SDTRIG_CONFIG,
            "--rand_mem_breakpoint_pct",
            "100",
            "--rand_mem_n_triggers",
            "4",
            "--rand_mem_max_fires",
            "20",
            "--rand_mem_inject_icount_pct",
            "100",
            "--rand_mem_icount_density",
            "often",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class TestRandMemBreakpointIcountOnlyMasterOff(BaseRiescuedTest):
    """Inner icount gate is set but master --rand_mem_breakpoint_pct is 0:
    icount injection must NOT happen (inner gate is rolled only when master rolls).
    Test still passes (no triggers armed)."""

    def setUp(self):
        self.testname = "dtest_framework/tests/sdtrig/rand_mem_breakpoint.s"
        super().setUp()

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            _SDTRIG_CONFIG,
            "--rand_mem_breakpoint_pct",
            "0",
            "--rand_mem_inject_icount_pct",
            "100",
            "--rand_mem_icount_density",
            "moderate",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)
