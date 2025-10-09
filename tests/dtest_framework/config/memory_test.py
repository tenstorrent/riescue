# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from typing import Union

from riescue.dtest_framework.config import CpuConfig, Memory
from riescue.dtest_framework.config.memory import IoRange, DramRange


class DramRangeTest(unittest.TestCase):
    """Test suite for :class:`riescue.dtest_framework.config.memory.DramRange`."""

    def test_basic_construction(self):
        """DramRange basic construction and properties."""
        dram = DramRange(0x1000, 0x100)
        self.assertEqual(dram.start, 0x1000)
        self.assertEqual(dram.size, 0x100)
        self.assertEqual(dram.end, 0x10FF)
        self.assertFalse(dram.secure)
        self.assertEqual(dram.to_dict(), {"address": 0x1000, "name": "", "size": 0x100, "secure": False})

    def test_from_dict_integers(self):
        """DramRange.from_dict handles integer inputs."""
        cfg: dict[str, Union[str, int, bool]] = {"address": 0x1000, "size": 0x100}
        mem = DramRange.from_dict(cfg)
        self.assertEqual(mem.start, 0x1000)
        self.assertEqual(mem.size, 0x100)
        self.assertFalse(mem.secure)
        self.assertEqual(mem.end, 0x10FF)

    def test_from_dict_hex_strings_secure(self):
        """DramRange.from_dict handles hexadecimal strings and secure flag."""
        cfg = {"address": "0x2000", "size": "0x200", "secure": True}
        mem = DramRange.from_dict(cfg)
        self.assertEqual(mem.start, 0x2000)
        self.assertEqual(mem.size, 0x200)
        self.assertTrue(mem.secure)
        self.assertEqual(mem.end, 0x21FF)

    def test_from_dict_missing_key(self):
        """DramRange.from_dict raises on missing keys."""
        with self.assertRaises(ValueError):
            DramRange.from_dict({"size": 0x100})

    def test_from_dict_negative_address(self):
        """DramRange.from_dict rejects negative addresses."""
        with self.assertRaises(ValueError):
            DramRange.from_dict({"address": -1, "size": 0x100})

    def test_from_dict_zero_size(self):
        """DramRange.from_dict rejects zero or negative sizes."""
        with self.assertRaises(ValueError):
            DramRange.from_dict({"address": 0x0, "size": 0})

    def test_from_dict_non_integer(self):
        """DramRange.from_dict rejects non-integer values."""
        with self.assertRaises(ValueError):
            DramRange.from_dict({"address": "not_int", "size": "0x100"})  # type: ignore # intentional type error here


class IoRangeTest(unittest.TestCase):
    """Test suite for :class:`riescue.dtest_framework.config.memory.IoRange`."""

    def test_defaults(self):
        """IoRange default construction."""
        mem = IoRange()
        self.assertEqual(mem.start, 0)
        self.assertEqual(mem.size, 0)
        self.assertFalse(mem.test_access)

    def test_custom_values(self):
        """IoRange custom construction."""
        mem = IoRange(0x4000, 0x1000, False)
        self.assertEqual(mem.start, 0x4000)
        self.assertEqual(mem.size, 0x1000)
        self.assertFalse(mem.test_access)

    def test_defaults_from_dict_empty(self):
        """IoRange.from_dict raises ``ValueError`` on empty dict."""
        with self.assertRaises(ValueError):
            mem = IoRange.from_dict({})

    def test_custom_values_from_dict(self):
        """IoRange custom construction from dict. Default to test_access=True"""
        custom = {
            "address": "0x200_c000",
            "size": "0x5ff_4000",
        }
        mem = IoRange.from_dict(custom)
        self.assertEqual(mem.start, 0x200C000)
        self.assertEqual(mem.size, 0x5FF4000)
        self.assertFalse(mem.test_access)

    def test_custom_values_test_access_from_dict(self):
        """IoRange custom construction from dict."""
        custom = {
            "address": "0x200_c000",
            "size": "0x5ff_4000",
            "test_access": False,
        }
        mem = IoRange.from_dict(custom)
        self.assertEqual(mem.start, 0x200C000)
        self.assertEqual(mem.size, 0x5FF4000)
        self.assertFalse(mem.test_access)


class MemoryTest(unittest.TestCase):
    """Test suite for :class:`riescue.dtest_framework.config.Memory`."""

    def test_default_construction(self):
        """Memory default construction."""
        mem = Memory()
        self.assertEqual(len(mem.dram_ranges), 1)
        self.assertEqual(len(mem.io_ranges), 1)

    def test_from_dict_empty(self):
        """Memory.from_dict handles empty dict."""
        mem = Memory.from_dict({})
        self.assertEqual(len(mem.dram_ranges), 1)
        self.assertEqual(len(mem.io_ranges), 1)

    def test_from_dict_with_dram(self):
        """Memory.from_dict handles DRAM configuration."""
        cfg = {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}}
        mem = Memory.from_dict(cfg)
        self.assertEqual(len(mem.dram_ranges), 1)
        self.assertEqual(mem.dram_ranges[0].start, 0x80000000)
        self.assertEqual(mem.dram_ranges[0].size, 0x10000000)

    def test_from_dict_with_io(self):
        """Memory.from_dict handles IO configuration."""
        cfg = {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}, "io": {"io0": {"address": "0x0", "size": "0x1000"}}}
        mem = Memory.from_dict(cfg)
        self.assertEqual(len(mem.io_ranges), 0)
        self.assertEqual(len(mem.reserved_ranges), 1, "IO region should be in reserved_ranges")

    def test_from_dict_with_io_test_access(self):
        """Memory.from_dict handles IO configuration."""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
            },
            "io": {
                "io0": {"address": "0x0", "size": "0x1000"},
                "io1": {"address": "0x1000", "size": "0x1000", "test_access": True},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual(len(mem.io_ranges), 1, "io1 region should be in io_ranges, since test_access is True")
        self.assertEqual(len(mem.reserved_ranges), 1, "IO region should be in reserved_ranges")

    def test_from_dict_missing_dram(self):
        """Memory.from_dict raises on missing DRAM."""
        with self.assertRaises(ValueError):
            Memory.from_dict({"io": {"io0": {"address": "0x0", "size": "0x1000"}}})

    def test_from_dict_empty_dram(self):
        """Memory.from_dict raises on empty DRAM."""
        with self.assertRaises(ValueError):
            Memory.from_dict({"dram": {}})

    def test_from_dict_secure_name(self):
        """Memory.from_dict handles DRAM ranges with secure in name"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "secure0": {"address": "0x9000_0000", "size": "0x1000_0000"},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual(len(mem.secure_ranges), 1, "There should only be one secure range since secure0 is in the name")
        self.assertEqual(len(mem.dram_ranges), 1, "There should only be one DRAM range since secure regions are not included in dram_ranges")

    def test_from_dict_secure_name_with_secure_key(self):
        """Memory.from_dict handles DRAM ranges with secure in name and secure key"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "secure0": {"address": "0x9000_0000", "size": "0x1000_0000", "secure": True},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual(len(mem.dram_ranges), 1, "There should only be one DRAM range since secure regions are not included in dram_ranges")
        self.assertEqual(len(mem.secure_ranges), 1, "There should only be one secure range since secure0 has secure: True")
