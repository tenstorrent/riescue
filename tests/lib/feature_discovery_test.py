#!/usr/bin/env python3

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import tempfile
import json
from pathlib import Path
from io import StringIO
from unittest.mock import patch
from riescue.lib.feature_discovery import FeatureDiscovery


class TestFeatureDiscovery(unittest.TestCase):
    """Test the FeatureDiscovery class functionality"""

    def setUp(self):
        """Set up test fixtures with a temporary config file"""
        self.test_features = {
            "i": {"supported": True, "enabled": True, "randomize": 0},
            "m": {"supported": True, "enabled": True, "randomize": 0},
            "a": {"supported": True, "enabled": True, "randomize": 0},
            "f": {"supported": True, "enabled": True, "randomize": 50},
            "d": {"supported": True, "enabled": False, "randomize": 25},
            "v": {"supported": True, "enabled": True, "randomize": 75},
            "zba": {"supported": True, "enabled": False, "randomize": 0},
            "zbb": {"supported": False, "enabled": False, "randomize": 0},
        }

        # Create config in the format expected by from_config method
        self.test_config = {"features": self.test_features}

        # Create temporary config file
        self.temp_config_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json")
        json.dump(self.test_config, self.temp_config_file)
        self.temp_config_file.close()
        self.config_path = Path(self.temp_config_file.name)

    def tearDown(self):
        """Clean up temporary files"""
        if self.config_path.exists():
            self.config_path.unlink()

    def test_from_config_loading(self):
        """Test loading configuration from file"""
        fd = FeatureDiscovery.from_config(self.config_path)
        self.assertIsNotNone(fd)
        self.assertEqual(fd.features, self.test_features)

    def test_list_enabled_features(self):
        """Test listing enabled features"""
        fd = FeatureDiscovery.from_config(self.config_path)
        enabled_features = fd.list_enabled_features()
        expected_enabled = ["i", "m", "a", "f", "v"]
        self.assertEqual(set(enabled_features), set(expected_enabled))

    def test_list_supported_features(self):
        """Test listing supported features"""
        fd = FeatureDiscovery.from_config(self.config_path)
        supported_features = fd.list_supported_features()
        expected_supported = ["i", "m", "a", "f", "d", "v", "zba"]
        self.assertEqual(set(supported_features), set(expected_supported))

    def test_get_compiler_march_string(self):
        """Test generating compiler march string"""
        fd = FeatureDiscovery.from_config(self.config_path)
        march_string = fd.get_compiler_march_string()

        # The march string should start with rv64 and include enabled features
        self.assertTrue(march_string.startswith("rv64"))

        # Check that enabled features are included
        enabled_features = ["i", "m", "a", "f", "v"]
        for feature in enabled_features:
            self.assertIn(feature, march_string)

    def test_is_feature_enabled(self):
        """Test checking if features are enabled"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test enabled features
        self.assertTrue(fd.is_feature_enabled("i"))
        self.assertTrue(fd.is_feature_enabled("f"))
        self.assertTrue(fd.is_feature_enabled("v"))

        # Test disabled features
        self.assertFalse(fd.is_feature_enabled("d"))
        self.assertFalse(fd.is_feature_enabled("zba"))

        # Test non-existent feature
        self.assertFalse(fd.is_feature_enabled("nonexistent"))

    def test_is_feature_supported(self):
        """Test checking if features are supported"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test supported features
        self.assertTrue(fd.is_feature_supported("i"))
        self.assertTrue(fd.is_feature_supported("f"))
        self.assertTrue(fd.is_feature_supported("d"))
        self.assertTrue(fd.is_feature_supported("zba"))

        # Test unsupported features
        self.assertFalse(fd.is_feature_supported("zbb"))

        # Test non-existent feature
        self.assertFalse(fd.is_feature_supported("nonexistent"))

    def test_get_feature_randomize(self):
        """Test getting feature randomization values"""
        fd = FeatureDiscovery.from_config(self.config_path)

        self.assertEqual(fd.get_feature_randomize("i"), 0)
        self.assertEqual(fd.get_feature_randomize("f"), 50)
        self.assertEqual(fd.get_feature_randomize("d"), 25)
        self.assertEqual(fd.get_feature_randomize("v"), 75)
        self.assertEqual(fd.get_feature_randomize("nonexistent"), 0)

    def test_get_feature_config(self):
        """Test getting complete feature configuration"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test enabled feature
        config = fd.get_feature_config("f")
        expected = {"supported": True, "enabled": True, "randomize": 50}
        self.assertEqual(config, expected)

        # Test disabled feature
        config = fd.get_feature_config("d")
        expected = {"supported": True, "enabled": False, "randomize": 25}
        self.assertEqual(config, expected)

        # Test non-existent feature
        config = fd.get_feature_config("nonexistent")
        # Non-existent features return empty dict
        self.assertEqual(config, {})

    def test_cli_functionality_list_enabled(self):
        """Test CLI-style functionality for listing enabled features"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test the functionality that was previously in __main__
        enabled_features = fd.list_enabled_features()
        self.assertIn("i", enabled_features)
        self.assertIn("m", enabled_features)
        self.assertIn("a", enabled_features)
        self.assertIn("f", enabled_features)
        self.assertIn("v", enabled_features)
        self.assertNotIn("d", enabled_features)
        self.assertNotIn("zba", enabled_features)

    def test_cli_functionality_list_supported(self):
        """Test CLI-style functionality for listing supported features"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test the functionality that was previously in __main__
        supported_features = fd.list_supported_features()
        self.assertIn("i", supported_features)
        self.assertIn("m", supported_features)
        self.assertIn("a", supported_features)
        self.assertIn("f", supported_features)
        self.assertIn("d", supported_features)
        self.assertIn("v", supported_features)
        self.assertIn("zba", supported_features)
        self.assertNotIn("zbb", supported_features)

    def test_cli_functionality_march_string(self):
        """Test CLI-style functionality for generating march string"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test the functionality that was previously in __main__
        march_string = fd.get_compiler_march_string()
        self.assertTrue(march_string.startswith("rv64"))

        # Should include enabled features
        for feature in ["i", "m", "a", "f", "v"]:
            self.assertIn(feature, march_string)

    def test_cli_functionality_specific_feature(self):
        """Test CLI-style functionality for checking specific features"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test the functionality that was previously in __main__
        # Check enabled feature
        feature_config = fd.get_feature_config("f")
        self.assertTrue(feature_config.get("supported", False))
        self.assertTrue(feature_config.get("enabled", False))
        self.assertEqual(feature_config.get("randomize", 0), 50)

        # Check disabled feature
        feature_config = fd.get_feature_config("d")
        self.assertTrue(feature_config.get("supported", False))
        self.assertFalse(feature_config.get("enabled", False))
        self.assertEqual(feature_config.get("randomize", 0), 25)

        # Check non-existent feature
        self.assertFalse(fd.is_feature_supported("nonexistent"))
        self.assertFalse(fd.is_feature_enabled("nonexistent"))
        self.assertEqual(fd.get_feature_randomize("nonexistent"), 0)

    def test_cli_functionality_all_features(self):
        """Test CLI-style functionality for showing all features"""
        fd = FeatureDiscovery.from_config(self.config_path)

        # Test the default behavior that was previously in __main__
        all_features = fd.features
        self.assertEqual(len(all_features), 8)  # Should have 8 features in test config

        # Verify each feature has the expected structure
        for name, config in all_features.items():
            self.assertIsInstance(config, dict)
            self.assertIn("supported", config)
            self.assertIn("enabled", config)
            self.assertIn("randomize", config)

    def test_error_handling(self):
        """Test error handling for invalid config files"""
        # Test with non-existent file
        with self.assertRaises(FileNotFoundError):
            FeatureDiscovery.from_config(Path("/nonexistent/file.json"))

        # Test with invalid JSON
        invalid_config_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json")
        invalid_config_file.write("invalid json content")
        invalid_config_file.close()

        try:
            with self.assertRaises(json.JSONDecodeError):
                FeatureDiscovery.from_config(Path(invalid_config_file.name))
        finally:
            Path(invalid_config_file.name).unlink()


if __name__ == "__main__":
    unittest.main()
