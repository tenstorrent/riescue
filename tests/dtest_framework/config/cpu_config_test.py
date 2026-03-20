# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import tempfile
import json
from pathlib import Path

from riescue.dtest_framework.config import CpuConfig, Memory
from riescue.dtest_framework.config.memory import IoRange, DramRange
from riescue.dtest_framework.config.cpu_config import TestGeneration
import riescue.lib.enums as RV


class TestTestGeneration(unittest.TestCase):
    """Test suite for :class:`riescue.dtest_framework.config.cpu_config.TestGeneration`."""

    def test_default_values(self):
        """TestGeneration builds without arguments."""
        TestGeneration()

    def test_custom_values(self):
        """TestGeneration accepts custom values."""
        tg = TestGeneration(
            secure_access_probability=50,
            secure_pt_probability=20,
            a_d_bit_randomization=10,
            pbmt_ncio_randomization=5,
            fs_randomization=40,
            fs_randomization_values=[1, 2],
            vs_randomization=60,
            vs_randomization_values=[2],
        )
        self.assertEqual(tg.secure_access_probability, 50)
        self.assertEqual(tg.secure_pt_probability, 20)
        self.assertEqual(tg.a_d_bit_randomization, 10)
        self.assertEqual(tg.pbmt_ncio_randomization, 5)
        self.assertEqual(tg.fs_randomization, 40)
        self.assertEqual(tg.fs_randomization_values, [1, 2])
        self.assertEqual(tg.vs_randomization, 60)
        self.assertEqual(tg.vs_randomization_values, [2])

    def test_from_dict(self):
        """TestGeneration.from_dict handles custom values."""
        tg = TestGeneration.from_dict(
            {
                "secure_access_probability": 50,
                "secure_pt_probability": 20,
                "a_d_bit_randomization": 10,
                "pbmt_ncio_randomization": 5,
                "fs_randomization": 40,
                "fs_randomization_values": [1, 2],
                "vs_randomization": 60,
                "vs_randomization_values": [2],
            }
        )
        self.assertEqual(tg.secure_access_probability, 50)
        self.assertEqual(tg.secure_pt_probability, 20)
        self.assertEqual(tg.a_d_bit_randomization, 10)
        self.assertEqual(tg.pbmt_ncio_randomization, 5)
        self.assertEqual(tg.fs_randomization, 40)
        self.assertEqual(tg.fs_randomization_values, [1, 2])
        self.assertEqual(tg.vs_randomization, 60)
        self.assertEqual(tg.vs_randomization_values, [2])

    def test_from_dict_empty(self):
        """TestGeneration.from_dict handles empty dict."""
        TestGeneration.from_dict({})

    def test_from_dict_missing_fields(self):
        """TestGeneration.from_dict handles missing fields."""
        tg = TestGeneration.from_dict({"secure_access_probability": 50})
        self.assertEqual(tg.secure_access_probability, 50)

    def test_from_dict_ignore_underscore_fields(self):
        """
        TestGeneration.from_dict ignores fields starting with underscore.
        """
        tg = TestGeneration.from_dict({"secure_access_probability": 50, "_comment": "test"})
        self.assertEqual(tg.secure_access_probability, 50)

    def test_from_dict_invalid_fields(self):
        """TestGeneration.from_dict raises ValueError for invalid fields."""
        with self.assertRaises(ValueError):
            TestGeneration.from_dict({"invalid_field": 50})


class TestCpuConfig(unittest.TestCase):
    """Test suite for :class:`riescue.dtest_framework.config.CpuConfig`."""

    def test_default_construction(self):
        """CpuConfig default construction sets expected values."""
        cfg = CpuConfig()
        self.assertEqual(cfg.reset_pc, 0x8000_0000)
        self.assertEqual(cfg.isa, [])
        self.assertIsInstance(cfg.memory, Memory)
        self.assertIsInstance(cfg.test_gen, TestGeneration)

    def test_from_dict_empty(self):
        """CpuConfig.from_dict handles empty dict."""
        cfg = CpuConfig.from_dict({})
        self.assertEqual(cfg.reset_pc, 0x8000_0000)
        self.assertEqual(cfg.isa, [])

    def test_from_dict_full(self):
        """CpuConfig.from_dict handles full configuration."""
        config = {
            "mmap": {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}},
            "features": {"zba": {"enabled": True}},
            "isa": ["c", "m"],
            "mp": "on",
            "mp_mode": "simultaneous",
            "reset_pc": "0x1000",
            "test_generation": {"secure_access_probability": 50, "secure_pt_probability": 20},
        }
        cfg = CpuConfig.from_dict(config)
        self.assertEqual(cfg.reset_pc, 0x1000)
        self.assertEqual(cfg.isa, ["c", "m"])
        self.assertEqual(cfg.test_gen.secure_access_probability, 50)
        self.assertEqual(cfg.test_gen.secure_pt_probability, 20)

    def test_from_json(self):
        """CpuConfig.from_json loads configuration from JSON file."""
        config = {
            "mmap": {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}},
            "features": {"zba": {"enabled": True}},
            "isa": ["c"],
            "mp": "on",
            "mp_mode": "simultaneous",
            "reset_pc": "0x1000",
        }
        with tempfile.NamedTemporaryFile(mode="w") as f:
            json.dump(config, f)
            f.flush()
            cfg = CpuConfig.from_json(Path(f.name))
            self.assertEqual(cfg.reset_pc, 0x1000)
            self.assertEqual(cfg.isa, ["c"])

    def test_reset_pc_formats(self):
        """CpuConfig handles different reset_pc formats."""
        test_cases = [("0x1000", 0x1000), ("0x8000_0000", 0x80000000), (0x2000, 0x2000)]
        for input_val, expected in test_cases:
            cfg = CpuConfig.from_dict({"reset_pc": input_val})
            self.assertEqual(cfg.reset_pc, expected)

    def test_feature_overrides(self):
        """CpuConfig handles feature overrides."""
        config = {"features": {"zba": {"enabled": False}}}
        overrides = "ext_v.enable ext_f.disable"
        cfg = CpuConfig.from_dict(config, overrides)
        self.assertTrue(cfg.features.is_feature_enabled("v"))
        self.assertFalse(cfg.features.is_feature_enabled("f"))

    def test_pma_config_loading(self):
        """CpuConfig loads PMA config from mmap.pma."""
        config = {
            "mmap": {
                "dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}},
                "pma": {
                    "max_regions": 15,
                    "regions": [{"name": "region1", "base": "0x80000000", "size": "0x1000000", "attributes": {"memory_type": "memory", "cacheability": "cacheable"}}],
                    "hints": [{"name": "hint1", "combinations": [{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}]}],
                },
            }
        }
        cfg = CpuConfig.from_dict(config)
        self.assertIsNotNone(cfg.pma_config)
        assert cfg.pma_config is not None  # Type narrowing for pyright
        self.assertEqual(cfg.pma_config.max_regions, 15)
        self.assertEqual(len(cfg.pma_config.regions), 1)
        self.assertEqual(len(cfg.pma_config.hints), 1)
        self.assertEqual(cfg.pma_config.regions[0].name, "region1")
        self.assertEqual(cfg.pma_config.hints[0].name, "hint1")

    def test_pma_config_optional(self):
        """CpuConfig handles missing PMA config gracefully."""
        config = {"mmap": {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}}}
        cfg = CpuConfig.from_dict(config)
        self.assertIsNone(cfg.pma_config)

    def test_pma_config_invalid_handling(self):
        """CpuConfig handles invalid PMA config with warning."""
        config = {"mmap": {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}, "pma": {"regions": "invalid"}}}  # Should be a list
        # Should not raise, but log warning
        cfg = CpuConfig.from_dict(config)
        self.assertIsNone(cfg.pma_config)  # Invalid config results in None

    def test_pma_config_with_adjacent_regions(self):
        """CpuConfig loads PMA config with adjacent regions."""
        config = {
            "mmap": {
                "dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}},
                "pma": {
                    "regions": [
                        {"name": "region1", "base": "0x80000000", "size": "0x1000000", "attributes": {"memory_type": "memory"}},
                        {"name": "region2", "adjacent_to": "region1", "attributes": {"memory_type": "memory"}},
                    ]
                },
            }
        }
        cfg = CpuConfig.from_dict(config)
        self.assertIsNotNone(cfg.pma_config)
        assert cfg.pma_config is not None  # Type narrowing for pyright
        self.assertEqual(len(cfg.pma_config.regions), 2)
        self.assertEqual(cfg.pma_config.regions[1].adjacent_to, "region1")

    def test_pma_config_hints_with_adjacent(self):
        """CpuConfig loads PMA hints with adjacent flag."""
        config = {
            "mmap": {
                "dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}},
                "pma": {"hints": [{"name": "hint1", "combinations": [{"memory_type": "memory", "cacheability": "cacheable"}], "adjacent": True, "min_regions": 2, "max_regions": 5}]},
            }
        }
        cfg = CpuConfig.from_dict(config)
        self.assertIsNotNone(cfg.pma_config)
        assert cfg.pma_config is not None  # Type narrowing for pyright
        hint = cfg.pma_config.hints[0]
        self.assertTrue(hint.adjacent)
        self.assertEqual(hint.min_regions, 2)
        self.assertEqual(hint.max_regions, 5)
