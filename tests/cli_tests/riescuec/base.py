# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from typing import Any, Optional, Generator

from riescue.riescuec import RiescueC
from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class BaseRiescueCTest(BaseRiescuedTest):
    """
    Base class for running RiescueC tests.

    Preovides additional methods for running tests
    """

    ELF_MAGIC = b"\x7fELF"

    def run_tp_mode(self, plan: str, cli_args: Optional[list] = None, **kwargs: Any) -> list[RiescueC]:
        """
        Just run RiescueC in TP mode
        """
        results = []
        if cli_args is None:
            cli_args = []
        args = cli_args + ["--test_plan", plan]
        for i in self.run_riescue_generator(args, mode="tp", **kwargs):
            results.append(i)
        return results

    def run_riescue(self, cli_args: list, mode: str = "bringpup", iterations: int = 5, starting_seed: int = 0) -> list[RiescueC]:
        """
        Runs test with arguments for iteration number of times. Uses seed=iteration.
        Optionally collects the results of each test for additional checking.
        """
        results = []
        for i in self.run_riescue_generator(cli_args, mode, iterations, starting_seed):
            results.append(i)
        return results

    def run_riescue_generator(
        self,
        cli_args: list,
        mode: str = "bringpup",
        iterations: int = 5,
        starting_seed: int = 0,
    ) -> Generator[RiescueC, Any, None]:
        """
        Runs test with arguments for iteration number of times. Uses seed=iteration.
        Optionally collects the results of each test for additional checking.

        :param testname: Name of the test to run
        :type testname: str
        :param cli_args: List of arguments to pass to the test
        :type cli_args: list
        :param iterations: Number of times to run the test
        :type iterations: int
        """

        for i in range(iterations):
            with self.subTest(seed=i):
                seed = str(i + starting_seed)
                test_dir = self.test_dir / f"seed_{seed}"
                command = cli_args + ["--mode", mode, "--seed", seed] + ["--run_dir", str(test_dir)]
                msg = f"test \n\t./riescuec.py {' '.join(str(c) for c in command)}"
                print("Running " + msg)
                result = RiescueC.run_cli(args=command)
                yield result

    # helper methods
    def check_valid_elf(self, path: Path) -> None:
        """
        Check that a test file Path was returned, it exists, is non-empty, and is an ELF file.
        """
        self.assertEqual(path.suffix, "", f"Expected final ELF file to have no suffix, but got {path.suffix}")
        self.assertIsInstance(path, Path, "path returned by TP should be a Path object")
        self.assertTrue(path.exists(), "file should have been written")
        self.assertTrue(path.stat().st_size > 0, "file should be non-empty")

        with path.open("rb") as f:
            magic = f.read(4)
            self.assertEqual(magic, self.ELF_MAGIC, f"invalid ELF file should have correct magic number/ Expected: {self.ELF_MAGIC}, got: {magic}")
