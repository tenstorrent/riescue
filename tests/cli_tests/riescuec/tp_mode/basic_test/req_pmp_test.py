# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from typing import Any

from riescue.compliance.test_plan import TestPlanGenerator
from riescue.compliance.test_plan.actions import ActionRegistry
from riescue.lib.rand import RandNum

from coretp import TestEnvCfg, TestScenario, TestPlan
from coretp.rv_enums import PageSize, PmpAttribute
from coretp.step import Load, Memory, Arithmetic, Store, RequestPmpRegion, ConditionalBlock

from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase


class RequestPmpGeneratorTest(BasicTestBase):
    """
    Basic TestPlan test. Checks that memory operations generate successfully. Doesn't check if generated assembly is valid beyond checking for None

    """

    def setUp(self):
        super().setUp()

    def simple_request_pmp_steps(self) -> list[Any]:
        """Return simple load steps"""
        code = []
        request_pmp = RequestPmpRegion(page_size=PageSize.SIZE_4K, pmp_attributes=PmpAttribute.READ | PmpAttribute.WRITE)
        load = Load(memory=request_pmp, op="lb")
        code.append(request_pmp)
        code.append(load)

        conditional_block = ConditionalBlock(memory=request_pmp, code=code)
        steps = [request_pmp, conditional_block]

        return steps

    def test_simple_request_pmp_non_allocatable(self):
        "Basic test that checks request PMP region works"
        self.iterations = 1
        steps = self.simple_request_pmp_steps()
        mem_map = {"mmap": {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x8000_0000"}}}}
        # note: this test doesn't actually test the PMP region, it just checks that the request PMP region works
        self.run_test("test_basic_load", steps, "rv32if")


if __name__ == "__main__":
    unittest.main(verbosity=2)
