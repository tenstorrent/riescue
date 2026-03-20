# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from typing import Any

from riescue.compliance.test_plan import TestPlanGenerator
from riescue.compliance.test_plan.actions import ActionRegistry
from riescue.lib.rand import RandNum

from coretp import TestEnvCfg, TestScenario, TestPlan
from coretp.rv_enums import PageSize, Extension
from coretp.step import Load, Memory, Arithmetic, Store, ConditionalBlock

from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase


class ConditionalBlockTest(BasicTestBase):
    """
    Checks that conditional blocks work correctly.
    """

    def simple_conditional_load(self) -> list[Any]:
        """Return simple load steps"""
        code = []
        mem = Memory(page_size=PageSize.SIZE_4K)
        load = Load(memory=mem, op="lb")
        code.append(mem)
        code.append(load)

        conditional_block = ConditionalBlock(enabled_features=[Extension.F], code=code)
        steps = [conditional_block]
        return steps

    def test_basic_load_no_f_extension(self):
        "Basic test that checks memory + load work"
        self.iterations = 1
        steps = self.simple_conditional_load()
        self.run_test("test_basic_load", steps, "rv32i")
        test_file = self.test_dir / "assertion_test.s"

        test_basic_load = []
        with open(test_file, "r") as f:
            for line in f:
                if "test_basic_load:" in line:
                    for line in f:
                        if line.strip():
                            test_basic_load.append(line.strip())
                        else:
                            break

        self.assertFalse([instr for instr in test_basic_load if "lb" in instr], "Expected lb instruction to not be present")

    def test_basic_load_with_f_extension(self):
        "Basic test that checks memory + load work"

        self.iterations = 1
        steps = self.simple_conditional_load()
        self.run_test("test_basic_load", steps, "rv32if")
        test_file = self.test_dir / "assertion_test.s"

        test_basic_load = []
        with open(test_file, "r") as f:
            for line in f:
                if "test_basic_load:" in line:
                    for line in f:
                        if line.strip():
                            test_basic_load.append(line.strip())
                        else:
                            break

        self.assertTrue([instr for instr in test_basic_load if "lb" in instr], f"Expected lb instruction to be present in {test_file}")

    def test_no_features_enabled(self):
        "Leaving blank features should raise an exception"

        code = []
        mem = Memory(page_size=PageSize.SIZE_4K)
        load = Load(memory=mem, op="lb")

        conditional_block = ConditionalBlock(code=[mem, load])
        steps = [conditional_block]

        with self.assertRaises(ValueError):
            self.run_test("test_no_features_enabled", steps, "rv32i")


if __name__ == "__main__":
    unittest.main(verbosity=2)
