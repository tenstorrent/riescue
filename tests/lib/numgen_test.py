#!/usr/bin/env python3

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import patch

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.lib.numgen import NumGen


class TestNumGen(unittest.TestCase):
    """
    Test Suite for NumGen Class
    Used by dtest_framework/generator.py to generate fp / int numbers

    """

    def setUp(self):
        """Setting up RNG and NumGen with a static seed. Should be able to run the same identical tests and get the same result"""
        # Create a seeded RandNum instance for deterministic tests
        self.rng = RandNum(seed=42)
        self.numgen = NumGen(self.rng)

    def test_init(self):
        """Test NumGen initialization."""
        # Verify that the NumGen instance is created with correct attributes
        self.assertIsInstance(self.numgen, NumGen)
        self.assertEqual(self.numgen.rng, self.rng)

        # Verify that options are initialized correctly
        self.assertIn(RV.NumGenOps.select_special_num, self.numgen.opts)
        self.assertIn(RV.NumGenOps.special_num, self.numgen.opts)
        self.assertIn(RV.NumGenOps.subnormal, self.numgen.opts)
        self.assertIn(RV.NumGenOps.fractional, self.numgen.opts)
        self.assertIn(RV.NumGenOps.low_fidelity, self.numgen.opts)
        self.assertIn(RV.NumGenOps.nan_box, self.numgen.opts)

    def test_randomize(self):
        """Test randomize method."""
        opts = self.numgen.randomize()

        # Verify that randomize returns a dictionary with the expected keys
        self.assertIsInstance(opts, dict)
        self.assertIn(RV.NumGenOps.select_special_num, opts)
        self.assertIn(RV.NumGenOps.special_num, opts)
        self.assertIn(RV.NumGenOps.subnormal, opts)
        self.assertIn(RV.NumGenOps.fractional, opts)
        self.assertIn(RV.NumGenOps.low_fidelity, opts)
        self.assertIn(RV.NumGenOps.nan_box, opts)

    def test_set_low_fidelity_num(self):
        """Test set_low_fidelity_num method for each floating point data type."""
        # Test FP8
        fp8_val = self.numgen.set_low_fidelity_num(RV.DataType.FP8)
        self.assertIn(fp8_val, self.numgen.low_fidelity_fp8)

        # Test FP16
        fp16_val = self.numgen.set_low_fidelity_num(RV.DataType.FP16)
        self.assertIn(fp16_val, self.numgen.low_fidelity_fp16)

        # Test FP32
        fp32_val = self.numgen.set_low_fidelity_num(RV.DataType.FP32)
        self.assertIn(fp32_val, self.numgen.low_fidelity_fp32)

        # Test FP64
        fp64_val = self.numgen.set_low_fidelity_num(RV.DataType.FP64)
        self.assertIn(fp64_val, self.numgen.low_fidelity_fp64)

        # Test invalid data type
        with self.assertRaises(ValueError):
            self.numgen.set_low_fidelity_num(RV.DataType.INT8)

    def test_set_special_num(self):
        """Test set_special_num method for each special number type and data type."""
        # Test NaN for each FP type
        fp8_nan = self.numgen.set_special_num(RV.SpecialFpNums.canonical_nan, RV.DataType.FP8)
        self.assertEqual(fp8_nan, self.numgen.special_nums_fp8[RV.SpecialFpNums.canonical_nan])

        fp16_nan = self.numgen.set_special_num(RV.SpecialFpNums.canonical_nan, RV.DataType.FP16)
        self.assertEqual(fp16_nan, self.numgen.special_nums_fp16[RV.SpecialFpNums.canonical_nan])

        fp32_nan = self.numgen.set_special_num(RV.SpecialFpNums.canonical_nan, RV.DataType.FP32)
        self.assertEqual(fp32_nan, self.numgen.special_nums_fp32[RV.SpecialFpNums.canonical_nan])

        fp64_nan = self.numgen.set_special_num(RV.SpecialFpNums.canonical_nan, RV.DataType.FP64)
        self.assertEqual(fp64_nan, self.numgen.special_nums_fp64[RV.SpecialFpNums.canonical_nan])

        # Test infinity for each FP type
        fp8_inf = self.numgen.set_special_num(RV.SpecialFpNums.pos_inf, RV.DataType.FP8)
        self.assertEqual(fp8_inf, self.numgen.special_nums_fp8[RV.SpecialFpNums.pos_inf])

        # Test negative infinity
        fp32_neg_inf = self.numgen.set_special_num(RV.SpecialFpNums.neg_inf, RV.DataType.FP32)
        self.assertEqual(fp32_neg_inf, self.numgen.special_nums_fp32[RV.SpecialFpNums.neg_inf])

        # Test zero
        fp16_zero = self.numgen.set_special_num(RV.SpecialFpNums.pos_zero, RV.DataType.FP16)
        self.assertEqual(fp16_zero, self.numgen.special_nums_fp16[RV.SpecialFpNums.pos_zero])

        # Test negative zero
        fp64_neg_zero = self.numgen.set_special_num(RV.SpecialFpNums.neg_zero, RV.DataType.FP64)
        self.assertEqual(fp64_neg_zero, self.numgen.special_nums_fp64[RV.SpecialFpNums.neg_zero])

        # Test invalid data type
        with self.assertRaises(ValueError):
            self.numgen.set_special_num(RV.SpecialFpNums.canonical_nan, RV.DataType.INT32)

    def test_rand_num_low_fidelity(self):
        """Test rand_num method with low_fidelity option."""
        # Test with FP32
        num = self.numgen.rand_num(RV.DataType.FP32)
        self.assertEqual(num, 0xCB800000)

    def test_rand_num_special_num(self):
        """Test rand_num method with special_num option."""
        # Force special_num to be selected
        self.numgen.opts[RV.NumGenOps.low_fidelity] = {True: 0, False: 100}
        self.numgen.opts[RV.NumGenOps.select_special_num] = {True: 100, False: 0}

        # Test with FP16
        num = self.numgen.rand_num(RV.DataType.FP16)
        self.assertEqual(num, 0xFFFF7E00)

    def test_rand_num_int(self):
        """Test rand_num method with integer type."""
        # Test with INT32
        num = self.numgen.rand_num(RV.DataType.INT32)
        self.assertEqual(num, 0xE465E151)

    def test_rand_num_int2(self):
        """Double checking static seed is preserved across runs"""
        # Test with INT32
        num = self.numgen.rand_num(RV.DataType.INT32)
        self.assertEqual(num, 0xE465E151)

    def test_rand_num_int3(self):
        """Triple checking static seed is preserved across runs"""
        # Test with INT32
        num = self.numgen.rand_num(RV.DataType.INT32)
        self.assertEqual(num, 0xE465E151)

    def test_rand_num_fp(self):
        """Test rand_num method with floating point type."""
        # Test with FP32
        num = self.numgen.rand_num(RV.DataType.FP32)
        self.assertEqual(num, 0xCB800000)

    def test_rand_num_nan_box(self):
        """Test rand_num method with nan_box option."""
        num = self.numgen.rand_num(RV.DataType.FP16)
        self.assertEqual(num, 0xFFFFC800)

    def test_rand_num_invalid_type(self):
        """Test rand_num method with invalid data type."""

        # Create a custom data type that isn't INT or FP
        class CustomDataType(RV.MyEnum):
            CUSTOM = 999

        # Test with invalid data type
        with patch("riescue.lib.enums.DataType.is_int", return_value=False):
            with patch("riescue.lib.enums.DataType.is_fp", return_value=False):
                with self.assertRaises(ValueError):
                    self.numgen.rand_num(CustomDataType.CUSTOM)


if __name__ == "__main__":
    unittest.main()
