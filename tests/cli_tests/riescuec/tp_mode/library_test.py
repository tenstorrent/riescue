# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import tempfile
import argparse
from pathlib import Path

from riescue.compliance import TpMode
from riescue.riescuec import RiescueC
from riescue.lib.toolchain import Toolchain, Whisper, Spike


class LibraryTest(unittest.TestCase):
    """
    TpMode library tests - checks that library path for running TpMode works.

    This is needed for ctk to work correctly.
    """

    ELF_MAGIC = b"\x7fELF"

    def setUp(self):
        self.run_dir = Path()  # FIXME: use a dynamic directory similar to CLI tests.

    # helper methods
    def check_testfile(self, path: Path) -> None:
        """
        Check that a test file Path was returned, it exists, is non-empty, and is an ELF file.
        """
        self.assertIsInstance(path, Path, "path returned by TP should be a Path object")
        self.assertTrue(path.exists(), "file should have been written")
        self.assertTrue(path.stat().st_size > 0, "file should be non-empty")

        with path.open("rb") as f:
            magic = f.read(4)
            self.assertEqual(magic, self.ELF_MAGIC, f"invalid ELF file should have correct magic number/ Expected: {self.ELF_MAGIC}, got: {magic}")

    def parse_riesceuc_args(self, args: list[str]) -> argparse.Namespace:
        """
        Parse command line arguments for RiescueC.
        """
        parser = argparse.ArgumentParser()
        RiescueC.add_arguments(parser)
        return parser.parse_args(args)

    def default_toolchain(self) -> Toolchain:
        """
        Return a default toolchain with Whisper and Spike.
        """
        return Toolchain(whisper=Whisper(), spike=Spike())

    # test methods
    def test_configure(self):
        """
        Check that TpMode configure() works correctly.
        """

        runner = TpMode(run_dir=self.run_dir)
        cfg = runner.configure(seed=0)
        other_cfg = runner.configure(seed=0)

        self.assertNotEqual(cfg, other_cfg, "separate configure() instances should be unique")
        self.assertNotEqual(cfg.featmgr, other_cfg.featmgr, "separate configure() instances should have separate featmgrs")

    def test_no_test_plan_raises_error(self):
        """
        Generating a test plan from run() or generate() shoudl raise a ValueError if the test plan is not found.
        """

        runner = TpMode(run_dir=self.run_dir)
        cfg = runner.configure(seed=0)
        cfg.test_plan_name = "nonexistent_test_plan"
        with self.assertRaises(ValueError):
            runner.generate(cfg, self.default_toolchain())

        with self.assertRaises(ValueError):
            runner.run(seed=0, toolchain=self.default_toolchain())

    def test_no_isa_raises_error(self):
        """
        Generating a test plan from generate() shoudl raise a ValueError if the ISA is not found.
        """

        runner = TpMode(run_dir=self.run_dir)
        cfg = runner.configure(seed=0)
        cfg.test_plan_name = "zicond"
        with self.assertRaises(ValueError):
            runner.generate(cfg, self.default_toolchain())

        cfg.isa = "invalid_isa"
        with self.assertRaises(ValueError):
            runner.generate(cfg, self.default_toolchain())

    # methods that generate tests and run ISS
    def test_configure_and_generate(self):
        """
        Check that TpMode configure() and generate() work correctly together.
        """
        runner = TpMode(run_dir=self.run_dir)

        cfg = runner.configure(seed=0)
        cfg.test_plan_name = "zicond"
        cfg.isa = "rv64i_zicond"

        testfile = runner.generate(cfg, self.default_toolchain())
        self.check_testfile(testfile)

    def test_run(self):
        """
        Check that TpMode run() works correctly.
        """
        runner = TpMode(run_dir=self.run_dir)
        cl_args = self.parse_riesceuc_args(["--isa", "rv64i_zicond", "--test_plan", "zicond"])
        testfile = runner.run(seed=0, cl_args=cl_args, toolchain=self.default_toolchain())
        self.check_testfile(testfile)


if __name__ == "__main__":
    unittest.main(verbosity=2)
