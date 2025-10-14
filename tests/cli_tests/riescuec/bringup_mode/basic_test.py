# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import json
from pathlib import Path

from riescue.compliance import BringupMode
from riescue.compliance.config import ResourceBuilder
from riescue.lib.toolchain import Toolchain, Whisper, Spike
from tests.cli_tests.riescuec.base import BaseRiescueCTest


class BasicTest(BaseRiescueCTest):
    """
    BringupMode basic tests - checks that library path for running BringupMode works.

    This is needed for ctk to work correctly.
    """

    def default_toolchain(self) -> Toolchain:
        """
        create a toolchain with Whisper and Spike. Necessary for running tests.
        """
        return Toolchain(whisper=Whisper(), spike=Spike())

    def test_multiple_runs_have_unique_rand_nums(self):
        """
        Check that multiple runs of BringupMode have unique RandNum instances.
        """
        resource = ResourceBuilder().build(seed=0, run_dir=self.test_dir)
        toolchain = self.default_toolchain()
        resource.include_instrs = ["add"]
        runner = BringupMode(self.test_dir)
        runner.generate(resource, toolchain=toolchain)
        rng_1 = resource.rng
        runner.generate(resource, toolchain=toolchain)
        rng_2 = resource.rng
        self.assertNotEqual(rng_1, rng_2, "Multiple runs of BringupMode should have unique RandNum instances")

    def test_build_and_generate(self):
        """
        Check that BringupMode can be run with a resource built from scratch.
        This is needed for library code to work. Want to check that abitrary Resource passed in works
        """

        resource = ResourceBuilder().build(seed=0, run_dir=self.test_dir)
        resource.testcase_name = "add_test"
        resource.include_instrs = ["add"]
        runner = BringupMode(self.test_dir)
        final_elf = runner.generate(resource, toolchain=self.default_toolchain())

        # checking output file is correct
        self.check_valid_elf(final_elf)

        # checking output files get generated correctly
        # want to make sure assembly files are where they should be
        all_output_files = set(x.name for x in self.test_dir.iterdir())
        self.assertIn("add_test", all_output_files, "Expected add_test to be generated")
        self.assertIn("add_test.s", all_output_files, "Expected add_test.s to be generated")

    def test_configure_and_generate(self):
        """
        Check that BringupMode can be run with a resource configured from scratch.
        """
        runner = BringupMode(self.test_dir)
        bringup_json = self.test_dir / "bringup.json"
        bringup_test = {
            "arch": "rv64",
            "include_extensions": ["m_ext", "f_ext"],
            "include_groups": [],
            "include_instrs": [],
            "exclude_groups": [],
            "exclude_instrs": ["fence"],
        }
        with open(bringup_json, "w") as f:
            json.dump(bringup_test, f)
            bringup_json_path = Path(f.name)

        resource = runner.configure(seed=0, bringup_test_json=bringup_json_path)
        self.assertIn("m_ext", resource.include_extensions)
        resource.include_instrs = ["add"]
        test = runner.generate(resource, toolchain=self.default_toolchain())
        self.check_valid_elf(test)

    def test_run(self):
        """
        Check that BringupMode can be run with a resource built from scratch using run()
        """
        runner = BringupMode(self.test_dir)
        bringup_json = self.test_dir / "bringup.json"
        bringup_test = {
            "arch": "rv64",
            "include_extensions": ["m_ext", "f_ext"],
            "include_groups": [],
            "include_instrs": [],
            "exclude_groups": [],
            "exclude_instrs": ["fence"],
        }
        with open(bringup_json, "w") as f:
            json.dump(bringup_test, f)
            bringup_json_path = Path(f.name)
        test_elf = runner.run(bringup_test_json=bringup_json_path, seed=0, toolchain=self.default_toolchain())
        self.check_valid_elf(test_elf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
