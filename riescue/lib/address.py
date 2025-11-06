# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
from __future__ import annotations
from dataclasses import dataclass, field
from riescue.lib.enums import AddressType, AddressQualifiers


@dataclass
class Address:
    """
    Data structure to hold address information

    :param name: Name of the address
    :type name: str
    :param address: Address value
    :type address: int
    :param type: Address type
    :type type_: AddressType
    :param qualifiers: List of address qualifiers
    :type qualifiers: list[AddressQualifiers]
    """

    name: str
    address: int
    type: AddressType
    qualifiers: list[AddressQualifiers] = field(default_factory=lambda: [])
