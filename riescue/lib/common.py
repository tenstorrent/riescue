# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

"""
Common module implements common methods used across all the Risecue code.
This includes bit manipulation methods and other common base classes like Singleton
"""

from enum import IntEnum
from typing import Any, cast


class BitValueType(IntEnum):
    """
    Enums for bit values
    """

    DISABLED = 0
    ENABLED = 1
    RANDOM = 2


def msb(value: int):
    """
    Return the MSB bit position, uses bit_length()
    """
    return value.bit_length() - 1


def bitn(value: int, bit: int) -> bool:
    """
    Return value at a bit position (1-bit value)
    """
    return (value >> bit & 1) != 0


def set_bitn(original: int, bit: int, value: bool) -> int:
    """
    Set bit-value in value
    """
    if value:
        return original | (1 << bit)
    else:
        return original & ~(1 << bit)


def bits(value: int, bit_hi: int, bit_lo: int) -> int:
    """
    Return value of a field in value specified by [bit_hi, bit_lo]
    """
    mask = (1 << (bit_hi - bit_lo + 1)) - 1

    return (value & (mask << bit_lo)) >> bit_lo


def set_bits(original_value: int, bit_hi: int, bit_lo: int, value: int) -> int:
    """
    Set value into original_value(bit_hi:bit_lo)
    """
    assert bit_hi > bit_lo

    result = original_value
    mask = (1 << (bit_hi - bit_lo + 1)) - 1
    result &= ~(mask << bit_lo)
    result |= (value & mask) << bit_lo

    return result


def is_hex_number(str_val: str) -> bool:
    """
    Check if number is either a hexadecimal number
    """
    ret_val = True
    try:
        int(str_val, 16)
    except ValueError:
        ret_val = False

    return ret_val


def is_number(str_val: str) -> bool:
    """
    Check if number is either a decimal or hexadecimal number
    """
    return str_val.isdigit() or is_hex_number(str_val)


def address_mask_from_size(size: int) -> int:
    """
    Return the address mask for the given pagesize
    """
    return 0xFFFFFFFFFFFFFFFF << (msb(size)) & 0xFFFFFFFFFFFFFFFF


class Singleton(type):
    """
    Generic Singleton Baseless class
        Just specify this as metaclass in the class that you want to be Singleton
    """

    _instance: dict[type[Any], Any] = {}

    def __call__(self, *args: Any, **kwds: Any) -> Any:
        if cast(type[Any], self) not in self._instance:
            self._instance[self] = super(Singleton, self).__call__(*args, **kwds)

        return self._instance[self]
