# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import patch

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.lib.addrgen.address_space import AddressSpace
from riescue.dtest_framework.lib.addrgen.types import AddressConstraint


class AddressSpaceTest(unittest.TestCase):
    """
    Test the addrgen module. Target AddressSpace
    """

    def setUp(self):
        self.rng = RandNum(0)

    @patch("riescue.dtest_framework.lib.addrgen.address_space.log")
    def test_address_space(self, mock_log):
        address_space = AddressSpace(self.rng, RV.AddressType.PHYSICAL)
        self.assertEqual(len(address_space.clusters), 64)

        # Allocate 2GiB of RAM
        address_space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, start=0x8000_0000, end=0x8000_0000)
        # address_space.reserve_memory(0x0,  0x200000)
        constraint = AddressConstraint(type=RV.AddressType.PHYSICAL, bits=32, size=0x1000, mask=0xFFFF_FFFF, start=0x8000_0000, qualifiers={RV.AddressQualifiers.ADDRESS_DRAM})
        # address = address_space.generate_address(constraint)
        # Not working. Need some missing setup


if __name__ == "__main__":
    unittest.main(verbosity=2)
