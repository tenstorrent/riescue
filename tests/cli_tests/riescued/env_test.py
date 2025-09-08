# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class TestEnvTests(BaseRiescuedTest):
    """
    Tests that change test environment and test output
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/riescued_ld_test.s"
        super().setUp()

    def test_ld_test(self):
        "Testing pasing ld_test"
        cli_args = ["--run_iss"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_single_assembly_file(self):
        "Test that generates a single assembly file"
        cli_args = ["--run_iss", "--single_assembly_file"]
        for _ in self.run_riescued_generator(testname=self.testname, cli_args=cli_args, iterations=self.iterations):
            for x in self.test_dir.iterdir():
                self.assertNotIn(".inc", x.name, "Shouldn't have any .inc files")

    def test_single_assembly_file_wysiwyg(self):
        "Test that generates a single assembly file"
        cli_args = ["--run_iss", "--wysiwyg", "--single_assembly_file"]
        for _ in self.run_riescued_generator(testname=self.testname, cli_args=cli_args, iterations=self.iterations):
            for x in self.test_dir.iterdir():
                self.assertNotIn(".inc", x.name, "Shouldn't have any .inc files")

    def test_wysiwyg(self):
        "Test that uses WYSIWYG mode"
        cli_args = ["--run_iss", "--wysiwyg"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_csr_init_mask(self):
        "Test that uses WYSIWYG mode"
        testname = "dtest_framework/tests/init_csr_test.s"
        cli_args = ["--run_iss", "--csr_init_mask", "mscratch=0xFFFFFFFF=0x12345678"]
        self.run_riescued(testname=testname, cli_args=cli_args, iterations=self.iterations)

    def test_pbmt_ncio_randomization(self):
        "Test that uses PBMT_NCIo_randomization"
        cli_args = ["--run_iss", "--pbmt_ncio_randomization", "100"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_ld_basic(self):
        args = ["--run_iss"]
        testname = "dtest_framework/tests/riescued_ld_test.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_ld_wysiwyg_single_asm(self):
        args = ["--run_iss", "--wysiwyg", "--single_assembly_file"]
        testname = "dtest_framework/tests/riescued_ld_test.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_paging_machine_mode(self):
        "test test_m_paging.s in machine mode"
        cli_args = ["--run_iss", "--enable_machine_paging"]
        testname = "dtest_framework/tests/test_m_paging.s"
        self.run_riescued(testname=testname, cli_args=cli_args, iterations=self.iterations)

    def test_paging_mode_any(self):
        "Default test with paging mode any"
        cli_args = ["--run_iss", "--test_paging_mode", "any"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_paging_g_mode_any(self):
        "Default test with paging g mode any"
        cli_args = ["--run_iss", "--test_paging_g_mode", "any"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_csr_init(self):
        "Default test with csr init"
        cli_args = ["--run_iss", "--csr_init", "mstatus=0x8000000A00046800", "--test_priv", "super", "--test_env", "virtualized", "--deleg_excp_to", "machine"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)

    def test_secure_mode_on(self):
        "Test that forces secure mode from the command line"
        # We have to be very selective of the test we run in secure mode. Idea is to force a non-secure test to run in secure mode.
        # We do not want to use test_stee as it forces secure mode from the test instead of the command line.
        # The default test (test.s) requests memory which can not be satisfied with our secure configs.
        # This test_long.s is a simple test that does not request memory with unsatisifiable constraints.
        cli_args = ["--run_iss", "--test_secure_mode=on"]
        cli_args += ["--cpuconfig=dtest_framework/lib/config_secure_0.json", "--whisper_config_json=dtest_framework/lib/whisper_secure_config.json"]
        self.run_riescued(testname="dtest_framework/tests/test_long.s", cli_args=cli_args, iterations=self.iterations)

    def test_secure_mode_random(self):
        "Test that randomizes secure mode from the command line"
        # We have to be very selective of the test we run in secure mode. Idea is to force a non-secure test to run in secure mode.
        # We do not want to use test_stee as it forces secure mode from the test instead of the command line.
        # The default test (test.s) requests memory which can not be satisfied with our secure configs.
        # This test_long.s is a simple test that does not request memory with unsatisifiable constraints.
        # When we do random secure mode, we need to use the secure config always to ensure when secure mode is randomly selected,
        # we have the correct config. But when secure mode is not selected, we need to setup pmp to work with the secure config.
        cli_args = ["--run_iss", "--test_secure_mode=random", "--setup_pmp"]
        cli_args += ["--cpuconfig=dtest_framework/lib/config_secure_0.json", "--whisper_config_json=dtest_framework/lib/whisper_secure_config.json"]
        self.run_riescued(testname="dtest_framework/tests/test_long.s", cli_args=cli_args, iterations=self.iterations)


class Bf16Test(BaseRiescuedTest):
    "Test for bf16"

    def setUp(self):
        self.testname = "dtest_framework/tests/bf16.s"
        super().setUp()

    def test_bf16_compiler_config(self):
        args = " ".join(
            [
                "--run_iss --compiler_path /tools_risc/opensrc/riscv-toolchain-06-13-23/llvm/linux/bin/clang",
                "--disassembler_path /tools_risc/opensrc/riscv-toolchain-06-13-23/llvm/linux/bin/llvm-objdump",
                "--compiler_march rv64imafdcv_zfh_zba_zbb_zbc_zbs_zfbfmin0p6",
                "--compiler_opts=-menable-experimental-extensions",
            ]
        )
        self.run_riescued(testname=self.testname, cli_args=args.split())


class Test_EquatesTests(BaseRiescuedTest):
    """
    Combined tests for test_equates
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_equates.s"
        super().setUp()

    def test_equates_single_define(self):
        args = ["--run_iss", "--test_equates", "CUSTOM_DEFINE_EN=1"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)

    def test_equates_multiple_defines(self):
        args = ["--run_iss", "--test_equates", "DEFINE_A=1", "-teq", "DEFINE_B=1"]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
