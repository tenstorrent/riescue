# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class PrivateMapsOffsetNamesTests(BaseRiescuedTest):
    """End-to-end --private_maps with named base+offset mappings and init_memory."""

    def setUp(self):
        self.testname = "dtest_framework/tests/private_maps_offset_names.s"
        super().setUp()

    def test_private_maps_offset_names_whisper_sv39(self):
        args = [
            "--cpuconfig",
            "dtest_framework/tests/cpu_config_mp_with_c.json",
            "--private_maps",
            "--run_iss",
            "--iss",
            "whisper",
            "--deleg_excp_to",
            "machine",
            "--test_priv_mode",
            "super",
            "--seed",
            "0",
            "--test_paging_mode",
            "sv39",
            "--mp",
            "on",
            "--mp_mode",
            "simultaneous",
            "--num_cpus",
            "2",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
