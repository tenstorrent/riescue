# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import os
from pathlib import Path
from typing import Any, Generator

from riescue.riescued import RiescueD
from riescue.lib.toolchain.exceptions import ToolchainError, ToolFailureType


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
                cli_args = ["--testname", "dtest_framework/tests/test.s", "--run_iss", "--cpuconfig", "riescue/dtest_framework/tests/cpu_config.json", "--seed", "0"]
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

    def get_iterations(self) -> int:
        """
        Returns the number of iterations to run the test.
        """
        if os.getenv("ITERATION_COUNT") is not None:
            return int(os.environ["ITERATION_COUNT"])
        else:
            return self.default_iterations

    def run_riescued(self, testname: str, cli_args: list, iterations: int = 5) -> list[Any]:
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

        results = []
        for i in self.run_riescued_generator(testname, cli_args, iterations):
            results.append(i)
        return results

    def run_riescued_generator(self, testname: str, cli_args: list, iterations: int = 5) -> Generator[Any, Any, Any]:
        """
        Generator for `run_riescued`. Consume results if using otherwise tests will not run. I.e. run `for i in self.run_riescued_generator(...)` not `self.run_riescued(...)`
        """
        for i in range(iterations):
            with self.subTest(seed=i):
                seed = str(i)
                command = ["--testname", testname, "--run_dir", str(self.test_dir)] + cli_args + ["--seed", seed]
                msg = f"test \n\t./riescued.py {' '.join(str(c) for c in command)}"
                print("Running " + msg)
                result = RiescueD.run_cli(args=command)
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

    def expect_toolchain_failure_generator(self, testname: str, cli_args: list, failure_kind: ToolFailureType, iterations: int = 5) -> Generator[ToolchainError, Any, Any]:
        for i in range(iterations):
            with self.subTest(seed=i):
                seed = str(i)
                command = ["--testname", testname, "--run_dir", str(self.test_dir)] + cli_args + ["--seed", seed]
                msg = f"test \n\t./riescued.py {' '.join(str(c) for c in command)}\nExpecting failure"
                print("Running " + msg)
                with self.assertRaises(ToolchainError, msg=msg) as runtime_error:
                    RiescueD.run_cli(args=command)
                self.assertEqual(runtime_error.exception.kind, failure_kind)
                yield runtime_error.exception
