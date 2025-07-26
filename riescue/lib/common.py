# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""
Common module implements common methods used across all the Risecue code.
This includes bit manipulation methods and other common base classes like Singleton
"""

import re
from enum import IntEnum


class BitValueType(IntEnum):
    """
    Enums for bit values
    """

    DISABLED = 0
    ENABLED = 1
    RANDOM = 2


def range1(start, end):
    """
    Return a range that's inclusive of both start and end. The original range()
    method has end excluded
    """
    return range(start, end + 1)


def msb(value):
    """
    Return the MSB bit position, uses bit_length()
    """
    return value.bit_length() - 1


def bitn(value, bit):
    """
    Return value at a bit position (1-bit value)
    """
    return (value >> bit & 1) != 0


def set_bitn(original, bit, value):
    """
    Set bit-value in value
    """
    if value:
        return original | (1 << bit)
    else:
        return original & ~(1 << bit)


def bits(value, bit_hi, bit_lo):
    """
    Return value of a field in value specified by [bit_hi, bit_lo]
    """
    mask = (1 << (bit_hi - bit_lo + 1)) - 1

    return (value & (mask << bit_lo)) >> bit_lo


def set_bits(original_value, bit_hi, bit_lo, value):
    """
    Set value into original_value(bit_hi:bit_lo)
    """
    assert bit_hi > bit_lo

    result = original_value
    mask = (1 << (bit_hi - bit_lo + 1)) - 1
    result &= ~(mask << bit_lo)
    result |= (value & mask) << bit_lo

    return result


def toggle_bit(value, bit):
    """
    Toggle bit in value
    """
    return value ^ (1 << bit)


def format_hex_list(lst):
    """
    Format a list into hex values
    """
    return [f"({', '.join(hex(x) for x in item)})" if isinstance(item, tuple) else hex(item) for item in lst]


def str_to_int(str_val, raise_error=True):
    """
    Convert a dec/hex string number to int (useful for parsing data)
    x = str_to_int('0xf') -> returns 15
    x = str_to_int('0xF') -> returns 15
    x = str_to_int('0fh') -> returns 15
    x = str_to_int('0f') -> returns error
    x = str_to_int('15') -> returns 15
    If raise_error is False, then return original string instead of
    raising error
    """
    s = None

    hex_match_1 = re.match(r"(0?)([0-9a-fA-F]+)(h)", str_val)
    hex_match_2 = re.match(r"(0x)([0-9a-fA-F]+)", str_val)
    dec_match = re.match(r"([0-9]+)", str_val)

    if hex_match_1:
        s = hex_match_1.group(2)
        return int(s, 16)
    elif hex_match_2:
        s = hex_match_2.group(2)
        return int(s, 16)
    elif dec_match:
        s = dec_match.group(1)
        return int(s)
    else:
        if raise_error:
            assert f"{str_val} is not a valid hex or int number"


def is_hex_number(str_val):
    """
    Check if number is either a hexadecimal number
    """
    ret_val = True
    try:
        int(str_val, 16)
    except ValueError:
        ret_val = False

    return ret_val


def is_number(str_val):
    """
    Check if number is either a decimal or hexadecimal number
    """
    if str_val.isdigit() or is_hex_number(str_val):
        return True

    return False


def address_mask_from_size(size):
    """
    Return the address mask for the given pagesize
    """
    return 0xFFFFFFFFFFFFFFFF << (msb(size)) & 0xFFFFFFFFFFFFFFFF


class Singleton(type):
    """
    Generic Singleton Baseless class
        Just specify this as metaclass in the class that you want to be Singleton
    """

    _instance = {}

    def __call__(self, *args, **kwds):
        if self not in self._instance:
            self._instance[self] = super(Singleton, self).__call__(*args, **kwds)

        return self._instance[self]
