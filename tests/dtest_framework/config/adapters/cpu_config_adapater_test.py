# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import json
import tempfile
from pathlib import Path

import riescue.lib.enums as RV
from riescue.dtest_framework.config.builder import FeatMgrBuilder
from riescue.dtest_framework.config.adapaters import CpuConfigAdapter
from riescue.dtest_framework.config.memory import Memory, DramRange
from riescue.lib.rand import RandNum


class CpuConfigAdapterTest(unittest.TestCase):
    """Test the CpuConfigAdapter module."""

    def setUp(self):
        self.adapter = CpuConfigAdapter()
        self.rng = RandNum(seed=0)
        self.builder = FeatMgrBuilder(rng=self.rng)
        self.temp_dir = tempfile.mkdtemp()

    def test_apply_with_valid_config(self):
        """Test apply method with valid CPU configuration."""
        config_data = {"mmap": {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}}, "reset_pc": "0x8000_0000"}
        with tempfile.NamedTemporaryFile(mode="w") as f:
            json.dump(config_data, f)
            f.flush()
            result_builder = self.adapter.apply(self.builder, Path(f.name))
        self.assertIsInstance(result_builder, FeatMgrBuilder)
        feat_mgr = result_builder.featmgr
        self.assertIsInstance(feat_mgr.memory, Memory)
        self.assertEqual(len(feat_mgr.memory.dram_ranges), 1)
        dram_range = feat_mgr.memory.dram_ranges[0]
        self.assertIsInstance(dram_range, DramRange)
        self.assertEqual(dram_range.start, 0x80000000)
        self.assertEqual(dram_range.size, 0x10000000)
        self.assertEqual(feat_mgr.reset_pc, 0x80000000)

    def test_apply_with_empty_config(self):
        """Test apply method with empty configuration."""
        config_data = {}
        with tempfile.NamedTemporaryFile(mode="w") as f:
            json.dump(config_data, f)
            f.flush()
            result_builder = self.adapter.apply(self.builder, Path(f.name))

        # Should use default memory configuration
        feat_mgr = result_builder.featmgr
        self.assertIsInstance(feat_mgr.memory, Memory)
        self.assertEqual(len(feat_mgr.memory.dram_ranges), 1)
        default_dram = feat_mgr.memory.dram_ranges[0]
        self.assertEqual(default_dram.start, 0x80000000)
        self.assertEqual(default_dram.size, 2**56)
        self.assertEqual(feat_mgr.reset_pc, 0x80000000)

    def test_apply_with_missing_file(self):
        """Test apply method with non-existent file."""
        non_existent_path = Path(self.temp_dir) / "non_existent.json"

        with self.assertRaises(FileNotFoundError):
            self.adapter.apply(self.builder, non_existent_path)

    def test_apply_with_invalid_json(self):
        """Test apply method with invalid JSON content."""
        invalid_json_path = Path(self.temp_dir) / "invalid.json"
        with open(invalid_json_path, "w") as f:
            f.write("invalid json content")

        with self.assertRaises(json.JSONDecodeError):
            self.adapter.apply(self.builder, invalid_json_path)

    def test_apply_with_full_config(self):
        """Test apply method with full CPU configuration."""
        config_data = {
            "mmap": {
                "dram": {"dram0": {"address": "0x8000_0000", "size": "0x2000_0000"}},
                "io": {"io0": {"address": "0x0", "size": "0x1000_0000"}},
            },
            "reset_pc": "0x8000_0000",
            "features": {"zba": {"enabled": True}},
            "isa": ["c", "m"],
        }
        with tempfile.NamedTemporaryFile(mode="w") as f:
            json.dump(config_data, f)
            f.flush()
            result_builder = self.adapter.apply(self.builder, Path(f.name))

        self.assertIsInstance(result_builder, FeatMgrBuilder)
        feat_mgr = result_builder.featmgr
        self.assertIsInstance(feat_mgr.memory, Memory)

        # Check other config
        self.assertEqual(feat_mgr.reset_pc, 0x80000000)
        self.assertIsNotNone(feat_mgr.cpu_config)
