# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
# pyright: reportMissingTypeStubs=false

import argparse
from pathlib import Path
from typing import Optional, Any

try:
    from coretp.plans.test_plan_registry import get_plan
    from coretp.rv_enums import PagingMode, PrivilegeMode
except ModuleNotFoundError:
    raise ImportError("coretp not installed. Run pip install git+https://github.com/tenstorrent/riscv-coretp.git")

from .base import BaseMode
from riescue.compliance.test_plan.generator import TestPlanGenerator
from riescue.riescued import RiescueD
from riescue.compliance.config import TpBuilder, TpCfg
from riescue.lib.rand import RandNum
from riescue.lib.toolchain import Toolchain
import riescue.lib.enums as RV


class TpMode(BaseMode[TpCfg]):
    """
    Runs RiescueC Test Plan generation flow.
    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        # generator = TestPlanGenerator(self.isa, self.rng)

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--isa", type=str, default="rv64imfda_zicsr_zk_zicond", help="ISA to use")
        parser.add_argument("--test_plan", dest="test_plan_name", type=str, default="zicond", help="Test plan to use")

    def run(self, seed: int, toolchain: Toolchain, cl_args: Optional[argparse.Namespace] = None) -> Path:
        """
        Top level wrapper to generate and simulate a test
        """
        cfg = self.configure(seed=seed, cl_args=cl_args)
        return self.generate(cfg, toolchain)

    def configure(
        self,
        seed: int,
        cl_args: Optional[argparse.Namespace] = None,
    ) -> TpCfg:
        """
        Configure the :class:`TpCfg` object for use in :py:meth:`generate`

        :param seed: The seed to use for the random number generator.
        :param cl_args: Optional command line arguments
        :return: :class:`TpCfg` object
        """

        # This method limits customization of the TpCfg object to only CLI arguments
        # Only allows for user to return configuration and modify it after returning.

        builder = TpBuilder()
        if cl_args is not None:
            builder.with_args(cl_args)
        return builder.build(seed)

    def generate(self, cfg: TpCfg, toolchain: Toolchain) -> Path:
        """
        Generate a test from test plan. Compiles and runs the test on ISS.

        :param cfg: :class:`TpCfg` object
        :return: Path to the generated ELF test file
        """

        # get test plan
        if not cfg.test_plan_name:
            raise ValueError("No test plan was provided. ")
        try:
            test_plan = get_plan(cfg.test_plan_name)
        except ValueError as e:
            raise ValueError(f"Test plan '{cfg.test_plan_name}' not found") from e

        rng = RandNum(cfg.seed)
        generator = TestPlanGenerator(cfg.isa, rng)
        discrete_tests = generator.build(test_plan)
        env = generator.solve(discrete_tests)

        cfg.featmgr.priv_mode = self._cast_privilege_mode(env.priv)
        cfg.featmgr.paging_mode = self._cast_paging_mode(env.paging_mode)
        test = generator.generate(discrete_tests, env, cfg.test_plan_name)

        # write test file
        test_assembly_file = self.run_dir / f"tp_{cfg.test_plan_name}_{cfg.seed}.s"
        with open(test_assembly_file, "w") as f:
            f.write(test)

        # run riescued to generate ELF file, reuse featmg, toolchain
        rd = RiescueD(testfile=test_assembly_file, seed=cfg.seed, toolchain=toolchain, run_dir=self.run_dir)
        rd.generate(cfg.featmgr)
        generated_files = rd.build(cfg.featmgr)
        if rd.toolchain.simulator is None:
            raise ValueError("No simulator configured in toolchain")
        whisper = rd.toolchain.whisper
        if whisper is None:
            raise ValueError("No whisper configured in toolchain. Ensure Whisper was built in toolchain")
        rd.simulate(cfg.featmgr, iss=whisper)

        return generated_files.elf

    def _cast_privilege_mode(self, priv: PrivilegeMode) -> RV.RiscvPrivileges:
        "Helper to convert coretp.rv_enums.PrivilegeMode to riescue.lib.enums.RiscvPrivileges"
        if priv == PrivilegeMode.M:
            return RV.RiscvPrivileges.MACHINE
        elif priv == PrivilegeMode.S:
            return RV.RiscvPrivileges.SUPER
        elif priv == PrivilegeMode.U:
            return RV.RiscvPrivileges.USER
        else:
            raise ValueError(f"Invalid privilege mode: {priv}")

    def _cast_paging_mode(self, paging_mode: PagingMode) -> RV.RiscvPagingModes:
        "Helper to convert coretp.rv_enums.PagingMode to riescue.lib.enums.RiscvPagingModes"
        if paging_mode == PagingMode.SV39:
            return RV.RiscvPagingModes.SV39
        elif paging_mode == PagingMode.SV48:
            return RV.RiscvPagingModes.SV48
        elif paging_mode == PagingMode.SV57:
            return RV.RiscvPagingModes.SV57
        elif paging_mode == PagingMode.DISABLED:
            return RV.RiscvPagingModes.DISABLE
        else:
            raise ValueError(f"Invalid paging mode: {paging_mode}")
