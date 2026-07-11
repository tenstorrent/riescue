# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue.dtest_framework.config import Conf, FeatMgr
import riescue.lib.enums as RV


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

    def test_split_conf_paths_single(self):
        """A single path passes through unchanged (backwards compatible)."""
        self.assertEqual(Conf.split_conf_paths([Path("x.py")]), [Path("x.py")])

    def test_split_conf_paths_comma_separated_preserves_order(self):
        """A comma-separated value is split into an ordered list of paths."""
        self.assertEqual(
            Conf.split_conf_paths([Path("a.py,b.py")]),
            [Path("a.py"), Path("b.py")],
        )

    def test_split_conf_paths_repeated_and_comma_mixed(self):
        """Repeated flags (list) combined with comma-separated values flatten in order."""
        self.assertEqual(
            Conf.split_conf_paths([Path("a.py,b.py"), Path("c.py")]),
            [Path("a.py"), Path("b.py"), Path("c.py")],
        )

    def test_split_conf_paths_strips_whitespace_and_drops_blanks(self):
        """Whitespace around entries is stripped and blank entries are dropped."""
        self.assertEqual(
            Conf.split_conf_paths(["a.py, b.py", "", "  ", "c.py,"]),
            [Path("a.py"), Path("b.py"), Path("c.py")],
        )

    def test_split_conf_paths_empty(self):
        """An empty list stays empty."""
        self.assertEqual(Conf.split_conf_paths([]), [])


class MultiConfHookOrderTest(unittest.TestCase):
    """
    Verify that hooks from multiple conf files are injected in the order the files are
    listed, mirroring the CLI usage ``--conf hook_conf_a.py,hook_conf_b.py``.
    """

    _DATA = Path(__file__).parent / "data"

    def _load_confs(self, names: list[str]) -> list[Conf]:
        # Exercise the real CLI path: comma-joined string -> split_conf_paths -> load_conf_from_path
        arg = ",".join(str(self._DATA / name) for name in names)
        return [Conf.load_conf_from_path(p) for p in Conf.split_conf_paths([arg])]

    def test_hook_order_a_then_b(self):
        featmgr = FeatMgr()
        for conf in self._load_confs(["hook_conf_a.py", "hook_conf_b.py"]):
            conf.add_hooks(featmgr)
        self.assertEqual(featmgr.call_hook(RV.HookPoint.PRE_LOADER), "HOOK_A\nHOOK_B")

    def test_hook_order_b_then_a(self):
        featmgr = FeatMgr()
        for conf in self._load_confs(["hook_conf_b.py", "hook_conf_a.py"]):
            conf.add_hooks(featmgr)
        self.assertEqual(featmgr.call_hook(RV.HookPoint.PRE_LOADER), "HOOK_B\nHOOK_A")
