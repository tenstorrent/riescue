# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import argparse
from pathlib import Path
from argparse import Namespace
from typing import Optional
from enum import Enum

import riescue.lib.logger as RiescueLogger
from riescue.dtest_framework.config import FeatMgrBuilder, Conf
from riescue.compliance import BringupMode, TpMode
from riescue.compliance.config import experimental_toolchain_from_args
from riescue.lib.toolchain import Compiler, Disassembler, Spike, Whisper, Toolchain
from riescue.lib.rand import initial_random_seed
from riescue.lib.cli_base import CliBase

log = logging.getLogger(__name__)


class ComplianceMode(Enum):
    BRINGUP = "bringup"
    TEST_PLAN = "tp"


class RiescueC(CliBase):
    """
    Top level entry for RiESCUE-C dtest framework. Instantiate an instance of this module
    to start a run for RiESCUE-C.
    """

    def __init__(self):
        """
        Ideally this should take an instance of commandline, which would have all the
        information needed to run a RiESCUE-C simulation

        Logging gets setup in Resource class (needs run dir for log file). Might need to change this or move Resource initialization sooner.

        :param mode: ComplianceMode
        """
        self.package_path = Path(__file__).parent

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser):
        # argparse doesn't allow a default subparser without modifying sys.argv
        # instead not using subparsers and using a single parser with all arguments
        parser.add_argument(
            "--mode",
            type=ComplianceMode,
            help="Compliance mode",
            choices=ComplianceMode,
            default=ComplianceMode.BRINGUP,
        )
        parser.add_argument(
            "--run_dir",
            "-rd",
            type=Path,
            default=Path("."),
            help="Run directory where the test will be run",
        )
        parser.add_argument(
            "--conf",
            type=Path,
            default=None,
            help="Path to conf.py file for additional config and hooks.",
        )
        parser.add_argument("--seed", type=int, help="Seed for the test")
        BringupMode.add_arguments(parser)
        TpMode.add_arguments(parser)
        FeatMgrBuilder.add_arguments(parser)
        RiescueLogger.add_arguments(parser)
        Toolchain.add_arguments(parser)

    @classmethod
    def run_cli(cls, args=None, **kwargs) -> Path:
        "Initialize from command line arguments and runs correct run_<mode> method"

        # legacy print, not sure if needed:
        if args is not None:
            print(f"Args to process: {args}")

        # Switch to this later
        parser = argparse.ArgumentParser(
            prog="riescuec",
            description="RiESCUE-C compliance test generation",
            formatter_class=argparse.RawTextHelpFormatter,
        )
        cls.add_arguments(parser)
        cl_args = parser.parse_args(args)
        mode = ComplianceMode(cl_args.mode)  # "choices" in add_arguments should guard against invalid modes
        riescue_c = cls()

        seed = cl_args.seed
        if seed is None:
            seed = initial_random_seed()
        run_dir = cl_args.run_dir

        if cl_args.conf is not None:
            conf = Conf.load_conf_from_path(cl_args.conf)
        else:
            conf = None

        toolchain = experimental_toolchain_from_args(cl_args)

        if mode == ComplianceMode.BRINGUP:
            if cl_args.json is None:
                raise RuntimeError("--json is a required flag when running on the command line")
            logger_file = cl_args.run_dir / f"{cl_args.json.name}.testlog"
            RiescueLogger.from_clargs(args=cl_args, default_logger_file=logger_file)
            return riescue_c.run_bringup(
                bringup_test_json=cl_args.json,
                seed=seed,
                run_dir=run_dir,
                args=cl_args,
                toolchain=toolchain,
                conf=conf,
            )
        elif mode == ComplianceMode.TEST_PLAN:
            logger_file = Path("riescuec_tp.testlog")
            RiescueLogger.from_clargs(args=cl_args, default_logger_file=logger_file)
            return riescue_c.run_test_plan(
                args=cl_args,
                seed=seed,
                run_dir=run_dir,
                toolchain=toolchain,
                conf=conf,
            )
        raise ValueError(f"Invalid mode: {mode}")

    def run_bringup(
        self,
        bringup_test_json: Path,
        seed: int,
        run_dir: Path = Path("."),
        args: Optional[Namespace] = None,
        toolchain: Optional[Toolchain] = None,
        conf: Optional[Conf] = None,
    ) -> Path:
        """
        Run bringup mode, targeting individual instructions, extensions, and/or groups

        :param bringup_test_json: The JSON file containing the bringup test configuration. Required
        :param seed: The seed to use for the random number generator. Defaults to a random number in range 0 to 2^32
        :param toolchain: Configured ``Toolchain`` object. If none provided, a default ``Toolchain`` object will be used (assumes whisper and spike are available in environment)
        :param run_dir: The directory to run the test in. Defaults to current directory
        """

        bringup_mode = BringupMode(run_dir=run_dir, conf=conf)
        if toolchain is None:
            toolchain = Toolchain(whisper=Whisper(), spike=Spike())
        return bringup_mode.run(bringup_test_json=bringup_test_json, seed=seed, cl_args=args, toolchain=toolchain)

    def run_test_plan(
        self,
        seed: int,
        run_dir: Path = Path("."),
        args: Optional[Namespace] = None,
        toolchain: Optional[Toolchain] = None,
        conf: Optional[Conf] = None,
    ) -> Path:
        """
        Runs test plan mode

        :param seed: The seed to use for the random number generator. Defaults to a random number in range 0 to 2^32
        :param toolchain: Configured ``Toolchain`` object. If none provided, a default ``Toolchain`` object will be used (assumes whisper is available in environment)
        :param run_dir: The directory to run the test in. Defaults to current directory
        """
        tp_mode = TpMode(run_dir=run_dir, conf=conf)
        if toolchain is None:
            toolchain = Toolchain(whisper=Whisper(), spike=Spike())
        return tp_mode.run(seed=seed, cl_args=args, toolchain=toolchain)


def main():
    RiescueC.run_cli()


if __name__ == "__main__":
    main()
