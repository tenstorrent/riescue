# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import tempfile
from typing import Optional
from pathlib import Path

from riescue.lib.toolchain import Compiler, Whisper, ToolchainError, ToolFailureType, Objcopy


class ToolTests(unittest.TestCase):
    """
    Tests for toolchain.
    Checks that Correct error are raised. ToolchainErrors are moslty needed for CliTests
    """

    def setUp(self):
        self.toolchain_dir = Path(__file__).parent
        self.whisper_config = Path("dtest_framework/lib/whisper_config.json")
        self.madeupfile = Path("madeupfile.elf")
        self.temp_out = tempfile.NamedTemporaryFile(delete=False)
        self.temp_log = tempfile.NamedTemporaryFile(delete=False)
        self.temp_out_path = Path(self.temp_out.name)
        self.temp_log_path = Path(self.temp_log.name)

    def tearDown(self):
        self.temp_out.close()
        self.temp_out_path.unlink()
        self.temp_log.close()
        self.temp_log_path.unlink()

    def compile_elf(self, opts: list[str] = [], test_equates: Optional[list[str]] = None) -> Path:
        "Helper method - Compiles a simple assembly program"
        linker_script = self.toolchain_dir / "simple_loader.ld"
        test_assembly = self.toolchain_dir / "simple_test.S"

        compiler = Compiler(compiler_path=None, compiler_opts=opts, compiler_march="rv64i", test_equates=test_equates, abi="lp64")
        output_elf = self.temp_out_path
        compiler.args += [
            "-T",
            str(linker_script),
            "-o",
            str(output_elf),
            str(test_assembly),
        ]
        compiler.run(Path("logfile.txt"))
        self.assertTrue(output_elf.exists(), f"Compiler should have created a simple elf: {output_elf}")
        return output_elf

    def test_compiler(self):
        "Tests compiler works with args and input file"
        self.compile_elf()

    def test_whsiper(self):
        "Tests compiler + Whisper works"
        elf = self.compile_elf()
        Whisper(Path("whisper/whisper"), [elf], self.whisper_config, 1000, "0x10000000000000000").run(elf_file=elf, output_file=Path("logfile.txt"))

    def test_whisper_missing_elf(self):
        "Tests that Whisper raises ELF_FAILURE when given a fake ELF path"
        self.assertFalse(self.madeupfile.exists(), f"This file should not exist: {self.madeupfile}")
        args = [self.madeupfile]

        whisper = Whisper(Path("whisper/whisper"), args, self.whisper_config, 1000, "0x10000000000000000")
        with self.assertRaises(ToolchainError) as excp:
            whisper.run(elf_file=self.madeupfile, output_file=self.temp_log_path)
        self.assertEqual(excp.exception.kind, ToolFailureType.ELF_FAILURE)
        if excp.exception.error_text is not None:
            self.assertIn("Failed to load ELF", excp.exception.error_text)
            self.assertIn("ran: ", str(excp.exception))
        else:
            self.assertIsNotNone(excp.exception.error_text, f"Error text should not be None: {excp.exception.error_text}")

    def test_whisper_no_elf(self):
        "Tests that Whisper raises ELF_FAILURE when no ELF is passed"
        whisper = Whisper(Path("whisper/whisper"), [], self.whisper_config, 1000, "0x10000000000000000")
        with self.assertRaises(ToolchainError) as excp:
            whisper.run(elf_file=None, output_file=self.temp_log_path)
        self.assertEqual(excp.exception.kind, ToolFailureType.ELF_FAILURE)

    def test_invalid_whisper_config(self):
        "Test that whisper fails with BAD_CONFIG if given bad whisper_config.json"
        invalid_whisper_config = self.toolchain_dir / "invalid_whisper_config.json"
        self.assertTrue(invalid_whisper_config.exists(), f"Invalid whisper config should exist: {invalid_whisper_config}")
        whisper = Whisper(Path("whisper/whisper"), [self.madeupfile], invalid_whisper_config, 1000, "0x10000000000000000")
        with self.assertRaises(ToolchainError) as excp:
            whisper.run(elf_file=self.madeupfile, output_file=self.temp_log_path)
        self.assertEqual(excp.exception.kind, ToolFailureType.BAD_CONFIG)

    def test_max_instruction_test(self):
        max_instruction_test = self.compile_elf(test_equates=["INFINITE_LOOP=1"])
        whisper = Whisper(Path("whisper/whisper"), [max_instruction_test], self.whisper_config, 10, "0x10000000000000000")
        with self.assertRaises(ToolchainError) as excp:
            whisper.run(elf_file=max_instruction_test, output_file=self.temp_log_path)
        self.assertEqual(excp.exception.kind, ToolFailureType.MAX_INSTRUCTION_LIMIT)

    def test_tohost_fail(self):
        "Test that Whisper raises TOHOST_FAIL when given a program that writes to tohost. Expects a value of 3"
        fail_test = self.compile_elf(test_equates=["TEST_FAIL=1"])
        whisper = Whisper(Path("whisper/whisper"), [fail_test], self.whisper_config, 100, "0x10000000000000000")
        with self.assertRaises(ToolchainError) as excp:
            whisper.run(elf_file=fail_test, output_file=self.temp_log_path)
        self.assertEqual(excp.exception.kind, ToolFailureType.TOHOST_FAIL)
        self.assertIn("write to tohost failure", str(excp.exception))
        self.assertIn("3", str(excp.exception))
        self.assertEqual(excp.exception.fail_code, 3, "Expected the fail code to be 3")
        self.assertIsNotNone(excp.exception.log_path, "Fail shuld have included log_path")


class ObjcopyTests(ToolTests):
    def setUp(self):
        super().setUp()
        self.temp_bin = tempfile.NamedTemporaryFile(delete=False)
        self.temp_bin_path = Path(self.temp_bin.name)

    def tearDown(self):
        super().tearDown()
        self.temp_bin.close()

    def test_objcopy(self):
        "Test that objcopy works; just copies elf to temp_bin_path"
        elf = self.compile_elf()
        objcopy = Objcopy(Path("objcopy/objcopy"), [elf, "-O", "binary", self.temp_bin_path])
        objcopy.run()
        self.assertTrue(self.temp_bin_path.exists(), f"Objcopy should have created a binary: {self.temp_bin_path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
