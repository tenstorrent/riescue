# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, TypeVar, Generic, Any

from riescue.lib.toolchain import Toolchain
from riescue.dtest_framework.config import Conf

Cfg = TypeVar("Cfg")  # Generic configuration class that all extended classes will pass between configure and generate


class BaseMode(ABC, Generic[Cfg]):
    """
    Generic runner pro used by RiESCUE-C to generate and run test cases. Extended classes should implement the run method, depending on the mode.

    :param run_dir: The directory to run the test in. Defaults to current directory
    """

    def __init__(self, run_dir: Path, conf: Optional[Conf] = None) -> None:
        self.run_dir = run_dir
        if not self.run_dir.exists():
            self.run_dir.mkdir(parents=True, exist_ok=True)

        self.package_path = Path(__file__).parents[1]
        self.conf = conf

    @staticmethod
    @abstractmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        """
        Add arguments to the parser.
        """

    @abstractmethod
    def configure(
        self,
        seed: int,
        cl_args: Optional[argparse.Namespace] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Cfg:
        """
        Generate a configuration for the runner. Used to optionally generate a configuration that gets passed to :func:`generate`

        :param seed: Seed for the random number generator.
        :param cl_args: Optional command line arguments
        :param args: Any extra arguments runner needs to configure. E.g. data structure
        :return: Configuration for the runner's `generate` method
        """
        pass

    @abstractmethod
    def generate(self, cfg: Cfg, toolchain: Toolchain) -> Path:
        """
        Generate a RiescueD assembly Test File. Returns the path where the test case is written.

        :param cfg: Configuration for the runner's `generate` method
        :param toolchain: Toolchain to use for the test
        :return: Path to the generated ELF test file
        """
        pass

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Path:
        """
        Run this Mode. This should invoke :func:`generate`, :func:`build`, and :func:`simulate`

        :return: The path to the generated ELF test file
        """
        pass
