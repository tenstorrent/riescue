# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue.compliance import BringupMode
from riescue.compliance.config import ResourceBuilder
from riescue.lib.toolchain import Toolchain

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class BaseRiescueCTest(BaseRiescuedTest):
    """
    Base class for running RiescueC tests.

    Preovides additional methods for running tests
    """

    ELF_MAGIC = b"\x7fELF"

    # helper methods
    def check_valid_elf(self, path: Path) -> None:
        """
        Check that a test file Path was returned, it exists, is non-empty, and is an ELF file.
        """
        self.assertEqual(path.suffix, "", f"Expected final ELF file to have no suffix, but got {path.suffix}")
        self.assertIsInstance(path, Path, "path returned by TP should be a Path object")
        self.assertTrue(path.exists(), "file should have been written")
        self.assertTrue(path.stat().st_size > 0, "file should be non-empty")

        with path.open("rb") as f:
            magic = f.read(4)
            self.assertEqual(magic, self.ELF_MAGIC, f"invalid ELF file should have correct magic number/ Expected: {self.ELF_MAGIC}, got: {magic}")
