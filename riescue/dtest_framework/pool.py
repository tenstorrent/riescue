# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import re
import copy
from typing import Optional, TYPE_CHECKING

import riescue.lib.enums as RV

from riescue.dtest_framework.lib.pmp import PmpRegion

if TYPE_CHECKING:
    from riescue.dtest_framework.parser import PmaInfo, ParsedTestHeader

log = logging.getLogger(__name__)


class Pool:
    """
    Pool module holds all the assets/resources pool that's needed to build the simulation state
    - data
    - addresses
        - linear
        - physical
    - pagetables
    - test headers
    - misc information
    """

    def __init__(self):
        # Parsed structures
        self.parsed_random_data = dict()
        self.parsed_res_mem = dict()
        self.parsed_random_addrs = dict()
        self.raw_parsed_page_mappings = []
        self.parsed_page_mappings = dict()
        self.parsed_page_mappings_with_lin_name = dict()
        self.parsed_page_maps = dict()
        self.parsed_test_header: Optional["ParsedTestHeader"] = None
        self.parsed_discrete_tests = dict()
        self.parsed_init_mem_addrs = []
        self.parsed_vectored_interrupts = []
        self.parsed_csr_accesses = {}
        self.parsed_sections = []

        # Structures to hold processed data
        self.discrete_tests = dict()
        self.random_data = dict()
        self.random_addrs = dict()
        self.page_mappings = dict()
        self.page_maps = dict()
        self.sections = dict()
        self.pma_dram_default: Optional[PmaInfo] = None
        self.pma_io_default = None
        self.pma_regions = dict()  # name -> PmaInfo
        self.pmp_regions: PmpRegion = PmpRegion()

        # os include files
        self.runtime_files = ["loader", "os", "exception"]

        self.testname = ""  # Update once we know the commandline args

    # parsed_random_data setters and getters
    def add_parsed_data(self, parsed_random_data):
        if parsed_random_data.name in self.parsed_random_data:
            raise ValueError(f"{parsed_random_data.name} is already defined")

        self.parsed_random_data[parsed_random_data.name] = parsed_random_data

    def get_parsed_datum(self, key):
        return self.parsed_random_data[key]

    def get_parsed_data(self):
        return self.parsed_random_data

    # parsed_reserve_memory setters and getters
    def add_parsed_res_mem(self, parsed_res_mem):
        parsed_res_mem.name = f"{parsed_res_mem.name}_{parsed_res_mem.addr_type}"
        if parsed_res_mem.name in self.parsed_res_mem:
            if self.parsed_res_mem[parsed_res_mem.name].addr_type == parsed_res_mem.addr_type:
                raise ValueError(f"{parsed_res_mem.name} is already defined")

        self.parsed_res_mem[parsed_res_mem.name] = parsed_res_mem

    def get_parsed_res_mem(self, key):
        return self.parsed_res_mem[key]

    def get_parsed_res_mems(self):
        return self.parsed_res_mem

    # parsed_random_addr setters and getters
    def random_addr_exists(self, addr_name):
        return addr_name in self.random_addrs

    def parsed_random_addr_exists(self, addr_name):
        if addr_name in self.parsed_random_addrs:
            return True

        return False

    def add_parsed_addr(self, parsed_random_addr, force_overwrite=False):
        if parsed_random_addr.name in self.parsed_random_addrs and not force_overwrite:
            raise ValueError(f"{parsed_random_addr.name} is already defined")

        self.parsed_random_addrs[parsed_random_addr.name] = parsed_random_addr

    def get_parsed_addr(self, key):
        return self.parsed_random_addrs[key]

    def get_parsed_addrs(self):
        return self.parsed_random_addrs

    # parsed_page_mappings setters and getters
    def parsed_page_mapping_exists(self, lin_name, map_name):
        return (lin_name, map_name) in self.parsed_page_mappings

    def parsed_page_mapping_with_lin_name_exists(self, lin_name):
        return lin_name in self.parsed_page_mappings_with_lin_name

    def get_parsed_page_mapping_with_lin_name(self, lin_name):
        return self.parsed_page_mappings_with_lin_name[lin_name]

    def add_parsed_page_mapping(self, parsed_page_mapping):
        self.parsed_page_mappings_with_lin_name[parsed_page_mapping.lin_name] = []
        if (len(parsed_page_mapping.page_maps) == 0) or not parsed_page_mapping.in_private_map:
            map_key = "map_os"
            self.parsed_page_mappings[parsed_page_mapping.lin_name, map_key] = parsed_page_mapping
            self.parsed_page_mappings_with_lin_name[parsed_page_mapping.lin_name].append(map_key)
        else:
            for map_key in parsed_page_mapping.page_maps:
                self.parsed_page_mappings[parsed_page_mapping.lin_name, map_key] = parsed_page_mapping
                self.parsed_page_mappings_with_lin_name[parsed_page_mapping.lin_name].append(map_key)

    def get_parsed_page_mappings(self):
        return self.parsed_page_mappings

    def get_parsed_page_mapping(self, key1, key2):
        return self.parsed_page_mappings[key1, key2]

    def add_parsed_page_map(self, parsed_page_map):
        self.parsed_page_maps[parsed_page_map.name] = parsed_page_map

    def get_parsed_page_maps(self):
        return self.parsed_page_maps

    def get_parsed_page_map(self, key):
        return self.parsed_page_maps[key]

    # test_header setters and getters
    def add_test_header(self, parsed_test_header: "ParsedTestHeader"):
        self.parsed_test_header = parsed_test_header

    def get_test_header(self):
        return self.parsed_test_header

    # discrete_test setters and getters
    def add_parsed_discrete_test(self, parsed_discrete_test):
        self.parsed_discrete_tests[parsed_discrete_test] = parsed_discrete_test

    def get_parsed_discrete_tests(self):
        return self.parsed_discrete_tests

    def get_parsed_discrete_test(self, key):
        return self.parsed_discrete_tests[key]

    # init_mem_addrs setters and getters
    def add_parsed_init_mem_addr(self, val):
        self.parsed_init_mem_addrs.append(val)

    def get_parsed_init_mem_addr(self, key):
        return self.parsed_init_mem_addrs[key]

    def get_parsed_init_mem_addrs(self):
        return self.parsed_init_mem_addrs

    # FIXME: Parser depends on Pool. Can't import types from parser here without circular dependency. Might need to reconsider dependency structure
    def add_parsed_vectored_interrupt(self, parsed_vectored_interrupt):
        self.parsed_vectored_interrupts.append(parsed_vectored_interrupt)

    def add_parsed_csr_access(self, parsed_csr_access):
        csr_name = parsed_csr_access.csr_name
        read_or_write = parsed_csr_access.read_or_write
        if csr_name in self.parsed_csr_accesses and read_or_write in self.parsed_csr_accesses[csr_name]:
            return
        if csr_name not in self.parsed_csr_accesses:
            self.parsed_csr_accesses[parsed_csr_access.csr_name] = {}
        self.parsed_csr_accesses[parsed_csr_access.csr_name][parsed_csr_access.read_or_write] = parsed_csr_access

    def get_parsed_csr_accesses(self):
        return self.parsed_csr_accesses

    def get_parsed_csr_access(self, csr, read_or_write):
        return self.parsed_csr_accesses[csr][read_or_write]

    # random structures
    # random_data setters and getters
    def add_random_datum(self, key, val):
        self.random_data[key] = val

    def get_random_data(self):
        return self.random_data

    def get_random_datum(self, key):
        return self.random_data[key]

    # random_addr setters and getters
    def add_random_addr(self, addr_name, addr, allow_duplicate=False):
        if addr_name in self.random_addrs and not allow_duplicate:
            raise ValueError(f"{addr_name} already exist in random_addr, trying to add again")
        self.random_addrs[addr_name] = addr

        # Also handle PMA info associated with the physical addresses
        if addr.type == RV.AddressType.PHYSICAL:
            # Check if this is has associated parsed_random_addr which has in_pma=1
            if self.parsed_random_addr_exists(addr_name):
                parsed_addr = self.get_parsed_addr(addr_name)
                if parsed_addr.in_pma:
                    # Make sure if pma_size is not specified, we default to same as random_addr size
                    if parsed_addr.pma_info.pma_size == 0:
                        parsed_addr.pma_info.pma_size = addr.address.size
                    parsed_addr.pma_info.pma_address = addr.address
                    self.pma_regions[addr_name] = parsed_addr.pma_info

    def get_random_addrs(self):
        return self.random_addrs

    def get_random_addr(self, addr_name):
        return self.random_addrs[addr_name]

    # page_mapping setters and getters
    def add_page_mapping(self, key, val):
        self.page_mappings[key] = val

    def get_page_mapping(self, key):
        return self.page_mappings[key]

    def get_page_mappings(self):
        return self.page_mappings

    # page_map setters and getters
    def add_page_map(self, map_instance):
        self.page_maps[map_instance.name] = map_instance

    def get_page_map(self, map_name):
        return self.page_maps[map_name]

    def get_page_maps(self):
        return self.page_maps

    def get_min_linear_addr_bits_for_page_maps(self, page_map_list=None):
        filtered_page_maps = self.page_maps
        if page_map_list is not None:
            filtered_page_maps = set(self.page_maps.keys()).intersection(page_map_list)
        return min([self.page_maps[x].get_linear_addr_bits() for x in filtered_page_maps])

    def get_min_physical_addr_bits_for_page_maps(self, page_map_list=None):
        filtered_page_maps = self.page_maps
        if page_map_list is not None:
            filtered_page_maps = set(self.page_maps.keys()).intersection(page_map_list)
        return min([self.page_maps[x].get_physical_addr_bits() for x in filtered_page_maps])

    def get_map(self, map_name):
        if map_name in self.page_maps:
            return self.page_maps[map_name]
        else:
            raise KeyError(f"{map_name} does not exist in page_maps")

    # Page management
    def page_exists(self, name, map_name):
        """
        Check if page with name exists in the map_name
        """
        if name in self.get_all_pages(map_name=map_name):
            return True
        else:
            return False

    def add_page(self, page, map_names):
        for map_name in map_names:
            map_inst = self.get_page_map(map_name)
            if (map_inst.name == "map_os") or not page.in_private_map:
                p = page
            else:
                p = copy.copy(page)

            map_inst.add_page(p)

    def get_page(self, page_name, map_name):
        map_inst = self.get_page_map(map_name)

        return map_inst.get_page(page_name)

    def get_all_pages(self, map_name):
        map_inst = self.get_page_map(map_name)

        return map_inst.pages.keys()

    def get_maps_for_page(self, page_name):
        for map in self.get_page_maps():
            if map.get_page(page_name):
                page = map.get_page(page_name)

                return page.maps

    # Processed sections
    def get_sections(self):
        return self.sections

    def get_section(self, section_name):
        return self.sections[section_name]

    def add_section(self, section_name, address=None):
        # Post process section_name and address
        # if section_name is a linear address => we need to find the physical address
        #                       physical addr => we can directly add address
        #                       number => then that's the physical address
        # TODO: Since we don't have paging enabled yet, we need to use linear addresses
        #       and also need to make lin_addr = phys_addr for paging_disable case
        if address is not None:
            self.sections[section_name] = address
            return

        address = None
        alias_skip = False
        if section_name.startswith("0x"):
            page_name = f"__auto_lin_{section_name}"
            map_name = "map_os"
            match = re.match(r".*_([^_]+)$", section_name)
            if match:
                map_name = match.group(1)
            map_os_page = self.get_page(page_name=page_name, map_name=map_name)
            address = map_os_page.phys_addr
        else:
            rand_addr_inst = self.get_random_addr(addr_name=section_name)
            if rand_addr_inst.type == RV.AddressType.PHYSICAL:
                # TODO: The following commented codes need to swapped out once we fix
                # the address generation for paging disable case. We always need to use
                # physical addresses
                # address = rand_addr_inst.address
                address = rand_addr_inst.address
            elif rand_addr_inst.type == RV.AddressType.LINEAR:
                # map_os_page = self.get_page(page_name=section_name, map_name='map_os')
                # address = map_os_page.phys_addr
                map_os_page = self.get_page(page_name=section_name, map_name="map_os")
                if map_os_page.alias:
                    # We want to skip this entry into linker script
                    alias_skip = True
                address = map_os_page.phys_addr
            else:
                raise ValueError(f"Unupported type {rand_addr_inst.type} in {self.add_section.__name__}")

        if alias_skip:
            log.info(f"Skipping section {section_name} as it is an alias")
            return

        self.sections[section_name] = address

    # discrete_test setters and getters
    def add_discrete_test(self, discrete_test):
        test_name = discrete_test.name
        self.discrete_tests[test_name] = discrete_test

    # sections setters and getters
    def add_parsed_sections(self, val, index=None):
        if index is not None:
            self.parsed_sections.insert(index, val)
        else:
            self.parsed_sections.append(val)

    def get_parsed_sections(self):
        return self.parsed_sections

    def add_raw_parsed_page_mapping(self, ppm):
        self.raw_parsed_page_mappings.append(ppm)

    def get_raw_parsed_page_mappings(self):
        return self.raw_parsed_page_mappings
