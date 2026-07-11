# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
CLI tests for RiescueD --hart_ids (discontiguous hart IDs), simulated on Whisper.

Whisper presents the discontiguous mhartid values via a cores/harts/core_hart_id_offset
topology supplied through --whisper_config_json (the framework leaves ISS hart-topology
config to the user once --hart_ids is given). ``whisper_config_discontiguous.json`` uses
cores=2, harts=1, core_hart_id_offset=2, i.e. mhartid values {0, 2}.
"""

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest
from riescue.lib.toolchain.exceptions import ToolFailureType

_DISCONTIGUOUS_WHISPER_CONFIG = "dtest_framework/lib/whisper_config_discontiguous.json"


def _read_generated(test_dir):
    "Concatenate all generated assembly (.s/.inc) under a run directory."
    text = ""
    for pat in ("*.s", "*.inc"):
        for path in test_dir.rglob(pat):
            text += path.read_text()
    return text


class HartIdsCliTest(BaseRiescuedTest):
    def setUp(self):
        self.testname = "dtest_framework/tests/mp_2p.s"
        super().setUp()

    def test_discontiguous_hart_ids_runs_on_whisper(self):
        "Build + simulate an MP test whose harts are mhartid {0, 2} on Whisper."
        cli_args = [
            "--num_cpus",
            "2",
            "--hart_ids",
            "0,2",
            "--run_iss",
            "--iss",
            "whisper",
            "--whisper_config_json",
            _DISCONTIGUOUS_WHISPER_CONFIG,
        ]
        # run_riescued raises if the ISS run fails, so reaching the assertions means it passed.
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=1)
        text = _read_generated(self.test_dir)
        self.assertIn("hart_id_table:", text)

    def test_default_num_cpus_has_no_lookup_table(self):
        "The default contiguous MP build must not emit the lookup table (unchanged behavior)."
        cli_args = ["--num_cpus", "2"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=1)
        text = _read_generated(self.test_dir)
        self.assertNotIn("hart_id_table:", text)
        self.assertNotIn("_hartid_to_index_loop", text)

    def test_discontiguous_hart_ids_mismatched_dut_fails(self):
        "A {0,4} test run on a DUT presenting mhartid {0,2} must fail: hart 2 is unexpected."
        # The discontiguous whisper config drives the hart topology (RiescueD does not pass
        # --harts when --hart_ids is set), so it presents mhartid {0, 2}; the test's lookup
        # table is {0, 4}, so the hart at mhartid 2 is not found and fails the test.
        cli_args = [
            "--num_cpus",
            "2",
            "--hart_ids",
            "0,4",
            "--run_iss",
            "--iss",
            "whisper",
            "--whisper_config_json",
            _DISCONTIGUOUS_WHISPER_CONFIG,
        ]
        for _ in self.expect_toolchain_failure_generator(
            testname=self.testname,
            cli_args=cli_args,
            failure_kind=ToolFailureType.TOHOST_FAIL,
            iterations=1,
        ):
            pass

    def test_contiguous_hart_ids_mismatched_dut_fails(self):
        "A contiguous {0,1} test run on Whisper presenting mhartid {0,2} must fail: hart 2 is out of range."
        cli_args = [
            "--num_cpus",
            "2",
            "--run_iss",
            "--iss",
            "whisper",
            "--whisper_config_json",
            _DISCONTIGUOUS_WHISPER_CONFIG,
        ]
        for _ in self.expect_toolchain_failure_generator(
            testname=self.testname,
            cli_args=cli_args,
            failure_kind=ToolFailureType.TOHOST_FAIL,
            iterations=1,
        ):
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
