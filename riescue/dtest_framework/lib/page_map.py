# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Module that will implement paging maps and paging related logic.
This modules is also responsible for making calls to pagetables module to
create pagetables for every page inside the page_map
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional

import riescue.dtest_framework.lib.addrgen as addrgen
import riescue.lib.enums as RV
import riescue.dtest_framework.lib.pagetables as pagetables
from riescue.lib.address import Address
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.lib.addrgen import AddrGen
from riescue.lib.rand import RandNum

import io

if TYPE_CHECKING:
    from riescue.dtest_framework.pool import Pool


log = logging.getLogger(__name__)


class Page:
    """
    Page class models a Page that has a virtual and physical address
    Also, a page belongs to one or more PagingMap
    """

    def __init__(
        self,
        name: str,
        phys_name: str,
        pool: "Pool",
        featmgr: FeatMgr,
        addrgen: AddrGen,
        maps: list[str] = [],  # FIXME: llm thinks this should be optional, I don't think so, double check
        pagesize: RV.RiscvPageSizes = RV.RiscvPageSizes.S4KB,
        gstage_pagesize: RV.RiscvPageSizes = RV.RiscvPageSizes.S4KB,
        in_private_map: bool = False,
        alias: bool = False,
        no_pbmt_ncio: int = 0,
    ):
        self.pool: Pool = pool
        self.featmgr: FeatMgr = featmgr
        self.addrgen: AddrGen = addrgen
        # Determine u-bit value based on the privilege mode
        ubit = self.featmgr.priv_mode == RV.RiscvPrivileges.USER
        # If 2-stage paging is enabled and vs-stage is disabled, we are only dealing with g-stage
        # we need to mark u=1 since all g-stage tablewalks are treated as user
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            ubit = 1
        self.name: str = name
        self.phys_name: str = phys_name
        self.maps: list[str] = maps
        # FIXME: Currently assuming only 'map_os' exists for the entire simulation
        self.map: PageMap = self.pool.get_page_map(map_name=maps[0])

        self.pagesize: RV.RiscvPageSizes = pagesize
        self.gstage_pagesize: RV.RiscvPageSizes = gstage_pagesize
        self.size: int = 0x1000

        self.lin_addr: Optional[int] = None
        self.phys_addr: Optional[int] = None
        self.alias: bool = alias
        self.in_private_map: bool = in_private_map
        self.no_pbmt_ncio: int = no_pbmt_ncio

        self.attrs: dict[str, Optional[int]] = {
            "v": 1,
            "v_level0": 1,
            "v_level1": 1,
            "v_level2": 1,
            "v_level3": 1,
            "v_level4": 1,
            "v_level0_glevel0": 1,
            "v_level0_glevel1": 1,
            "v_level0_glevel2": 1,
            "v_level0_glevel3": 1,
            "v_level0_glevel4": 1,
            "v_level1_glevel0": 1,
            "v_level1_glevel1": 1,
            "v_level1_glevel2": 1,
            "v_level1_glevel3": 1,
            "v_level1_glevel4": 1,
            "v_level2_glevel0": 1,
            "v_level2_glevel1": 1,
            "v_level2_glevel2": 1,
            "v_level2_glevel3": 1,
            "v_level2_glevel4": 1,
            "v_level3_glevel0": 1,
            "v_level3_glevel1": 1,
            "v_level3_glevel2": 1,
            "v_level3_glevel3": 1,
            "v_level3_glevel4": 1,
            "v_level4_glevel0": 1,
            "v_level4_glevel1": 1,
            "v_level4_glevel2": 1,
            "v_level4_glevel3": 1,
            "v_level4_glevel4": 1,
            "a": None,
            "a_level0": 1,
            "a_level1": 0,
            "a_level2": 0,
            "a_level3": 0,
            "a_level4": 0,
            "a_level0_glevel0": 1,
            "a_level0_glevel1": 0,
            "a_level0_glevel2": 0,
            "a_level0_glevel3": 0,
            "a_level0_glevel4": 0,
            "a_level1_glevel0": 1,
            "a_level1_glevel1": 0,
            "a_level1_glevel2": 0,
            "a_level1_glevel3": 0,
            "a_level1_glevel4": 0,
            "a_level2_glevel0": 1,
            "a_level2_glevel1": 0,
            "a_level2_glevel2": 0,
            "a_level2_glevel3": 0,
            "a_level2_glevel4": 0,
            "a_level3_glevel0": 1,
            "a_level3_glevel1": 0,
            "a_level3_glevel2": 0,
            "a_level3_glevel3": 0,
            "a_level3_glevel4": 0,
            "a_level4_glevel0": 1,
            "a_level4_glevel1": 0,
            "a_level4_glevel2": 0,
            "a_level4_glevel3": 0,
            "a_level4_glevel4": 0,
            "d": None,
            "d_level0": 1,
            "d_level1": 0,
            "d_level2": 0,
            "d_level3": 0,
            "d_level4": 0,
            "d_level0_glevel0": 1,
            "d_level0_glevel1": 0,
            "d_level0_glevel2": 0,
            "d_level0_glevel3": 0,
            "d_level0_glevel4": 0,
            "d_level1_glevel0": 1,
            "d_level1_glevel1": 0,
            "d_level1_glevel2": 0,
            "d_level1_glevel3": 0,
            "d_level1_glevel4": 0,
            "d_level2_glevel0": 1,
            "d_level2_glevel1": 0,
            "d_level2_glevel2": 0,
            "d_level2_glevel3": 0,
            "d_level2_glevel4": 0,
            "d_level3_glevel0": 1,
            "d_level3_glevel1": 0,
            "d_level3_glevel2": 0,
            "d_level3_glevel3": 0,
            "d_level3_glevel4": 0,
            "d_level4_glevel0": 1,
            "d_level4_glevel1": 0,
            "d_level4_glevel2": 0,
            "d_level4_glevel3": 0,
            "d_level4_glevel4": 0,
            "r": 1,
            "r_level0": 1,
            "r_level1": 0,
            "r_level2": 0,
            "r_level3": 0,
            "r_level4": 0,
            "r_level0_glevel0": 1,
            "r_level0_glevel1": 0,
            "r_level0_glevel2": 0,
            "r_level0_glevel3": 0,
            "r_level0_glevel4": 0,
            "r_level1_glevel0": 1,
            "r_level1_glevel1": 0,
            "r_level1_glevel2": 0,
            "r_level1_glevel3": 0,
            "r_level1_glevel4": 0,
            "r_level2_glevel0": 1,
            "r_level2_glevel1": 0,
            "r_level2_glevel2": 0,
            "r_level2_glevel3": 0,
            "r_level2_glevel4": 0,
            "r_level3_glevel0": 1,
            "r_level3_glevel1": 0,
            "r_level3_glevel2": 0,
            "r_level3_glevel3": 0,
            "r_level3_glevel4": 0,
            "r_level4_glevel0": 1,
            "r_level4_glevel1": 0,
            "r_level4_glevel2": 0,
            "r_level4_glevel3": 0,
            "r_level4_glevel4": 0,
            "w": 1,
            "w_level0": 1,
            "w_level1": 0,
            "w_level2": 0,
            "w_level3": 0,
            "w_level4": 0,
            "w_level0_glevel0": 1,
            "w_level0_glevel1": 0,
            "w_level0_glevel2": 0,
            "w_level0_glevel3": 0,
            "w_level0_glevel4": 0,
            "w_level1_glevel0": 1,
            "w_level1_glevel1": 0,
            "w_level1_glevel2": 0,
            "w_level1_glevel3": 0,
            "w_level1_glevel4": 0,
            "w_level2_glevel0": 1,
            "w_level2_glevel1": 0,
            "w_level2_glevel2": 0,
            "w_level2_glevel3": 0,
            "w_level2_glevel4": 0,
            "w_level3_glevel0": 1,
            "w_level3_glevel1": 0,
            "w_level3_glevel2": 0,
            "w_level3_glevel3": 0,
            "w_level3_glevel4": 0,
            "w_level4_glevel0": 1,
            "w_level4_glevel1": 0,
            "w_level4_glevel2": 0,
            "w_level4_glevel3": 0,
            "w_level4_glevel4": 0,
            "x": 1,
            "x_level0": 1,
            "x_level1": 0,
            "x_level2": 0,
            "x_level3": 0,
            "x_level4": 0,
            "x_level0_glevel0": 1,
            "x_level0_glevel1": 0,
            "x_level0_glevel2": 0,
            "x_level0_glevel3": 0,
            "x_level0_glevel4": 0,
            "x_level1_glevel0": 1,
            "x_level1_glevel1": 0,
            "x_level1_glevel2": 0,
            "x_level1_glevel3": 0,
            "x_level1_glevel4": 0,
            "x_level2_glevel0": 1,
            "x_level2_glevel1": 0,
            "x_level2_glevel2": 0,
            "x_level2_glevel3": 0,
            "x_level2_glevel4": 0,
            "x_level3_glevel0": 1,
            "x_level3_glevel1": 0,
            "x_level3_glevel2": 0,
            "x_level3_glevel3": 0,
            "x_level3_glevel4": 0,
            "x_level4_glevel0": 1,
            "x_level4_glevel1": 0,
            "x_level4_glevel2": 0,
            "x_level4_glevel3": 0,
            "x_level4_glevel4": 0,
            "u": ubit,
            "u_level0": ubit,
            "u_level1": 0,
            "u_level2": 0,
            "u_level3": 0,
            "u_level4": 0,
            "u_level0_glevel0": 1,
            "u_level0_glevel1": 0,
            "u_level0_glevel2": 0,
            "u_level0_glevel3": 0,
            "u_level0_glevel4": 0,
            "u_level1_glevel0": 1,
            "u_level1_glevel1": 0,
            "u_level1_glevel2": 0,
            "u_level1_glevel3": 0,
            "u_level1_glevel4": 0,
            "u_level2_glevel0": 1,
            "u_level2_glevel1": 0,
            "u_level2_glevel2": 0,
            "u_level2_glevel3": 0,
            "u_level2_glevel4": 0,
            "u_level3_glevel0": 1,
            "u_level3_glevel1": 0,
            "u_level3_glevel2": 0,
            "u_level3_glevel3": 0,
            "u_level3_glevel4": 0,
            "u_level4_glevel0": 1,
            "u_level4_glevel1": 0,
            "u_level4_glevel2": 0,
            "u_level4_glevel3": 0,
            "u_level4_glevel4": 0,
            "g": 0,
            "g_level0": 0,
            "g_level1": 0,
            "g_level2": 0,
            "g_level3": 0,
            "g_level4": 0,
            "g_level0_glevel0": 0,
            "g_level0_glevel1": 0,
            "g_level0_glevel2": 0,
            "g_level0_glevel3": 0,
            "g_level0_glevel4": 0,
            "g_level1_glevel0": 0,
            "g_level1_glevel1": 0,
            "g_level1_glevel2": 0,
            "g_level1_glevel3": 0,
            "g_level1_glevel4": 0,
            "g_level2_glevel0": 0,
            "g_level2_glevel1": 0,
            "g_level2_glevel2": 0,
            "g_level2_glevel3": 0,
            "g_level2_glevel4": 0,
            "g_level3_glevel0": 0,
            "g_level3_glevel1": 0,
            "g_level3_glevel2": 0,
            "g_level3_glevel3": 0,
            "g_level3_glevel4": 0,
            "g_level4_glevel0": 0,
            "g_level4_glevel1": 0,
            "g_level4_glevel2": 0,
            "g_level4_glevel3": 0,
            "g_level4_glevel4": 0,
            "gstage_g": 0,
            "gstage_g_level0": 0,
            "gstage_g_level1": 0,
            "gstage_g_level2": 0,
            "gstage_g_level3": 0,
            "gstage_g_level4": 0,
            "rsw": 0,
            "rsw_level0": 0,
            "rsw_level1": 0,
            "rsw_level2": 0,
            "rsw_level3": 0,
            "rsw_level4": 0,
            "gstage_rsw": 0,
            "gstage_rsw_level0": 0,
            "gstage_rsw_level1": 0,
            "gstage_rsw_level2": 0,
            "gstage_rsw_level3": 0,
            "gstage_rsw_level4": 0,
            "reserved": 0,
            "reserved_level0": 0,
            "reserved_level1": 0,
            "reserved_level2": 0,
            "reserved_level3": 0,
            "reserved_level4": 0,
            "gstage_reserved": 0,
            "gstage_reserved_level0": 0,
            "gstage_reserved_level1": 0,
            "gstage_reserved_level2": 0,
            "gstage_reserved_level3": 0,
            "gstage_reserved_level4": 0,
            "pbmt": 0,
            "gstage_pbmt": 0,
            "n": 0,
            "secure": 0,
            "gstage_n": 0,
            "modify_pt": 0,
            "gstage_modify_pt": 0,
        }

        # G-stage paging attributes, only added to satisfy lint errors in pagetables.py L757
        # should be revisited, see FIXME in same location
        self.gstage_vs_leaf_address_size: Optional[int] = None
        self.gstage_vs_leaf_address_mask: Optional[int] = None
        self.gstage_vs_leaf_pagesize: Optional[RV.RiscvPageSizes] = None
        self.gstage_vs_nonleaf_address_size: Optional[int] = None
        self.gstage_vs_nonleaf_address_mask: Optional[int] = None
        self.gstage_vs_nonleaf_pagesize: Optional[RV.RiscvPageSizes] = None

        self.pt_entries: list[pagetables.PTEntry] = list()

    def __str__(self) -> str:
        s = f"Page: {self.name}:\n"
        s += f"    phys_name: {self.phys_name}\n"
        if self.lin_addr is not None:
            s += f"    lin_addr: {self.lin_addr:016x}\n"
        if self.phys_addr is not None:
            s += f"    phys_addr: {self.phys_addr:016x}\n"
        s += f"    size: {self.size:x}\n"
        s += f"    pagesize: {self.pagesize}\n"
        s += f"    in_private_map: {self.in_private_map}\n"

        s += "    "
        for attr, val in self.attrs.items():
            s += f"{attr}: {val}, "

        return s

    def set_pagetable_levels(self, page_map: PageMap) -> None:
        """
        Default levels is same as number of levels for the PageMap
        TODO:
        Also change the levels based on the pagesize
        """
        self.max_levels = page_map.max_levels

        # stop_level() provides how many levels to stop at
        self.pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(self.pagesize)

    def set_pagesize(self) -> None:  # FIXME: why does this exist?
        """
        Set pagesize based on
          - the pagesize attribute
          - agreeable pagesize, if Page exists in multiple pagemaps
        """
        pass

    def create_pagetables(self, rng: RandNum, page_map: PageMap) -> None:
        """
        Create pagetables by using lower level of Pagetable class
        """
        self.set_pagetable_levels(page_map=page_map)

        pt = pagetables.Pagetables(page=self, page_map=page_map, pool=self.pool, featmgr=self.featmgr, addrgen=self.addrgen)
        pt.create_pagetables(rng=rng)

    def insert_pt(self, pt_entry: pagetables.PTEntry) -> None:
        self.pt_entries.append(pt_entry)


class PageMap:
    def __init__(self, name: str, paging_mode: RV.RiscvPagingModes, pool: "Pool", featmgr: FeatMgr, addrgen: AddrGen, g_map: bool = False):
        self.name: str = name
        self.paging_mode: RV.RiscvPagingModes = paging_mode
        self.g_map: bool = g_map

        self.pool: "Pool" = pool
        self.featmgr: FeatMgr = featmgr
        self.addrgen: AddrGen = addrgen

        # Set the pagetable levels based on the paging_mode
        self.max_levels: int = RV.RiscvPagingModes.max_levels(self.paging_mode)

        # Store the pages related to this map
        self.pages: dict[str, Page] = dict()  # 'name' -> Page()
        # Dict to hold pages that are used for modifying the pagetable pages
        # These pages are merged to self.pages once the self.create_pagetables() is called
        self.pt_pages: dict[str, Page] = dict()
        self.base_addr: Optional[int] = None
        self.basetable: Optional[pagetables.PTTable] = None  # Set to PTTable after initialize()

    def initialize(self) -> None:
        # Create the sptrb page
        self.create_sptbr()

        # Also create the base table
        self.create_base_table()

    def get_linear_addr_bits(self) -> int:
        """
        Return the number of bits for the linear address
        """
        return RV.RiscvPagingModes.linear_addr_bits(self.paging_mode)

    def get_physical_addr_bits(self) -> int:
        """
        Return the number of bits for the physical address
        """
        return RV.RiscvPagingModes.physical_addr_bits(self.paging_mode)

    # Add raw page to this map (used for creating pagetables for pagetables)
    def add_raw_pt_page(
        self,
        linear_name: str,
        physical_name: str,
        linear_addr: int,
        physical_addr: int,
        attrs: Optional[dict[str, int]] = None,
        pagesize: RV.RiscvPageSizes = RV.RiscvPageSizes.S4KB,
    ) -> None:
        if attrs is None:  # FIXME: added this as a fallback, do we want the user to have to pass in a blank dict instead and remove "optional"?
            attrs = dict()

        log.debug(f"Adding raw page: {linear_name} -> {physical_name}, linear_addr: {linear_addr:x}, physical_addr: {physical_addr:x}, map: {self.name}")
        page = Page(
            name=linear_name,
            phys_name=physical_name,
            pool=self.pool,
            featmgr=self.featmgr,
            addrgen=self.addrgen,
            maps=["map_os"],
            pagesize=pagesize,
        )
        page.lin_addr = linear_addr
        page.phys_addr = physical_addr

        for attr, val in attrs.items():
            page.attrs[attr] = val

        # Make sure to mark u=1 in the user mode
        if self.featmgr.priv_mode == RV.RiscvPrivileges.USER:
            page.attrs["u"] = 1  # always user

        self.add_pt_page(page=page)

        # Create random linear address
        addr_inst = Address(name=linear_name, type=RV.AddressType.LINEAR, address=linear_addr)
        self.pool.add_random_addr(addr_name=linear_name, addr=addr_inst)

        # Create random physical address
        addr_inst = Address(name=physical_name, type=RV.AddressType.PHYSICAL, address=physical_addr)
        self.pool.add_random_addr(addr_name=physical_name, addr=addr_inst)

    def add_page(self, page: Page) -> None:
        page_name = page.name
        self.pages[page_name] = page

    def add_pt_page(self, page: Page) -> None:
        page_name = page.name
        self.pt_pages[page_name] = page

    def get_page(self, name: str) -> Page:
        return self.pages[name]

    def get_basetable(self) -> pagetables.PTTable:
        """
        Return the pointer to the base of the pagetable for this map
        """
        if self.basetable is None:  # FIXME: added this as a fallback to make sure basetable is set, satisfies other type hinting
            raise ValueError(f"PageMap {self.name} basetable is None - initialize() must be called before accessing basetable")
        return self.basetable

    def create_sptbr(self) -> None:
        """
        Create a new page for the SPTBR
        """
        linear_name = self.name + "_sptbr_lin"
        physical_name = self.name + "_sptbr"

        lin_addr_c = addrgen.AddressConstraint(
            type=RV.AddressType.LINEAR,
            bits=32,
            mask=0xFFFFFFFFFFE00000,
            size=0x200000,
        )
        lin_addr = self.addrgen.generate_address(constraint=lin_addr_c)

        # Default root page table is at least 4KB aligned
        phys_mask = 0xFFFFFFFFFFFFE000
        size = 0x4000
        # Hypervisor root page table is at least 16KB aligned
        if self.g_map:
            phys_mask = 0xFFFFFFFFFFE00000
            size = 0x200000
        phys_addr_c = addrgen.AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
            bits=32,  # TODO: Change to 64 if 32bit requirement is not a must.
            mask=phys_mask,
            size=size,
        )
        self.sptbr = self.addrgen.generate_address(constraint=phys_addr_c)

        # Create and add sptbr page to Pool
        maps_to_add = ["map_os"]

        sptbr_page = Page(
            name=linear_name,
            phys_name=physical_name,
            pool=self.pool,
            featmgr=self.featmgr,
            addrgen=self.addrgen,
            maps=maps_to_add,
        )
        # sptbr_page.lin_addr = lin_addr
        sptbr_page.lin_addr = self.sptbr
        sptbr_page.phys_addr = self.sptbr
        self.pool.add_page(page=sptbr_page, map_names=maps_to_add)
        addr_inst = Address(name=physical_name, type=RV.AddressType.PHYSICAL, address=self.sptbr)
        self.pool.add_random_addr(addr_name=physical_name, addr=addr_inst)

    def create_base_table(self) -> None:
        self.basetable = pagetables.PTTable(self.sptbr)

    def create_pagetables(self, rng: RandNum) -> None:
        """
        For every page in this pagemap, create pagetables
        """
        for page_name in self.pages:
            page = self.pool.get_page(page_name=page_name, map_name=self.name)
            page.create_pagetables(rng=rng, page_map=self)

        # The above method might have added some additional pages to modify the pagetables themselves
        # if the modify_pt=1 is present in the ;page_mapping()
        # So, we need to iterate through the self.pt_pages and create pagetables for the new pages
        for page_name, page in self.pt_pages.items():
            self.pool.add_page(page=page, map_names=[self.name])
            page = self.pool.get_page(page_name=page_name, map_name=self.name)
            page.create_pagetables(rng=rng, page_map=self)

    def print_pagetables(self, file_handle: io.TextIOWrapper) -> None:
        self._print_pagetables_helper(file_handle=file_handle)
        self.print_pagetables_per_page(file_handle=file_handle)

    def _print_pagetables_helper(self, file_handle: io.TextIOWrapper, basetable: Optional[pagetables.PTTable] = None) -> None:
        """
        - find each table recursively
        - once you find a table, print each entry from that table
        """
        if basetable is None:
            if self.basetable is None:  # FIXME: added this as a fallback to make sure basetable is set, satisfies other type hinting
                raise ValueError(f"PageMap {self.name} basetable is None - initialize() must be called before print_pagetables()")
            basetable = self.basetable
        # TODO: Work in progress
        # for pt_entry in basetable.get_entries():
        #     base_addr = pt_entry.basetable.base_addr
        #     self.print_pagetables(pt_entry.basetable)

        # Print the table itself
        self._print_table_entries(file_handle=file_handle, table=basetable)
        for pt_entry in basetable.get_entries():
            self._print_pagetables_helper(file_handle=file_handle, basetable=pt_entry.basetable)

    def _print_table_entries(self, file_handle: io.TextIOWrapper, table: pagetables.PTTable) -> None:
        if not table.leaf:
            # print(f'.org 0x{table.base_addr:016x}')
            section_name = f"__pagetable_{self.name}_0x{table.base_addr:016x}"
            file_handle.write(f'.section .{section_name}, "aw"\n')
            self.pool.add_section(section_name=section_name, address=table.base_addr)
            file_handle.write(f"{section_name}:\n.globl {section_name}\n")

        for index, pt_entry in sorted(table.table.items()):
            # We need to sort the pt_entries by index so the gcc does not complain about backwords .org
            base_addr = pt_entry.basetable.base_addr
            entry_size = RV.RiscvPagingModes.pt_entry_size(mode=self.paging_mode)
            entry_size_str = "" if (entry_size == 0) else f"{entry_size}"
            offset = entry_size * index
            # BOZO: For some reason sometimes gcc doesn't like ".org 0" and complains about "moving org backwards"
            file_handle.write(f"    .org 0x{offset:x}\n")
            file_handle.write(f"        .{entry_size_str}byte 0x{pt_entry.get_value():016x}\n")
            file_handle.write(f"            #level: {pt_entry.level}: index: 0x{index:x}, base_addr: 0x{base_addr:016x}, value: {pt_entry.get_value():016x}\n")
            file_handle.write(f"            #attrs: {pt_entry.pt_attr}\n")

    def print_pagetables_per_page(self, file_handle: io.TextIOWrapper) -> None:
        file_handle.write("\n#==========================================================\n")
        file_handle.write(f"#printing pagetables for debug: map: {self.name}, base_addr: {self.sptbr:016x}, paging_mode: {self.paging_mode}\n")
        file_handle.write("#==========================================================")
        for page in self.pages.values():
            file_handle.write(f"\n#Page: {page.name}, 0x{page.lin_addr:016x} -> {page.phys_name}, 0x{page.phys_addr:016x}, pagesize:{page.pagesize}\n")
            for pt_entry in page.pt_entries:
                file_handle.write(f"#level: {pt_entry.level}: base_addr: 0x{pt_entry.get_base_addr():016x}, value: {pt_entry.get_value():016x}, attr: {pt_entry.pt_attr}, map: \n")
