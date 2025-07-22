#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Centralized feature discovery module for Riescue.

This module provides a unified API for discovering and querying features from config.json files.
It consolidates feature discovery logic that was previously scattered across dtest_framework modules.

Usage:
    .. code-block:: python

        from riescue.lib.feature_discovery import FeatureDiscovery

        # Load features from config.json
        fd = FeatureDiscovery.from_config("path/to/config.json")

        # Query features
        enabled_features = fd.get_enabled_features()
        march_string = fd.get_compiler_march_string()
        is_supported = fd.is_feature_supported("v")
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

log = logging.getLogger(__name__)


class FeatureDiscovery:
    """
    Centralized feature discovery and querying class.

    This class provides a clean API for loading features from config.json files
    and querying their status (enabled, supported, randomization percentage).

    Example:
        .. code-block:: python

            # Load features from config file
            fd = FeatureDiscovery.from_config("config.json")

            # Check if vector extension is enabled
            if fd.is_feature_enabled("v"):
                print("Vector extension is enabled")

            # Get all enabled features
            enabled = fd.list_enabled_features()
            print(f"Enabled features: {enabled}")
    """

    def __init__(self, features: Dict[str, Any]):
        """
        Initialize with a features dictionary.

        Args:
            features: Dictionary of features with their configurations

        Example:
            .. code-block:: python

                features = {
                    "v": {"supported": True, "enabled": True, "randomize": 50},
                    "f": {"supported": True, "enabled": False, "randomize": 0}
                }
                fd = FeatureDiscovery(features)
        """
        self.features = features

    @classmethod
    def from_config(cls, config_path: Union[str, Path]) -> "FeatureDiscovery":
        """
        Create FeatureDiscovery instance from a config.json file.

        Args:
            config_path: Path to config.json file

        Returns:
            FeatureDiscovery instance with loaded features

        Raises:
            FileNotFoundError: If config file doesn't exist
            json.JSONDecodeError: If config file is invalid JSON

        Example:
            .. code-block:: python

                # Load from config.json file
                fd = FeatureDiscovery.from_config("path/to/config.json")

                # Check features
                if fd.is_feature_enabled("v"):
                    print("Vector extension enabled")
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r") as f:
            config = json.load(f)

        features = {}

        # Load features from config.json
        if "features" in config:
            features = config["features"]
        elif "isa" in config:
            # If only ISA is specified, initialize features based on ISA
            features = cls._load_features_from_isa(config["isa"])

        return cls(features)

    @classmethod
    def from_config_with_overrides(cls, config_path: Union[str, Path], test_features: Optional[str] = None) -> "FeatureDiscovery":
        """
        Create FeatureDiscovery instance from config.json with test header overrides.

        Args:
            config_path: Path to config.json file
            test_features: Test header features string (e.g., "ext_v.enable ext_fp.disable")

        Returns:
            FeatureDiscovery instance with merged features

        Example:
            .. code-block:: python

                # Load with test header overrides
                test_features = "ext_v.enable ext_f.disable"
                fd = FeatureDiscovery.from_config_with_overrides("config.json", test_features)

                # Vector will be enabled, FP will be disabled
                assert fd.is_feature_enabled("v")
                assert not fd.is_feature_enabled("f")
        """
        fd = cls.from_config(config_path)

        if test_features:
            fd._apply_test_feature_overrides(test_features)

        return fd

    def is_feature_supported(self, feature_name: str) -> bool:
        """
        Check if a feature is supported.

        Args:
            feature_name: Name of the feature to check

        Returns:
            True if the feature is supported, False otherwise

        Example:
            .. code-block:: python

                if fd.is_feature_supported("v"):
                    print("Vector extension is supported")
                else:
                    print("Vector extension is not supported")
        """
        feature_config = self.features.get(feature_name, {})
        if isinstance(feature_config, dict):
            return feature_config.get("supported", False)
        else:
            return bool(feature_config)

    def is_feature_enabled(self, feature_name: str) -> bool:
        """
        Check if a feature is enabled.

        Args:
            feature_name: Name of the feature to check

        Returns:
            True if the feature is enabled, False otherwise

        Example:
            .. code-block:: python

                if fd.is_feature_enabled("v"):
                    print("Vector extension is enabled")
                else:
                    print("Vector extension is disabled")
        """
        feature_config = self.features.get(feature_name, {})
        if isinstance(feature_config, dict):
            return feature_config.get("enabled", False)
        else:
            return bool(feature_config)

    def get_feature_randomize(self, feature_name: str) -> int:
        """
        Get the randomization percentage for a feature.

        Args:
            feature_name: Name of the feature to check

        Returns:
            Randomization percentage (0-100) for the feature

        Example:
            .. code-block:: python

                randomize_pct = fd.get_feature_randomize("v")
                print(f"Vector extension has {randomize_pct}% randomization")
        """
        feature_config = self.features.get(feature_name, {})
        if isinstance(feature_config, dict):
            return feature_config.get("randomize", 0)
        else:
            return 0

    def get_feature_config(self, feature_name: str) -> Dict[str, Any]:
        """
        Get the complete configuration for a feature.

        Args:
            feature_name: Name of the feature to check

        Returns:
            Dictionary containing 'supported', 'enabled', and 'randomize' values

        Example:
            .. code-block:: python

                config = fd.get_feature_config("v")
                print(f"Vector config: {config}")
                # Output: {'supported': True, 'enabled': True, 'randomize': 50}
        """
        feature_config = self.features.get(feature_name, {})
        if isinstance(feature_config, dict):
            return feature_config.copy()
        else:
            # Handle simple boolean features
            return {"supported": bool(feature_config), "enabled": bool(feature_config), "randomize": 0}

    def list_features(self) -> List[str]:
        """
        Get a list of all feature names.

        Returns:
            List of feature names

        Example:
            .. code-block:: python

                all_features = fd.list_features()
                print(f"All features: {all_features}")
                # Output: ['rv64', 'i', 'm', 'a', 'f', 'd', 'c', 'v', 'zba', ...]
        """
        return list(self.features.keys())

    def list_enabled_features(self) -> List[str]:
        """
        Get a list of all enabled feature names.

        Returns:
            List of enabled feature names

        Example:
            .. code-block:: python

                enabled = fd.list_enabled_features()
                print(f"Enabled features: {enabled}")
                # Output: ['rv64', 'i', 'm', 'a', 'f', 'd', 'c', 'v']
        """
        return [name for name in self.features.keys() if self.is_feature_enabled(name)]

    def list_supported_features(self) -> List[str]:
        """
        Get a list of all supported feature names.

        Returns:
            List of supported feature names

        Example:
            .. code-block:: python

                supported = fd.list_supported_features()
                print(f"Supported features: {supported}")
                # Output: ['rv64', 'i', 'm', 'a', 'f', 'd', 'c', 'v', 'zba']
        """
        return [name for name in self.features.keys() if self.is_feature_supported(name)]

    def get_compiler_march_string(self) -> str:
        """
        Generate a compiler march string from enabled features.

        Returns:
            String suitable for use with gcc's -march parameter

        Example:
            .. code-block:: python

                march = fd.get_compiler_march_string()
                print(f"Compiler march: {march}")
                # Output: rv64imafdcv_zba_zbb_zbs
        """
        # Start with base architecture
        march_components = []

        # Base architecture (rv32 or rv64)
        if self.is_feature_enabled("rv64"):
            march_components.append("rv64")
        elif self.is_feature_enabled("rv32"):
            march_components.append("rv32")
        else:
            march_components.append("rv64")  # Default to rv64

        # Base extensions in standard order
        base_extensions = ["i", "m", "a", "f", "d", "c", "h", "v", "u", "s"]
        for ext in base_extensions:
            if self.is_feature_enabled(ext):
                march_components.append(ext)

        # Z-extensions (alphabetically sorted)
        z_extensions = []
        for feature_name in sorted(self.features.keys()):
            if feature_name.startswith("z") and self.is_feature_enabled(feature_name):
                z_extensions.append(feature_name)

        # S-extensions (supervisor extensions)
        s_extensions = []
        for feature_name in sorted(self.features.keys()):
            if feature_name.startswith("sv") and self.is_feature_enabled(feature_name):
                s_extensions.append(feature_name)

        # Combine base architecture with base extensions (no underscores between them)
        if len(march_components) > 1:
            base_string = march_components[0] + "".join(march_components[1:])
        else:
            base_string = march_components[0]

        # Start with base string
        result = [base_string]

        # Add Z-extensions with underscores
        result.extend(z_extensions)

        # Add S-extensions with underscores
        result.extend(s_extensions)

        # Join with underscores
        return "_".join(result)

    def enable_features_by_randomization(self, rng=None) -> None:
        """
        Enable features based on their randomization percentages.

        Args:
            rng: Random number generator (optional). If not provided, uses random.random()

        Example:
            .. code-block:: python

                import random

                # Enable features based on randomization percentages
                fd.enable_features_by_randomization(random.Random(42))

                # Check which features were randomly enabled
                enabled = fd.list_enabled_features()
                print(f"Randomly enabled: {enabled}")
        """
        import random

        if rng is None:
            rng = random

        for feature_name, feature_config in self.features.items():
            if isinstance(feature_config, dict):
                randomize_percent = feature_config.get("randomize", 0)
                if randomize_percent > 0:
                    # Generate random number and enable if within percentage
                    if rng.random() * 100 < randomize_percent:
                        feature_config["enabled"] = True
                        log.info(f"Randomly enabled feature: {feature_name}")

    def _apply_test_feature_overrides(self, test_features: str) -> None:
        """
        Apply test header feature overrides to current features.

        Args:
            test_features: Test header features string (e.g., "ext_v.enable ext_fp.disable")

        Example:
            .. code-block:: python

                # Apply test header overrides
                fd._apply_test_feature_overrides("ext_v.enable ext_f.disable")

                # Vector will be enabled, FP will be disabled
                assert fd.is_feature_enabled("v")
                assert not fd.is_feature_enabled("f")
        """
        if not test_features:
            return

        feature_tokens = test_features.strip().split()
        for token in feature_tokens:
            if "." in token:
                feature_name, action = token.split(".", 1)
                # Remove common prefixes to get the core feature name
                if feature_name.startswith("ext_"):
                    feature_name = feature_name[4:]  # Remove "ext_" prefix

                # Initialize feature if not exists
                if feature_name not in self.features:
                    self.features[feature_name] = {"supported": True, "enabled": False, "randomize": 0}

                # Apply the action
                if action == "enable":
                    self.features[feature_name]["enabled"] = True
                    self.features[feature_name]["supported"] = True
                elif action == "disable":
                    self.features[feature_name]["enabled"] = False
            elif token == "wysiwyg":
                # Handle wysiwyg as a special boolean feature
                self.features["wysiwyg"] = True

    @staticmethod
    def _load_features_from_isa(isa_str: Union[str, List[str]]) -> Dict[str, Any]:
        """
        Load features from ISA string in config.json.

        Args:
            isa_str: ISA string or list of extensions

        Returns:
            Dictionary of features initialized from ISA

        Example:
            .. code-block:: python

                # From ISA string
                features = FeatureDiscovery._load_features_from_isa("rv64imafdcv")

                # From list
                features = FeatureDiscovery._load_features_from_isa(["rv64", "i", "m", "a", "f", "d", "c", "v"])
        """
        features = {}

        # Handle both string and list inputs
        if isinstance(isa_str, list):
            extensions = isa_str
        else:
            extensions = isa_str.split("_")

        # Parse base ISA
        if "rv64" in extensions:
            features["rv64"] = {"supported": True, "enabled": True, "randomize": 100}
        elif "rv32" in extensions:
            features["rv32"] = {"supported": True, "enabled": True, "randomize": 100}

        # Parse base extensions
        base_extensions = ["i", "m", "a", "f", "d", "c", "v"]
        for ext in base_extensions:
            if ext in extensions:
                features[ext] = {"supported": True, "enabled": True, "randomize": 100}

        # Parse additional extensions
        for ext in extensions:
            if ext.startswith("z"):
                features[ext] = {"supported": True, "enabled": True, "randomize": 0}

        return features

    def is_enabled(self, feature_name: str) -> bool:
        """
        Backward compatibility: alias for is_feature_enabled

        Example:
            .. code-block:: python

                # Legacy API
                if fd.is_enabled("v"):
                    print("Vector extension is enabled")
        """
        return self.is_feature_enabled(feature_name)

    def is_supported(self, feature_name: str) -> bool:
        """
        Backward compatibility: alias for is_feature_supported

        Example:
            .. code-block:: python

                # Legacy API
                if fd.is_supported("v"):
                    print("Vector extension is supported")
        """
        return self.is_feature_supported(feature_name)


# Convenience functions for backward compatibility and simple use cases
def load_config_features(config_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Load features from config.json file.

    Args:
        config_path: Path to config.json file

    Returns:
        Dictionary of features from config.json

    Example:
        .. code-block:: python

            features = load_config_features("config.json")
            print(f"Loaded features: {features}")
    """
    return FeatureDiscovery.from_config(config_path).features


def get_enabled_features(config_path: Union[str, Path]) -> List[str]:
    """
    Get list of enabled features from config.json.

    Args:
        config_path: Path to config.json file

    Returns:
        List of enabled feature names

    Example:
        .. code-block:: python

            enabled = get_enabled_features("config.json")
            print(f"Enabled features: {enabled}")
    """
    return FeatureDiscovery.from_config(config_path).list_enabled_features()


def get_supported_features(config_path: Union[str, Path]) -> List[str]:
    """
    Get list of supported features from config.json.

    Args:
        config_path: Path to config.json file

    Returns:
        List of supported feature names

    Example:
        .. code-block:: python

            supported = get_supported_features("config.json")
            print(f"Supported features: {supported}")
    """
    return FeatureDiscovery.from_config(config_path).list_supported_features()


def generate_compiler_march_from_config(config_path: Union[str, Path]) -> str:
    """
    Generate a compiler march string from config.json features.

    Args:
        config_path: Path to config.json file

    Returns:
        String suitable for use with gcc's -march parameter

    Example:
        .. code-block:: python

            march = generate_compiler_march_from_config("config.json")
            print(f"Compiler march: {march}")
    """
    return FeatureDiscovery.from_config(config_path).get_compiler_march_string()
