# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
from pathlib import Path

try:
    from coretp.plans.test_plan_registry import get_plan, query_plans
    from coretp.rv_enums import PagingMode, PrivilegeMode
except ModuleNotFoundError:
    raise ImportError("coretp not installed. Run pip install git+https://github.com/tenstorrent/riscv-coretp.git")

from .base import BaseMode
from riescue.compliance.test_plan.generator import TestPlanGenerator
from riescue.riescued import RiescueD
from riescue.compliance.config import TpCfg
import riescue.lib.enums as RV


class TpMode(BaseMode):
    """
    Runs RiescueC Test Plan generation flow.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not isinstance(self.cfg, TpCfg):
            raise RuntimeError(f"cfg must be an instance of TpCfg, not {type(self.cfg)}")
        cfg = self.cfg.build(self.rng, self.run_dir)
        self.isa = cfg.isa
        self.featmgr = cfg.featmgr
        self.toolchain = cfg.toolchain

        try:
            self.test_plan = get_plan(cfg.test_plan_name)
        except ValueError as e:
            raise ValueError(f"Test plan '{cfg.test_plan_name}' not found. Avalailable test plans: {query_plans()}") from e

        self.generator = TestPlanGenerator(self.isa, self.rng)

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--isa", type=str, default="rv64imfda_zicsr_zk_zicond", help="ISA to use")
        parser.add_argument("--test_plan", dest="test_plan_name", type=str, default="zicond", help="Test plan to use")

    def run(self) -> None:
        """ """

        test = self.generate()

        rd = RiescueD(testfile=test, seed=self.seed, toolchain=self.toolchain)
        generator = rd.generate(featmgr=self.featmgr)
        rd.build(featmgr=self.featmgr, generator=generator)

        whisper = rd.toolchain.whisper
        if whisper is None:
            raise ValueError("No whisper configured in toolchain")

        rd.simulate(featmgr=self.featmgr, iss=whisper)

    def generate(self) -> Path:
        """
        Generate a test from test plan. WIP, will need to be conditional at some point. For now hardcoding it
        """

        discrete_tests = self.generator.build(self.test_plan)
        env = self.generator.solve(discrete_tests)

        if env.priv == PrivilegeMode.M:
            self.featmgr.priv_mode = RV.RiscvPrivileges.MACHINE
        elif env.priv == PrivilegeMode.S:
            self.featmgr.priv_mode = RV.RiscvPrivileges.SUPER
        elif env.priv == PrivilegeMode.U:
            self.featmgr.priv_mode = RV.RiscvPrivileges.USER
        else:
            raise ValueError(f"Invalid privilege mode: {env.priv}")

        if env.paging_mode == PagingMode.SV39:
            self.featmgr.paging_mode = RV.RiscvPagingModes.SV39
        elif env.paging_mode == PagingMode.SV48:
            self.featmgr.paging_mode = RV.RiscvPagingModes.SV48
        elif env.paging_mode == PagingMode.SV57:
            self.featmgr.paging_mode = RV.RiscvPagingModes.SV57
        elif env.paging_mode == PagingMode.DISABLED:
            self.featmgr.paging_mode = RV.RiscvPagingModes.DISABLE
        else:
            raise ValueError(f"Invalid paging mode: {env.paging_mode}")

        test = self.generator.generate(discrete_tests, env, self.test_plan.name)

        test_file = self.run_dir / "tp_test.s"
        if not self.run_dir.exists():
            self.run_dir.mkdir()
        with open(test_file, "w") as f:
            f.write(test)
        return test_file
