# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from typing import Union

import riescue.lib.enums as RV
from riescue.dtest_framework.config import Memory
from riescue.riemap.memory import CustomRange, DramRange, IoRange


class DramRangeTest(unittest.TestCase):
    """Test suite for :class:`riescue.riemap.memory.DramRange`."""

    def test_basic_construction(self):
        """DramRange basic construction and properties."""
        dram = DramRange(0x1000, 0x100)
        self.assertEqual(dram.start, 0x1000)
        self.assertEqual(dram.size, 0x100)
        self.assertEqual(dram.end, 0x10FF)
        self.assertFalse(dram.secure)
        self.assertEqual(
            dram.to_dict(),
            {
                "address": 0x1000,
                "name": "",
                "size": 0x100,
                "secure": False,
                "cacheable": False,
                "configurable": False,
                "permissions": "rwx",
                "tags": [],
                "pma_randomization": True,
            },
        )

    def test_direct_constructor_validates_range_and_types(self):
        for args in (
            (-1, 0x100),
            (0x1000, 0),
            (True, 0x100),
            (0x1000, 1.5),
            (0xFFFFFFFFFFFFFFFF, 2),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                DramRange(*args)

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

    def test_from_dict_cachable_true_string(self):
        """DramRange.from_dict rejects non-integer values."""
        with self.assertRaises(ValueError):
            DramRange.from_dict({"address": 0x0, "size": 0, "cacheable": "true"})

    def test_split_dram_ranges(self):
        cfg: dict[str, Union[str, int, bool]] = {"address": 0x8000_0000, "size": 0x10_0000, "configurable": True}
        mem = DramRange.from_dict(cfg)
        dram0, dram1 = mem.split(0x1000)
        self.assertEqual(dram0.start, 0x8000_0000)
        self.assertEqual(dram0.size, 0x1000)
        self.assertEqual(dram1.start, 0x8000_1000)
        self.assertEqual(dram1.size, 0x10_0000 - 0x1000)

    def test_make_secure_preserves_metadata_and_sets_secure(self):
        mem = DramRange(
            start=0x8000_0000,
            size=0x20_0000,
            secure=False,
            cacheable=True,
            configurable=True,
            name="main",
            permissions=RV.PmpAttributes.R_W,
        )

        secure = mem.make_secure()

        self.assertTrue(secure.secure)
        self.assertEqual(secure.name, "main")
        self.assertEqual(secure.permissions, RV.PmpAttributes.R_W)
        self.assertEqual(secure.start, mem.start | (1 << 55))

    def test_split_preserves_metadata(self):
        mem = DramRange(
            start=0x8000_0000,
            size=0x20_0000,
            secure=True,
            cacheable=True,
            configurable=True,
            name="main",
            permissions=RV.PmpAttributes.R_W,
        )

        left, right = mem.split(0x1000)

        for part in (left, right):
            self.assertEqual(part.name, "main")
            self.assertEqual(part.permissions, RV.PmpAttributes.R_W)
            self.assertTrue(part.secure)

    def test_split_rejects_empty_parts(self):
        mem = DramRange(
            start=0x8000_0000,
            size=0x20_0000,
            configurable=True,
        )
        for size in (0, mem.size):
            with self.subTest(size=size), self.assertRaises(ValueError):
                mem.split(size)

    def test_split_dram_ranges_not_configurable(self):
        cfg: dict[str, Union[str, int, bool]] = {"address": 0x8000_0000, "size": 0x10_0000}
        mem = DramRange.from_dict(cfg)
        with self.assertRaises(ValueError):
            mem.split(0x1000)

    def test_split_dram_ranges_size_too_large(self):
        cfg: dict[str, Union[str, int, bool]] = {"address": 0x8000_0000, "size": 0x10_0000, "configurable": True}
        mem = DramRange.from_dict(cfg)
        with self.assertRaises(ValueError):
            mem.split(0x100000000)


class IoRangeTest(unittest.TestCase):
    """Test suite for :class:`riescue.riemap.memory.IoRange`."""

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

    def test_direct_constructor_validates_range_and_types(self):
        for args in (
            (-1, 0x100),
            (0x1000, 0),
            (True, 0x100),
            (0x1000, 1.5),
            (0xFFFFFFFFFFFFFFFF, 2),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                IoRange(*args)

    def test_direct_constructor_requires_exact_test_access_boolean(self):
        for value in (0, 1, "false", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "test_access"):
                IoRange(0x1000, 0x100, value)

    def test_defaults_from_dict_empty(self):
        """IoRange.from_dict raises ``ValueError`` on empty dict."""
        with self.assertRaises(ValueError):
            IoRange.from_dict({})

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

    def test_range_collections_are_immutable(self):
        mem = Memory()
        with self.assertRaises((AttributeError, TypeError)):
            mem.dram_ranges.append(DramRange(0x1000, 0x1000))

    def test_ranges_inside_memory_are_immutable(self):
        mem = Memory()
        with self.assertRaises((AttributeError, TypeError)):
            mem.dram_ranges[0].start = 0

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
        self.assertTrue(mem.secure_ranges[0].secure, "a secure-prefixed name must normalize the range's secure flag")

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

    def test_pma_randomization_key_splits_range_out_of_dram_ranges(self):
        """pma_randomization: false is what makes a fixed window, whatever the range is tagged"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "win_a": {"address": "0x9000_0000", "size": "0x1_0000", "tags": ["derr"], "pma_randomization": False},
                "win_b": {"address": "0xA000_0000", "size": "0x2_0000", "tags": ["nderr"], "pma_randomization": False},
                "win_c": {"address": "0xB000_0000", "size": "0x4_0000", "pma_randomization": False},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual(len(mem.dram_ranges), 1, "fixed ranges must not stay in dram_ranges")
        self.assertEqual(len(mem.secure_ranges), 0)
        self.assertEqual([r.name for r in mem.pma_fixed_ranges], ["win_a", "win_b", "win_c"])
        self.assertEqual([r.start for r in mem.pma_fixed_ranges], [0x9000_0000, 0xA000_0000, 0xB000_0000])
        self.assertFalse(any(r.pma_randomization for r in mem.pma_fixed_ranges))
        # The name stays a label the test refers to; a tag only makes the range selectable
        self.assertEqual(mem.pma_fixed_ranges[0].tags, ("derr",))

    def test_tags_do_not_default_pma_randomization_off(self):
        """No tag implies a fixed window: derr/nderr/stee/secure-tagged ranges stay in the general pool"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "still_random": {"address": "0x9000_0000", "size": "0x1_0000", "tags": ["derr", "nderr"]},
                "also_random": {"address": "0xA000_0000", "size": "0x1_0000", "tags": ["stee", "secure"]},
                "fixed_untagged": {"address": "0xB000_0000", "size": "0x1_0000", "pma_randomization": False},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual(sorted(r.name for r in mem.dram_ranges), ["also_random", "dram0", "still_random"])
        self.assertEqual(mem.secure_ranges, (), "the secure tag must not promote the range to secure")
        self.assertEqual([r.name for r in mem.pma_fixed_ranges], ["fixed_untagged"])

    def test_tag_name_is_not_a_classifier(self):
        """A range merely *named* derr0 stays ordinary DRAM; only pma_randomization classifies"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "derr0": {"address": "0x9000_0000", "size": "0x1_0000"},
                "nderr0": {"address": "0xA000_0000", "size": "0x1_0000"},
                "stee0": {"address": "0xB000_0000", "size": "0x1_0000"},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual(len(mem.dram_ranges), 4)
        self.assertEqual(mem.pma_fixed_ranges, ())

    def test_secure_tag_does_not_make_a_range_secure(self):
        """The secure tag is just a label; the secure key is what classifies"""
        cfg = {"dram": {"win": {"address": "0x9000_0000", "size": "0x1_0000", "tags": ["secure"]}}}
        mem = Memory.from_dict(cfg)
        self.assertEqual(mem.secure_ranges, (), "the tag must not promote the range to secure")
        self.assertEqual([r.name for r in mem.dram_ranges], ["win"])
        self.assertEqual(mem.pma_fixed_ranges, ())

    def test_secure_key_outranks_fixed_classification(self):
        """A secure range stays in secure_ranges even with pma_randomization off"""
        cfg = {"dram": {"win": {"address": "0x9000_0000", "size": "0x1_0000", "secure": True, "tags": ["derr"], "pma_randomization": False}}}
        mem = Memory.from_dict(cfg)
        self.assertEqual([r.name for r in mem.secure_ranges], ["win"])
        self.assertEqual(mem.pma_fixed_ranges, ())

    def test_multiple_tags_allowed(self):
        """Unlike the flags it replaces, a range may carry several tags"""
        cfg = {"dram": {"win": {"address": "0x9000_0000", "size": "0x1_0000", "tags": ["derr", "stee", "bank0"]}}}
        mem = Memory.from_dict(cfg)
        self.assertEqual(mem.dram_ranges[0].tags, ("derr", "stee", "bank0"))

    def test_malformed_tags_raise(self):
        """tags must be a list of unique non-empty strings"""
        for tags, expected in (
            ("derr", "bare string"),
            (5, "got int"),
            ([""], "non-empty strings"),
            ([1], "non-empty strings"),
            (["derr", "derr"], "duplicate tags"),
        ):
            cfg = {"dram": {"win": {"address": "0x9000_0000", "size": "0x1_0000", "tags": tags}}}
            with self.subTest(tags=tags), self.assertRaises(ValueError) as ctx:
                Memory.from_dict(cfg)
            self.assertIn(expected, str(ctx.exception))

    def test_to_dict_includes_tags_and_pma_randomization(self):
        """Memory.to_dict round-trips the fixed range list with its tags"""
        mem = Memory(pma_fixed_ranges=[DramRange(start=0x9000_0000, size=0x1_0000, name="win", tags=("derr",), pma_randomization=False)])
        as_dict = mem.to_dict()
        self.assertEqual(as_dict["pma_fixed"][0]["address"], 0x9000_0000)
        self.assertEqual(as_dict["pma_fixed"][0]["tags"], ["derr"])
        self.assertFalse(as_dict["pma_fixed"][0]["pma_randomization"])

    def test_default_fixed_ranges_empty(self):
        """Default Memory has no fixed ranges and randomizable DRAM"""
        mem = Memory()
        self.assertEqual(mem.pma_fixed_ranges, ())
        self.assertTrue(mem.dram_ranges[0].pma_randomization)

    def test_resolve_custom_region_by_name_and_tag(self):
        """A custom_region spec resolves by exact name first, else by tag across every targetable range"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "win_a": {"address": "0x9000_0000", "size": "0x1_0000", "tags": ["derr", "poison"]},
                "win_b": {"address": "0xA000_0000", "size": "0x1_0000", "tags": ["derr"]},
                "low_bank": {"address": "0xB000_0000", "size": "0x1_0000", "tags": ["bank0"]},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual([r.name for r in mem.resolve_custom_region("win_a")], ["win_a"])
        self.assertEqual([r.name for r in mem.resolve_custom_region("derr")], ["win_a", "win_b"])
        self.assertEqual([r.name for r in mem.resolve_custom_region("poison")], ["win_a"])
        self.assertEqual([r.name for r in mem.resolve_custom_region("nope")], [])
        # low_bank keeps pma_randomization, so it stays in the general pool but is still selectable
        self.assertEqual([r.name for r in mem.resolve_custom_region("bank0")], ["low_bank"])
        self.assertIn("low_bank", [r.name for r in mem.dram_ranges])

    def test_resolve_custom_region_prefers_name_over_tag(self):
        """A range named X wins even when another range is tagged X"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "derr": {"address": "0x9000_0000", "size": "0x1_0000", "pma_randomization": False},
                "other": {"address": "0xA000_0000", "size": "0x1_0000", "tags": ["derr"]},
            },
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual([r.name for r in mem.resolve_custom_region("derr")], ["derr"])

    def test_untagged_pool_ranges_are_not_targetable(self):
        """An ordinary dram/io range is not a custom_region target unless it is tagged or fixed"""
        cfg = {
            "dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}},
            "io": {"io0": {"address": "0x0", "size": "0x1_0000", "test_access": True}},
        }
        mem = Memory.from_dict(cfg)
        self.assertEqual(mem.targetable_ranges(), ())
        self.assertEqual(mem.custom_region_choices(), ([], []))

    def test_custom_region_choices_lists_names_and_tags(self):
        """custom_region_choices reports every accepted spelling, for error messages"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000"},
                "win_a": {"address": "0x9000_0000", "size": "0x1_0000", "tags": ["derr", "poison"]},
            },
            "custom": {"probe": {"address": "0x6000_0000", "size": "0x1_0000"}},
        }
        names, tags = Memory.from_dict(cfg).custom_region_choices()
        self.assertEqual(names, ["probe", "win_a"])
        self.assertEqual(tags, ["derr", "poison"])

    def test_address_qualifier_of(self):
        """A pinned region's qualifier has to match the segment AddrGen declares over that span"""
        cfg = {
            "dram": {
                "dram0": {"address": "0x8000_0000", "size": "0x1000_0000", "tags": ["bank0"]},
                "win": {"address": "0x9000_0000", "size": "0x1_0000", "tags": ["derr"], "pma_randomization": False},
                "secure0": {"address": "0xC000_0000", "size": "0x1_0000", "tags": ["hot"]},
            },
            "io": {"io0": {"address": "0x1_0000", "size": "0x1_0000", "test_access": True, "tags": ["dev"]}},
            "custom": {"probe": {"address": "0x6000_0000", "size": "0x1_0000"}},
        }
        mem = Memory.from_dict(cfg)
        qualifier_of = {rng.name: mem.address_qualifier_of(rng) for rng in mem.targetable_ranges()}
        self.assertEqual(qualifier_of["probe"], RV.AddressQualifiers.ADDRESS_CUSTOM)
        self.assertEqual(qualifier_of["win"], RV.AddressQualifiers.ADDRESS_CUSTOM)
        self.assertEqual(qualifier_of["secure0"], RV.AddressQualifiers.ADDRESS_SECURE)
        self.assertEqual(qualifier_of["io0"], RV.AddressQualifiers.ADDRESS_MMIO)
        self.assertEqual(qualifier_of["dram0"], RV.AddressQualifiers.ADDRESS_DRAM)

    def test_from_dict_cacheable(self):
        """Memory.from_dict handles cacheable configuration."""
        cfg = {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000", "cacheable": True}}}
        mem = Memory.from_dict(cfg)
        self.assertEqual(mem.dram_ranges[0].cacheable, True)

    def test_from_dict_configurable(self):
        """Memory.from_dict handles configurable configuration."""
        cfg = {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000", "configurable": True}}}
        mem = Memory.from_dict(cfg)
        self.assertEqual(mem.dram_ranges[0].configurable, True)

    def test_malformed_io_and_custom_sections_are_rejected(self):
        dram = {"dram0": {"address": "0x80000000", "size": "0x1000"}}
        for section in ("io", "custom"):
            for value in (None, [], "not-a-mapping"):
                with self.subTest(section=section, value=value):
                    try:
                        Memory.from_dict({"dram": dram, section: value})
                    except Exception as error:
                        self.assertIsInstance(error, ValueError)
                        self.assertRegex(str(error), section)
                    else:
                        self.fail("ValueError not raised")


class CustomRangeTest(unittest.TestCase):
    def test_direct_constructor_validates_range_and_types(self):
        for args in (
            (-1, 0x100),
            (0x1000, 0),
            (True, 0x100),
            (0x1000, 1.5),
            (0xFFFFFFFFFFFFFFFF, 2),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                CustomRange(*args)
