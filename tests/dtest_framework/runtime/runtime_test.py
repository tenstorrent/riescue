# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path
from typing import Generator

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime import Runtime
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.pool import Pool
from riescue.lib.rand import RandNum


class RuntimeTest(unittest.TestCase):
    """
    Test the Runtime module.

    Just checking that generated text works correctly and is formatted well.
    """

    def setUp(self):
        """
        Set up the test environment.
        """
        self.rng = RandNum(seed=42)
        self.pool = Pool()
        self.featmgr = FeatMgr()

    def test_runtime(self):
        """
        Test the Runtime module.

        This shouldn't generate any code, just yield the contents of runtime

        """

        runtime = Runtime(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        for module_name, code_generator in runtime.generate():
            self.assertIsInstance(module_name, str)
            self.assertIsInstance(code_generator, Generator, "Runtime.generate() should return a generator to stream lines")
            self.assertIsInstance("\n".join(code_generator), str)
