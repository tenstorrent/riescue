# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue import RiescueD
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class DefaultTest(BaseRiescuedTest):
    """
    Default tests
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test.s"
        super().setUp()

    def test_cli_whisper(self):
        "Default test with spike"
        cli_args = ["--run_iss", "--seed", "0"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_cli_spike(self):
        "Default test with spike"
        cli_args = ["--run_iss", "--iss", "spike"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_cli_cpuconfig(self):
        "Default test with cpuconfig"
        cli_args = ["--run_iss", "--cpuconfig", "dtest_framework/tests/cpu_config.json"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_2gb_dragm_4kb_pages(self):
        "Default test with 2gb dram and 4kb pages"
        cli_args = ["--run_iss", "--cpuconfig", "dtest_framework/lib/twogb_dram_config.json", "--reserve_partial_phys_memory", "--all_4kb_pages", "--seed", "0"]
        for result in self.run_riescued_generator(testname=self.testname, cli_args=cli_args, iterations=self.iterations):
            for addr in self.get_all_physical_addresses(result):
                self.assertTrue(addr < 2**32, f"Physical address {addr} is greater than {2**32} (2GB DRAM). The memory map should constrain the generated addresses.")

    def test_cli_force_alignment_whisper(self):
        "Default test with force alignment"
        cli_args = ["--run_iss", "--force_alignment", "--iss", "whisper", "--seed", "0"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_cli_force_alignment_spike(self):
        "Default test with force alignment"
        cli_args = ["--run_iss", "--force_alignment", "--iss", "spike", "--seed", "0"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_long_spike(self):
        args = ["--run_iss", "--iss", "spike", "--tohost", "auto", "--cpuconfig", "dtest_framework/lib/twogb_dram_config.json", "--all_4kb_pages"]
        testname = "dtest_framework/tests/test_long.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


class TutorialTest(BaseRiescuedTest):
    def test_example_test(self):
        "Example test used in tutorial, should always pass"
        args = ["--run_iss", "--iss", "whisper"]
        testname = "dtest_framework/tests/tutorials/example_test.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_randomization_test(self):
        "Example test used in tutorial, should always pass"
        args = ["--run_iss", "--iss", "whisper"]
        testname = "riescue/dtest_framework/tests/tutorials/randomization_test.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_virtual_memory_test(self):
        "Example test used in tutorial, should always pass"
        args = ["--run_iss", "--iss", "whisper"]
        testname = "riescue/dtest_framework/tests/tutorials/virtual_memory.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_tutorial_cpu_configuration(self):
        "Example test used in tutorial, should always pass"
        args = ["--run_iss", "--cpuconfig", "riescue/dtest_framework/tests/tutorials/cpu_config.json"]
        testname = "riescue/dtest_framework/tests/tutorials/virtual_memory.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
