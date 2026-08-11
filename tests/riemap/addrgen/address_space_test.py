# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import patch

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.addrgen.address_space import AddressSpace
from riescue.riemap.addrgen.types import AddressConstraint


class AddressSpaceTest(unittest.TestCase):
    """
    Test the addrgen module. Target AddressSpace
    """

    def setUp(self):
        self.rng = RandNum(0)

    @patch("riescue.riemap.addrgen.address_space.log")
    def test_address_space(self, mock_log):
        address_space = AddressSpace(self.rng, RV.AddressType.PHYSICAL)
        self.assertEqual(len(address_space.clusters), 64)

        address_space.define_segment(
            RV.AddressQualifiers.ADDRESS_DRAM,
            start=0x8000_0000,
            end=0x8000_0FFF,
        )
        constraint = AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            bits=32,
            size=0x1000,
            mask=0xFFFF_FFFF,
            start=0x8000_0000,
            end=0x8000_0FFF,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
        )
        self.assertEqual(address_space.generate_address(constraint), 0x8000_0000)

    def test_window_at_address_zero_is_reachable(self):
        """Address 0 belongs to a cluster, so a window anchored there can still be drawn."""
        address_space = AddressSpace(self.rng, RV.AddressType.LINEAR)
        address_space.define_segment(
            RV.AddressQualifiers.ADDRESS_LINEAR,
            start=0,
            end=0x1FFF,
        )
        constraint = AddressConstraint(
            type=RV.AddressType.LINEAR,
            bits=64,
            size=0x20,
            mask=0xFFFF_FFFF_FFFF_FFFF,
            start=0,
            end=0x1F,
            qualifiers={RV.AddressQualifiers.ADDRESS_LINEAR},
        )

        self.assertEqual(address_space.generate_address(constraint), 0)

    def test_generation_may_span_adjacent_clusters(self):
        address_space = AddressSpace(self.rng, RV.AddressType.PHYSICAL)
        address_space.define_segment(
            RV.AddressQualifiers.ADDRESS_DRAM,
            start=0xF00,
            end=0x10FF,
        )
        constraint = AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            bits=16,
            size=0x200,
            mask=0xFFFF,
            start=0xF00,
            end=0x10FF,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
        )

        self.assertEqual(address_space.generate_address(constraint), 0xF00)


if __name__ == "__main__":
    unittest.main(verbosity=2)
