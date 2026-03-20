# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.dtest_framework.config.pma_config import (
    PmaAttributes,
    PmaRegionConfig,
    PmaHintConfig,
    PmaConfig,
)


class PmaAttributesTest(unittest.TestCase):
    """Test PmaAttributes class"""

    def test_default_attributes(self):
        """Test default attribute values"""
        attrs = PmaAttributes()
        self.assertEqual(attrs.memory_type, "memory")
        self.assertEqual(attrs.cacheability, "cacheable")
        self.assertEqual(attrs.read, True)
        self.assertEqual(attrs.write, True)
        self.assertEqual(attrs.execute, True)
        self.assertEqual(attrs.amo_type, "arithmetic")
        self.assertEqual(attrs.routing, "coherent")

    def test_memory_type_validation(self):
        """Test memory type validation"""
        # Valid types
        for mem_type in ["memory", "io", "ch0", "ch1"]:
            attrs = PmaAttributes(memory_type=mem_type)
            self.assertEqual(attrs.memory_type, mem_type)

        # Invalid type
        with self.assertRaises(ValueError):
            PmaAttributes(memory_type="invalid")

    def test_cacheability_for_memory(self):
        """Test cacheability for memory type"""
        # Valid cacheability
        attrs = PmaAttributes(memory_type="memory", cacheability="cacheable")
        self.assertEqual(attrs.cacheability, "cacheable")

        attrs = PmaAttributes(memory_type="memory", cacheability="noncacheable")
        self.assertEqual(attrs.cacheability, "noncacheable")

        # Default cacheability for memory
        attrs = PmaAttributes(memory_type="memory")
        self.assertEqual(attrs.cacheability, "cacheable")

        # Invalid cacheability
        with self.assertRaises(ValueError):
            PmaAttributes(memory_type="memory", cacheability="invalid")

    def test_combining_for_io(self):
        """Test combining for IO type"""
        # Valid combining
        attrs = PmaAttributes(memory_type="io", combining="combining")
        self.assertEqual(attrs.combining, "combining")

        attrs = PmaAttributes(memory_type="io", combining="noncombining")
        self.assertEqual(attrs.combining, "noncombining")

        # Default combining for io
        attrs = PmaAttributes(memory_type="io")
        self.assertEqual(attrs.combining, "noncombining")

        # Invalid combining
        with self.assertRaises(ValueError):
            PmaAttributes(memory_type="io", combining="invalid")

    def test_amo_type_validation(self):
        """Test AMO type validation"""
        for amo_type in ["none", "logical", "swap", "arithmetic"]:
            attrs = PmaAttributes(amo_type=amo_type)
            self.assertEqual(attrs.amo_type, amo_type)

        with self.assertRaises(ValueError):
            PmaAttributes(amo_type="invalid")

    def test_routing_validation(self):
        """Test routing validation"""
        for routing in ["coherent", "noncoherent"]:
            attrs = PmaAttributes(routing=routing)
            self.assertEqual(attrs.routing, routing)

        with self.assertRaises(ValueError):
            PmaAttributes(routing="invalid")

    def test_from_dict(self):
        """Test creating from dictionary"""
        cfg = {"memory_type": "memory", "cacheability": "noncacheable", "read": False, "write": True, "execute": True, "amo_type": "none", "routing": "noncoherent"}
        attrs = PmaAttributes.from_dict(cfg)
        self.assertEqual(attrs.memory_type, "memory")
        self.assertEqual(attrs.cacheability, "noncacheable")
        self.assertEqual(attrs.read, False)
        self.assertEqual(attrs.write, True)
        self.assertEqual(attrs.execute, True)
        self.assertEqual(attrs.amo_type, "none")
        self.assertEqual(attrs.routing, "noncoherent")

    def test_to_pma_info_dict(self):
        """Test conversion to PmaInfo-compatible dictionary"""
        attrs = PmaAttributes(memory_type="memory", cacheability="cacheable", read=True, write=True, execute=True, amo_type="arithmetic", routing="coherent")
        result = attrs.to_pma_info_dict()
        self.assertEqual(result["pma_memory_type"], "memory")
        self.assertEqual(result["pma_cacheability"], "cacheable")
        self.assertEqual(result["pma_read"], True)
        self.assertEqual(result["pma_write"], True)
        self.assertEqual(result["pma_execute"], True)
        self.assertEqual(result["pma_amo_type"], "arithmetic")
        self.assertEqual(result["pma_routing_to"], "coherent")

        # Test IO type
        attrs_io = PmaAttributes(memory_type="io", combining="combining")
        result_io = attrs_io.to_pma_info_dict()
        self.assertEqual(result_io["pma_memory_type"], "io")
        self.assertEqual(result_io["pma_combining"], "combining")
        self.assertNotIn("pma_cacheability", result_io)


class PmaRegionConfigTest(unittest.TestCase):
    """Test PmaRegionConfig class"""

    def test_basic_region(self):
        """Test basic region configuration"""
        region = PmaRegionConfig(name="test_region")
        self.assertEqual(region.name, "test_region")
        self.assertIsNone(region.base)
        self.assertIsNone(region.size)
        self.assertFalse(region.auto_generate)

    def test_region_with_address(self):
        """Test region with base address"""
        region = PmaRegionConfig(name="test_region", base=0x80000000, size=0x1000000)
        self.assertEqual(region.base, 0x80000000)
        self.assertEqual(region.size, 0x1000000)

    def test_region_name_validation(self):
        """Test region name validation"""
        with self.assertRaises(ValueError):
            PmaRegionConfig(name="")

    def test_region_size_validation(self):
        """Test region size validation"""
        with self.assertRaises(ValueError):
            PmaRegionConfig(name="test", size=-1)

        with self.assertRaises(ValueError):
            PmaRegionConfig(name="test", size=0)

    def test_region_base_validation(self):
        """Test region base validation"""
        with self.assertRaises(ValueError):
            PmaRegionConfig(name="test", base=-1)

    def test_from_dict(self):
        """Test creating from dictionary"""
        cfg = {
            "name": "test_region",
            "base": "0x80000000",
            "size": "0x1000000",
            "attributes": {"memory_type": "memory", "cacheability": "cacheable"},
            "adjacent_to": "other_region",
            "auto_generate": True,
        }
        region = PmaRegionConfig.from_dict(cfg)
        self.assertEqual(region.name, "test_region")
        self.assertEqual(region.base, 0x80000000)
        self.assertEqual(region.size, 0x1000000)
        self.assertEqual(region.attributes.memory_type, "memory")
        self.assertEqual(region.adjacent_to, "other_region")
        self.assertTrue(region.auto_generate)

    def test_from_dict_integer_addresses(self):
        """Test creating from dictionary with integer addresses"""
        cfg = {"name": "test_region", "base": 2147483648, "size": 16777216, "attributes": {}}  # 0x80000000  # 0x1000000
        region = PmaRegionConfig.from_dict(cfg)
        self.assertEqual(region.base, 0x80000000)
        self.assertEqual(region.size, 0x1000000)


class PmaHintConfigTest(unittest.TestCase):
    """Test PmaHintConfig class"""

    def test_basic_hint(self):
        """Test basic hint configuration"""
        hint = PmaHintConfig(name="test_hint")
        self.assertEqual(hint.name, "test_hint")
        self.assertEqual(hint.combinations, [])
        self.assertFalse(hint.adjacent)

    def test_hint_with_combinations(self):
        """Test hint with combinations"""
        combinations = [{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}, {"memory_type": "memory", "cacheability": "noncacheable", "rwx": "rwx"}]
        hint = PmaHintConfig(name="test_hint", combinations=combinations)
        self.assertEqual(len(hint.combinations), 2)

    def test_hint_name_validation(self):
        """Test hint name validation"""
        with self.assertRaises(ValueError):
            PmaHintConfig(name="")

    def test_hint_min_max_validation(self):
        """Test min/max regions validation"""
        # Valid min/max
        hint = PmaHintConfig(name="test", min_regions=2, max_regions=5)
        self.assertEqual(hint.min_regions, 2)
        self.assertEqual(hint.max_regions, 5)

        # Invalid: negative
        with self.assertRaises(ValueError):
            PmaHintConfig(name="test", min_regions=-1)

        with self.assertRaises(ValueError):
            PmaHintConfig(name="test", max_regions=-1)

        # Invalid: min > max
        with self.assertRaises(ValueError):
            PmaHintConfig(name="test", min_regions=5, max_regions=2)

    def test_from_dict(self):
        """Test creating from dictionary"""
        cfg = {"name": "test_hint", "combinations": [{"memory_type": "memory", "cacheability": "cacheable"}], "adjacent": True, "min_regions": 2, "max_regions": 5}
        hint = PmaHintConfig.from_dict(cfg)
        self.assertEqual(hint.name, "test_hint")
        self.assertEqual(len(hint.combinations), 1)
        self.assertTrue(hint.adjacent)
        self.assertEqual(hint.min_regions, 2)
        self.assertEqual(hint.max_regions, 5)


class PmaConfigTest(unittest.TestCase):
    """Test PmaConfig class"""

    def test_empty_config(self):
        """Test empty configuration"""
        config = PmaConfig()
        self.assertEqual(len(config.regions), 0)
        self.assertEqual(len(config.hints), 0)
        self.assertEqual(config.max_regions, 15)

    def test_config_with_regions(self):
        """Test configuration with regions"""
        regions = [PmaRegionConfig(name="region1"), PmaRegionConfig(name="region2")]
        config = PmaConfig(regions=regions)
        self.assertEqual(len(config.regions), 2)

    def test_config_with_hints(self):
        """Test configuration with hints"""
        hints = [PmaHintConfig(name="hint1"), PmaHintConfig(name="hint2")]
        config = PmaConfig(hints=hints)
        self.assertEqual(len(config.hints), 2)

    def test_max_regions_validation(self):
        """Test max_regions validation"""
        # Valid range
        for max_regions in [1, 10, 15]:
            config = PmaConfig(max_regions=max_regions)
            self.assertEqual(config.max_regions, max_regions)

        # Invalid: too low
        with self.assertRaises(ValueError):
            PmaConfig(max_regions=0)

        # Invalid: too high
        with self.assertRaises(ValueError):
            PmaConfig(max_regions=16)

    def test_duplicate_region_names(self):
        """Test duplicate region name detection"""
        regions = [PmaRegionConfig(name="duplicate"), PmaRegionConfig(name="duplicate")]
        with self.assertRaises(ValueError):
            PmaConfig(regions=regions)

    def test_duplicate_hint_names(self):
        """Test duplicate hint name detection"""
        hints = [PmaHintConfig(name="duplicate"), PmaHintConfig(name="duplicate")]
        with self.assertRaises(ValueError):
            PmaConfig(hints=hints)

    def test_from_dict(self):
        """Test creating from dictionary"""
        cfg = {
            "regions": [{"name": "region1", "base": "0x80000000", "size": "0x1000000", "attributes": {"memory_type": "memory", "cacheability": "cacheable"}}],
            "hints": [{"name": "hint1", "combinations": [{"memory_type": "memory", "cacheability": "cacheable"}]}],
            "max_regions": 10,
        }
        config = PmaConfig.from_dict(cfg)
        self.assertEqual(len(config.regions), 1)
        self.assertEqual(len(config.hints), 1)
        self.assertEqual(config.max_regions, 10)

    def test_from_dict_invalid_regions(self):
        """Test error handling for invalid regions"""
        cfg = {"regions": "not_a_list"}
        with self.assertRaises(ValueError):
            PmaConfig.from_dict(cfg)

    def test_from_dict_invalid_hints(self):
        """Test error handling for invalid hints"""
        cfg = {"hints": "not_a_list"}
        with self.assertRaises(ValueError):
            PmaConfig.from_dict(cfg)

    def test_from_dict_missing_name(self):
        """Test error handling for missing name in region"""
        cfg = {"regions": [{"base": "0x80000000"}]}  # Missing name
        with self.assertRaises(ValueError):
            PmaConfig.from_dict(cfg)

    def test_from_dict_string_max_regions(self):
        """Test parsing max_regions as string"""
        cfg = {"max_regions": "10"}
        config = PmaConfig.from_dict(cfg)
        self.assertEqual(config.max_regions, 10)

        cfg = {"max_regions": "0xf"}
        config = PmaConfig.from_dict(cfg)
        self.assertEqual(config.max_regions, 15)


if __name__ == "__main__":
    unittest.main()
