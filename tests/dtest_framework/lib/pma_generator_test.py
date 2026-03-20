# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import MagicMock

from riescue.dtest_framework.lib.pma_generator import PmaGenerator
from riescue.dtest_framework.lib.pma import PmaInfo
from riescue.dtest_framework.parser import ParsedPmaHint
from riescue.dtest_framework.config.pma_config import PmaConfig, PmaAttributes, PmaRegionConfig, PmaHintConfig
from riescue.dtest_framework.config.memory import Memory, DramRange, IoRange
from riescue.lib.rand import RandNum


class PmaGeneratorTest(unittest.TestCase):
    """Test PmaGenerator class"""

    def setUp(self):
        """Set up test fixtures"""
        self.rng = RandNum(seed=42)
        self.memory = Memory(dram_ranges=[DramRange(start=0x80000000, size=0x10000000)], io_ranges=[IoRange(start=0x10000000, size=0x1000000)])

    def test_generate_from_simple_hint(self):
        """Test generating regions from simple hint"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="test_hint", memory_types=["memory"], cacheability=["cacheable"], rwx_combos=["rwx"])

        regions = generator.generate_from_hint(hint)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].pma_memory_type, "memory")
        self.assertEqual(regions[0].pma_cacheability, "cacheable")
        self.assertTrue(regions[0].pma_read)
        self.assertTrue(regions[0].pma_write)
        self.assertTrue(regions[0].pma_execute)

    def test_generate_from_hint_with_combinations(self):
        """Test generating from hint with specific combinations"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(
            name="test_hint", combinations=[{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}, {"memory_type": "memory", "cacheability": "noncacheable", "rwx": "rwx"}]
        )

        regions = generator.generate_from_hint(hint)
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0].pma_cacheability, "cacheable")
        self.assertEqual(regions[1].pma_cacheability, "noncacheable")

    def test_expand_combinations(self):
        """Test combination expansion"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="test", memory_types=["memory"], cacheability=["cacheable", "noncacheable"], rwx_combos=["rwx", "rw"])

        combinations = generator._expand_combinations(hint)
        # Should generate 2 (cacheability) × 2 (rwx) = 4 combinations
        self.assertEqual(len(combinations), 4)

    def test_expand_combinations_io(self):
        """Test combination expansion for IO"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="test", memory_types=["io"], combining=["combining", "noncombining"], rwx_combos=["rw"])

        combinations = generator._expand_combinations(hint)
        # Should generate 2 (combining) × 1 (rwx) = 2 combinations
        self.assertEqual(len(combinations), 2)
        self.assertEqual(combinations[0]["memory_type"], "io")
        self.assertIn("combining", combinations[0])

    def test_adjacent_regions(self):
        """Test adjacent region placement"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(
            name="test", combinations=[{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}, {"memory_type": "memory", "cacheability": "noncacheable", "rwx": "rwx"}], adjacent=True
        )

        regions = generator.generate_from_hint(hint)
        self.assertEqual(len(regions), 2)

        # Check that second region is adjacent to first
        expected_base = (regions[0].pma_address + regions[0].pma_size + 0xFFF) & ~0xFFF
        self.assertEqual(regions[1].pma_address, expected_base)

    def test_max_regions_limit(self):
        """Test that max_regions limit is respected"""
        generator = PmaGenerator(None, self.memory, self.rng)
        generator.max_regions = 3  # Set low limit

        hint = ParsedPmaHint(name="test", memory_types=["memory"], cacheability=["cacheable", "noncacheable"], rwx_combos=["rwx", "rw", "rx", "r"])  # 2 × 4 = 8 combinations

        regions = generator.generate_from_hint(hint)
        # Should be limited to 3
        self.assertEqual(len(regions), 3)

    def test_min_max_regions(self):
        """Test min/max regions constraints"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="test", memory_types=["memory"], cacheability=["cacheable", "noncacheable", "cacheable"], rwx_combos=["rwx"], min_regions=2, max_regions=2)  # 3 combinations

        regions = generator.generate_from_hint(hint)
        # Should be limited to max_regions=2
        self.assertEqual(len(regions), 2)

    def test_generate_from_config(self):
        """Test generating from config regions"""
        region_cfg = PmaRegionConfig(name="config_region", base=0x90000000, size=0x2000000, attributes=PmaAttributes(memory_type="memory", cacheability="cacheable"))

        pma_config = PmaConfig(regions=[region_cfg])
        generator = PmaGenerator(pma_config, self.memory, self.rng)

        regions = generator.generate_all([])
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].pma_name, "config_region")
        self.assertEqual(regions[0].pma_address, 0x90000000)
        self.assertEqual(regions[0].pma_size, 0x2000000)

    def test_generate_all_with_hints_and_config(self):
        """Test generating from both hints and config"""
        region_cfg = PmaRegionConfig(name="config_region", base=0x90000000, size=0x1000000, attributes=PmaAttributes(memory_type="memory"))

        pma_config = PmaConfig(regions=[region_cfg])
        generator = PmaGenerator(pma_config, self.memory, self.rng)

        hint = ParsedPmaHint(name="hint1", combinations=[{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}])

        regions = generator.generate_all([hint])
        # Should have 1 from config + 1 from hint = 2
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0].pma_name, "config_region")
        self.assertEqual(regions[1].pma_name, "pma_hint1_0")

    def test_adjacent_to_config(self):
        """Test adjacent_to in config"""
        region1 = PmaRegionConfig(name="region1", base=0x90000000, size=0x1000000, attributes=PmaAttributes(memory_type="memory"))

        region2 = PmaRegionConfig(name="region2", adjacent_to="region1", size=0x1000000, attributes=PmaAttributes(memory_type="memory"))

        pma_config = PmaConfig(regions=[region1, region2])
        generator = PmaGenerator(pma_config, self.memory, self.rng)

        regions = generator.generate_all([])
        self.assertEqual(len(regions), 2)

        # Find regions by name (order may vary)
        region1_info = next(r for r in regions if r.pma_name == "region1")
        region2_info = next(r for r in regions if r.pma_name == "region2")

        # Check that region2 is adjacent to region1
        expected_base = (region1_info.pma_address + region1_info.pma_size + 0xFFF) & ~0xFFF
        self.assertEqual(region2_info.pma_address, expected_base)

    def test_find_free_space(self):
        """Test finding free address space"""
        generator = PmaGenerator(None, self.memory, self.rng)

        # Add a region
        region1 = PmaInfo(pma_name="region1", pma_address=0x80000000, pma_size=0x1000000)
        generator.generated_regions = [region1]

        # Find space after region1
        base = generator._find_free_space(0x80000000, 0x10000000, 0x1000000)
        self.assertGreaterEqual(base, 0x81000000)

    def test_address_alignment(self):
        """Test that addresses are 4KB aligned"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="test", combinations=[{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}])

        regions = generator.generate_from_hint(hint)
        self.assertEqual(len(regions), 1)
        # Address should be 4KB aligned
        self.assertEqual(regions[0].pma_address & 0xFFF, 0)

    def test_io_memory_type(self):
        """Test generating IO memory regions"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="test", combinations=[{"memory_type": "io", "combining": "noncombining", "rwx": "rw"}])

        regions = generator.generate_from_hint(hint)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].pma_memory_type, "io")
        self.assertEqual(regions[0].pma_combining, "noncombining")

    def test_ch0_ch1_memory_types(self):
        """Test generating ch0/ch1 memory regions"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="test", combinations=[{"memory_type": "ch0", "rwx": "rwx"}, {"memory_type": "ch1", "rwx": "rwx"}])

        regions = generator.generate_from_hint(hint)
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0].pma_memory_type, "ch0")
        self.assertEqual(regions[1].pma_memory_type, "ch1")

    def test_empty_hint(self):
        """Test handling empty hint"""
        generator = PmaGenerator(None, self.memory, self.rng)

        hint = ParsedPmaHint(name="empty")

        regions = generator.generate_from_hint(hint)
        # Should generate default combination
        self.assertGreater(len(regions), 0)

    def test_rwx_parsing(self):
        """Test RWX permission parsing"""
        generator = PmaGenerator(None, self.memory, self.rng)

        test_cases = [
            ("r", True, False, False),
            ("w", False, True, False),
            ("x", False, False, True),
            ("rw", True, True, False),
            ("rx", True, False, True),
            ("wx", False, True, True),
            ("rwx", True, True, True),
        ]

        for rwx, read, write, execute in test_cases:
            combo = {"memory_type": "memory", "cacheability": "cacheable", "rwx": rwx}
            region = generator._generate_region(combo, ParsedPmaHint(name="test"), 0)
            self.assertEqual(region.pma_read, read, f"Failed for rwx={rwx}")
            self.assertEqual(region.pma_write, write, f"Failed for rwx={rwx}")
            self.assertEqual(region.pma_execute, execute, f"Failed for rwx={rwx}")


if __name__ == "__main__":
    unittest.main()
