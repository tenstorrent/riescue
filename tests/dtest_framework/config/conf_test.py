# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

from riescue.dtest_framework.config import Conf, FeatMgr
import riescue.lib.enums as RV
from tests.dtest_framework.config.data.extension_enablement_conf import DISABLE_SNIPPET, ENABLE_SNIPPET


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

    def test_get_extension_enablement_default_empty(self):
        """Base Conf returns an empty mapping so consumers can iterate unconditionally."""
        self.assertEqual(Conf().get_extension_enablement(), {})

    def test_get_extension_enablement_subclass_mapping(self):
        """A subclass can return an extension-name to enable/disable snippet mapping."""

        class ZacasConf(Conf):
            def get_extension_enablement(self) -> dict[str, dict[str, str]]:
                return {
                    "zacas": {
                        "enable": "ENABLE_ZACAS",
                        "disable": "DISABLE_ZACAS",
                    }
                }

        self.assertEqual(
            ZacasConf().get_extension_enablement(),
            {"zacas": {"enable": "ENABLE_ZACAS", "disable": "DISABLE_ZACAS"}},
        )

    def test_normalize_extension_enablement_lowercases_and_strips_ext_prefix(self):
        """Names are lowercased and an optional ext_ prefix is stripped."""
        raw = {
            "ext_Zacas": {"enable": "ENABLE_ZACAS", "disable": "DISABLE_ZACAS"},
            "ZVBB": ("ENABLE_ZVBB", "DISABLE_ZVBB"),
        }
        self.assertEqual(
            Conf.normalize_extension_enablement(raw),
            {
                "zacas": {"enable": "ENABLE_ZACAS", "disable": "DISABLE_ZACAS"},
                "zvbb": {"enable": "ENABLE_ZVBB", "disable": "DISABLE_ZVBB"},
            },
        )

    def test_normalize_extension_enablement_none_and_empty(self):
        """None and an empty mapping both normalize to an empty mapping."""
        self.assertEqual(Conf.normalize_extension_enablement(None), {})
        self.assertEqual(Conf.normalize_extension_enablement({}), {})

    def test_normalize_extension_enablement_identical_aliases_ok(self):
        """Aliases that normalize to the same name are accepted when snippets match."""
        raw = {
            "zacas": {"enable": "EN", "disable": "DIS"},
            "ext_Zacas": {"enable": "EN", "disable": "DIS"},
        }
        self.assertEqual(
            Conf.normalize_extension_enablement(raw),
            {"zacas": {"enable": "EN", "disable": "DIS"}},
        )

    def test_normalize_extension_enablement_rejects_conflicting_aliases(self):
        """Aliases that normalize to the same name with different snippets are errors."""
        raw = {
            "zacas": {"enable": "EN1", "disable": "DIS"},
            "ext_zacas": {"enable": "EN2", "disable": "DIS"},
        }
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement(raw)

    def test_normalize_extension_enablement_requires_non_empty_snippets(self):
        """Enable and disable snippets must be non-empty strings."""
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"zacas": {"enable": "  ", "disable": "DIS"}})
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"zacas": {"enable": "EN", "disable": ""}})

    def test_normalize_extension_enablement_detects_malformed_entries(self):
        """Non-mappings, missing keys, non-string snippets, and empty names are errors."""
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement(["zacas"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({1: {"enable": "EN", "disable": "DIS"}})
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"": {"enable": "EN", "disable": "DIS"}})
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"ext_": {"enable": "EN", "disable": "DIS"}})
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"zacas": {"enable": "EN"}})
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"zacas": "ENABLE_ZACAS"})
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"zacas": ("EN",)})
        with self.assertRaises(ValueError):
            Conf.normalize_extension_enablement({"zacas": {"enable": 1, "disable": "DIS"}})

    def test_load_conf_from_path_extension_enablement(self):
        """A loaded Conf file can supply extension enablement consumed after normalization."""
        conf = Conf.load_conf_from_path(Path(__file__).parent / "data/extension_enablement_conf.py")
        self.assertEqual(
            Conf.normalize_extension_enablement(conf.get_extension_enablement()),
            {"zacas": {"enable": ENABLE_SNIPPET, "disable": DISABLE_SNIPPET}},
        )


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
