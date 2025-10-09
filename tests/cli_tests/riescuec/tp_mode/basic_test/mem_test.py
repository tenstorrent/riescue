# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from typing import Any

from riescue.compliance.test_plan import TestPlanGenerator
from riescue.compliance.test_plan.actions import ActionRegistry
from riescue.lib.rand import RandNum

from coretp import TestEnvCfg, TestScenario, TestPlan
from coretp.rv_enums import PageSize
from coretp.step import Load, Memory, Arithmetic, Store

from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase


class MemoryGeneratorTest(BasicTestBase):
    """
    Basic TestPlan test. Checks that memory operations generate successfully. Doesn't check if generated assembly is valid beyond checking for None

    Currently tests imports, TestScenario types, etc. Eventually will want to move coretp tests somewhere else.
    """

    def simple_load_steps(self) -> list[Any]:
        """Return simple load steps"""
        steps = []
        mem = Memory(page_size=PageSize.SIZE_4K)
        load = Load(memory=mem)
        steps.append(mem)
        steps.append(load)
        return steps

    def test_basic_load(self):
        "Basic test that checks memory + load work"
        steps = self.simple_load_steps()
        self.run_test("test_basic_load", steps, "rv32i")

    def simple_store_steps(self) -> list[Any]:
        """Return simple store steps"""
        steps = []
        mem = Memory(page_size=PageSize.SIZE_4K)
        store = Store(memory=mem)
        steps = [mem, store]
        return steps

    def test_basic_store(self):
        "Basic test that checks memory + store work"
        steps = self.simple_store_steps()
        self.run_test("test_basic_store", steps, "rv32i")

    def simple_memory_arithmetic_steps(self) -> list[Any]:
        """Return simple memory arithmetic steps"""
        steps = []
        mem = Memory(page_size=PageSize.SIZE_4K)
        load = Load(memory=mem)
        op = Arithmetic(src1=load)
        steps = [mem, load, op]
        return steps

    def test_basic_memory_with_arithmetic(self):
        steps = self.simple_memory_arithmetic_steps()
        text = self.run_test("test_basic_memory_with_arithmetic", steps, "rv32i")

    def simple_load_store_chain_steps(self) -> list[Any]:
        """Return simple load store chain steps"""
        steps = []
        mem = Memory(page_size=PageSize.SIZE_4K)
        load = Load(memory=mem)
        op = Arithmetic(src1=load)
        store = Store(memory=mem, value=op)
        steps = [mem, load, op, store]
        return steps

    def test_basic_load_store_chain(self):
        """
        Test load , operate on value, store value
        """
        steps = self.simple_load_store_chain_steps()
        self.run_test("test_basic_load_store_chain", steps, "rv32i")

    def simple_load_store_chain_rv64ima_steps(self) -> list[Any]:
        """Return simple load store chain steps"""
        steps = []
        mem = Memory(page_size=PageSize.SIZE_4K)
        load = Load(memory=mem)
        op = Arithmetic(src1=load)
        store = Store(memory=mem, value=op)
        steps = [mem, load, op, store]
        return steps

    def test_basic_load_store_chain_rv64ima(self):
        """
        Test load , operate on value, store value
        """
        steps = self.simple_load_store_chain_rv64ima_steps()
        self.run_test("test_basic_load_store_chain_rv64ima", steps, "rv64imafd")


if __name__ == "__main__":
    unittest.main(verbosity=2)
