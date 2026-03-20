# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import List
import re
import struct
import subprocess
from pathlib import Path

from riescue.lib.toolchain.exceptions import ToolchainError, ToolFailureType

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class TestExecLogTests(BaseRiescuedTest):
    """
    Tests for test_execution_logger functionality
    """

    def test_basic_log(self):
        cli_args = ["--run_iss", "--log_test_execution", "--whisper_dumpmem", "tel.hex:@test_execution_data:@test_execution_data+0x1000"]
        results = self.run_riescued(testname="dtest_framework/tests/test.s", cli_args=cli_args, iterations=1)
        testname = Path("dtest_framework/tests/test.s").stem  # Extract 'test' from the test.s path
        expected_inc_file = Path(results[0].run_dir) / f"{testname}_test_execution_logger.inc"
        assert expected_inc_file.exists(), f"Expected execution logger include file not found: {expected_inc_file}"
        expected_hex_file = Path(results[0].run_dir) / "tel.hex"
        assert expected_hex_file.exists(), f"Expected execution logger hex file not found: {expected_hex_file}"

        # Read the tel.hex file expected at expected_hex_file
        with open(expected_hex_file, "r") as f:
            # Find the first line that starts with '@' (address)
            for line in f:
                if line.startswith("@"):
                    break  # line consumed; next lines are hex content

            # Gather all hex bytes as a flat list
            hex_bytes = []
            for line in f:
                line = line.strip()
                if not line:
                    continue
                hex_bytes.extend(line.split())

        # Convert hex byte strings to actual bytes
        hex_bytes = [int(b, 16) for b in hex_bytes]
        hex_data = bytes(hex_bytes)

        # Each dword is 8 bytes (little-endian, u64)
        def get_dword(data, idx):
            offset = idx * 8
            return struct.unpack("<Q", data[offset : offset + 8])[0]

        test_count = get_dword(hex_data, 0)
        assert test_count != 0, "Test count is zero in hex log (should have at least one executed test)"
        print("Test count:", test_count)

        # we cannot do any other checks on the data as it can vary.
        # Cannot do a non-zero check as it is possible that test_ptr is at offset 0 after paging
