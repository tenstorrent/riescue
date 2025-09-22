# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from riescue.lib.rand import RandNum
from riescue.compliance.config.mode_cfg import ModeCfg


class BaseMode(ABC):
    """
    Runner used by RiESCUE-C to generate and run test cases. Subclasses should implement the run method, depending on the mode.

    Currently just a build method, but these classes should contain any logic specific to the mode.

    :param seed: Fully validated configuration produced by the orchestrator
    :param run_dir: The directory to run the test in. Defaults to current directory
    """

    def __init__(self, seed: int, run_dir: Path, cfg: ModeCfg) -> None:
        self.cfg = cfg
        self.seed = seed
        self.rng = RandNum(seed)
        self.run_dir = run_dir

        self.package_path = Path(__file__).parents[1]

    @staticmethod
    @abstractmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        """
        Add arguments to the parser.
        """

    @abstractmethod
    def run(self) -> None:
        """
        Run the runner. Should generate test case.

        FIXME: Later this should also run the test case, for now just return the generated test case
        """

    def cleanup(self) -> None:
        """
        Remove temporary files and finalise any reporting. Implement to add custom cleanup logic
        """
        pass
