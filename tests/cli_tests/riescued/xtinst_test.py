# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest import mock

from riescue import RiescueD
import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.trap_handler import TrapHandler
from riescue.lib.toolchain.exceptions import ToolFailureType

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest

TESTNAME = "dtest_framework/tests/non_instr_tests/xtinst.s"


class XtinstTests(BaseRiescuedTest):
    """
    CLI tests for the trap handler's ``mtinst``/``htinst`` check.

    The fixture takes one trap per shape the spec allows the trap instruction
    register to hold, and the check runs underneath each of them, so a passing run
    already means every value whisper wrote was accepted. These tests additionally
    assert that the check actually *executed* -- otherwise a gating bug would make
    the fixture pass by never checking anything.

    The fixture's page-straddling case has no label of its own to assert on; it
    flows through the LOAD kind with a nonzero Addr. Offset, which the check must
    accept because the compare mask excludes that field.
    """

    def xtinst_checks(self, runs: list[RiescueD], *, expect: tuple = ()):
        """Assert the check ran, reached no failure path, and covered ``expect``."""
        for run in runs:
            subroutines = self.get_all_executed_subrountines(run)
            entered = [s for s in subroutines if s.endswith("xtinst_check")]
            self.assertGreater(len(entered), 0, "Expected the xtinst check to execute at least once")
            failures = [s for s in subroutines if s.endswith("xtinst_fail")]
            self.assertEqual(failures, [], f"xtinst check rejected a value: {failures}")
            for label in expect:
                matched = [s for s in subroutines if s.endswith(label)]
                self.assertGreater(len(matched), 0, f"Expected the xtinst check to reach {label}")

    def executed_labels(self, elf_name: str = "xtinst") -> set:
        """Labels executed by the last run, read straight out of the run directory.

        The base class trace helpers take a RiescueD result, which a run that is *expected* to fail
        never produces, so resolve the disassembly and the whisper log from the run directory
        instead. Same regexes, same PC-to-label mapping.
        """
        labels = {}
        for line in (self.test_dir / f"{elf_name}.dis").read_text().splitlines():
            match = self.dis_regex.match(line.strip())
            if match:
                labels[int(match.group(1), 16)] = match.group(2)
        executed = set()
        for line in (self.test_dir / f"{elf_name}_whisper.log").read_text().splitlines():
            match = self.whisper_regex.match(line)
            if match:
                label = labels.get(int(match.group(3), 16))
                if label is not None:
                    executed.add(label)
        return executed

    def test_xtinst(self):
        """Bare-metal HS: traps land in the S-mode handler, which reads htinst.

        The env is pinned because the fixture header allows either, and a virtualized
        seed with a randomized hedeleg can route these faults to the VS handler, which
        has no trap instruction CSR and so no check to observe.
        """
        args = ["--run_iss", "--check_xtinst", "--disassemble_test", "--test_env", "bare_metal"]
        runs = self.run_riescued(testname=TESTNAME, cli_args=args, iterations=self.iterations)
        self.xtinst_checks(runs, expect=("xtinst_dir_load", "xtinst_dir_store", "xtinst_fetch_compressed", "xtinst_dir_any"))

    def test_xtinst_machine_deleg(self):
        """Non-delegated traps land in the M-mode handler, which reads mtinst and
        re-fetches the trapping instruction through MPRV."""
        args = ["--run_iss", "--check_xtinst", "--disassemble_test", "--deleg_excp_to=machine"]
        runs = self.run_riescued(testname=TESTNAME, cli_args=args, iterations=self.iterations)
        self.xtinst_checks(runs, expect=("trap_handler_m__xtinst_check", "trap_handler_m__xtinst_zero_fields"))

    def test_xtinst_virtualized_hs(self):
        """VS-mode traps delegated to HS: the HS handler re-fetches the guest
        instruction with hlvx.hu, under a real two-stage walk. This is also the only
        configuration that reaches the HLV kind, since HLV needs V=0."""
        args = ["--run_iss", "--check_xtinst", "--disassemble_test", "--test_env", "virtualized", "--medeleg=0xffffffff", "--hedeleg=0x0", "--test_paging_g_mode", "sv39"]
        runs = self.run_riescued(testname=TESTNAME, cli_args=args, iterations=self.iterations)
        self.xtinst_checks(runs, expect=("xtinst_fetch_word_guest", "xtinst_dir_hyper"))

    def test_xtinst_sv48(self):
        runs = self.run_riescued(testname=TESTNAME, cli_args=["--run_iss", "--check_xtinst", "--disassemble_test", "--test_paging_mode", "sv48"], iterations=self.iterations)
        self.xtinst_checks(runs)

    def test_xtinst_rejects(self):
        """The generated check must actually reject a value the cause does not permit.

        Every other test here asserts the check accepts what the DUT wrote, which a check that
        never rejects anything would also satisfy. There is no way to make whisper write an
        illegal value, so instead narrow the spec table for one cause: with LOAD_PAGE_FAULT no
        longer permitting a transformed instruction, the transformed load whisper writes in
        test01 becomes illegal and the run must fail at to-host.
        """
        narrowed = dict(TrapHandler.XTINST_CAUSE_OPTIONS)
        narrowed[RV.RiscvExcpCauses.LOAD_PAGE_FAULT.value] = TrapHandler.XtinstOption.ZERO
        with mock.patch.object(TrapHandler, "XTINST_CAUSE_OPTIONS", narrowed):
            self.expect_toolchain_failure(
                testname=TESTNAME,
                cli_args=["--run_iss", "--check_xtinst", "--disassemble_test"],
                failure_kind=ToolFailureType.TOHOST_FAIL,
                iterations=1,
            )
        # ... and it must fail *because* of the check, not for some unrelated reason.
        executed = self.executed_labels()
        rejected = {label for label in executed if label.endswith("xtinst_fail")}
        self.assertNotEqual(rejected, set(), f"Expected the check to reject the transformed load; executed labels: {sorted(executed)}")


if __name__ == "__main__":
    unittest.main()
