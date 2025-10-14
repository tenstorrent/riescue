# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest
from pathlib import Path
from riescue.ctk import Ctk
from riescue.lib.toolchain import Toolchain, Whisper, Spike


class BasicTest(unittest.TestCase):
    """
    Basic test for Ctk. Checks that simple directory works
    """

    def default_toolchain(self) -> Toolchain:
        """
        create a toolchain with Whisper and Spike. Necessary for running tests.
        """
        return Toolchain(whisper=Whisper(), spike=Spike())

    def rm_rf(self, path: Path):
        for p in path.iterdir():
            if p.is_dir():
                self.rm_rf(p)
                p.rmdir()
            else:
                p.unlink()

    def test_basic(self):
        """
        Test that Ctk can be initialized and run.
        """
        seed = 0
        run_dir = Path("test_out/basic_ctk_test")
        if run_dir.exists():
            self.rm_rf(run_dir)

        Ctk(seed, run_dir, toolchain=self.default_toolchain())
        self.assertTrue(run_dir.exists(), "Run directory should get created after construction")

    def test_basic_generate(self):
        """
        Test that Ctk can be initialized and run.
        """
        seed = 0
        run_dir = Path("test_out/basic_ctk_test_run")

        if run_dir.exists():
            self.rm_rf(run_dir)

        ctk = Ctk(seed, run_dir, toolchain=self.default_toolchain())
        cfg = ctk.configure()
        cfg.test_count = 5
        cfg.isa = "rv64i"
        cfg.flat_directory_structure = True
        test_kit = ctk.generate(cfg)

        # expect 5 binary files in the test kit, flat
        self.assertEqual(
            len(list(test_kit.iterdir())),
            5,
            f"Expected 5 binary files in the test kit,{test_kit.resolve()}",
        )
        for x in test_kit.iterdir():
            self.assertTrue(x.is_file(), f"Output file {x} is not a file")
            self.assertTrue(x.name.endswith(""), f"Output file {x} is not an elf file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
