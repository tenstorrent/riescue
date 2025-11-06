# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Union

import riescue.lib.enums as RV
import riescue.lib.common as common
from riescue.lib.rand import RandNum


class FpOffsets:
    "Defines the bit fields for RV floating point numbers."

    def __init__(self, data_type: RV.DataType):
        if data_type == RV.DataType.FP8:
            self.mantissa = 0
            self.exponent = 3
            self.sign = 7
        elif data_type == RV.DataType.FP16:
            self.mantissa = 0
            self.exponent = 10
            self.sign = 15
        elif data_type == RV.DataType.FP32:
            self.mantissa = 0
            self.exponent = 23
            self.sign = 31
        elif data_type == RV.DataType.FP64:
            self.mantissa = 0
            self.exponent = 52
            self.sign = 63
        else:
            self.mantissa = None
            self.exponent = None
            self.sign = None


class Widths:
    "Defines bit widths or lengths for RV integers and floating point numbers."

    def __init__(self, data_type: RV.DataType):
        self.mantissa = None
        self.exponent = None
        self.sign = None
        self.payload = None

        if data_type == RV.DataType.INT8:
            self.payload = 8
        elif data_type == RV.DataType.INT16:
            self.payload = 16
        elif data_type == RV.DataType.INT32:
            self.payload = 32
        elif data_type == RV.DataType.INT64:
            self.payload = 64
        elif data_type == RV.DataType.FP8:
            self.mantissa = 3
            self.exponent = 4
            self.sign = 1
        elif data_type == RV.DataType.FP16:
            self.mantissa = 11
            self.exponent = 5
            self.sign = 1
        elif data_type == RV.DataType.FP32:
            self.mantissa = 23
            self.exponent = 8
            self.sign = 1
        elif data_type == RV.DataType.FP64:
            self.mantissa = 52
            self.exponent = 11
            self.sign = 1


class FP:
    def __init__(self, data_type: RV.DataType, rnd_gen: RandNum, val: int = 0):
        self.val = val
        self.offsets = FpOffsets(data_type)
        self.widths = Widths(data_type)
        self.rnd_gen = rnd_gen

    def set_exponent(self, exponent: int):
        if self.widths.exponent is None:
            raise ValueError("Exponent width is not defined for this data type.")
        if self.offsets.exponent is None:
            raise ValueError("offset exponent is not defined for this data type.")
        if exponent > (1 << self.widths.exponent):
            raise ValueError(f"Invalid exponent value: {exponent}. Should be less than {1 << self.widths.exponent}.")
        self.val = common.set_bits(
            self.val,
            self.offsets.exponent + self.widths.exponent - 1,
            self.offsets.exponent,
            exponent,
        )
        return

    def set_mantissa(self, mantissa: int):
        if self.widths.mantissa is None:
            raise ValueError("Mantissa width is not defined for this data type.")
        if self.offsets.mantissa is None:
            raise ValueError("offset mantissa is not defined for this data type.")
        if mantissa > (1 << self.widths.mantissa):
            raise ValueError(f"Invalid mantissa value: {mantissa}. Should be less than {1 << self.widths.mantissa}.")
        self.val = common.set_bits(
            self.val,
            self.offsets.mantissa + self.widths.mantissa - 1,
            self.offsets.mantissa,
            mantissa,
        )
        return

    def set_sign(self, sign: int):
        if self.widths.sign is None:
            raise ValueError("Sign width is not defined for this data type.")
        if self.offsets.sign is None:
            raise ValueError("offset sign is not defined for this data type.")
        if sign != 0 and sign != 1:
            raise ValueError(f"Invalid sign value: {sign}. Should be 0 or 1.")
        self.val = common.set_bitn(self.val, self.offsets.sign + self.widths.sign - 1, bool(sign))

    def randomize(self, subnormal: bool, fractional: bool):
        if self.widths.mantissa is None:
            raise ValueError("Mantissa width is not defined for this data type.")
        if self.widths.exponent is None:
            raise ValueError("Exponent width is not defined for this data type.")
        if self.widths.sign is None:
            raise ValueError("Sign width is not defined for this data type.")
        self.val = 0
        self.set_sign(self.rnd_gen.get_rand_bits(self.widths.sign))
        self.set_mantissa(int(self.rnd_gen.random_in_range(0, 2**self.widths.mantissa)))
        self.set_exponent(int(self.rnd_gen.random_in_range(0, 2**self.widths.exponent)))
        if subnormal:
            self.set_exponent(0)
        if fractional:
            self.set_mantissa(0)


class INT:
    def __init__(self, data_type: RV.DataType, rnd_gen: RandNum, val: int = 0):
        self.val = val
        self.offsets = FpOffsets(data_type)
        self.widths = Widths(data_type)
        self.rnd_gen = rnd_gen

    def set_val(self, val: int):
        self.val = val

    def randomize(self):
        if self.widths.payload is None:
            raise ValueError("Payload width is not defined for this data type.")
        self.val = 0
        self.set_val(int(self.rnd_gen.random_in_range(0, 2**self.widths.payload)))


class NumGen:
    # Easy power of 2 numbers without mantissa (FP-formatted-int)
    low_fidelity_fp8 = [0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70]
    # 1 2 4 8 16 32 64 128 256
    low_fidelity_fp32 = [
        0x3F800000,
        0x40000000,
        0x40400000,
        0x41000000,
        0x41980000,
        0x41800000,
        0x42100000,
        0x42800000,
        0x43000000,
        0x43800000,
    ]

    low_fidelity_fp16 = [0x3C00, 0x4000, 0x4400, 0x4800, 0x4C00, 0x5000, 0x5400]
    low_fidelity_fp64 = [
        0x3FF0000000000000,  # 1.0
        0x4000000000000000,  # 2.0
        0x4010000000000000,  # 4.0
        0x4020000000000000,  # 8.0
        0x4030000000000000,  # 16.0
    ]
    special_nums_fp8 = {
        RV.SpecialFpNums.canonical_nan: 0x7F,
        RV.SpecialFpNums.pos_inf: 0x78,
        RV.SpecialFpNums.neg_inf: 0xF8,
        RV.SpecialFpNums.pos_zero: 0x00,
        RV.SpecialFpNums.neg_zero: 0x80,
        RV.SpecialFpNums.pos_sat: 0x77,
        RV.SpecialFpNums.neg_sat: 0xF7,
    }  # Note: nonstandard, using 1.4.3 format for FP8

    special_nums_fp16 = {
        RV.SpecialFpNums.canonical_nan: 0x7E00,
        RV.SpecialFpNums.pos_inf: 0x7C00,
        RV.SpecialFpNums.neg_inf: 0xFC00,
        RV.SpecialFpNums.pos_zero: 0x0000,
        RV.SpecialFpNums.neg_zero: 0x8000,
        RV.SpecialFpNums.pos_sat: 0x7BFF,
        RV.SpecialFpNums.neg_sat: 0xFBFF,
    }

    special_nums_fp32 = {
        RV.SpecialFpNums.canonical_nan: 0x7FC00000,
        RV.SpecialFpNums.pos_inf: 0x7F800000,
        RV.SpecialFpNums.neg_inf: 0xFF800000,
        RV.SpecialFpNums.pos_zero: 0x00000000,
        RV.SpecialFpNums.neg_zero: 0x80000000,
        RV.SpecialFpNums.pos_sat: 0x7F7FFFFF,
        RV.SpecialFpNums.neg_sat: 0xFF7FFFFF,
    }

    special_nums_fp64 = {
        RV.SpecialFpNums.canonical_nan: 0x7FF8000000000000,
        RV.SpecialFpNums.pos_inf: 0x7FF0000000000000,
        RV.SpecialFpNums.neg_inf: 0xFFF0000000000000,
        RV.SpecialFpNums.pos_zero: 0x0000000000000000,
        RV.SpecialFpNums.neg_zero: 0x8000000000000000,
        RV.SpecialFpNums.pos_sat: 0x7FEFFFFFFFFFFFFF,
        RV.SpecialFpNums.neg_sat: 0xFFEFFFFFFFFFFFFF,
    }
    """
    A class that generates random integer and floating point numbers
    """

    def __init__(self, rng: RandNum):
        """Initialize a random number generator for various data types.

        :param rng: Random number generator instance to use
        :type rng: RandNum
        """
        self.rng = rng  # ; RandNum instance

        self.opts: dict[RV.NumGenOps, dict[Union[bool, RV.SpecialFpNums], int]] = {}
        self.opts[RV.NumGenOps.select_special_num] = {True: 5, False: 95}
        self.opts[RV.NumGenOps.special_num] = {
            RV.SpecialFpNums.canonical_nan: 20,
            RV.SpecialFpNums.pos_inf: 10,
            RV.SpecialFpNums.neg_inf: 10,
            RV.SpecialFpNums.pos_zero: 20,
            RV.SpecialFpNums.neg_zero: 20,
            RV.SpecialFpNums.pos_sat: 5,
            RV.SpecialFpNums.neg_sat: 5,
        }
        self.opts[RV.NumGenOps.subnormal] = {True: 10, False: 90}
        self.opts[RV.NumGenOps.fractional] = {True: 100, False: 0}
        self.opts[RV.NumGenOps.low_fidelity] = {True: 0, False: 100}
        self.opts[RV.NumGenOps.nan_box] = {True: 90, False: 10}

    def default_genops(self):
        """Configure generator with safe default options.

        Sets subnormal, low_fidelity, and fractional options to False.
        """
        self.opts[RV.NumGenOps.subnormal] = {False: 100}
        self.opts[RV.NumGenOps.low_fidelity] = {False: 100}
        self.opts[RV.NumGenOps.fractional] = {False: 100}

    def randomize(self) -> dict[RV.NumGenOps, Union[bool, RV.SpecialFpNums]]:
        """Select random options for number generation based on configured weights.

        :return: Dictionary of randomly selected options
        :rtype: dict
        """
        rand_opts: dict[RV.NumGenOps, Union[bool, RV.SpecialFpNums]] = {}
        for opt in self.opts:
            rand_opts[opt] = self.rng.random_choice_weighted(self.opts[opt])
        return rand_opts

    def set_low_fidelity_num(self, data_type: RV.DataType) -> int:
        """Generate a low fidelity floating point number (powers of 2 without mantissa).

        :param data_type: The floating point data type
        :type data_type: RV.DataType
        :return: Integer representation of a low fidelity floating point value
        :rtype: int
        :raises ValueError: If data_type isn't a supported floating point type

        .. code-block:: python

           # Generate a low fidelity FP32 number
           num_gen = NumGen(my_rand)
           fp32_val = num_gen.set_low_fidelity_num(RV.DataType.FP32)
        """
        if data_type == RV.DataType.FP32:
            return self.rng.random_entry_in(self.low_fidelity_fp32)
        elif data_type == RV.DataType.FP16:
            return self.rng.random_entry_in(self.low_fidelity_fp16)
        elif data_type == RV.DataType.FP8:
            return self.rng.random_entry_in(self.low_fidelity_fp8)
        elif data_type == RV.DataType.FP64:
            return self.rng.random_entry_in(self.low_fidelity_fp64)
        else:
            raise ValueError(f"Unsupported data type for low fidelity: {data_type}")

    def set_special_num(self, special_num: RV.SpecialFpNums, data_type: RV.DataType) -> int:
        """Generate a special floating point value.

        :param special_num: Special floating point value type to generate
        :type special_num: RV.SpecialFpNums
        :param data_type: The floating point data type
        :type data_type: RV.DataType
        :return: Integer representation of the requested special value
        :rtype: int
        :raises ValueError: If data_type isn't a supported floating point type

        .. code-block:: python

           # Generate a NaN value in FP16 format
           num_gen = NumGen(my_rand)
           nan_val = num_gen.set_special_num(RV.SpecialFpNums.canonical_nan, RV.DataType.FP16)
        """
        num = 0
        if data_type == RV.DataType.FP8:
            num = self.special_nums_fp8[special_num]
        elif data_type == RV.DataType.FP16:
            num = self.special_nums_fp16[special_num]
        elif data_type == RV.DataType.FP32:
            num = self.special_nums_fp32[special_num]
        elif data_type == RV.DataType.FP64:
            num = self.special_nums_fp64[special_num]
        else:
            raise ValueError(f"Unsupported data type: {data_type}. Should be a floating point type.")
        return num

    def rand_num(self, data_type: RV.DataType) -> int:
        """Generate a random number of the specified data type.

        :param data_type: The data type to generate
        :type data_type: RV.DataType
        :return: Integer representation of a random value
        :rtype: int
        :raises ValueError: If data_type isn't a supported type

        .. code-block:: python

           # Generate a random FP32 value
           num_gen = NumGen(my_rand)
           fp32_val = num_gen.rand_num(RV.DataType.FP32)
        """
        num = 0
        opts: dict[RV.NumGenOps, Union[bool, RV.SpecialFpNums]] = self.randomize()  # Select opts based on weights
        is_int = RV.DataType.is_int(data_type)
        is_fp = RV.DataType.is_fp(data_type)
        # Priority selected mutually exclusive randomization modes
        if opts[RV.NumGenOps.low_fidelity] and not is_int:
            num = self.set_low_fidelity_num(data_type)
        elif opts[RV.NumGenOps.select_special_num] and not is_int:  # FIXME: any special INTs?
            a = opts[RV.NumGenOps.special_num]
            if isinstance(a, RV.SpecialFpNums):
                num = self.set_special_num(a, data_type)
            else:
                raise ValueError(f"Invalid special number: {a}. Should be a SpecialFpNums enum value.")
        elif is_int:
            num = INT(data_type, self.rng)
            num.randomize()
            num = num.val
        elif is_fp:
            num = FP(data_type, self.rng)
            subnormal = bool(opts[RV.NumGenOps.subnormal])
            fractional = bool(opts[RV.NumGenOps.fractional])
            num.randomize(subnormal, fractional)
            num = num.val
        else:
            raise ValueError(f"Unsupported data type: {data_type}. Should be an integer or floating point type.")
        # FIXME: implement or_mask/and_mask. There's functions in lib/rand.py for this
        if opts[RV.NumGenOps.nan_box] and data_type == RV.DataType.FP16:
            num = num | 0xFFFF0000
        return num
