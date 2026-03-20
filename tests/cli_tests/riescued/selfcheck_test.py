# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import List
import re
import struct
import subprocess
from pathlib import Path

from riescue.lib.toolchain.exceptions import ToolchainError, ToolFailureType

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class SelfcheckTests(BaseRiescuedTest):
    """
    Tests that employ selfcheck functionality
    """

    def setUp(self):
        self.testname = "dtest_framework/tests/test_selfcheck.s"
        super().setUp()

    def _parse_selfcheck_dump(self, dump_path: Path) -> bytes:
        """Parse selfcheck hex dump file and return raw bytes."""
        with open(dump_path) as f:
            lines = f.readlines()
        # Skip first line (address)
        data = []
        for line in lines[1:]:
            for byte_str in line.strip().split():
                data.append(int(byte_str, 16))
        return bytes(data)

    def _get_selfcheck_section_info(self, elf_path: Path) -> tuple[int, int]:
        """Get offset and size of .selfcheck_data section using readelf."""
        result = subprocess.run(
            ["readelf", "-S", str(elf_path)],
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse readelf output for .selfcheck_data section
        # readelf -S output spans two lines per section:
        # Line 1: [Nr] Name Type Address Offset
        # Line 2: Size EntSize Flags Link Info Align
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if ".selfcheck_data" in line:
                # Extract offset from this line (last hex value)
                hex_vals = re.findall(r"[0-9a-f]{6,}", line)
                if len(hex_vals) >= 2:
                    offset = int(hex_vals[-1], 16)  # Offset is last hex on this line
                    # Size is on the next line (first hex value)
                    if i + 1 < len(lines):
                        size_hex = re.findall(r"[0-9a-f]{6,}", lines[i + 1])
                        if size_hex:
                            size = int(size_hex[0], 16)
                            return offset, size
        raise ValueError("No .selfcheck_data section found in ELF")

    def test_default_test_selfcheck(self):
        "Testing selfcheck mechanism with default test"
        cli_args = ["--run_iss", "--selfcheck"]
        self.run_riescued(testname="dtest_framework/tests/test.s", cli_args=cli_args, iterations=self.iterations)

    def _run_selfcheck_test_selfcheck(self, args: List[str] = []):
        """
        Run test_selfcheck and verify:
          Part A - overriding each of the 9 state macros produces a different checksum dump
          Part B - corrupting each stored checksum block causes the ISS to report failure
        """
        base_cli_args = ["--run_iss", "--selfcheck"] + args

        # --- Baseline run (seed 0) ---
        baseline_results = self.run_riescued(
            testname=self.testname,
            cli_args=base_cli_args,
            iterations=1,
            starting_seed=0,
        )
        baseline_result = baseline_results[0]

        baseline_dump_path = baseline_result.generated_files.selfcheck_dump
        self.assertIsNotNone(baseline_dump_path, "Baseline selfcheck dump was not generated")
        assert baseline_dump_path is not None  # Type narrowing for pyright
        self.assertTrue(baseline_dump_path.exists(), f"Baseline selfcheck dump not found: {baseline_dump_path}")

        baseline_data = self._parse_selfcheck_dump(baseline_dump_path)
        self.assertGreater(len(baseline_data), 0, "Baseline selfcheck dump is empty")

        # --- Part A: verify every resource type and test phase is included in the checksum ---
        # Override each of the 9 state macros with a different value and assert the dump changes.
        # This covers all three resource types (GPR, vector, FP) across all three test phases
        # (setup, test01, cleanup).
        macro_overrides = [
            # (macro_name, override_value, description)
            ("SETUP_GPR_VAL", "0x1122334455667788", "GPR in test_setup"),
            ("SETUP_VEC_VAL", "0x0102030405060708", "vector in test_setup"),
            ("SETUP_FP_VAL", "0x4010000000000000", "FP in test_setup"),
            ("TEST01_GPR_VAL", "0x1111222233334444", "GPR in test01"),
            ("TEST01_VEC_VAL", "0xdeadbeefcafebabe", "vector in test01"),
            ("TEST01_FP_VAL", "0x3fe0000000000000", "FP in test01"),
            ("CLEANUP_GPR_VAL", "0x5555666677778888", "GPR in test_cleanup"),
            ("CLEANUP_VEC_VAL", "0x0f0e0d0c0b0a0908", "vector in test_cleanup"),
            ("CLEANUP_FP_VAL", "0x4020000000000000", "FP in test_cleanup"),
        ]

        for seed_offset, (macro, value, description) in enumerate(macro_overrides, start=1):
            override_cli_args = base_cli_args + ["--test_equates", f"{macro}={value}"]
            override_results = self.run_riescued(
                testname=self.testname,
                cli_args=override_cli_args,
                iterations=1,
                starting_seed=seed_offset,
            )
            override_result = override_results[0]

            override_dump_path = override_result.generated_files.selfcheck_dump
            self.assertIsNotNone(override_dump_path, f"Override selfcheck dump not generated for {macro}")
            assert override_dump_path is not None  # Type narrowing for pyright
            self.assertTrue(override_dump_path.exists(), f"Override selfcheck dump not found: {override_dump_path}")

            override_data = self._parse_selfcheck_dump(override_dump_path)
            self.assertNotEqual(
                baseline_data,
                override_data,
                f"Selfcheck dump unchanged after overriding {macro} ({description}) - " f"checksum is not folding this value",
            )

        # --- Part B: verify every stored checksum block is actually checked ---
        # Read used_bytes from the dump header to determine how many 16-byte checksum blocks
        # were written, then corrupt the first byte of each block and verify the ISS detects it.
        elf_path = baseline_result.generated_files.elf
        self.assertTrue(elf_path.exists(), f"ELF file not found: {elf_path}")

        section_offset, _section_size = self._get_selfcheck_section_info(elf_path)

        # used_bytes is the first 8 bytes of the section (little-endian)
        used_bytes = struct.unpack_from("<Q", baseline_data, 0)[0]
        checksum_size = 16  # SELFCHECK_CHECKSUM_SIZE: 8 bytes sum1 + 8 bytes sum2
        num_checksum_blocks = used_bytes // checksum_size
        self.assertGreater(num_checksum_blocks, 0, "No checksum blocks found in selfcheck dump")

        iss = baseline_result.toolchain.whisper
        self.assertIsNotNone(iss, "Whisper simulator not available")
        assert iss is not None  # Type narrowing for pyright

        with open(elf_path, "rb") as f:
            original_elf_data = f.read()

        for block_idx in range(num_checksum_blocks):
            # First byte of this block's sum1 (header is 8 bytes, each block is 16 bytes)
            corrupt_abs_offset = section_offset + 8 + block_idx * checksum_size

            elf_data = bytearray(original_elf_data)
            elf_data[corrupt_abs_offset] ^= 1

            corrupted_elf = elf_path.parent / f"{elf_path.stem}_corrupted_block{block_idx}{elf_path.suffix}"
            with open(corrupted_elf, "wb") as f:
                f.write(elf_data)

            corrupted_log = corrupted_elf.parent / f"{corrupted_elf.name}.log"

            with self.assertRaises(
                ToolchainError,
                msg=f"Corrupting checksum block {block_idx} did not cause failure",
            ) as ctx:
                iss.run_iss(
                    output_file=corrupted_log,
                    elf_file=corrupted_elf,
                    cwd=elf_path.parent,
                    timeout=120,
                )

            self.assertEqual(
                ctx.exception.kind,
                ToolFailureType.TOHOST_FAIL,
                f"Expected TOHOST_FAIL for corrupted checksum block {block_idx} but got {ctx.exception.kind}",
            )

    def test_selfcheck(self):
        "Run test_selfcheck and ensure state changes affect the checksum and that corrupting a checksum causes the test to fail"
        self._run_selfcheck_test_selfcheck()

    def test_selfcheck_machine(self):
        "Run test_selfcheck in M mode and ensure state changes affect the checksum and that corrupting a checksum causes the test to fail"
        # Test pass/fail handling is a little special in M mode, so worth testing that selfcheck still works as expected
        self._run_selfcheck_test_selfcheck(["--test_priv=machine"])
