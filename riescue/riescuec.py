# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import argparse
from pathlib import Path
from argparse import Namespace
from typing import Optional
from enum import Enum

import riescue.lib.logger as RiescueLogger
from riescue.dtest_framework.config import FeatMgrBuilder
from riescue.compliance import BringupMode, TpMode
from riescue.lib.cmdline import CmdLine
from riescue.lib.toolchain import Compiler, Disassembler, Spike, Whisper
from riescue.lib.rand import initial_random_seed
from riescue.lib.cli_base import CliBase
from riescue.compliance.config import BringupCfg, TpCfg

log = logging.getLogger(__name__)


class ComplianceMode(Enum):
    BRINGUP = "bringup"
    COMPLIANCE = "compliance"
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
        parser.add_argument("--mode", type=ComplianceMode, help="Compliance mode", choices=ComplianceMode, default=ComplianceMode.BRINGUP)
        parser.add_argument("--run_dir", "-rd", type=Path, default=Path("."), help="Run directory where the test will be run")
        parser.add_argument("--seed", type=int, help="Seed for the test")
        BringupMode.add_arguments(parser)
        TpMode.add_arguments(parser)
        FeatMgrBuilder.add_arguments(parser)
        RiescueLogger.add_arguments(parser)
        Whisper.add_arguments(parser)
        Spike.add_arguments(parser)
        Compiler.add_arguments(parser)
        Disassembler.add_arguments(parser)

    @classmethod
    def run_cli(cls, args=None, **kwargs):
        "Initialize from command line arguments and runs correct run_<mode> method"

        # legacy print, not sure if needed:
        if args is not None:
            print(f"Args to process: {args}")

        # Switch to this later
        parser = argparse.ArgumentParser(prog="riescuec", description="RiESCUE-C compliance test generation", formatter_class=argparse.RawTextHelpFormatter)
        cls.add_arguments(parser)
        cl_args = parser.parse_args(args)
        mode = ComplianceMode(cl_args.mode)  # "choices" in add_arguments should guard against invalid modes
        riescue_c = cls()

        seed = cl_args.seed
        if seed is None:
            seed = initial_random_seed()
        run_dir = cl_args.run_dir

        if mode == ComplianceMode.BRINGUP:
            if cl_args.json is None:
                raise RuntimeError("--json is a required flag when running on the command line")
            logger_file = cl_args.run_dir / f"{cl_args.json.name}.testlog"
            RiescueLogger.from_clargs(args=cl_args, default_logger_file=logger_file)
            riescue_c.run_bringup(bringup_test_json=cl_args.json, seed=seed, run_dir=run_dir, args=cl_args)
        elif mode == ComplianceMode.COMPLIANCE:
            riescue_c.run_compliance(seed=seed, run_dir=run_dir)
        elif mode == ComplianceMode.TEST_PLAN:
            riescue_c.run_test_plan(args=cl_args, seed=seed, run_dir=run_dir)
        return riescue_c

    def run_bringup(
        self,
        bringup_test_json: Path,
        seed: Optional[int] = None,
        run_dir: Path = Path("."),
        args: Optional[Namespace] = None,
    ):
        """
        Run bringup mode, targeting individual instructions, extensions, and/or groups

        :param bringup_test_json: The JSON file containing the bringup test configuration. Required
        :param seed: The seed to use for the random number generator. Defaults to a random number in range 0 to 2^32
        :param run_dir: The directory to run the test in. Defaults to current directory
        """
        # TODO: make args optional argument in run_bringup. Only call from_clargs if args are provided
        # Priority of building up cfg is cfg defaults, then run_bringup arguments (bringup_test_json, user_config, etc), then argparse.Namespace args

        cfg = BringupCfg()
        cfg.with_bringup_test_json(bringup_test_json)
        if args is not None:
            cfg.with_args(args)
        else:
            raise RuntimeError("args is required when running on the command line for right now ")

        bringup_runner = BringupMode(
            seed=seed,
            run_dir=run_dir,
            cfg=cfg,
        )
        bringup_runner.run()

    def run_test_plan(
        self,
        seed: Optional[int] = None,
        run_dir: Path = Path("."),
        args: Optional[Namespace] = None,
    ):
        """
        Runs test plan mode. Using separate runner class to

        :param seed: The seed to use for the random number generator. Defaults to a random number in range 0 to 2^32q
        :param run_dir: The directory to run the test in. Defaults to current directory
        """

        tp_cfg = TpCfg()
        if args is not None:
            tp_cfg.with_args(args)

        test_plan_runner = TpMode(
            cfg=tp_cfg,
            seed=seed,
            run_dir=run_dir,
        )
        test_plan_runner.run()

    def run_compliance(self, seed: Optional[int] = None, run_dir: Path = Path(".")):
        """
        Run compliance mode, targeting individual instructions, extensions, and/or groups

        :param seed: The seed to use for the random number generator. Defaults to a random number in range 0 to 2^32q
        :param run_dir: The directory to run the test in. Defaults to current directory
        """
        raise NotImplementedError("Compliance mode is not implemented")
