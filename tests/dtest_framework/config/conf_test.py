# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue.dtest_framework.config import Conf


class ConfTest(unittest.TestCase):
    """Test suite for :class:`riescue.dtest_framework.config.Conf`."""

    def test_load_conf_from_path(self):
        """Test that Conf can be loaded from a path."""
        conf = Conf.load_conf_from_path(Path(__file__).parent / "data/example_conf.py")
        self.assertIsInstance(conf, Conf)

    def test_load_conf_from_path_missing_setup(self):
        """
        Test that a RuntimeError is raised if the configuration file does not contain a setup() method.
        """
        with self.assertRaises(RuntimeError):
            Conf.load_conf_from_path(Path(__file__).parent / "data/conf_missing_setup.py")
