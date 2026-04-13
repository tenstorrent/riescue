# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import re
import copy
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING, Union

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.pma import PmaRegion
from riescue.dtest_framework.lib.pmp import PmpRegion
from riescue.dtest_framework.parser import (
    PmaInfo,
    ParsedTestHeader,
    ParsedRandomData,
    ParsedReserveMemory,
    ParsedRandomAddress,
    ParsedPageMapping,
    ParsedPageMap,
    ParsedVectoredInterrupt,
    ParsedCustomHandler,
    ParsedVectorDelegation,
    ParsedCsrAccess,
    ParsedPmaHint,
    ParsedTriggerConfig,
    ParsedTriggerDisable,
    ParsedTriggerEnable,
)
from riescue.dtest_framework.config.memory import Memory
from riescue.lib.address import Address
from riescue.dtest_framework.lib.discrete_test import DiscreteTest

from riescue.dtest_framework.lib.page_map import PageMap, Page


log = logging.getLogger(__name__)


@dataclass
class SectionInfo:
    """Holds both the virtual memory address (VMA) and load/physical memory address (LMA) for a section."""

    vma: int
    lma: int


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
        self.parsed_random_data: dict[str, ParsedRandomData] = dict()
        self.parsed_res_mem: dict[str, ParsedReserveMemory] = dict()
        self.parsed_random_addrs: dict[str, ParsedRandomAddress] = dict()
        self.raw_parsed_page_mappings: list[ParsedPageMapping] = []
        self.parsed_page_mappings: dict[tuple[str, str], ParsedPageMapping] = dict()
        self.parsed_page_mappings_with_lin_name: dict[str, list[str]] = dict()
        self.parsed_page_maps: dict[str, ParsedPageMap] = dict()
        self.parsed_test_header: Optional[ParsedTestHeader] = None
        self.parsed_discrete_tests: dict[str, str] = dict()
        # ;#discrete_debug_test: list of body lines until next ;# or .section (only one per test file)
        self.parsed_discrete_debug_test: Optional[list[str]] = None
        self.parsed_init_mem_addrs: list[str] = []
        self.parsed_vectored_interrupts: list[ParsedVectoredInterrupt] = []
        self.parsed_custom_handlers: list[ParsedCustomHandler] = []
        self.parsed_vector_delegations: list[ParsedVectorDelegation] = []
        # Interrupt handler pointers: equate_name → default_label.
        # TrapHandler registers an entry here during generate() for each vector that has a
        # custom per-segment handler.  OpSys reads this in generate()/generate_equates() to
        # emit a writable .dword in .os_data and a pair of VA/PA equates so that PROLOGUE/
        # EPILOGUE macros and the vector jump stubs can reach them via li (no PC-relative la).
        self._interrupt_handler_pointers: dict[str, str] = {}
        self.parsed_csr_accesses: dict[str, dict[str, ParsedCsrAccess]] = {}
        self.parsed_sections: list[str] = []
        self.init_aplic_interrupts = False
        self.ext_aplic_interrupts = dict[int, dict[str, Optional[Union[int, str]]]()]()
        self.max_aplic_irq: int = 1023
        self.parsed_pma_hints: dict[str, ParsedPmaHint] = dict()
        self.parsed_trigger_configs: list[ParsedTriggerConfig] = []
        self.parsed_trigger_disable: list[ParsedTriggerDisable] = []
        self.parsed_trigger_enable: list[ParsedTriggerEnable] = []

        # Structures to hold processed data
        self.discrete_tests: dict[str, DiscreteTest] = dict()
        self.random_data: dict[str, int] = dict()
        self.random_addrs: dict[str, "Address"] = dict()
        self.page_mappings: dict[str, ParsedPageMapping] = dict()
        self.page_maps: dict[str, "PageMap"] = dict()
        self.sections: dict[str, SectionInfo] = dict()
        self.pma_regions: PmaRegion = PmaRegion()
        self.pmp_regions: PmpRegion = PmpRegion()

        # os include files
        self.runtime_files: list[str] = ["loader", "os", "exception"]

        self.testname: str = ""  # Update once we know the commandline args

    # parsed_random_data setters and getters
    def add_parsed_data(self, parsed_random_data: ParsedRandomData) -> None:
        if parsed_random_data.name in self.parsed_random_data:
            raise ValueError(f"{parsed_random_data.name} is already defined")

        self.parsed_random_data[parsed_random_data.name] = parsed_random_data

    def get_parsed_datum(self, key: str) -> ParsedRandomData:
        return self.parsed_random_data[key]

    def get_parsed_data(self) -> dict[str, ParsedRandomData]:
        return self.parsed_random_data

    # parsed_reserve_memory setters and getters
    def add_parsed_res_mem(self, parsed_res_mem: ParsedReserveMemory) -> None:
        parsed_res_mem.name = f"{parsed_res_mem.name}_{parsed_res_mem.addr_type}"
        if parsed_res_mem.name in self.parsed_res_mem:
            if self.parsed_res_mem[parsed_res_mem.name].addr_type == parsed_res_mem.addr_type:
                raise ValueError(f"{parsed_res_mem.name} is already defined")

        self.parsed_res_mem[parsed_res_mem.name] = parsed_res_mem

    def get_parsed_res_mem(self, key: str) -> ParsedReserveMemory:
        return self.parsed_res_mem[key]

    def get_parsed_res_mems(self) -> dict[str, ParsedReserveMemory]:
        return self.parsed_res_mem

    # parsed_random_addr setters and getters
    def random_addr_exists(self, addr_name: str) -> bool:
        return addr_name in self.random_addrs

    def parsed_random_addr_exists(self, addr_name: str) -> bool:
        if addr_name in self.parsed_random_addrs:
            return True

        return False

    def add_parsed_addr(self, parsed_random_addr: ParsedRandomAddress, force_overwrite: bool = False) -> None:
        if parsed_random_addr.name in self.parsed_random_addrs and not force_overwrite:
            raise ValueError(f"{parsed_random_addr.name} is already defined")

        self.parsed_random_addrs[parsed_random_addr.name] = parsed_random_addr

    def get_parsed_addr(self, key: str) -> ParsedRandomAddress:
        return self.parsed_random_addrs[key]

    def get_parsed_addrs(self) -> dict[str, ParsedRandomAddress]:
        return self.parsed_random_addrs

    # parsed_page_mappings setters and getters
    def parsed_page_mapping_exists(self, lin_name: str, map_name: str) -> bool:
        return (lin_name, map_name) in self.parsed_page_mappings

    def parsed_page_mapping_with_lin_name_exists(self, lin_name: str) -> bool:
        return lin_name in self.parsed_page_mappings_with_lin_name

    def get_parsed_page_mapping_with_lin_name(self, lin_name: str) -> list[str]:
        return self.parsed_page_mappings_with_lin_name[lin_name]

    def add_parsed_page_mapping(self, parsed_page_mapping: ParsedPageMapping) -> None:
        self.parsed_page_mappings_with_lin_name[parsed_page_mapping.lin_name] = []
        if (len(parsed_page_mapping.page_maps) == 0) or not parsed_page_mapping.in_private_map:
            map_key = "map_os"
            self.parsed_page_mappings[parsed_page_mapping.lin_name, map_key] = parsed_page_mapping
            self.parsed_page_mappings_with_lin_name[parsed_page_mapping.lin_name].append(map_key)
        else:
            for map_key in parsed_page_mapping.page_maps:
                self.parsed_page_mappings[parsed_page_mapping.lin_name, map_key] = parsed_page_mapping
                self.parsed_page_mappings_with_lin_name[parsed_page_mapping.lin_name].append(map_key)

    def get_parsed_page_mappings(self) -> dict[tuple[str, str], ParsedPageMapping]:
        return self.parsed_page_mappings

    def get_parsed_page_mapping(self, key1: str, key2: str) -> ParsedPageMapping:
        return self.parsed_page_mappings[key1, key2]

    def resolve_canonical_lin_name(self, lin_name: str, map_name: str) -> str:
        """If lin_name is an alias in map_name, return the canonical (non-alias) lin_name
        that shares the same phys_name. Otherwise return lin_name unchanged."""
        if not self.parsed_page_mapping_exists(lin_name, map_name):
            return lin_name
        ppm = self.get_parsed_page_mapping(lin_name, map_name)
        if not ppm.alias:
            return lin_name
        for (other_lin, other_map), other_ppm in self.parsed_page_mappings.items():
            if other_map == map_name and other_ppm.phys_name == ppm.phys_name and not other_ppm.alias:
                return other_lin
        return lin_name

    def add_parsed_page_map(self, parsed_page_map: ParsedPageMap) -> None:
        self.parsed_page_maps[parsed_page_map.name] = parsed_page_map

    def get_parsed_page_maps(self) -> dict[str, ParsedPageMap]:
        return self.parsed_page_maps

    def get_parsed_page_map(self, key: str) -> ParsedPageMap:
        return self.parsed_page_maps[key]

    # test_header setters and getters
    def add_test_header(self, parsed_test_header: ParsedTestHeader) -> None:
        self.parsed_test_header = parsed_test_header

    def get_test_header(self) -> Optional[ParsedTestHeader]:
        return self.parsed_test_header

    # discrete_test setters and getters
    def add_parsed_discrete_test(self, parsed_discrete_test: str) -> None:
        self.parsed_discrete_tests[parsed_discrete_test] = parsed_discrete_test

    def get_parsed_discrete_tests(self) -> dict[str, str]:
        return self.parsed_discrete_tests

    def get_parsed_discrete_test(self, key: str) -> str:
        return self.parsed_discrete_tests[key]

    # discrete_debug_test: single body_lines when ;#discrete_debug_test() is used (only one per test file)
    def set_parsed_discrete_debug_test(self, body_lines: list[str]) -> None:
        if self.parsed_discrete_debug_test is not None:
            raise ValueError("multiple ;#discrete_debug_test() not allowed; only one per test file")
        self.parsed_discrete_debug_test = body_lines

    def get_parsed_discrete_debug_test(self) -> Optional[list[str]]:
        return self.parsed_discrete_debug_test

    # init_mem_addrs setters and getters
    def add_parsed_init_mem_addr(self, val: str) -> None:
        self.parsed_init_mem_addrs.append(val)

    def get_parsed_init_mem_addr(self, key: int) -> str:
        return self.parsed_init_mem_addrs[key]

    def get_parsed_init_mem_addrs(self) -> list[str]:
        return self.parsed_init_mem_addrs

    def add_parsed_vectored_interrupt(self, parsed_vectored_interrupt: ParsedVectoredInterrupt) -> None:
        self.parsed_vectored_interrupts.append(parsed_vectored_interrupt)

    def add_parsed_custom_handler(self, parsed_custom_handler: ParsedCustomHandler) -> None:
        self.parsed_custom_handlers.append(parsed_custom_handler)

    def add_parsed_vector_delegation(self, parsed_vector_delegation: ParsedVectorDelegation) -> None:
        self.parsed_vector_delegations.append(parsed_vector_delegation)

    def add_parsed_csr_access(self, parsed_csr_access: ParsedCsrAccess) -> None:
        csr_name = parsed_csr_access.csr_name
        read_write_set_clear = parsed_csr_access.read_write_set_clear
        # For write_subfield/read_subfield, qualify the key with the field name
        # so that multiple fields on the same CSR each get their own jump table entry.
        if read_write_set_clear in ("write_subfield", "read_subfield"):
            if not parsed_csr_access.field:
                raise ValueError(f"csr_rw {read_write_set_clear} for {csr_name} requires a field parameter")
            read_write_set_clear = f"{read_write_set_clear}_{parsed_csr_access.field}"
            parsed_csr_access.read_write_set_clear = read_write_set_clear
        # Qualify key with _force_machine so that force_machine and
        # non-force_machine accesses for the same CSR+operation coexist.
        key = read_write_set_clear
        if parsed_csr_access.force_machine_rw:
            key = f"{read_write_set_clear}_force_machine"
        if csr_name in self.parsed_csr_accesses and key in self.parsed_csr_accesses[csr_name]:
            return
        if csr_name not in self.parsed_csr_accesses:
            self.parsed_csr_accesses[csr_name] = {}
        self.parsed_csr_accesses[csr_name][key] = parsed_csr_access

    def get_parsed_csr_accesses(self) -> dict[str, dict[str, ParsedCsrAccess]]:
        return self.parsed_csr_accesses

    def get_parsed_csr_access(self, csr: str, read_write_set_clear: str, field: str | None = None, force_machine_rw: bool = False) -> ParsedCsrAccess:
        lookup_key = read_write_set_clear
        if read_write_set_clear in ("write_subfield", "read_subfield") and field:
            lookup_key = f"{read_write_set_clear}_{field}"
        if force_machine_rw:
            lookup_key = f"{lookup_key}_force_machine"
        return self.parsed_csr_accesses[csr][lookup_key]

    def get_next_csr_id(self) -> int:
        max_id = 0
        for csr_group in self.parsed_csr_accesses.values():
            for csr in csr_group.values():
                max_id = max(max_id, csr.csr_id)
        return max_id + 1

    # parsed_pma_hint setters and getters
    def add_parsed_pma_hint(self, parsed_pma_hint: ParsedPmaHint) -> None:
        """Add parsed PMA hint to pool"""
        if parsed_pma_hint.name in self.parsed_pma_hints:
            log.warning(f"PMA hint {parsed_pma_hint.name} already exists, overwriting")
        self.parsed_pma_hints[parsed_pma_hint.name] = parsed_pma_hint

    def get_parsed_pma_hints(self) -> dict[str, ParsedPmaHint]:
        """Get all parsed PMA hints"""
        return self.parsed_pma_hints

    def get_parsed_pma_hint(self, name: str) -> ParsedPmaHint:
        """Get parsed PMA hint by name"""
        return self.parsed_pma_hints[name]

    # parsed_trigger_config / disable / enable
    def add_parsed_trigger_config(self, cfg: ParsedTriggerConfig) -> None:
        self.parsed_trigger_configs.append(cfg)

    def add_parsed_trigger_disable(self, cfg: ParsedTriggerDisable) -> None:
        self.parsed_trigger_disable.append(cfg)

    def add_parsed_trigger_enable(self, cfg: ParsedTriggerEnable) -> None:
        self.parsed_trigger_enable.append(cfg)

    def get_parsed_trigger_configs(self) -> list[ParsedTriggerConfig]:
        return self.parsed_trigger_configs

    def get_parsed_trigger_disable(self) -> list[ParsedTriggerDisable]:
        return self.parsed_trigger_disable

    def get_parsed_trigger_enable(self) -> list[ParsedTriggerEnable]:
        return self.parsed_trigger_enable

    # random structures
    # random_data setters and getters
    def add_random_datum(self, key: str, val: int) -> None:
        self.random_data[key] = val

    def get_random_data(self) -> dict[str, int]:
        return self.random_data

    def get_random_datum(self, key: str) -> int:
        return self.random_data[key]

    # random_addr setters and getters
    def add_random_addr(self, addr_name: str, addr: "Address", allow_duplicate: bool = False) -> None:
        if addr_name in self.random_addrs and not allow_duplicate:
            raise ValueError(f"{addr_name} already exist in random_addr, trying to add again")
        self.random_addrs[addr_name] = addr

        # Also handle PMA info associated with the physical addresses
        if addr.type == RV.AddressType.PHYSICAL:
            # Check if this is has associated parsed_random_addr which has in_pma=1
            if self.parsed_random_addr_exists(addr_name):
                parsed_addr = self.get_parsed_addr(addr_name)
                if parsed_addr.in_pma:
                    if parsed_addr.pma_info is None:
                        raise ValueError(f"PMA info is not specified for {addr_name}, {parsed_addr}")
                    # Make sure if pma_size is not specified, we default to same as random_addr size
                    if parsed_addr.pma_info.pma_size == 0:
                        parsed_addr.pma_info.pma_size = parsed_addr.size

                    # Update the PMA region address if it was pre-allocated
                    # (If it's already in pool, it means it was pre-allocated and added)
                    if parsed_addr.pma_info.pma_address == 0:
                        # Set the address
                        parsed_addr.pma_info.pma_address = addr.address
                        # Only add to pool if not already added (pre-allocated regions are already added)
                        # Check if this region is already in the pool by checking if address matches
                        existing_region = self.pma_regions.find_region_for_address(addr.address)
                        if existing_region is None or existing_region.pma_address != addr.address:
                            # Not found or different region, add it
                            self.pma_regions.add_entry(parsed_addr.pma_info)
                    else:
                        # Address already set (from pre-allocation), just update if needed
                        if parsed_addr.pma_info.pma_address != addr.address:
                            log.warning(f"PMA region for {addr_name} has address 0x{parsed_addr.pma_info.pma_address:x}, " f"but generated address is 0x{addr.address:x}. Updating PMA region address.")
                            parsed_addr.pma_info.pma_address = addr.address

    def get_random_addrs(self) -> dict[str, "Address"]:
        return self.random_addrs

    def get_random_addr(self, addr_name: str) -> "Address":
        return self.random_addrs[addr_name]

    # page_mapping setters and getters
    def add_page_mapping(self, key: str, val: ParsedPageMapping) -> None:
        self.page_mappings[key] = val

    def get_page_mapping(self, key: str) -> ParsedPageMapping:
        return self.page_mappings[key]

    def get_page_mappings(self) -> dict[str, ParsedPageMapping]:
        return self.page_mappings

    # page_map setters and getters
    def add_page_map(self, map_instance: "PageMap") -> None:
        self.page_maps[map_instance.name] = map_instance

    def get_page_map(self, map_name: str) -> "PageMap":
        return self.page_maps[map_name]

    def get_page_maps(self) -> dict[str, "PageMap"]:
        return self.page_maps

    def get_min_linear_addr_bits_for_page_maps(self, page_map_list: Optional[list[str]] = None) -> int:
        filtered_page_maps = self.page_maps
        if page_map_list is not None:
            filtered_page_maps = set(self.page_maps.keys()).intersection(page_map_list)
        return min([self.page_maps[x].get_linear_addr_bits() for x in filtered_page_maps])

    def get_min_physical_addr_bits_for_page_maps(self, page_map_list: Optional[list[str]] = None) -> int:
        filtered_page_maps = self.page_maps
        if page_map_list is not None:
            filtered_page_maps = set(self.page_maps.keys()).intersection(page_map_list)
        return min([self.page_maps[x].get_physical_addr_bits() for x in filtered_page_maps])

    def get_map(self, map_name: str) -> "PageMap":
        if map_name in self.page_maps:
            return self.page_maps[map_name]
        else:
            raise KeyError(f"{map_name} does not exist in page_maps")

    # Page management
    def page_exists(self, name: str, map_name: str) -> bool:
        """
        Check if page with name exists in the map_name
        """
        return name in self.get_all_pages(map_name=map_name)

    def add_page(self, page: "Page", map_names: list[str]) -> None:
        for map_name in map_names:
            map_inst = self.get_page_map(map_name)
            if (map_inst.name == "map_os") or not page.in_private_map:
                p = page
            else:
                p = copy.copy(page)

            map_inst.add_page(p)

    def get_page(self, page_name: str, map_name: str) -> "Page":
        map_inst = self.get_page_map(map_name)

        return map_inst.get_page(page_name)

    def get_all_pages(self, map_name: str) -> dict[str, "Page"]:
        map_inst = self.get_page_map(map_name)

        return map_inst.pages

    # Processed sections
    def get_sections(self) -> dict[str, SectionInfo]:
        return self.sections

    def get_section(self, section_name: str) -> SectionInfo:
        return self.sections[section_name]

    def add_section(self, section_name: str, address: Optional[int] = None) -> None:
        # Post process section_name and address
        # if section_name is a linear address => we need to find the physical address
        #                       physical addr => we can directly add address
        #                       number => then that's the physical address
        if address is not None:
            # Explicit address provided (e.g. pagetable sections) — VMA == LMA
            self.sections[section_name] = SectionInfo(vma=address, lma=address)
            return

        alias_skip = False
        lin_address: Optional[int] = None
        phys_address: Optional[int] = None
        if section_name.startswith("0x"):
            page_name = f"__auto_lin_{section_name}"
            map_name = "map_os"
            match = re.match(r".*_([^_]+)$", section_name)
            if match:
                map_name = match.group(1)
            map_os_page = self.get_page(page_name=page_name, map_name=map_name)
            lin_address = map_os_page.lin_addr
            phys_address = map_os_page.phys_addr
        else:
            rand_addr_inst = self.get_random_addr(addr_name=section_name)
            if rand_addr_inst.type == RV.AddressType.PHYSICAL:
                lin_address = rand_addr_inst.address
                phys_address = rand_addr_inst.address
            elif rand_addr_inst.type == RV.AddressType.LINEAR:
                map_os_page = self.get_page(page_name=section_name, map_name="map_os")
                if map_os_page.alias:
                    # We want to skip this entry into linker script
                    alias_skip = True
                lin_address = map_os_page.lin_addr
                phys_address = map_os_page.phys_addr
            else:
                raise ValueError(f"Unsupported type {rand_addr_inst.type} in {self.add_section.__name__}")

        if alias_skip:
            log.info(f"Skipping section {section_name} as it is an alias")
            return

        if phys_address is not None:
            assert lin_address is not None
            self.sections[section_name] = SectionInfo(vma=lin_address, lma=phys_address)

    # discrete_test setters and getters
    def add_discrete_test(self, discrete_test: DiscreteTest) -> None:
        test_name = discrete_test.name
        self.discrete_tests[test_name] = discrete_test

    # sections setters and getters
    def add_parsed_sections(self, val: str, index: Optional[int] = None) -> None:
        if index is not None:
            self.parsed_sections.insert(index, val)
        else:
            self.parsed_sections.append(val)

    def get_parsed_sections(self) -> list[str]:
        return self.parsed_sections

    # interrupt handler pointer setters and getters
    def add_interrupt_handler_pointer(self, equate_name: str, default_label: str) -> None:
        """Register a per-vector handler pointer for OpSys to emit in .os_data.

        Only the first registration for a given equate_name is kept; subsequent calls
        for the same vector (e.g. from multiple custom handlers on the same vector) are
        no-ops since all share the same pointer slot.
        """
        if equate_name not in self._interrupt_handler_pointers:
            self._interrupt_handler_pointers[equate_name] = default_label

    def get_interrupt_handler_pointers(self) -> dict[str, str]:
        """Return mapping of equate_name → default_label for all registered handler pointers."""
        return self._interrupt_handler_pointers

    def add_raw_parsed_page_mapping(self, ppm: ParsedPageMapping) -> None:
        self.raw_parsed_page_mappings.append(ppm)

    def get_raw_parsed_page_mappings(self) -> list[ParsedPageMapping]:
        return self.raw_parsed_page_mappings
