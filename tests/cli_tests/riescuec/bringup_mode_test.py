# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import tempfile
import json
from pathlib import Path

from riescue import RiescueC
from riescue.compliance import BringupMode
from riescue.compliance.config import ResourceBuilder
from riescue.lib.toolchain import Toolchain


class BringupModeTest(unittest.TestCase):
    """
    RiescueC bringup mode tests.
    Checks that RiescueC.run_bringup works correctly since this is part of the public interface.
    """

    def setUp(self):
        self.rc = RiescueC()
        self.temp_files: list[Path] = []

    def tearDown(self):
        for temp_file in self.temp_files:
            if temp_file.exists():
                temp_file.unlink()

    def make_temp_bringup_test_json(self, content: dict) -> Path:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(content, f)
            temp_file = Path(f.name)
            self.temp_files.append(temp_file)
            return temp_file

    def test_basic_generate(self):
        """
        Check that BringupMode works with default Resource with just `add` instruction.
        This is needed for library code to work. Want to check that abitrary Resource passed in works
        """

        bringup_test_json = self.make_temp_bringup_test_json(
            {
                "arch": "rv64",
                "include_extensions": [],
                "include_groups": [],
                "exclude_groups": [],
                "include_instrs": ["add"],
                "exclude_instrs": [],
            }
        )

        testfile = self.rc.run_bringup(bringup_test_json=bringup_test_json, seed=0)
        self.assertIsInstance(testfile, Path, "Should have returned a Path")
        self.assertTrue(testfile.exists(), "Should have written a testfile")
