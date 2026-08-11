# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for FeatMgr.get_misa_clear_bits and the loader's misa clear emission.

The loader historically only ORed bits into misa (``csrrs``), so a base extension marked
``enabled: false`` in cpuconfig still read as present whenever the reset misa had its bit set. That
made the "extension absent" direction unreachable -- notably misa.H=0, which several coverage bins
require. These tests pin which bits are eligible to be cleared and, critically, which are not.
"""

import unittest
from unittest.mock import MagicMock

from riescue.dtest_framework.config.featmanager import FeatMgr


def _featmgr(features: dict):
    """FeatMgr instance with only the cpu_config.features surface the method under test uses."""
    fm = FeatMgr.__new__(FeatMgr)
    cfg = MagicMock()
    cfg.features.features = list(features)
    cfg.features.is_feature_enabled.side_effect = lambda f: features.get(f, False)
    fm.cpu_config = cfg
    return fm


class TestMisaClearBits(unittest.TestCase):
    def test_nothing_to_clear_when_all_enabled(self):
        fm = _featmgr({"m": True, "a": True, "f": True, "d": True, "c": True, "v": True, "h": True})
        self.assertEqual(fm.get_misa_clear_bits(), 0, "an all-enabled config must emit no clear")

    def test_disabled_h_clears_bit_7(self):
        fm = _featmgr({"h": False, "m": True})
        self.assertEqual(fm.get_misa_clear_bits(), 1 << 7)

    def test_disabled_v_clears_bit_21(self):
        fm = _featmgr({"v": False})
        self.assertEqual(fm.get_misa_clear_bits(), 1 << 21)

    def test_multiple_disabled_extensions_accumulate(self):
        fm = _featmgr({"h": False, "v": False, "c": False})
        self.assertEqual(fm.get_misa_clear_bits(), (1 << 7) | (1 << 21) | (1 << 2))

    def test_absent_feature_is_not_cleared(self):
        """Only features the config actually mentions are candidates; silence is not 'disabled'."""
        fm = _featmgr({"m": True})
        self.assertEqual(fm.get_misa_clear_bits(), 0, "features absent from the config must be left alone")

    def test_base_isa_bit_is_never_cleared(self):
        """Clearing misa.I would be nonsense, so 'i' must not be a candidate even when disabled."""
        fm = _featmgr({"i": False})
        self.assertEqual(fm.get_misa_clear_bits() & (1 << 8), 0, "misa.I must never be cleared")

    def test_z_extensions_never_clear_base_bits(self):
        """get_misa_bits aliases some Z features onto F/A/V's bits; a disabled Z must not clear those."""
        fm = _featmgr({"zfh": False, "zba": False, "zvbb": False, "f": True, "a": True, "v": True})
        self.assertEqual(fm.get_misa_clear_bits(), 0, "disabled Z extensions must not clear F/A/V")

    def test_disabled_z_alongside_disabled_base_only_clears_the_base(self):
        fm = _featmgr({"zfh": False, "h": False})
        self.assertEqual(fm.get_misa_clear_bits(), 1 << 7, "only the base extension contributes")


class TestLoaderMisaEmission(unittest.TestCase):
    """Misa clears are emitted separately so register initialization can run first."""

    def _loader(self, clear_bits, set_bits=0x400000000020112D):
        from riescue.dtest_framework.runtime.loader import Loader

        loader = Loader.__new__(Loader)
        loader.featmgr = MagicMock()
        loader.featmgr.get_misa_bits.return_value = set_bits
        loader.featmgr.get_misa_clear_bits.return_value = clear_bits
        return loader

    def test_no_clear_emitted_when_nothing_disabled(self):
        loader = self._loader(clear_bits=0)
        self.assertIn("csrrs", loader.set_misa_bits(), "the set path must always be emitted")
        self.assertEqual(loader.clear_misa_bits(), "", "no clear instruction when there is nothing to clear")

    def test_clear_emitted_with_the_right_mask(self):
        code = self._loader(clear_bits=1 << 7).clear_misa_bits()
        self.assertIn("csrrc", code)
        self.assertIn("0x80", code, "clear mask must appear as an immediate")

    def test_set_does_not_clear_before_register_initialization(self):
        """The caller emits register initialization between these two independent snippets."""
        code = self._loader(clear_bits=1 << 21).set_misa_bits()
        self.assertNotIn("csrrc", code, "set_misa_bits must not disable V before vector initialization")


if __name__ == "__main__":
    unittest.main(verbosity=2)
