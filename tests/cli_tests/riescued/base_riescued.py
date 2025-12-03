# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import os
import logging
import re
from pathlib import Path
from typing import Any, Generator

from riescue.riescued import RiescueD
from riescue.lib.toolchain.exceptions import ToolchainError, ToolFailureType


class NoOpHandler(logging.Handler):
    def emit(self, record):
        pass  # Do nothing


class BaseRiescuedTest(unittest.TestCase):
    """
    Base class for running RiescueD tests.

    Provides methods for running test with `iteration` number of seeds. Aim for at least 10 seconds of tests.

    Setup methods create test directory in $TEST_DIR or test_out/class_name/test_name. Doesn't remove results after test

    $ITERATION_COUNT can be used to set the total number of seeds to run per test case. Defaults to `BaseRiescuedTest.default_iterations` if unset

    Usage:
    .. code-block:: python

        from .base_riescued import BaseRiescuedTest

        class TestRiescued(BaseRiescuedTest):
            def setUp(self):
                super().setUp()

            def test_riescued(self):
                cli_args = ["--testname", "dtest_framework/tests/test.s", "--run_iss", "--cpuconfig", "dtest_framework/tests/cpu_config.json", "--seed", "0"]
                self.run_riescued()

    """

    default_iterations = 5  # ; Default number of seeds to run per test

    def setUp(self):
        """
        Sets up the test directory. If $TEST_DIR is set, use it, otherwise use test_out/class_name/test_name.
        """
        test_dir = os.getenv("TEST_DIR")
        if test_dir is None:
            test_dir = Path("test_out") / self.__class__.__name__
        else:
            # Handle
            if "$" in test_dir:
                test_dir_env = test_dir.replace("$", "")
                if test_dir_env not in os.environ:
                    raise ValueError(f"TEST_DIR is set to {test_dir}, but {test_dir_env} is not set in the environment")
                test_dir = Path(os.environ[test_dir_env])
            test_dir = Path(test_dir)
        self.test_dir = test_dir / self._testMethodName
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.iterations = self.get_iterations()

        self.equates_regex = re.compile(r".equ ([a-zA-Z_0-9]*) *, ([0-9xa-f]+)")
        self.phys_addr_ld_regex = re.compile(r"\. = ([0-9xa-f]*);")

    def get_iterations(self) -> int:
        """
        Returns the number of iterations to run the test.
        """
        if os.getenv("ITERATION_COUNT") is not None:
            return int(os.environ["ITERATION_COUNT"])
        else:
            return self.default_iterations

    def run_riescued_cli(self, command: list[str], **kwargs) -> RiescueD:
        "Run RiescueD with arguments and return the result. Override this to change how RiescueD is run"
        return RiescueD.run_cli(args=command)

    def run_riescued(self, testname: str, cli_args: list, iterations: int = 5, starting_seed: int = 0) -> list[RiescueD]:
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

        results: list[RiescueD] = []
        for i in self.run_riescued_generator(testname, cli_args, iterations, starting_seed=starting_seed):
            results.append(i)
        return results

    def run_riescued_generator(self, testname: str, cli_args: list, iterations: int = 5, starting_seed: int = 0) -> Generator[RiescueD, Any, Any]:
        """
        Generator for `run_riescued`. Consume results if using otherwise tests will not run. I.e. run `for i in self.run_riescued_generator(...)` not `self.run_riescued(...)`
        """
        for i in range(iterations):
            with self.subTest(seed=i):
                seed = str(i + starting_seed)
                test_dir = self.test_dir / f"seed_{seed}"
                command = ["--testname", testname] + cli_args + ["--seed", seed] + ["--run_dir", str(test_dir)]
                msg = f"test \n\t./riescued.py {' '.join(str(c) for c in command)}"
                print("Running " + msg)
                result = self.run_riescued_cli(command)
                yield result

    def expect_toolchain_failure(self, testname: str, cli_args: list, failure_kind: ToolFailureType, iterations: int = 5) -> list[ToolchainError]:
        """
        Runs test that's expected to fail with arguments for iteration number of times; checks for expected failure type.
        Adds seeds implicilty to the command line arguments.
        Collects the exceptions of each test for additional checking.

        :param testname: Name of the test to run
        :type testname: str
        :param cli_args: List of arguments to pass to the test
        :type cli_args: list
        :param iterations: Number of times to run the test
        :type iterations: int
        :param failure_kind: Expected enumerated failure type
        :type failure_kind: ToolFailureType

        Usage:
        .. code-block:: python
            self.testname = "dtest_framework/tests/test.s"
            args = ["--run_iss"]
            failures = self.expect_toolchain_failure(testname=self.testname, cli_args=args, failure_kind=ToolFailureType.ELF_FAILURE)
        """
        results = []
        for i in self.expect_toolchain_failure_generator(testname, cli_args, failure_kind, iterations):
            results.append(i)
        return results

    def expect_toolchain_failure_generator(self, testname: str, cli_args: list, failure_kind: ToolFailureType, iterations: int = 5, starting_seed: int = 0) -> Generator[ToolchainError, Any, Any]:
        for i in range(iterations):
            with self.subTest(seed=i):
                seed = str(i + starting_seed)
                command = ["--testname", testname, "--run_dir", str(self.test_dir)] + cli_args + ["--seed", seed]
                msg = f"test \n\t./riescued.py {' '.join(str(c) for c in command)}\nExpecting failure"
                print("Running " + msg)
                with self.assertRaises(ToolchainError, msg=msg) as runtime_error:
                    self.run_riescued_cli(command)
                self.assertEqual(
                    runtime_error.exception.kind,
                    failure_kind,
                    f"Expected failure kind {failure_kind} but got {runtime_error.exception.kind}. Error text: \n{runtime_error.exception.error_text}",
                )
                yield runtime_error.exception

    def enable_logging(self, level: int = logging.DEBUG):
        "Enables debbug logging. Should really only be called when running single tests and not checked in"
        logging.basicConfig(level=level)

    def disable_logging(self):
        "Should disable logging since riescue.lib.logger checks for existing"
        riescue_logger = logging.getLogger("riescue")
        riescue_logger.addHandler(NoOpHandler())

    # helper and analysis methods
    def get_all_equates(self, result: RiescueD) -> dict[str, int]:
        "Get all equates from the result's _equates.inc file and return dictionary of equates to numbers. Only supports decimal and hexadecimal equates."
        # get equates file, this falls apart of _equates.inc isn't present.
        test_elf = result.generated_files.elf
        equates_file = test_elf.with_name(f"{test_elf.stem}_equates.inc")
        self.assertTrue(equates_file.exists(), "Equates file not found")

        # read file, regex match all and put into dict
        with open(equates_file, "r") as f:
            equates: dict[str, int] = {}
            equates_content = f.read()
            for match in self.equates_regex.findall(equates_content):
                equate = match[0]
                value = int(match[1], 0)
                equates[equate] = value
        return equates

    def get_all_physical_addresses(self, result: RiescueD) -> list[int]:
        "Get all physical addresses from the result's linker script. All plain addresses are physical addresses."

        linker_script = result.generated_files.linker_script
        self.assertTrue(linker_script.exists(), f"Linker script not found at {linker_script}")
        physical_addresses: list[int] = []
        with open(linker_script, "r") as f:
            for match in self.equates_regex.findall(f.read()):
                addr = int(match[0], 0)
                physical_addresses.append(addr)
        return physical_addresses
