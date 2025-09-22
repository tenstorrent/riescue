# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
from pathlib import Path

from coretp.plans.test_plan_registry import get_plan, query_plans  # type: ignore # FIXME: remove ignore whenc coretp is properly installed

from .base import BaseMode
from riescue.compliance.test_plan.generator import TestPlanGenerator
from riescue.riescued import RiescueD
from riescue.compliance.config import TpCfg


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

        self.generator = TestPlanGenerator(self.isa, self.rng)

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--isa", type=str, default="rv64ifda_zicsr", help="ISA to use")

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

        test_plan = get_plan("paging")
        if test_plan is None:
            raise ValueError("Test plan 'paging' not found")
        test = self.generator.generate_test_plan(test_plan)

        test_file = Path("tp_test.s")
        with open(test_file, "w") as f:
            f.write(test)
        return test_file
