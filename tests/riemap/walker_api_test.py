# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""The walker's ``Page`` / ``PageMap`` carry no consumer bookkeeping.

Consumer-specific state lives with the consumer. RiescueD's name-keyed record
is ``pool.PageInfo``, which read-back fills, and PBMT policy is declared in the
request builder. These tests pin that boundary.
"""

import inspect
import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import PageInfo
from riescue.riemap.config import PagingParams
from riescue.riemap.page_map import Page, PageMap


def _page_map():
    return PageMap(paging_mode=RV.RiscvPagingModes.SV39, featmgr=PagingParams(), addrgen=None)  # type: ignore[arg-type]


class TestWalkerPageHasNoConsumerState(unittest.TestCase):
    _GONE = ("in_private_map", "alias", "no_pbmt_ncio", "size")

    def _page(self):
        return Page(page_map=_page_map(), featmgr=PagingParams(), addrgen=None)  # type: ignore[arg-type]

    def test_the_dropped_members_are_absent(self):
        page = self._page()
        for name in self._GONE:
            self.assertFalse(hasattr(page, name), f"walker Page grew {name} back")

    def test_the_dropped_kwargs_are_rejected(self):
        params = set(inspect.signature(Page.__init__).parameters)
        for name in self._GONE:
            self.assertNotIn(name, params)

    def test_the_consumer_record_owns_that_state(self):
        # Where alias / in_private_map actually live: RiescueD's own page record, filled by
        # generator._readback_allocation from the ParsedPageMapping.
        fields = set(PageInfo.__dataclass_fields__)
        self.assertIn("alias", fields)
        self.assertIn("in_private_map", fields)

    def test_str_still_renders(self):
        page = self._page()
        page.lin_addr = 0x1000
        page.phys_addr = 0x80001000
        self.assertIn("pagesize", str(page))


class TestWalkerPageMapHasNoAddrWidthHelpers(unittest.TestCase):
    def test_addr_bit_helpers_are_gone(self):
        # Address widths are the builder's business (_own_va_bits / physical_addr_bits); the
        # walker never derived one from its own map.
        for name in ("get_linear_addr_bits", "get_physical_addr_bits"):
            self.assertFalse(hasattr(_page_map(), name), f"PageMap grew {name} back")

    def test_empty_set_pagesize_is_gone(self):
        self.assertFalse(hasattr(Page(page_map=_page_map(), featmgr=PagingParams(), addrgen=None), "set_pagesize"))  # type: ignore[arg-type]


class TestWalkerTopologyConflicts(unittest.TestCase):
    def test_existing_leaf_does_not_silently_drop_deeper_mapping(self):
        page_map = _page_map()
        page_map.pinned_sptbr = 0x100000
        page_map.initialize()

        coarse = Page(
            page_map=page_map,
            featmgr=PagingParams(),
            addrgen=None,  # type: ignore[arg-type]
            pagesize=RV.RiscvPageSizes.S2MB,
        )
        coarse.lin_addr = 0x400000
        coarse.phys_addr = 0x80000000
        coarse.pinned_frame_bases[2] = 0x110000

        deeper = Page(
            page_map=page_map,
            featmgr=PagingParams(),
            addrgen=None,  # type: ignore[arg-type]
            pagesize=RV.RiscvPageSizes.S4KB,
        )
        deeper.lin_addr = 0x401000
        deeper.phys_addr = 0x90000000
        deeper.pinned_frame_bases.update({2: 0x110000, 1: 0x120000})
        page_map.add_page(coarse)
        page_map.add_page(deeper)

        with self.assertRaisesRegex(ValueError, "leaf|deeper|conflict"):
            page_map.create_pagetables(RandNum(seed=1))


if __name__ == "__main__":
    unittest.main()
