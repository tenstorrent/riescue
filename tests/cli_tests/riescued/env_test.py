# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue.lib.toolchain.exceptions import ToolFailureType

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

    def test_fs_vs_randomization_equates(self):
        """FS/VS randomization options are written to equates.inc with correct values and value-list comments."""
        cli_args = [
            "--run_iss",
            "--fs_randomization",
            "50",
            "--fs_randomization_values",
            "1, 2",
            "--vs_randomization",
            "75",
            "--vs_randomization_values",
            "2",
        ]
        results = self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=1)
        self.assertGreater(len(results), 0)
        equates = self.get_all_equates(results[0])
        self.assertIn("FS_RANDOMIZATION", equates)
        self.assertIn("VS_RANDOMIZATION", equates)
        self.assertEqual(equates["FS_RANDOMIZATION"], 50)
        self.assertEqual(equates["VS_RANDOMIZATION"], 75)
        # Check value-list comments appear in equates file
        equates_file = results[0].generated_files.elf.with_name(f"{results[0].generated_files.elf.stem}_equates.inc")
        self.assertTrue(equates_file.exists())
        content = equates_file.read_text()
        self.assertIn("FS_RANDOMIZATION_VALUES:", content)
        self.assertIn("VS_RANDOMIZATION_VALUES:", content)
        self.assertIn("1, 2", content)
        self.assertIn("0=Off 1=Initial 2=Clean 3=Dirty", content)

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

    def test_sstateen(self):
        "Test that sstateen setup works when smstateen extension is enabled"
        cli_args = ["--run_iss", "--cpuconfig=dtest_framework/lib/smstateen_config.json", "--whisper_config_json=dtest_framework/lib/whisper_smstateen_config.json"]
        self.run_riescued(testname="dtest_framework/tests/test.s", cli_args=cli_args, iterations=self.iterations)

    def test_hstateen(self):
        "Test that hstateen setup works when smstateen extension is enabled"
        cli_args = ["--run_iss", "--test_env=virtualized", "--cpuconfig=dtest_framework/lib/smstateen_config.json", "--whisper_config_json=dtest_framework/lib/whisper_smstateen_config.json"]
        self.run_riescued(testname="dtest_framework/tests/test.s", cli_args=cli_args, iterations=self.iterations)

    def test_no_va_pa_overlap(self):
        args = [
            "--run_iss",
            "--cpuconfig=dtest_framework/lib/config_no_va_pa_overlap.json",
            "--test_paging_mode=sv48",
        ]
        self.run_riescued(testname=self.testname, cli_args=args, iterations=self.iterations)


class BigEndianTest(BaseRiescuedTest):
    "Tests for big-endian mode"

    def setUp(self):
        self.testname = "dtest_framework/tests/riescued_ld_test.s"
        super().setUp()

    def test_big_endian(self):
        "Test that big-endian compiles and runs successfully"
        cli_args = ["--run_iss", "--big_endian", "--cpuconfig=dtest_framework/lib/config_big_endian.json"]
        self.run_riescued(testname=self.testname, cli_args=cli_args, iterations=self.iterations)


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


class ConfTests(BaseRiescuedTest):
    """
    Combined tests for --conf. Example conf
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test.s"
        super().setUp()

    def test_conf_loop(self):
        "Passed in Conf should cause test to hit max instruction limit"
        loop_conf = Path(__file__).parent / "data" / "loop_conf.py"
        args = ["--run_iss", "--conf", str(loop_conf), "--whisper_max_instr", "2500"]
        for failure in self.expect_toolchain_failure_generator(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.MAX_INSTRUCTION_LIMIT, iterations=self.iterations):
            pass  # We expect a failure, so we don't need to do anything


if __name__ == "__main__":
    unittest.main(verbosity=2)
