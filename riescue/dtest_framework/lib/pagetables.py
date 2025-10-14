# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""
Low level module that's responsible for creating pagetables.
  inputs:
    - virtual address
    - physical addres
    - paging_map
  output:
    - pagetables in the pagetables object
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

This module is also responsible for setting up the page attributes for every
page
"""

import collections
import logging
from typing import TYPE_CHECKING, Optional

import riescue.dtest_framework.lib.addrgen as addrgen
import riescue.lib.common as common
import riescue.lib.enums as RV
import riescue.lib.raw_attributes as raw_attributes
from riescue.lib.rand import RandNum
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.lib.addrgen import AddrGen

if TYPE_CHECKING:
    from riescue.dtest_framework.pool import Pool
    from riescue.dtest_framework.lib.page_map import Page, PageMap

log = logging.getLogger(__name__)


class PTTable:
    """
    Actually holds all the pagetables entries and pointer to the next table
    """

    def __init__(self, base_addr: int, leaf: bool = False):
        self.base_addr: int = base_addr
        self.leaf: bool = leaf
        self.table: collections.OrderedDict[int, PTEntry] = collections.OrderedDict()

    def __str__(self) -> str:
        strn = ""
        for index, entry in self.table.items():
            strn += f"index: 0x{index:x} -> {self.get_entry(index).get_value():x}\n"

        return strn

    def insert_entry(self, entry: "PTEntry", index: int) -> None:
        """
        Insert an entry at given index
        """
        self.table[index] = entry

    def entry_exists(self, index: int) -> bool:
        if index in self.table:
            return True
        else:
            return False

    def get_entry(self, index: int) -> "PTEntry":
        """
        Return the entry at given index
        """
        return self.table[index]

    def get_entries(self):  # TODO: how to annotate this?
        return self.table.values()


class PTAttrs(raw_attributes.RawAttributes):
    """
    Model pagetable attributes for a given page
    """

    # FIXME: can we have int defaults here instead of None?
    base_attrs: dict[str, Optional[int]] = {  # TODO: is this correct?
        "v": None,
        "v_level0": 1,
        "v_level1": 1,
        "v_level2": 1,
        "v_level3": 1,
        "v_level4": 1,
        "v_level4_glevel0": 1,
        "v_level4_glevel1": 1,
        "v_level4_glevel2": 1,
        "v_level4_glevel3": 1,
        "v_level4_glevel4": 1,
        "v_level3_glevel0": 1,
        "v_level3_glevel1": 1,
        "v_level3_glevel2": 1,
        "v_level3_glevel3": 1,
        "v_level3_glevel4": 1,
        "v_level2_glevel0": 1,
        "v_level2_glevel1": 1,
        "v_level2_glevel2": 1,
        "v_level2_glevel3": 1,
        "v_level2_glevel4": 1,
        "v_level1_glevel0": 1,
        "v_level1_glevel1": 1,
        "v_level1_glevel2": 1,
        "v_level1_glevel3": 1,
        "v_level1_glevel4": 1,
        "v_level0_glevel0": 1,
        "v_level0_glevel1": 1,
        "v_level0_glevel2": 1,
        "v_level0_glevel3": 1,
        "v_level0_glevel4": 1,
        "a": None,
        "a_level0": 0,
        "a_level1": 0,
        "a_level2": 0,
        "a_level3": 0,
        "a_level4": 0,
        "a_level4_glevel0": 1,
        "a_level4_glevel1": 0,
        "a_level4_glevel2": 0,
        "a_level4_glevel3": 0,
        "a_level4_glevel4": 0,
        "a_level3_glevel0": 1,
        "a_level3_glevel1": 0,
        "a_level3_glevel2": 0,
        "a_level3_glevel3": 0,
        "a_level3_glevel4": 0,
        "a_level2_glevel0": 1,
        "a_level2_glevel1": 0,
        "a_level2_glevel2": 0,
        "a_level2_glevel3": 0,
        "a_level2_glevel4": 0,
        "a_level1_glevel0": 1,
        "a_level1_glevel1": 0,
        "a_level1_glevel2": 0,
        "a_level1_glevel3": 0,
        "a_level1_glevel4": 0,
        "a_level0_glevel0": 1,
        "a_level0_glevel1": 0,
        "a_level0_glevel2": 0,
        "a_level0_glevel3": 0,
        "a_level0_glevel4": 0,
        "d": None,
        "d_level0": 0,
        "d_level1": 0,
        "d_level2": 0,
        "d_level3": 0,
        "d_level4": 0,
        "d_level4_glevel0": 1,
        "d_level4_glevel1": 0,
        "d_level4_glevel2": 0,
        "d_level4_glevel3": 0,
        "d_level4_glevel4": 0,
        "d_level3_glevel0": 1,
        "d_level3_glevel1": 0,
        "d_level3_glevel2": 0,
        "d_level3_glevel3": 0,
        "d_level3_glevel4": 0,
        "d_level2_glevel0": 1,
        "d_level2_glevel1": 0,
        "d_level2_glevel2": 0,
        "d_level2_glevel3": 0,
        "d_level2_glevel4": 0,
        "d_level1_glevel0": 1,
        "d_level1_glevel1": 0,
        "d_level1_glevel2": 0,
        "d_level1_glevel3": 0,
        "d_level1_glevel4": 0,
        "d_level0_glevel0": 1,
        "d_level0_glevel1": 0,
        "d_level0_glevel2": 0,
        "d_level0_glevel3": 0,
        "d_level0_glevel4": 0,
        "r": 1,
        "r_level0": 1,
        "r_level1": 0,
        "r_level2": 0,
        "r_level3": 0,
        "r_level4": 0,
        "r_level4_glevel0": 1,
        "r_level4_glevel1": 0,
        "r_level4_glevel2": 0,
        "r_level4_glevel3": 0,
        "r_level4_glevel4": 0,
        "r_level3_glevel0": 1,
        "r_level3_glevel1": 0,
        "r_level3_glevel2": 0,
        "r_level3_glevel3": 0,
        "r_level3_glevel4": 0,
        "r_level2_glevel0": 1,
        "r_level2_glevel1": 0,
        "r_level2_glevel2": 0,
        "r_level2_glevel3": 0,
        "r_level2_glevel4": 0,
        "r_level1_glevel0": 1,
        "r_level1_glevel1": 0,
        "r_level1_glevel2": 0,
        "r_level1_glevel3": 0,
        "r_level1_glevel4": 0,
        "r_level0_glevel0": 1,
        "r_level0_glevel1": 0,
        "r_level0_glevel2": 0,
        "r_level0_glevel3": 0,
        "r_level0_glevel4": 0,
        "w": 1,
        "w_level0": 1,
        "w_level1": 0,
        "w_level2": 0,
        "w_level3": 0,
        "w_level4": 0,
        "w_level4_glevel0": 1,
        "w_level4_glevel1": 0,
        "w_level4_glevel2": 0,
        "w_level4_glevel3": 0,
        "w_level4_glevel4": 0,
        "w_level3_glevel0": 1,
        "w_level3_glevel1": 0,
        "w_level3_glevel2": 0,
        "w_level3_glevel3": 0,
        "w_level3_glevel4": 0,
        "w_level2_glevel0": 1,
        "w_level2_glevel1": 0,
        "w_level2_glevel2": 0,
        "w_level2_glevel3": 0,
        "w_level2_glevel4": 0,
        "w_level1_glevel0": 1,
        "w_level1_glevel1": 0,
        "w_level1_glevel2": 0,
        "w_level1_glevel3": 0,
        "w_level1_glevel4": 0,
        "w_level0_glevel0": 1,
        "w_level0_glevel1": 0,
        "w_level0_glevel2": 0,
        "w_level0_glevel3": 0,
        "w_level0_glevel4": 0,
        "x": 1,
        "x_level0": 1,
        "x_level1": 0,
        "x_level2": 0,
        "x_level3": 0,
        "x_level4": 0,
        "x_level4_glevel0": 1,
        "x_level4_glevel1": 0,
        "x_level4_glevel2": 0,
        "x_level4_glevel3": 0,
        "x_level4_glevel4": 0,
        "x_level3_glevel0": 1,
        "x_level3_glevel1": 0,
        "x_level3_glevel2": 0,
        "x_level3_glevel3": 0,
        "x_level3_glevel4": 0,
        "x_level2_glevel0": 1,
        "x_level2_glevel1": 0,
        "x_level2_glevel2": 0,
        "x_level2_glevel3": 0,
        "x_level2_glevel4": 0,
        "x_level1_glevel0": 1,
        "x_level1_glevel1": 0,
        "x_level1_glevel2": 0,
        "x_level1_glevel3": 0,
        "x_level1_glevel4": 0,
        "x_level0_glevel0": 1,
        "x_level0_glevel1": 0,
        "x_level0_glevel2": 0,
        "x_level0_glevel3": 0,
        "x_level0_glevel4": 0,
        "u": 0,
        "u_level0": 0,
        "u_level1": 0,
        "u_level2": 0,
        "u_level3": 0,
        "u_level4": 0,
        "u_level4_glevel0": 1,
        "u_level4_glevel1": 0,
        "u_level4_glevel2": 0,
        "u_level4_glevel3": 0,
        "u_level4_glevel4": 0,
        "u_level3_glevel0": 1,
        "u_level3_glevel1": 0,
        "u_level3_glevel2": 0,
        "u_level3_glevel3": 0,
        "u_level3_glevel4": 0,
        "u_level2_glevel0": 1,
        "u_level2_glevel1": 0,
        "u_level2_glevel2": 0,
        "u_level2_glevel3": 0,
        "u_level2_glevel4": 0,
        "u_level1_glevel0": 1,
        "u_level1_glevel1": 0,
        "u_level1_glevel2": 0,
        "u_level1_glevel3": 0,
        "u_level1_glevel4": 0,
        "u_level0_glevel0": 1,
        "u_level0_glevel1": 0,
        "u_level0_glevel2": 0,
        "u_level0_glevel3": 0,
        "u_level0_glevel4": 0,
        "g": 0,
        "g_level0": 0,
        "g_level1": 0,
        "g_level2": 0,
        "g_level3": 0,
        "g_level4": 0,
        "g_level4_glevel0": 0,
        "g_level4_glevel1": 0,
        "g_level4_glevel2": 0,
        "g_level4_glevel3": 0,
        "g_level4_glevel4": 0,
        "g_level3_glevel0": 0,
        "g_level3_glevel1": 0,
        "g_level3_glevel2": 0,
        "g_level3_glevel3": 0,
        "g_level3_glevel4": 0,
        "g_level2_glevel0": 0,
        "g_level2_glevel1": 0,
        "g_level2_glevel2": 0,
        "g_level2_glevel3": 0,
        "g_level2_glevel4": 0,
        "g_level1_glevel0": 0,
        "g_level1_glevel1": 0,
        "g_level1_glevel2": 0,
        "g_level1_glevel3": 0,
        "g_level1_glevel4": 0,
        "g_level0_glevel0": 0,
        "g_level0_glevel1": 0,
        "g_level0_glevel2": 0,
        "g_level0_glevel3": 0,
        "g_level0_glevel4": 0,
        "rsw": 0,
        "rsw_level0": 0,
        "rsw_level1": 0,
        "rsw_level2": 0,
        "rsw_level3": 0,
        "rsw_level4": 0,
        "reserved": 0,
        "reserved_level0": 0,
        "reserved_level1": 0,
        "reserved_level2": 0,
        "reserved_level3": 0,
        "reserved_level4": 0,
        "pbmt": 0,
        "n": 0,
        "secure": 0,
        "gstage_n": 0,
        "modify_pt": 0,
        "gstage_modify_pt": 0,
    }

    def __init__(self, rng: RandNum, featmgr: FeatMgr, level: int, page: "Optional[Page]" = None, leaf: bool = False):
        super().__init__(valid_attrs=PTAttrs.base_attrs)
        self.featmgr: FeatMgr = featmgr
        self.leaf: bool = leaf

        if page is not None:
            for attr_name in page.attrs.keys():
                # print(f'pagetable: {page.name}, attr_name: {attr_name}, value: {page.attrs[attr_name]}')
                value = page.attrs[attr_name]  # FIXME: bandaid fix for None values in page.attrs, do we want to allow None values?
                if value is not None:
                    self.__setattr__(attr_name, value)

        # Now set the level specific attribute value from attr_level{level} values
        for attr in ["v", "a", "d", "r", "w", "x", "g", "u", "rsw", "reserved", "secure"]:
            if leaf or (attr == "secure"):
                if attr == "a" or attr == "d":
                    # If value of a is None, then randomize based on svadu is enabled
                    if self.__getattribute__(attr) is None:
                        if self.featmgr.svadu:
                            # 50% of chance to set a=1 else a=0
                            self.__setattr__(attr, 1 if rng.with_probability_of(50) else 0)
                        else:
                            # If svadu is not enabled, then set a=1, d=1
                            self.__setattr__(attr, 1)
                self.__setattr__(attr, self.__getattribute__(f"{attr}"))
                # print(f'leaf: {attr}={self.__getattribute__(f"{attr}")}')
                if attr == "secure":
                    break
            # else:
            self.__setattr__(attr, self.__getattribute__(f"{attr}_level{level}"))

    def __str__(self) -> str:  # FIXME: there are two of these, maybe get rid of one
        str = ""
        for attr in PTAttrs.base_attrs:
            if "level" not in attr:
                str += f"{attr}={int(self.get(attr))}, "

        return str

    def get_value(self) -> int:
        value = 0

        value |= common.set_bitn(original=value, bit=0, value=self.v)
        value |= common.set_bitn(original=value, bit=1, value=self.r)
        value |= common.set_bitn(original=value, bit=2, value=self.w)
        value |= common.set_bitn(original=value, bit=3, value=self.x)
        value |= common.set_bitn(original=value, bit=4, value=self.u)
        value |= common.set_bitn(original=value, bit=5, value=self.g)
        value |= common.set_bitn(original=value, bit=6, value=self.a)
        value |= common.set_bitn(original=value, bit=7, value=self.d)
        value |= common.set_bits(original_value=value, bit_hi=9, bit_lo=8, value=self.rsw)
        value |= common.set_bits(original_value=value, bit_hi=60, bit_lo=54, value=self.reserved)
        if self.leaf:
            value |= common.set_bits(original_value=value, bit_hi=62, bit_lo=61, value=self.pbmt)
        else:
            value |= common.set_bits(original_value=value, bit_hi=62, bit_lo=61, value=0)
        value |= common.set_bitn(original=value, bit=63, value=self.n)

        return value


class PTEntry:
    """
    Model and store each pagetable entry here
    It holds following information
    """

    def __init__(self, basetable: PTTable, pt_attr: PTAttrs, level: int):
        if basetable.base_addr is None:
            raise ValueError("basetable cannot be None for PTEntry")
        self.basetable: PTTable = basetable
        self.pt_attr: PTAttrs = pt_attr
        self.level: int = level
        self.leaf: bool = False

    def get_base_addr(self) -> int:
        return self.basetable.base_addr

    def get_pt_attrs(self) -> PTAttrs:
        return self.pt_attr

    def get_value(self) -> int:
        # FIXME: this is not good. we need to apply a real mask on top bits
        log.debug(f"PTEntry: get_value: {self.basetable.base_addr:x}, {self.pt_attr.get_value():x}")
        val = ((self.basetable.base_addr >> 12) << 10) | self.pt_attr.get_value()

        return val


class Pagetables:
    def __init__(self, page: "Page", page_map: "PageMap", pool: "Pool", featmgr: FeatMgr, addrgen: AddrGen):
        """
        Create pagetables for "page" in given "page_map"
          - return a list of PTEntry(s) with entires at each level
          - update the page_map.base_table with the newly created pagetables
        """
        self.page: "Page" = page
        self.page_map: "PageMap" = page_map

        self.pool: "Pool" = pool
        self.featmgr: FeatMgr = featmgr
        self.addrgen: AddrGen = addrgen

        self.set_level()

    def set_level(self) -> None:
        # Page class needs to set number of levels at generator.py
        # TODO: Need to use self.page.max_levels
        # self.max_levels = self.page.max_levels
        self.max_levels = self.page_map.max_levels

    def create_pagetables(self, rng: RandNum) -> None:
        """
        For a given linear address and physical address, create pagetables
        for a page in given page_map
        Following things we need to know:
          - linear_addr
          - physical_addr
          - page_size
          - page_map mode (sv32, sv39, sv48, sv57)
          - number of levels of pagetables to create
          - pagetable attributes that are specified in the page_mapping()
        """
        basetable = self.page_map.basetable
        if basetable is None:  # FIXME: added this as a fallback to make sure basetable is set, satisfies other type hinting
            raise ValueError(f"PageMap {self.page_map.name} basetable is None - initialize() must be called before create_pagetables()")

        # print(f'Creating pagetables for {self.page.name}, {self.page.lin_addr:x}')
        current_level = self.max_levels - 1
        for _ in range(self.max_levels - 1):
            if current_level == self.page.pt_leaf_level or basetable is None:
                break
            basetable = self._create_pt_non_leaf(rng, pt_level=current_level, base_table=basetable)
            current_level -= 1

        # Create the leaf entry
        # basrtable can be None if we had an overlap with another address with different pagesize
        if basetable is not None:
            self._create_pt_leaf(rng, base_table=basetable, pt_level=current_level)

    def _create_pt_non_leaf(self, rng: RandNum, pt_level: int, base_table: PTTable) -> Optional[PTTable]:
        """
        Create the non-leaf pt_entry
          - no-leaf entry is marked with XWR=3'b000 in the entry
        """
        # Since we are creating non-leaf entry, clear X/W/R bits
        pt_attr = PTAttrs(rng=rng, featmgr=self.featmgr, level=pt_level, page=self.page)
        # print(f'{self.page.name}_{pt_level}: pt_attrs: {self.page.attrs}, attrs: {pt_attr}') # FIXME: is this still used?
        # pt_attr.r = 0
        # pt_attr.w = 0
        # pt_attr.x = 0

        # Following are the reserved bits and must be zero for non-leaf entries
        # TODO: Need to allow user to set these bits
        # pt_attr.a = 0
        # pt_attr.d = 0
        # pt_attr.u = 0

        # We need to create a new PTTable and it's base address if not already
        # created previously with this entry
        index = self.calc_index_from_va(level=pt_level)

        if base_table.entry_exists(index):
            # We already have an entry for this index, just use it
            pt_entry = base_table.get_entry(index)
            base_table = pt_entry.basetable
            if pt_entry.leaf:
                info = (
                    "What this means is that most likely some other address has pagesize of >4kb, but did not reserve enough space "
                    + "or this address is a fixed address, which is adjecent to the previous page \n"
                    + "One example is that we have two addresses \n"
                    + "0x5000 -> random_phys and 0x6000 -> random_physical \n"
                    + "But we selected 2mb pagesize for 0x5000, this will reserve 2mb of space and leaving 0x6000 out of scope "
                )

                # Disabling the assertion since we need to allow the above condition if two virtual addresses point
                # to the same physical address with different pagesizes
                return None
        else:
            # Entry does not exist for this index, create one # FIXME can remove this?
            # lin_name = f'_pt_lin_{self.page_map.name}_{self.page.name}_level{pt_level}'
            # phys_name = f'_pt_phys_{self.page_map.name}_{self.page.name}_level{pt_level}'

            # Constraints for pagetable base address
            (size, mask, pagesize) = self.generate_pt_constraints(leaf=False)

            secure_access_generated = False
            qualifiers = {RV.AddressQualifiers.ADDRESS_DRAM}  # TODO: review, changed to set from list to match AddressConstraint types
            # Randomize pagetables to be secure with probability
            if self.featmgr.secure_mode and rng.with_probability_of(self.featmgr.secure_pt_probability):
                qualifiers = {RV.AddressQualifiers.ADDRESS_SECURE}  # TODO: review, changed to set from list to match AddressConstraint types
                secure_access_generated = True

            phys_addr_c = addrgen.AddressConstraint(type=RV.AddressType.PHYSICAL, qualifiers=qualifiers, bits=self.featmgr.physical_addr_bits, size=size, mask=mask)
            # print(f'constraints: {phys_addr_c}, page: {self.page.name} {self.page}')
            base_addr = self.addrgen.generate_address(constraint=phys_addr_c)
            if secure_access_generated:
                base_addr |= 0x0080000000000000

            # Add g-stage mapping if this is a vs-map and g-stage is enabled
            if not self.page_map.g_map and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                # Generate a system physical address for this guest physical address
                sys_phys_addr_c = addrgen.AddressConstraint(
                    type=RV.AddressType.PHYSICAL,
                    qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
                    # bits=RV.RiscvPagingModes.physical_addr_bits(self.page_map.paging_mode),
                    # address_bits=32,
                    bits=self.featmgr.physical_addr_bits,
                    # size=0x1000,
                    size=0x200000,
                    mask=0xFFFFFFFFFFE00000,
                )
                # sys_phys_addr = self.addrgen.generate_address(constraint=sys_phys_addr_c)

                # Go through all the g_maps for this page and create a mapping for this address
                # Creating mapping in map_hyp by default. Converting list to set to list to uniquefy the list since
                # we may have added map_hyp multiple times
                for map_name in list(set(self.page.maps + ["map_hyp"])):
                    map = self.pool.get_map(map_name)
                    if map.g_map:
                        linear_name = f"{self.page.name}__vslevel{pt_level}__gpa".replace("+", "_")
                        physical_name = f"{self.page.name}__vslevel{pt_level}__phys".replace("+", "_")

                        # Only add raw_pt_page if the address is not already in the random_addr
                        if not (self.pool.random_addr_exists(physical_name) or self.pool.random_addr_exists(linear_name)):
                            # print(f'adding g-stage mapping for intermediate {self.page.name} {self.page.lin_addr:x} at level {pt_level} for base {base_addr:x} in map {map.name} \
                            #       {pagesize}')
                            attrs = {"x": 1}
                            for attr in ["v", "a", "d", "g", "u", "r", "w", "x"]:
                                for level in range(self.max_levels):
                                    attr_level = f"{attr}_level{level}"
                                    # If level is leaf level then we only update v=0 and not v_level0. These are some of the stupid things I want to clean up, but for some other day
                                    leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
                                    value = self.page.attrs[f"{attr}_level{pt_level}_glevel{level}"]  # FIXME: bandaid fix for None values in page.attrs, do we want to allow None values?
                                    if value is not None:
                                        attrs[attr_level] = value
                            map.add_raw_pt_page(
                                linear_name=linear_name,
                                physical_name=physical_name,
                                linear_addr=base_addr,
                                physical_addr=base_addr,
                                # attrs={'x':1, 'v':1},
                                attrs=attrs,
                                pagesize=pagesize,
                            )

            # If we need to create pagetables for this pagetable, then add them now to Page.pt_pages
            if pt_attr.modify_pt and not self.page_map.g_map:
                # Calculate next level index since the address is generated for the next level from current context
                # next_level_index = self.calc_index_from_va(level=pt_level-1)
                lin_addr_c = addrgen.AddressConstraint(
                    type=RV.AddressType.LINEAR,
                    size=0x1000,
                    mask=0xFFFFFFFFFFFFF000,
                    # bits=RV.RiscvPagingModes.linear_addr_bits(self.page_map.paging_mode)-1
                    bits=38,
                )
                lin_addr = self.addrgen.generate_address(constraint=lin_addr_c)

                # Add the page with the above virtual -> physical address
                linear_name = f"{self.page.name}__pt_level{pt_level}"
                physical_name = f"{linear_name}__phys"
                self.page_map.add_raw_pt_page(
                    linear_name=linear_name,
                    physical_name=physical_name,
                    linear_addr=lin_addr + (index * 8),
                    physical_addr=base_table.base_addr + (index * 8),
                )

            next_basetable = PTTable(base_addr=base_addr)
            # print(f'PTEntry: Creating non-leaf entry for {self.page.name} at level {pt_level} with pt_attr: {pt_attr}, {pt_attr.get_value():x}')
            pt_entry = PTEntry(basetable=next_basetable, pt_attr=pt_attr, level=pt_level)
            base_table.insert_entry(entry=pt_entry, index=index)
            # print(f'create basetable: {base_table}')
            # print(f'insert entry {pt_entry.get_base_addr():x} for page {self.page.lin_addr:x} at index {index*8:x} \
            #       at level {pt_level} into {base_table.base_addr:x} with map {self.page_map.name}')

            # Update the base_table to return for the next level of pagetables
            base_table = next_basetable

        # Insert the pagetable entry into Page
        self.page.insert_pt(pt_entry)

        return base_table

    def _create_pt_leaf(self, rng: RandNum, base_table: PTTable, pt_level: int) -> None:
        """
        Create the leaf pt_entry
        """
        # Attributes with default values
        pt_attr = PTAttrs(rng=rng, featmgr=self.featmgr, level=pt_level, page=self.page, leaf=True)
        if self.featmgr.pbmt_ncio and not self.page.no_pbmt_ncio:
            # Randomize NC vs IO to 50%
            pt_attr.pbmt = 1 if rng.with_probability_of(50) else 2  # type: ignore
            # FIXME: raw attributes are not type safe, change to dataclass in future

        # If creating pagetables for g-stage, leaf level is always treated as user
        if self.page_map.g_map:  # FIXME: does this need to exist?
            pass
            # pt_attr.u = 1
            # pt_attr.g = 0

        index = self.calc_index_from_va(level=pt_level)
        # print(f'Leaf entry for {self.page.name} {self.page.lin_addr:x} with {self.page_map.name} at level {pt_level} for index {index:x}, base_table={base_table.base_addr:x}')

        if pt_attr.modify_pt and not self.page_map.g_map:
            # Calculate next level index since the address is generated for the next level from current context
            lin_addr_c = addrgen.AddressConstraint(
                type=RV.AddressType.LINEAR,
                size=0x1000,
                mask=0xFFFFFFFFFFFFF000,
                bits=RV.RiscvPagingModes.linear_addr_bits(self.page_map.paging_mode) - 1,
            )
            lin_addr = self.addrgen.generate_address(constraint=lin_addr_c)

            # Add the page with the above virtual -> physical address
            linear_name = f"{self.page.name}__pt_level{pt_level}"
            physical_name = f"{linear_name}__phys"
            self.page_map.add_raw_pt_page(
                linear_name=linear_name,
                physical_name=physical_name,
                linear_addr=lin_addr + (index * 8),
                physical_addr=base_table.base_addr + (index * 8),
            )

        phys_addr = self.page.phys_addr
        if phys_addr is None:  # FIXME: added because of optional none for phys_addr
            raise ValueError(f"Physical address is None for page {self.page.name}")
        if pt_attr.secure:
            phys_addr |= 0x0080000000000000
        leaf_basetable = PTTable(base_addr=phys_addr, leaf=True)
        pt_entry = PTEntry(basetable=leaf_basetable, pt_attr=pt_attr, level=pt_level)
        # Also mark the pt_entry as leaf, so we can error out if any other address tried to use this as non-leaf
        pt_entry.leaf = True
        base_table.insert_entry(entry=pt_entry, index=index)
        log.debug(
            f"insert leaf entry {pt_entry.get_base_addr():x} for page {self.page.name} {self.page.lin_addr:x} at index {index*8:x} \
            at level {pt_level} into {base_table.base_addr:x} with map {self.page_map.name}, {self.page}",
        )

        # Add g-stage mapping if this is a vs-map and if g-stage is enabled
        if not self.page_map.g_map and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            # Constraints for pagetable base address
            (size, mask, pagesize) = self.generate_pt_constraints(leaf=True)

            # Generate a system physical address for this guest physical address
            sys_phys_addr_c = addrgen.AddressConstraint(
                type=RV.AddressType.PHYSICAL,
                qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
                # bits=RV.RiscvPagingModes.linear_addr_bits(self.page_map.paging_mode),
                bits=39,
                # address_bits=32,
                size=0x1000,
                mask=0xFFFFFFFFFFFFF000,
            )
            # sys_phys_addr = self.addrgen.generate_address(constraint=sys_phys_addr_c)

            # Go through all the g_maps for this page and create a mapping for this address
            # Creating mapping in map_hyp by default. Converting list to set to list to uniquefy the list since
            # we may have added map_hyp multiple times
            for map_name in list(set(self.page.maps + ["map_hyp"])):
                map = self.pool.get_map(map_name)
                if map.g_map:
                    linear_name = f"{self.page.name}__vsleaf{pt_level}__gpa".replace("+", "_")
                    physical_name = f"{self.page.name}__vsleaf{pt_level}__phys".replace("+", "_")

                    # Only add raw_pt_page if the address is not already in the random_addr
                    if not (self.pool.random_addr_exists(physical_name) or self.pool.random_addr_exists(linear_name)):
                        # FIXME: Currently, cannot enable pagesize logic here since we need to constraint the physical address (GPA)
                        #        to have same alignment as the leaf pagesize. This needs to happen in generator.py
                        if self.page.phys_addr is None:  # FIXME: fallback to make sure phys_addr is not None
                            raise ValueError(f"Physical address is None for page {self.page.name}")
                        attrs = {"x": 1}
                        for attr in ["v", "a", "d", "g", "u", "r", "w", "x"]:
                            for level in range(self.max_levels):
                                attr_level = f"{attr}_level{level}"
                                # If level is leaf level then we only update v=0 and not v_level0. These are some of the stupid things I want to clean up, but for some other day
                                leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
                                value = self.page.attrs[f"{attr}_level{pt_level}_glevel{level}"]  # FIXME: bandaid fix for None values in page.attrs, do we want to allow None values?
                                if value is not None:
                                    attrs[attr_level] = value
                        map.add_raw_pt_page(
                            linear_name=linear_name,
                            physical_name=physical_name,
                            linear_addr=self.page.phys_addr,
                            physical_addr=self.page.phys_addr,
                            attrs=attrs,
                            pagesize=pagesize,
                        )

        self.page.insert_pt(pt_entry)

    def generate_pt_constraints(self, leaf: bool) -> "tuple[int, int, RV.RiscvPageSizes]":
        """
        Generate constraints for the pagetable base address
        """
        # We need to constraint this base don the g_stage pagetable attributes
        # size = 0x200000
        size = 0x1000
        mask = 0xFFFFFFFFFFFFF000
        pagesize = RV.RiscvPageSizes.S4KB

        # If g-stage is enabled, then we need to make sure that the base address is aligned to the
        # g-stage page size
        # FIXME: probably should review this, try/catch only exists because these traits are only set in some generator paths
        # we added all of the gstage_vs_* traits to page class to avoid lint errors as a bandaid but should be revisited
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            # Check if the g_stage page size is specified
            if leaf:
                # The try-catch is needed since I haven't added the gstage_vs_leaf_pagesize attribute to all the pages
                # generated for sections like code, data, text etc
                try:
                    # print(f'PT getting leaf {self.page.name} {self.page.gstage_vs_leaf_pagesize}')
                    if self.page.gstage_vs_leaf_address_size is not None:  # FIXME: added these checks because these are optional
                        size = self.page.gstage_vs_leaf_address_size
                    if self.page.gstage_vs_leaf_address_mask is not None:
                        mask = self.page.gstage_vs_leaf_address_mask
                    if self.page.gstage_vs_leaf_pagesize is not None:
                        pagesize = self.page.gstage_vs_leaf_pagesize
                except Exception:
                    # FIXME: Need specific exception
                    pass
            else:  # Non-leaf
                try:
                    # print(f'PT getting nonleaf {self.page.name} {self.page.gstage_vs_nonleaf_pagesize}, {self.page.gstage_vs_nonleaf_address_size:x}, {self.page.gstage_vs_nonleaf_address_mask:x}')
                    if self.page.gstage_vs_nonleaf_address_size is not None:
                        size = self.page.gstage_vs_nonleaf_address_size
                    if self.page.gstage_vs_nonleaf_address_mask is not None:
                        mask = self.page.gstage_vs_nonleaf_address_mask
                    if self.page.gstage_vs_nonleaf_pagesize is not None:
                        pagesize = self.page.gstage_vs_nonleaf_pagesize
                except Exception:
                    # FIXME: Need specific exception
                    pass

        return (size, mask, pagesize)

    def calc_index_from_va(self, level: int) -> int:
        """
        Calculate the index for a given level of pagetable
        """
        page_offset_bits = 12
        index_bits_result = RV.RiscvPagingModes.index_bits(mode=self.page_map.paging_mode, level=level)  # FIXME: added because of None fall-through case in index_bits()
        if index_bits_result is None:
            raise ValueError(f"index_bits returned None for paging_mode={self.page_map.paging_mode}, level={level}")
        index_hi, index_lo = index_bits_result

        # index_lo = (index_bits * level) + page_offset_bits
        # index_hi = index_lo + index_bits - 1

        lin_addr = self.page.lin_addr
        if lin_addr is None:  # FIXME: added because of optional none for lin_addr
            raise ValueError(f"Linear address is None for page {self.page.name}")
        index = common.bits(value=lin_addr, bit_hi=index_hi, bit_lo=index_lo)

        return index
