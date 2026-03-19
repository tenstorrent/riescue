# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import re
import io
import logging
from pathlib import Path
from typing import Optional
import copy

import riescue.dtest_framework.lib.addrgen as addrgen
import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.selfcheck import SELFCHECK_CHECKSUM_SIZE
from riescue.dtest_framework.runtime.test_execution_logger import TEST_EXECUTION_DATA_PER_HART_SIZE
from riescue.lib.address import Address
from riescue.lib.numgen import NumGen
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.parser import PmaInfo, ParsedPageMapping, Parser, ParsedRandomAddress, ParsedRandomData
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.lib.page_map import Page, PageMap
from riescue.dtest_framework.generator.assembly_writer import AssemblyWriter
from riescue.dtest_framework.artifacts import GeneratedFiles
from riescue.dtest_framework.config.memory import IoRange

log = logging.getLogger(__name__)


class Generator:
    """
    This module interfaces with pool and configurator modules to generate
    1. Randomized Data
    2. Randomized Addresses.
    3. Resolve Page mappings.
    4. Processes init_mem constructs.
    """

    def __init__(self, rng: RandNum, pool: Pool, featmgr: FeatMgr, run_dir=Path.cwd()) -> None:
        self.pool = pool
        self.rng = rng
        self.featmgr = featmgr
        # Auto-enable debug_mode when ;#discrete_debug_test() is present in the test
        if self.pool.get_parsed_discrete_debug_test() is not None:
            self.featmgr.debug_mode = True
        self.numgen = NumGen(self.rng)
        self.numgen.default_genops()

        self.run_dir = run_dir
        self.writer = AssemblyWriter(rng=self.rng, pool=self.pool, run_dir=self.run_dir, featmgr=self.featmgr)

        # Set MISA bits based on enabled features
        self.misa_bits = self.featmgr.get_misa_bits()

        # Output files (excludes inc files)
        self.testname = self.pool.testname
        self.linker_script = self.run_dir / f"{self.pool.testname}.ld"

        # Default sections
        self.os_code_sections = ["runtime"]
        self.os_data_sections = ["os_data", "hart_context"]
        if self.featmgr.selfcheck:
            self.os_data_sections.append("selfcheck_data")
        if self.featmgr.log_test_execution:
            self.os_data_sections.append("test_execution_data")
        self.io_sections = ["io_htif"]  # IO sections to be added
        if self.featmgr.io_maplic_addr is not None:
            self.io_sections.append("maplic")
        if self.featmgr.io_saplic_addr is not None:
            self.io_sections.append("saplic")
        if self.featmgr.io_imsic_mfile_addr is not None:
            self.io_sections.append("imsic_mfile")
        if self.featmgr.io_imsic_sfile_addr is not None:
            self.io_sections.append("imsic_sfile")
        if self.featmgr.debug_mode and self.featmgr.debug_rom_address is not None and self.featmgr.debug_rom_size is not None:
            self.io_sections.append("debug_rom")

        self.c_used_sections = [
            "bss",
            "sbss",
            "sdata",
            "c_text",
            "rela.c_text",
            "c_stack",
            "rodata",
            "data",
            "c_comment",
            "symtab",
            "strtab",
        ]
        self.gcc_cstdlib_sections = [
            "text.srand",
            "text.rand",
            "text.memcpy",
            "text.memcmp" "text.acos",
            "text.asin",
            "text.exp",
            "text.log",
            "text.log10",
            "text.sqrt",
            "text.atan",
            "text.cos",
            "text.sin",
            "text.tan",
            "text.exp2",
            "text.sqrtf",
            "text.cbrt",
            "text.expm1",
            "text.log1p",
            "text.nan",
            "text.log2",
            "text.with_errno",
            "text.xflow",
            "text.__math_uflow",
            "text.__math_may_uflow",
            "text.__math_oflow",
            "text.__math_divzero",
            "text.__math_invalid",
            "text.__math_check_uflow",
            "text.__math_check_oflow",
            "text.__ieee754_sqrt",
            "text.__ieee754_sqrtf",
            "text.fabs",
            "text.finite",
            "text.__kernel_cos",
            "text.__kernel_sin",
            "text.__kernel_tan",
            "text.__ieee754_acos",
            "text.__ieee754_asin",
            "text.__ieee754_exp",
            "text.__ieee754_log",
            "text.__ieee754_log10",
            "text.__ieee754_rem_pio2",
            "text.pow",
            "text.__kernel_rem_pio2",
            "text.__ieee754_pow",
            "text.floor",
            "text.scalbn",
            "text.qsort",
            "text.memmove",
            "text.__fp_lock",
            "text.stdio_exit_handler",
            "text.cleanup_stdio",
            "text.__fp_unlock",
            "sdata._impure_ptr",
            "data._impure_data",
            "data.__sglue",
            "sdata.__malloc_sbrk_base",
            "sdata.__malloc_trim_threshold",
            "data.__malloc_av_",
        ]
        if self.featmgr.add_gcc_cstdlib_sections:
            self.c_used_sections += self.gcc_cstdlib_sections
        self.next_c_section_lin_addr = None

        # Track pre-allocated PMA regions for in_pma=1 addresses
        self._pre_allocated_pma_regions: dict[str, "PmaInfo"] = {}

        memory = featmgr.memory

        # Setup PMAs and PMP regions
        for range in memory.dram_ranges:
            # this should scale to arbitrary PMA attributes
            pma_cacheability = "cacheable" if range.cacheable else "noncacheable"
            self.pool.pma_regions.add_region(
                base=range.start,
                size=range.size,
                type="memory",
                cacheability=pma_cacheability,
            )
            self.pool.pmp_regions.add_region(range=range)

        for range in memory.secure_ranges:
            self.pool.pma_regions.add_region(base=range.start, size=range.size, type="memory")

            # Since this secure region, we need to set bit-55 to 1 for PMP entries only
            self.pool.pmp_regions.add_region(range=range, secure=True)

        for range in memory.io_ranges + memory.reserved_ranges:
            self.pool.pma_regions.add_region(base=range.start, size=range.size, type="io")
            if isinstance(range, IoRange) and (range.name == "htif" or range in memory.io_ranges):
                self.pool.pmp_regions.add_region(range=range)

        # Generate PMA regions from hints and configuration
        self._generate_pma_from_hints(memory)

        # Pre-allocate PMA regions for all in_pma=1 addresses
        # This ensures all PMA regions are determined during initialization
        self._pre_allocate_pma_regions_for_in_pma(memory)

        self.addrgen = addrgen.AddrGen(
            self.rng, memory, self.featmgr.addrgen_limit_indices, self.featmgr.addrgen_limit_way_predictor_multihit, pma_regions=self.pool.pma_regions  # Pass PMA regions for address checking
        )

        # Set the linear and physical address bits
        self.linear_addr_bits = RV.RiscvPagingModes.linear_addr_bits(self.featmgr.paging_mode)
        # If we are in virtualized mode, then we need to use the g-mode if vs-stage is disabled for linear address bits
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                self.linear_addr_bits = min(
                    52,
                    RV.RiscvPagingModes.linear_addr_bits(self.featmgr.paging_g_mode, gstage=True),
                )
            else:
                self.linear_addr_bits = min(
                    self.linear_addr_bits,
                    RV.RiscvPagingModes.linear_addr_bits(self.featmgr.paging_g_mode, gstage=True),
                )
        # Set linear address bits in feature manager
        self.featmgr.linear_addr_bits = self.linear_addr_bits
        log.debug(f"Using linear address bits: {self.linear_addr_bits}")

        # Calculate Physical address bits
        self.physical_addr_bits = RV.RiscvPagingModes.physical_addr_bits(self.featmgr.paging_mode)
        # Since physical addresses are copied into linear when paging_mode is BARE, we need to make sure when in virtualized mode vs-stage=BARE and
        # g-stage is not disabled, we use the g-stage virtual address bits
        # if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                self.physical_addr_bits = min(self.linear_addr_bits, self.physical_addr_bits)
        # Set physical address bits in feature manager
        self.featmgr.physical_addr_bits = self.physical_addr_bits
        log.debug(f"Using physical address bits: {self.physical_addr_bits}")

    def _generate_pma_from_hints(self, memory) -> None:
        """
        Generate PMA regions from hints and configuration.

        This method:
        1. Gets PMA config from cpu_config (if available)
        2. Gets parsed hints from pool
        3. Uses PmaGenerator to generate regions
        4. Adds generated regions to pool

        :param memory: Memory configuration object
        """
        from riescue.dtest_framework.lib.pma_generator import PmaGenerator

        # Get PMA config from feature manager
        pma_config = None
        if hasattr(self.featmgr, "cpu_config") and self.featmgr.cpu_config:
            pma_config = self.featmgr.cpu_config.pma_config

        # Get parsed hints from pool
        parsed_hints = list(self.pool.get_parsed_pma_hints().values())

        # If no hints and no config, skip
        if not parsed_hints and not (pma_config and (pma_config.hints or pma_config.regions)):
            log.debug("No PMA hints or config found, skipping PMA generation from hints")
            return

        # Create generator
        pma_generator = PmaGenerator(pma_config, memory, self.rng)

        # Generate regions
        generated_regions = pma_generator.generate_all(parsed_hints)

        # Add to pool
        for pma_info in generated_regions:
            self.pool.pma_regions.add_entry(pma_info)
            log.info(f"Generated PMA region: {pma_info.pma_name} at 0x{pma_info.pma_address:x}, " f"size 0x{pma_info.pma_size:x}, type={pma_info.pma_memory_type}")

        # Log summary
        total_regions = len(self.pool.pma_regions.consolidated_entries())
        log.info(f"Generated {len(generated_regions)} PMA regions from hints and config. " f"Total PMA regions: {total_regions}")

        # Warn if approaching limit
        max_regions = pma_config.max_regions if pma_config else 15
        if total_regions > max_regions:
            log.warning(f"Total PMA regions ({total_regions}) exceeds max_regions limit ({max_regions}). " f"Some regions may not be used.")

    def _pre_allocate_pma_regions_for_in_pma(self, memory) -> None:
        """
        Pre-allocate PMA regions for all addresses with in_pma=1.

        This ensures all PMA regions are determined during initialization.
        First tries to reuse existing PMA hint regions, then creates new ones if needed.

        :param memory: Memory configuration object
        """
        from riescue.dtest_framework.lib.pma import PmaInfo

        # Get PMA config to check max_regions
        pma_config = None
        if hasattr(self.featmgr, "cpu_config") and self.featmgr.cpu_config:
            pma_config = self.featmgr.cpu_config.pma_config
        max_regions = pma_config.max_regions if pma_config else 15

        # Count current PMA regions (after consolidation)
        current_regions = len(self.pool.pma_regions.consolidated_entries())
        available_slots = max_regions - current_regions

        if available_slots <= 0:
            log.warning(f"No PMA slots available for in_pma=1 addresses. " f"Current regions: {current_regions}, max: {max_regions}")

        # Find all parsed addresses with in_pma=1
        in_pma_addresses = []
        for addr_name, parsed_addr in self.pool.get_parsed_addrs().items():
            if parsed_addr.in_pma:
                in_pma_addresses.append((addr_name, parsed_addr))

        if not in_pma_addresses:
            log.debug("No addresses with in_pma=1 found, skipping pre-allocation")
            return

        log.info(f"Pre-allocating PMA regions for {len(in_pma_addresses)} addresses with in_pma=1")

        # Track pre-allocated regions (name -> PmaInfo)
        self._pre_allocated_pma_regions: dict[str, PmaInfo] = {}
        # Track pre-allocated regions by attributes for sharing within this batch
        pre_allocated_by_attrs: dict[tuple, PmaInfo] = {}

        for addr_name, parsed_addr in in_pma_addresses:
            if available_slots <= 0:
                log.error(f"Cannot pre-allocate PMA region for {addr_name}: " f"no available slots (max_regions={max_regions})")
                continue

            # Ensure pma_info exists
            if parsed_addr.pma_info is None:
                parsed_addr.pma_info = PmaInfo()

            # Set default pma_size if not specified
            if parsed_addr.pma_info.pma_size == 0:
                parsed_addr.pma_info.pma_size = parsed_addr.size

            # Try to find matching existing PMA hint region
            matching_region = self._find_matching_pma_region(parsed_addr.pma_info)

            # If no hint region matches, check if we've already pre-allocated a region with matching attributes
            if not matching_region:
                # Create a key from PMA attributes to check for sharing
                attr_key = (
                    parsed_addr.pma_info.pma_memory_type,
                    parsed_addr.pma_info.pma_cacheability,
                    parsed_addr.pma_info.pma_combining,
                    parsed_addr.pma_info.pma_read,
                    parsed_addr.pma_info.pma_write,
                    parsed_addr.pma_info.pma_execute,
                    parsed_addr.pma_info.pma_amo_type,
                    parsed_addr.pma_info.pma_routing_to,
                )
                # Check if we've already pre-allocated a region with these attributes
                if attr_key in pre_allocated_by_attrs:
                    existing_pre_alloc = pre_allocated_by_attrs[attr_key]
                    # Check if the existing region is large enough
                    if existing_pre_alloc.pma_size >= parsed_addr.pma_info.pma_size:
                        matching_region = existing_pre_alloc
                        log.debug(f"Reusing pre-allocated region '{existing_pre_alloc.pma_name}' " f"for address {addr_name} (matching attributes)")

            if matching_region:
                # Reuse existing PMA hint region
                log.debug(f"Reusing PMA hint region '{matching_region.pma_name}' " f"for address {addr_name}")
                # Store reference to existing region
                self._pre_allocated_pma_regions[addr_name] = matching_region
                # Update parsed_addr to point to existing region
                parsed_addr.pma_info = matching_region
            else:
                # Need to create new PMA region
                # Generate a placeholder address (will be updated when actual address is generated)
                pma_info = PmaInfo(
                    pma_name=f"pma_{addr_name}",
                    pma_address=0,  # Will be set when address is generated
                    pma_size=parsed_addr.pma_info.pma_size,
                    pma_memory_type=parsed_addr.pma_info.pma_memory_type,
                    pma_read=parsed_addr.pma_info.pma_read,
                    pma_write=parsed_addr.pma_info.pma_write,
                    pma_execute=parsed_addr.pma_info.pma_execute,
                    pma_amo_type=parsed_addr.pma_info.pma_amo_type,
                    pma_cacheability=parsed_addr.pma_info.pma_cacheability,
                    pma_combining=parsed_addr.pma_info.pma_combining,
                    pma_routing_to=parsed_addr.pma_info.pma_routing_to,
                    pma_valid=True,
                )

                # Store pre-allocated region
                self._pre_allocated_pma_regions[addr_name] = pma_info
                # Update parsed_addr to point to pre-allocated region
                parsed_addr.pma_info = pma_info

                # Track this region by attributes for potential sharing
                attr_key = (
                    pma_info.pma_memory_type,
                    pma_info.pma_cacheability,
                    pma_info.pma_combining,
                    pma_info.pma_read,
                    pma_info.pma_write,
                    pma_info.pma_execute,
                    pma_info.pma_amo_type,
                    pma_info.pma_routing_to,
                )
                pre_allocated_by_attrs[attr_key] = pma_info

                # Note: We don't add it to pool.pma_regions yet because we don't have the address
                # It will be added in handle_random_addr when the address is generated
                available_slots -= 1
                log.debug(f"Pre-allocated PMA region for {addr_name}: " f"size=0x{pma_info.pma_size:x}, type={pma_info.pma_memory_type}")

        # Log summary
        reused = sum(1 for r in self._pre_allocated_pma_regions.values() if r.pma_address != 0)  # Address != 0 means it's from existing region
        new_regions = len(self._pre_allocated_pma_regions) - reused
        log.info(f"Pre-allocated PMA regions: {reused} reused from hints, {new_regions} new regions. " f"Remaining slots: {available_slots}")

    def _find_matching_pma_region(self, pma_info: "PmaInfo") -> Optional["PmaInfo"]:
        """
        Find an existing PMA region that matches the given PMA attributes.

        :param pma_info: PMA info to match against
        :return: Matching PmaInfo if found, None otherwise
        """
        from riescue.dtest_framework.lib.pma import PmaInfo

        # Check consolidated entries (final PMA regions)
        for region in self.pool.pma_regions.consolidated_entries():
            # Check if attributes match (excluding address and size)
            if (
                region.pma_memory_type == pma_info.pma_memory_type
                and region.pma_read == pma_info.pma_read
                and region.pma_write == pma_info.pma_write
                and region.pma_execute == pma_info.pma_execute
                and region.pma_amo_type == pma_info.pma_amo_type
                and region.pma_cacheability == pma_info.pma_cacheability
                and region.pma_combining == pma_info.pma_combining
                and region.pma_routing_to == pma_info.pma_routing_to
            ):
                # Check if region has enough space (at least the requested size)
                if region.pma_size >= pma_info.pma_size:
                    return region

        return None

    def _generate_address_in_pma_region(self, region: "PmaInfo", constraints: addrgen.AddressConstraint) -> Optional[int]:
        """
        Generate an address within the specified PMA region.

        :param region: PMA region to generate address within
        :param constraints: Address constraints (size, mask, etc.)
        :return: Generated address or None if not possible
        """
        # Ensure address fits within region
        min_addr = region.pma_address
        region_end = region.get_end_address()
        max_addr = region_end - constraints.size

        # Check if region is large enough
        if max_addr < min_addr or region.pma_size < constraints.size:
            return None  # Region too small

        # Ensure we have valid range
        if max_addr <= min_addr:
            return None

        # Generate address within region bounds
        try:
            address = self.rng.random_in_range(min_addr, max_addr)
        except ValueError:
            return None  # Invalid range

        # Apply alignment mask
        address = address & constraints.mask
        # Ensure it's still within bounds after alignment
        if address < min_addr:
            address = (min_addr + constraints.mask) & ~constraints.mask
        if address + constraints.size > region_end:
            return None  # Can't fit after alignment

        return address

    def generate(self, file_in: Path, generated_files: GeneratedFiles):
        """
        Generate random data, randomize addresses, create page mappings, reserve memory, and write all files
        """
        # The order of calling following functions is very important, please do not change
        # unless you know what you are doing
        self.process_raw_parsed_page_mappings()
        self.generate_data()
        self.add_page_maps()
        self.generate_sections()
        self.initialize_page_maps()
        self.handle_res_mem()
        self.generate_addr()
        self.handle_page_mappings()
        self.generate_init_mem()

        self.writer.write(rasm=file_in, generated_files=generated_files)

    def generate_data(self):
        for name, random_data in self.pool.get_parsed_data().items():
            data_type = random_data.type
            rand_val = 0
            if "bits" in data_type:
                num_bits = int(re.findall(r"bits(\d+)", data_type)[0])
                or_mask = random_data.or_mask & (2**num_bits - 1)
                if num_bits == 1:
                    rand_val = (self.rng.get_rand_bits(1) & random_data.and_mask) | or_mask
                else:
                    rand_val = (self.rng.random_in_bitrange(1, num_bits - 1) & random_data.and_mask) | or_mask
            elif "fp" in data_type or "int" in data_type:
                rand_val = self.numgen.rand_num(RV.DataType[data_type.upper()])
            self.pool.add_random_datum(random_data.name, rand_val)

    def handle_res_mem(self):
        for name, parsed_res_mem in self.pool.get_parsed_res_mems().items():
            address = 0
            if common.is_hex_number(parsed_res_mem.start_addr):
                address = int(parsed_res_mem.start_addr, 16)
            else:
                address = int(parsed_res_mem.start_addr)
            size = parsed_res_mem.size

            addr_type = RV.AddressType.LINEAR
            if parsed_res_mem.addr_type == "linear":
                pass
            elif parsed_res_mem.addr_type == "physical":
                addr_type = RV.AddressType.PHYSICAL
            else:
                # TODO: Raise an error here, also add 'memory' support above
                pass

            self.addrgen.reserve_memory(address_type=addr_type, start_address=address, size=size)

    def add_page_maps(self):
        """
        Add all the required page maps here
        """
        # Before generating any addresses, create default map_os paging_map
        # If paging is disabled, we still need to add dummy map_os (let's choose the paging mode to be SV39)
        map_os = PageMap(
            name="map_os",
            pool=self.pool,
            paging_mode=self.featmgr.paging_mode,
            addrgen=self.addrgen,
            featmgr=self.featmgr,
        )
        self.pool.add_page_map(map_instance=map_os)
        # map_os.initialize()

        # If virtualization is enabled and g_stage is not bare, setup map_hyp
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and (self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE or self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE):
            map_hyp = PageMap(
                name="map_hyp",
                pool=self.pool,
                paging_mode=self.featmgr.paging_g_mode,
                addrgen=self.addrgen,
                featmgr=self.featmgr,
                g_map=True,
            )
            self.pool.add_page_map(map_instance=map_hyp)
            # map_hyp.initialize()

        # Handle user defined maps
        for map_name, parsed_map in self.pool.get_parsed_page_maps().items():
            mode = self.featmgr.paging_mode
            if parsed_map.mode != "testmode":
                mode = RV.RiscvPagingModes[parsed_map.mode.upper()]
            map_inst = PageMap(
                name=map_name,
                pool=self.pool,
                paging_mode=mode,
                addrgen=self.addrgen,
                featmgr=self.featmgr,
            )
            self.pool.add_page_map(map_instance=map_inst)
            # map_inst.initialize()

    def initialize_page_maps(self):
        """
        Initialize all the page maps.
        Need to separate out the initialization from the creation of page maps since
        the initialization needs to be called after creating the sections. This is
        because the sections have some fixed addresses and we do not want sptbr (which
        is generated during the initialization of page maps) to overwrite those addresses.
        """
        # Initialize default map_os paging_map
        map_os = self.pool.get_page_map("map_os")
        map_os.initialize()

        # Initialize hypervisor map
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            map_hyp = self.pool.get_page_map("map_hyp")
            map_hyp.initialize()

        # Handle user defined maps
        for map_name, parsed_map in self.pool.get_parsed_page_maps().items():
            map_inst = self.pool.get_page_map(map_name)
            map_inst.initialize()

    def handle_fixed_page_mappings(self, page_mapping):
        """
        Handles syntax like:
        ;#page_mapping(lin_addr=0x5000, phys_addr=0x5000, v=1, r=1, w=1)
        ;#page_mapping(lin_addr=0x5000, phys_addr=&random, v=1, r=1, w=1)
        """
        lin_addr_name = None
        phys_addr_name = None
        lin_addr = None
        phys_addr = None

        # Handle maps
        # Everything goes in map_os, map_hyp by default
        page_maps = ["map_os"]
        # Add hypervisor map if we are in virtualized mode
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            page_maps += ["map_hyp"]
        page_maps += page_mapping.page_maps

        if self.featmgr.private_maps:
            # The command line has --private_maps enabled. This means
            # that the only non-private map is map_os and any of its
            # mappings should be available in all private maps.
            if not page_mapping.in_private_map:
                for map in self.pool.get_page_maps().values():
                    if map.name == "map_os":
                        continue
                    page_maps += [map.name]

        self.randomize_pagesize(page_mapping)
        phys_addr_size = address_size = page_mapping.address_size
        if self.featmgr.reserve_partial_phys_memory:
            phys_addr_size = address_size = 0x1000
        phys_addr_mask = address_mask = page_mapping.address_mask
        # If g-stage s enabled, this physical address becomes GPA. It means that the alignment of
        # this address should be at least the size of gstage_vs_leaf pagesize
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            phys_addr_mask = min(phys_addr_mask, page_mapping.gstage_vs_leaf_address_mask)
            phys_addr_size = max(phys_addr_size, page_mapping.gstage_vs_leaf_address_size)
            if self.featmgr.reserve_partial_phys_memory:
                phys_addr_size = 0x1000

        if page_mapping.lin_addr_specified:
            lin_addr_name = page_mapping.lin_name
            lin_addr = int(page_mapping.lin_addr, 0)
        else:
            lin_addr_name = page_mapping.lin_name

        if page_mapping.phys_addr_specified:
            phys_addr_name = page_mapping.phys_name
            phys_addr = int(page_mapping.phys_addr, 0)
            addr_inst = Address(name=phys_addr_name, type=RV.AddressType.PHYSICAL, address=phys_addr)
            self.pool.add_random_addr(addr_name=phys_addr_name, addr=addr_inst, allow_duplicate=True)

            # If lin_name was specified with with phys_addr in page_mapping, then we need to handle it here
            if not page_mapping.lin_addr_specified:
                lin_addr_name = page_mapping.lin_name
                lin_addr = phys_addr

                # FIXME: Make linear address size and mask same as physical address for now. Eventually, we will need
                # to generate an address for linear address
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.LINEAR,
                    start_address=lin_addr,
                    size=address_size,
                )

                # Also add the random_addr for this linear address
                addr_inst = Address(name=lin_addr_name, type=RV.AddressType.LINEAR, address=lin_addr)
                self.pool.add_random_addr(addr_name=lin_addr_name, addr=addr_inst)

        else:
            # phys_name is specified to be either an actual name or special &random
            # Here, we only handle &random along with a fixed linear address
            if page_mapping.phys_name == "&random":
                if not (self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE):
                    phys_addr_size = RV.RiscvPageSizes.memory(page_mapping.final_pagesize)
                    if self.featmgr.reserve_partial_phys_memory:
                        phys_addr_size = 0x1000
                    phys_addr_mask = RV.RiscvPageSizes.address_mask(page_mapping.final_pagesize)
                    # If g-stage s enabled, this physical address becomes GPA. It means that the alignment of
                    # this address should be at least the size of gstage_vs_leaf pagesize
                    if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                        phys_addr_mask = min(phys_addr_mask, page_mapping.gstage_vs_leaf_address_mask)
                        phys_addr_size = 0x1000
                        if self.featmgr.reserve_partial_phys_memory:
                            phys_addr_size = max(phys_addr_size, page_mapping.gstage_vs_leaf_address_size)
                qualifiers = [RV.AddressQualifiers.ADDRESS_DRAM]
                marked_secure = False
                if self.featmgr.secure_mode:
                    if page_mapping.secure or (self.featmgr.secure_mode and self.rng.with_probability_of(self.featmgr.secure_access_probability)):
                        marked_secure = True
                        qualifiers = [RV.AddressQualifiers.ADDRESS_SECURE]
                        # If machine mode then we need to set the bit-55 of linear and physical address
                        if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
                            lin_addr = lin_addr | (1 << 55)
                phys_addr_name = "__auto_phys_" + lin_addr_name
                phys_addr_c = addrgen.AddressConstraint(
                    type=RV.AddressType.PHYSICAL,
                    qualifiers=qualifiers,
                    bits=min(self.physical_addr_bits, self.pool.get_min_physical_addr_bits_for_page_maps(page_maps)),
                    size=phys_addr_size,
                    mask=phys_addr_mask,
                )
                if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                    phys_addr = lin_addr
                    log.debug(f"Generator: reserving memory: {phys_addr_name}, {phys_addr:016x}, {phys_addr_size:x}")
                    self.addrgen.reserve_memory(
                        address_type=RV.AddressType.PHYSICAL,
                        start_address=phys_addr,
                        size=address_size,
                    )
                else:
                    phys_addr = self.addrgen.generate_address(constraint=phys_addr_c)
                    if marked_secure:
                        phys_addr = phys_addr | (1 << 55)

                addr_inst = Address(name=phys_addr_name, type=RV.AddressType.PHYSICAL, address=phys_addr)

                self.pool.add_random_addr(addr_name=lin_addr_name, addr=addr_inst)

            else:
                # datum.phys_name is already specified
                phys_addr_name = page_mapping.phys_name

        # Also create an instance of Page for this page_mapping
        # TODO: Handle specified page_maps
        p = Page(
            name=lin_addr_name,
            phys_name=phys_addr_name,
            pool=self.pool,
            featmgr=self.featmgr,
            addrgen=self.addrgen,
            maps=page_maps,
        )
        p.lin_addr = lin_addr
        p.size = page_mapping.address_size
        p.pagesize = page_mapping.final_pagesize
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            self.update_page_attrs(page=p, parsed_page_mapping=page_mapping)
            # p.gstage_vs_leaf_pagesize = page_mapping.gstage_vs_leaf_final_pagesize
            # p.gstage_vs_nonleaf_pagesize = page_mapping.gstage_vs_nonleaf_final_pagesize
            # p.gstage_vs_leaf_address_size = page_mapping.gstage_vs_leaf_address_size
            # p.gstage_vs_nonleaf_address_size = page_mapping.gstage_vs_nonleaf_address_size
            # p.gstage_vs_leaf_address_mask = page_mapping.gstage_vs_leaf_address_mask
            # p.gstage_vs_nonleaf_address_mask = page_mapping.gstage_vs_nonleaf_address_mask
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            p.phys_addr = lin_addr
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.PHYSICAL,
                start_address=p.phys_addr,
                size=p.size,
            )
        else:
            p.phys_addr = phys_addr
        self.pass_parsed_attrs(page=p, parsed_page_mapping=page_mapping)
        if page_mapping.in_private_map:
            p.in_private_map = True
        self.pool.add_page(page=p, map_names=page_maps)

        # return tuple([lin_addr_name, lin_addr]), tuple([phys_addr_name, phys_addr])

    def handle_random_page_mappings(self, page_mapping):
        """
        Handle syntax like:
        ;#page_mapping(lin_addr=lin1, phys_addr=&random, v=1, r=1, w=1)
        """

        lin_addr_name = page_mapping.lin_name

        if self.pool.random_addr_exists(lin_addr_name):
            return
        phys_addr_name = "__auto_phys_" + lin_addr_name

        # Handle maps
        page_maps = ["map_os"]  # Everything goes in map_os, map_hyp by default
        # Add hypervisor map if we are in virtualized mode
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            page_maps += ["map_hyp"]
        page_maps += page_mapping.page_maps

        lin_parsed_addr = self.pool.get_parsed_addr(lin_addr_name)
        if lin_parsed_addr.addr_bits is None:
            lin_parsed_addr.addr_bits = min(self.linear_addr_bits, self.pool.get_min_linear_addr_bits_for_page_maps(page_maps))

        self.randomize_pagesize(page_mapping)

        phys_addr_bits = min(self.physical_addr_bits, self.pool.get_min_physical_addr_bits_for_page_maps(page_maps))
        phys_addr_size = address_size = max(page_mapping.address_size, lin_parsed_addr.size)
        if self.featmgr.reserve_partial_phys_memory:
            phys_addr_size = address_size = 0x1000
        phys_addr_mask = address_mask = min(page_mapping.address_mask, lin_parsed_addr.and_mask)

        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            phys_addr_bits = min(lin_parsed_addr.addr_bits, phys_addr_bits)
        else:
            phys_addr_size = RV.RiscvPageSizes.memory(page_mapping.final_pagesize)
            if self.featmgr.reserve_partial_phys_memory:
                phys_addr_size = 0x1000
            phys_addr_mask = RV.RiscvPageSizes.address_mask(page_mapping.final_pagesize)
            # If g-stage s enabled, this physical address becomes GPA. It means that the alignment of
            # this address should be at least the size of gstage_vs_leaf pagesize
            if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                phys_addr_mask = min(phys_addr_mask, page_mapping.gstage_vs_leaf_address_mask)
                phys_addr_size = max(phys_addr_size, page_mapping.gstage_vs_leaf_address_size)
                if self.featmgr.reserve_partial_phys_memory:
                    phys_addr_size = 0x1000

        # # Convert mask like 0x0000003ffffff000 to address_bits like 38 using top set bit
        # # Find the top bit set in the mask
        # addr_bits = address_mask.bit_length()

        # Handle linear address
        lin_addr_c = addrgen.AddressConstraint(
            type=RV.AddressType.LINEAR,
            # address_bits=int(re.findall(r'linear(\d+)',lin_parsed_addr.type)[0]),
            bits=lin_parsed_addr.addr_bits,
            # address_bits=addr_bits,
            size=address_size,
            mask=address_mask,
        )
        lin_addr_orig = self.addrgen.generate_address(constraint=lin_addr_c)
        lin_addr = self.canonicalize_lin_addr(lin_addr_orig)
        log.debug(f"Adding addr: {lin_addr_name}, constraint: {lin_addr_c}")
        log.debug(f"Adding addr: {lin_addr_name}, addr: {lin_addr_orig:016x}")

        # Handle physical address
        qualifiers = [RV.AddressQualifiers.ADDRESS_DRAM]
        marked_secure = False
        if self.featmgr.secure_mode:
            if page_mapping.secure or (self.featmgr.secure_mode and self.rng.with_probability_of(self.featmgr.secure_access_probability)):
                marked_secure = True
                qualifiers = [RV.AddressQualifiers.ADDRESS_SECURE]
        phys_addr_c = addrgen.AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            qualifiers=qualifiers,
            bits=phys_addr_bits,
            size=phys_addr_size,
            mask=phys_addr_mask,
        )
        phys_addr = self.addrgen.generate_address(constraint=phys_addr_c)
        if marked_secure:
            phys_addr = phys_addr | (1 << 55)
        log.debug(f"Adding addr: {phys_addr_name}, constraint: {phys_addr_c}")
        log.debug(f"Adding addr: {phys_addr_name}, addr: {phys_addr:016x}")
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.LINEAR,
                start_address=phys_addr,
                size=address_size,
            )
            # Clear bit-55 and canonicalize the linear address since we are using it as physical address
            lin_addr = phys_addr
            if not marked_secure:
                lin_addr = phys_addr & ~(1 << 55)
            lin_addr = self.canonicalize_lin_addr(lin_addr)
        # else:
        #     phys_addr = addrgen.generate_address(phys_addr_c)
        # Add the linear address ince it's handled for the PAGING_DISABLE case
        addr_inst = Address(name=lin_addr_name, type=RV.AddressType.LINEAR, address=lin_addr)
        self.pool.add_random_addr(addr_name=lin_addr_name, addr=addr_inst)

        addr_inst = Address(name=phys_addr_name, type=RV.AddressType.PHYSICAL, address=phys_addr)

        self.pool.add_random_addr(addr_name=phys_addr_name, addr=addr_inst)

        # Also create an instance of Page for this page_mapping
        # TODO: Handle specified page_maps
        p = Page(
            name=lin_addr_name,
            phys_name=phys_addr_name,
            pool=self.pool,
            featmgr=self.featmgr,
            addrgen=self.addrgen,
            maps=page_maps,
        )
        p.lin_addr = lin_addr
        p.phys_addr = phys_addr
        p.size = page_mapping.address_size
        p.pagesize = page_mapping.final_pagesize
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            self.update_page_attrs(page=p, parsed_page_mapping=page_mapping)
            # p.gstage_vs_leaf_pagesize = page_mapping.gstage_vs_leaf_final_pagesize
            # p.gstage_vs_nonleaf_pagesize = page_mapping.gstage_vs_nonleaf_final_pagesize
            # p.gstage_vs_leaf_address_size = page_mapping.gstage_vs_leaf_address_size
            # p.gstage_vs_nonleaf_address_size = page_mapping.gstage_vs_nonleaf_address_size
            # p.gstage_vs_leaf_address_mask = page_mapping.gstage_vs_leaf_address_mask
            # p.gstage_vs_nonleaf_address_mask = page_mapping.gstage_vs_nonleaf_address_mask

        self.pass_parsed_attrs(page=p, parsed_page_mapping=page_mapping)
        self.pool.add_page(page=p, map_names=page_maps)

        # return tuple([lin_addr_name, lin_addr]), tuple([phys_addr_name, phys_addr])

    def canonicalize_lin_addr(self, lin_addr):
        """
        Canonicalize the linear address to the correct size
        """
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            return lin_addr
        else:
            # Check if the top bit is set for lin_addr
            if common.bitn(lin_addr, self.linear_addr_bits - 1) == 1:
                # Set the top bits for the canonical address
                canon_mask = (1 << (64 - self.linear_addr_bits)) - 1
                log.debug(f"canonicalizing: {lin_addr:x}, {canon_mask:x}")
                return lin_addr | (canon_mask << self.linear_addr_bits)

        return lin_addr

    def handle_random_addr(self, random_addr: ParsedRandomAddress):
        """
        Generate addresses for left over random_addrs
        """
        addr_name = random_addr.name
        addr_type = random_addr.type

        phys_address_size = address_size = random_addr.size
        phys_address_mask = address_mask = random_addr.and_mask
        secure = False

        log.debug(f"handle_random_addr: name={addr_name}, type={addr_type}, phys_address_size=0x{phys_address_size:x}, phys_address_mask=0x{phys_address_mask:x}")
        if self.pool.parsed_page_mapping_with_lin_name_exists(addr_name) and self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
            log.debug(f"Random Address {addr_name} already exists in a page mapping")
            for map_key in self.pool.get_parsed_page_mapping_with_lin_name(addr_name):
                parsed_page_mapping = self.pool.get_parsed_page_mapping(addr_name, map_key)
                address_mask = parsed_page_mapping.address_mask
                log.debug(f"parsed_page_mapping address_mask: 0x{address_mask:x}")
                phys_address_size = RV.RiscvPageSizes.memory(parsed_page_mapping.final_pagesize)
                phys_address_mask = RV.RiscvPageSizes.address_mask(parsed_page_mapping.final_pagesize)
                # If g-stage s enabled, this physical address becomes GPA. It means that the alignment of
                # this address should be at least the size of gstage_vs_leaf pagesize
                if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                    phys_address_mask = min(phys_address_mask, parsed_page_mapping.gstage_vs_leaf_address_mask)
                    phys_address_size = max(phys_address_size, parsed_page_mapping.gstage_vs_leaf_address_size)

                # also update address_bits/mask for the physical address, if fixed address is not proivded in the page_mapping
                if self.pool.parsed_random_addr_exists(addr_name=parsed_page_mapping.phys_name):
                    log.debug(f"parsed_random_addr_exists: {parsed_page_mapping.phys_name}. Using it for {addr_name}")
                    phys_random_addr = self.pool.get_parsed_addr(parsed_page_mapping.phys_name)
                    phys_random_addr.size = address_size
                    phys_random_addr.align = address_mask

        # FIXME: The type should be cast to an enum already
        if addr_type == RV.AddressType.NONE:
            raise Exception(f"Address type is not specified for {addr_name}")
        elif addr_type == RV.AddressType.LINEAR or re.match(r"linear", random_addr.type):
            if random_addr.addr_bits is None:
                random_addr.addr_bits = self.linear_addr_bits
            address_contstaint = addrgen.AddressConstraint(
                type=RV.AddressType.LINEAR,
                # address_bits=int(re.findall(r'linear(\d+)', random_addr.type)[0]),
                bits=random_addr.addr_bits,
                size=address_size,
                mask=address_mask,
                or_mask=random_addr.or_mask,
            )
            log.debug(f"Adding addr: {addr_name}, constraint: {address_contstaint}")
            address_orig = self.addrgen.generate_address(constraint=address_contstaint)
            address = self.canonicalize_lin_addr(address_orig)
            log.debug(f"Adding addr: {addr_name}, addr: {address:016x}")

            addr_inst = Address(name=addr_name, type=RV.AddressType.LINEAR, address=address)

            self.pool.add_random_addr(addr_name=addr_name, addr=addr_inst)

        elif addr_type == RV.AddressType.PHYSICAL or re.match(r"physical", random_addr.type):
            if random_addr.addr_bits is None:
                random_addr.addr_bits = self.physical_addr_bits
            addr_q = [RV.AddressQualifiers.ADDRESS_DRAM]
            # Handle secure addresses
            marked_secure = False
            if self.featmgr.secure_mode:
                log.debug(f"Checking secure for {addr_name}")
                if self.pool.parsed_random_addr_exists(addr_name=addr_name):
                    log.debug(f"Checking secure for {addr_name}")
                    phys_random_addr = self.pool.get_parsed_addr(addr_name)
                    secure = phys_random_addr.secure
                    if secure or (self.featmgr.secure_mode and self.rng.with_probability_of(self.featmgr.secure_access_probability)):
                        log.debug(f"Marking secure for {addr_name}")
                        marked_secure = True
                        addr_q = [RV.AddressQualifiers.ADDRESS_SECURE]
            if random_addr.io:
                addr_q = [RV.AddressQualifiers.ADDRESS_MMIO]
                marked_secure = False
            address_contstaint = addrgen.AddressConstraint(
                type=RV.AddressType.PHYSICAL,
                qualifiers=addr_q,
                # address_bits=int(re.findall(r'physical(\d+)', random_addr.type)[0]),
                bits=random_addr.addr_bits,
                size=phys_address_size,
                mask=phys_address_mask,
                or_mask=random_addr.or_mask,
            )
            log.debug(f"Adding addr: {addr_name}, addr_c: {address_contstaint}")

            # Check if this address has a pre-allocated PMA region
            use_pma_region = False
            if addr_name in self._pre_allocated_pma_regions:
                pre_allocated_region = self._pre_allocated_pma_regions[addr_name]

                # If region already has an address (reused from hint), generate within it
                # But only if the region is large enough and address is set
                if pre_allocated_region.pma_address != 0 and pre_allocated_region.pma_size >= phys_address_size:
                    log.debug(f"Using pre-allocated PMA region '{pre_allocated_region.pma_name}' " f"for address {addr_name}")
                    address = self._generate_address_in_pma_region(pre_allocated_region, address_contstaint)
                    if address is None:
                        log.warning(f"Could not generate address within PMA region " f"'{pre_allocated_region.pma_name}' for {addr_name}, " f"falling back to normal generation")
                    else:
                        use_pma_region = True

            if not use_pma_region:
                # Normal address generation
                try:
                    address = self.addrgen.generate_address(constraint=address_contstaint)
                except Exception as e:
                    log.error(f"Error generating address for {addr_name}")
                    raise e

                # If we have a pre-allocated region (but no address yet), update it
                if addr_name in self._pre_allocated_pma_regions:
                    pre_allocated_region = self._pre_allocated_pma_regions[addr_name]
                    if pre_allocated_region.pma_address == 0:
                        # For shared regions, we need to ensure the address is within the region
                        # But since we don't know the region address yet, we'll set it to the first generated address
                        # and subsequent addresses in the same region will be generated within it
                        pre_allocated_region.pma_address = address
                        # Only add to pool if not already added (might be shared with other addresses)
                        existing_region = self.pool.pma_regions.find_region_for_address(address)
                        if existing_region is None or existing_region.pma_address != address:
                            # Add to pool (will be consolidated later)
                            self.pool.pma_regions.add_entry(pre_allocated_region)
                        log.debug(f"Updated pre-allocated PMA region '{pre_allocated_region.pma_name}' " f"with address 0x{address:x} for {addr_name}")
                    else:
                        # Region already has an address (shared region), try to generate within it
                        # But only if the region is large enough and we can fit the address
                        if pre_allocated_region.pma_size >= phys_address_size:
                            # Calculate valid range within the region
                            region_min = pre_allocated_region.pma_address
                            region_max = pre_allocated_region.get_end_address() - phys_address_size
                            if region_max >= region_min:
                                address_in_region = self._generate_address_in_pma_region(pre_allocated_region, address_contstaint)
                                if address_in_region is not None:
                                    address = address_in_region
                                    log.debug(f"Generated address 0x{address:x} within shared PMA region " f"'{pre_allocated_region.pma_name}' for {addr_name}")

            if marked_secure:
                address = address | (1 << 55)
            if address is None:
                raise ValueError(f"Could not generate address for {addr_name}")

            addr_inst = Address(name=addr_name, type=RV.AddressType.PHYSICAL, address=address)

            log.debug(f"Adding addr: {addr_name}, constraint: {address_contstaint}")
            log.debug(f"Adding addr: {addr_name}, addr: {address:016x}")
            self.pool.add_random_addr(addr_name=addr_name, addr=addr_inst)

            # If paging is disabled, we assign physical addresses to linear, so reserve it in the linear
            # address space now than later
            if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.PHYSICAL,
                    start_address=address,
                    size=address_size,
                )
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.LINEAR,
                    start_address=address,
                    size=address_size,
                )

        else:
            raise ValueError(f"Generator does not support address type: {random_addr.type} {type(random_addr.type)} just yet")

    def randomize_pagesize(self, page_mapping):
        """
        Randomize the page size for the given page mapping. Also calculate memory needed for the
        selected pagesize
        """
        # Log entering this function with page_mapping information
        log.debug(f"Handling page: {page_mapping}")

        # If the paging is disabled, we don't need to do anything with the pagesize
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE and self.featmgr.paging_g_mode == RV.RiscvPagingModes.DISABLE:
            return

        addr_size = 0x1000  # Default to 4KB
        final_pagesize = RV.RiscvPageSizes.S4KB

        log.debug(f"Page: {page_mapping.lin_name}")
        lin_name = page_mapping.lin_name
        if self.pool.random_addr_exists(addr_name=lin_name):
            # If size specified for the linear address then use that as default
            addr_size = self.pool.get_random_addr(addr_name=lin_name).size

        # Check what pagesizes are specified and pick one and set the address size
        # Generally, we want to use main paging mode, but in virtualization if vs-stage is BARE then use g-mode paging
        # paging_mode = self.featmgr.paging_mode
        # if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
        #     if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
        #         paging_mode = self.featmgr.paging_g_mode
        # valid_pagesizes = RV.RiscvPagingModes.supported_pagesizes(paging_mode)
        # valid_fixed_pagesizes_str = [f'{str(x).lower()}page' for x in valid_pagesizes]
        # specified_pagesizes = page_mapping.pagesizes
        # log.debug(f'specified_ps {lin_name}: {specified_pagesizes}, {valid_pagesizes}')
        # # if not RV.RiscvPageSizes.256TB in specified_pagesizes:
        # if not '256tb' in specified_pagesizes:
        #     # Remove 256TB from the randomized pagesizes
        #     if RV.RiscvPageSizes.S256TB in valid_pagesizes:
        #         # Delete 256TB from valid_pagesizes
        #         valid_pagesizes.remove(RV.RiscvPageSizes.S256TB)
        # allowed_pagesizes = dict()
        # # Find out if any of the _<pagesize>page specified
        # for pagesize in specified_pagesizes:
        #     matching = [s for s in valid_fixed_pagesizes_str if pagesize in s]
        #     if matching:
        #         selected_pagesize = RV.RiscvPageSizes['S' + matching[0][:-4].upper()]
        #         allowed_pagesizes[selected_pagesize] = RV.RiscvPageSizes.weights(selected_pagesize)

        # # If we found any pagesizes then pick one
        # if allowed_pagesizes and any(choice_weight > 0 for choice_weight in allowed_pagesizes.values()):
        #     log.debug(f'allowed_ps: {allowed_pagesizes}')
        #     final_pagesize = self.rng.random_choice_weighted(allowed_pagesizes)
        #     addr_size = RV.RiscvPageSizes.memory(final_pagesize)
        # else:
        #     valid_pagesizes_weighted = {pagesize:RV.RiscvPageSizes.weights(pagesize) for pagesize in valid_pagesizes}
        #     final_pagesize = self.rng.random_choice_weighted(valid_pagesizes_weighted)
        #     addr_size = RV.RiscvPageSizes.memory(final_pagesize)
        # addr_mask = RV.RiscvPageSizes.address_mask(final_pagesize)

        specified_pagesizes = page_mapping.pagesizes
        log.debug(f"lin_name: {lin_name}, specified_pagesizes: {specified_pagesizes}")
        (final_pagesize, addr_size, addr_mask) = self.pick_pagesize(
            specified_pagesizes=specified_pagesizes,
            paging_mode=self.featmgr.paging_mode,
            page_mapping=page_mapping,
        )
        log.debug(f"lin_name: {lin_name}, final_pagesize: {final_pagesize}, addr_size: {addr_size:x}, addr_mask: {addr_mask:x}")

        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            if not page_mapping.gstage_vs_leaf_pagesizes:
                # Not specified — use the exact same pagesize as the VS-stage page
                gstage_vs_leaf_final_pagesize = final_pagesize
                gstage_vs_leaf_addr_size = RV.RiscvPageSizes.memory(final_pagesize)
                gstage_vs_leaf_addr_mask = RV.RiscvPageSizes.address_mask(final_pagesize)
                log.debug(f"lin_name_leaf: {lin_name}, gstage_vs_leaf_pagesize defaulting to VS-stage pagesize: {final_pagesize}")
            else:
                specified_pagesizes = page_mapping.gstage_vs_leaf_pagesizes
                log.debug(f"lin_name_leaf: {lin_name}, specified_pagesizes: {specified_pagesizes}")
                (
                    gstage_vs_leaf_final_pagesize,
                    gstage_vs_leaf_addr_size,
                    gstage_vs_leaf_addr_mask,
                ) = self.pick_pagesize(
                    specified_pagesizes=specified_pagesizes,
                    paging_mode=self.featmgr.paging_g_mode,
                    page_mapping=page_mapping,
                )
            log.debug(f"lin_name_nonleaf: {lin_name}, specified_pagesizes: {specified_pagesizes}")
            specified_pagesizes = page_mapping.gstage_vs_nonleaf_pagesizes
            (
                gstage_vs_nonleaf_final_pagesize,
                gstage_vs_nonleaf_addr_size,
                gstage_vs_nonleaf_addr_mask,
            ) = self.pick_pagesize(
                specified_pagesizes=specified_pagesizes,
                paging_mode=self.featmgr.paging_g_mode,
                page_mapping=page_mapping,
            )

        # Generally, we want to use main paging mode, but in virtualization if vs-stage is BARE then use g-mode paging
        paging_mode = self.featmgr.paging_mode
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                paging_mode = self.featmgr.paging_g_mode

        # Handle the non-leaf attributes forcing, i.e. v_level1=0 etc
        for attr in ["v", "a", "d", "g", "u", "x", "w", "r", "n", "pbmt"]:
            (addr_size, addr_mask) = self.randomize_pt_attrs(
                attr=attr,
                page_mapping=page_mapping,
                paging_mode=paging_mode,
                final_pagesize=final_pagesize,
                address_size=addr_size,
                address_mask=addr_mask,
            )

        # Handle gstage atrribute randomization
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            attrs = [
                "v_nonleaf_gnonleaf",
                "v_nonleaf_gleaf",
                "v_leaf_gnonleaf",
                "v_leaf_gleaf",
                "a_nonleaf_gnonleaf",
                "a_nonleaf_gleaf",
                "a_leaf_gnonleaf",
                "a_leaf_gleaf",
                "d_nonleaf_gnonleaf",
                "d_nonleaf_gleaf",
                "d_leaf_gnonleaf",
                "d_leaf_gleaf",
                "g_nonleaf_gnonleaf",
                "g_nonleaf_gleaf",
                "g_leaf_gnonleaf",
                "g_leaf_gleaf",
                "w_nonleaf_gnonleaf",
                "w_nonleaf_gleaf",
                "w_leaf_gnonleaf",
                "w_leaf_gleaf",
                "r_nonleaf_gnonleaf",
                "r_nonleaf_gleaf",
                "r_leaf_gnonleaf",
                "r_leaf_gleaf",
                "x_nonleaf_gnonleaf",
                "x_nonleaf_gleaf",
                "x_leaf_gnonleaf",
                "x_leaf_gleaf",
                "u_nonleaf_gnonleaf",
                "u_nonleaf_gleaf",
                "u_leaf_gnonleaf",
                "u_leaf_gleaf",
                "n_nonleaf_gnonleaf",
                "n_nonleaf_gleaf",
                "n_leaf_gnonleaf",
                "n_leaf_gleaf",
                "pbmt_nonleaf_gnonleaf",
                "pbmt_nonleaf_gleaf",
                "pbmt_leaf_gnonleaf",
                "pbmt_leaf_gleaf",
            ]

            # Setup the U-bit
            for attr in ["u", "r", "w", "x", "a", "d"]:
                self.setup_uwrx_bit(
                    attr,
                    page_mapping=page_mapping,
                    final_pagesize=final_pagesize,
                    gstage_vs_leaf_final_pagesize=gstage_vs_leaf_final_pagesize,
                    gstage_vs_nonleaf_final_pagesize=gstage_vs_nonleaf_final_pagesize,
                )

            for attr in attrs:
                (
                    gstage_vs_leaf_addr_size,
                    gstage_vs_leaf_addr_mask,
                    gstage_vs_nonleaf_addr_size,
                    gstage_vs_nonleaf_addr_mask,
                ) = self.randomize_gstage_pt_attrs(
                    attr=attr,
                    page_mapping=page_mapping,
                    paging_mode_g=self.featmgr.paging_g_mode,
                    paging_mode_vs=self.featmgr.paging_mode,
                    final_pagesize_vs=final_pagesize,
                    final_pagesize_gleaf=gstage_vs_leaf_final_pagesize,
                    final_pagesize_gnonleaf=gstage_vs_nonleaf_final_pagesize,
                    address_size_nonleaf=gstage_vs_nonleaf_addr_size,
                    address_mask_nonleaf=gstage_vs_nonleaf_addr_mask,
                    address_size_leaf=gstage_vs_leaf_addr_size,
                    address_mask_leaf=gstage_vs_leaf_addr_mask,
                )

                # If vs-stage paging is disabled, then we are overloading the gstage_vs_leaf_addr_* variables to hold the values
                if paging_mode == RV.RiscvPagingModes.DISABLE:
                    (addr_size, addr_mask) = (
                        gstage_vs_leaf_addr_size,
                        gstage_vs_leaf_addr_mask,
                    )

        if page_mapping.modify_pt == 1:
            # We need to modify the pagetables for this page
            # ;page_mapping(modify_pt=1)
            #  => reserve a top level pagetable entry, since no other page can share any pagetables with this page anymore
            #  => add storage for pt_pages on PageMap, which will hold pages needed for pagetables. This is because we cannot add
            #       pages to self.pages which iterating over it
            #  => at the time of creating pages for pagetables, add extra pagetable pages based on if the Page has modify_pt
            #  => after calling the create_pagetables() on PageMap.pages, call create_pagetables() on PageMap.pt_pages
            map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode)
            log.debug(f"max_levels: {map_max_levels}, index_bits: {RV.RiscvPagingModes.index_bits(self.featmgr.paging_mode, map_max_levels-1)}")
            addr_size = 2 ** ((RV.RiscvPagingModes.index_bits(paging_mode, map_max_levels - 1))[1])
            addr_mask = 0xFFFFFFFFFFFFFFFF << (common.msb(addr_size)) & 0xFFFFFFFFFFFFFFFF
            log.debug(f"modify_pt: name: {page_mapping.lin_name}, {addr_size:x}")

            # Note about modify_pt with multiple page_maps for the same page:
            #  It would be virtually impossible to have modify_pt to work with multiple page_maps for the same page
            #  The reason is that modify_pt needs to reserve a top level pagetable entry and reserve the enought memory for that.
            #  This means that if a page is part of page_maps in modes sv39 and sv48, it would need to reserve more memory than what's available in
            #  in the sv39 mode and that would fail. So, we will not support this case for now.

        # At the end, we need to only have the final pagesize set to True in the page mapping
        # log.debug(f'randomize pagesize for page: {page_mapping.lin_name}, {gstage_vs_leaf_final_pagesize}, {gstage_vs_nonleaf_final_pagesize}')
        page_mapping.final_pagesize = final_pagesize
        page_mapping.address_size = addr_size
        page_mapping.address_mask = addr_mask
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            log.debug(f"adding for {page_mapping.lin_name} gstage_vs_leaf_final_pagesize: {gstage_vs_leaf_final_pagesize}, gstage_vs_nonleaf_final_pagesize: {gstage_vs_nonleaf_final_pagesize}")
            page_mapping.gstage_vs_nonleaf_final_pagesize = gstage_vs_nonleaf_final_pagesize
            page_mapping.gstage_vs_leaf_final_pagesize = gstage_vs_leaf_final_pagesize
            page_mapping.gstage_vs_nonleaf_address_size = gstage_vs_nonleaf_addr_size
            page_mapping.gstage_vs_leaf_address_size = gstage_vs_leaf_addr_size
            page_mapping.gstage_vs_nonleaf_address_mask = gstage_vs_nonleaf_addr_mask
            page_mapping.gstage_vs_leaf_address_mask = gstage_vs_leaf_addr_mask
            # If the vs-stage size is less than the gstage_vs_leaf_address_size then we need to make it match the bigger size
            if page_mapping.address_size < page_mapping.gstage_vs_leaf_address_size:
                page_mapping.address_size = page_mapping.gstage_vs_leaf_address_size
                page_mapping.address_mask = page_mapping.gstage_vs_leaf_address_mask

        # For physical addresses, there's a case where we will not be looking at page_mapping.address_size while generating the address
        # So, make sure we update the address_size/mask in the random_address instance as well
        if self.pool.parsed_random_addr_exists(page_mapping.phys_name):
            phys_random_addr = self.pool.get_parsed_addr(page_mapping.phys_name)
            phys_random_addr.size = max(page_mapping.address_size, phys_random_addr.size)
            phys_random_addr.and_mask = min(page_mapping.address_mask, phys_random_addr.and_mask)

    def setup_uwrx_bit(
        self,
        attr,
        page_mapping,
        final_pagesize,
        gstage_vs_leaf_final_pagesize,
        gstage_vs_nonleaf_final_pagesize,
    ):
        """
        Setup initial state of U-bit for the page
        """
        map_vs_max_levels = RV.RiscvPagingModes.max_levels(self.featmgr.paging_mode)
        map_max_levels = RV.RiscvPagingModes.max_levels(self.featmgr.paging_g_mode)
        pt_vs_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize)
        pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(gstage_vs_leaf_final_pagesize)

        # For most bits, clearing it will generate a fault, e.g. v=0. But, for g-bit common case is to set it to 0
        # If current mode is super mode, then significant bit is 1, else 0
        if self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
            significant_bit_value = 1
        # First mark the correct value for U-bit since we can't set it at the time-0 since we don't know the priv-mode
        # By default risc-v requires U=1 at nonleaf and U=<if_usermode> at leaf
        # Mark the U-bit for the nonleaf pagetable
        log.debug(f"Handling {page_mapping.lin_name} {range(map_vs_max_levels, pt_vs_leaf_level)}, {range(map_max_levels, pt_leaf_level)}")
        log.debug(f"{page_mapping.lin_name}, final_pagesize_vs: {final_pagesize}, final_pagesize_gleaf: {gstage_vs_leaf_final_pagesize}, final_pagesize_gnonleaf: {gstage_vs_nonleaf_final_pagesize}")
        for vs_level in range(pt_vs_leaf_level, map_vs_max_levels):
            # Need to change the pt_leaf level below based on the g_level leaf/nonleaf
            if vs_level == pt_vs_leaf_level:
                pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(gstage_vs_leaf_final_pagesize)
            else:
                pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(gstage_vs_nonleaf_final_pagesize)
            for g_level in range(pt_leaf_level, map_max_levels):
                if g_level == pt_leaf_level:
                    # Leaf level U-bit needs to be 1 since all the G-stage translations are treated as user
                    log.debug(f"Setup_u_bit: {page_mapping.lin_name} u_level{vs_level}_glevel{g_level} = 1")
                    # All bits w/r/u need to be default to 1 for gstage leaf level
                    bit_val = 1
                    # if attr == 'x':
                    #     bit_val = 0
                    page_mapping.__setattr__(f"{attr}_level{vs_level}_glevel{g_level}", bit_val)
                    # If vs-stage is disabled, we need to omit _level*_glevel* attributes
                    if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                        page_mapping.__setattr__(f"{attr}_level{g_level}", bit_val)
                else:
                    # For all the non-leaf levels, all of these bits need to 0 by default
                    page_mapping.__setattr__(f"{attr}_level{vs_level}_glevel{g_level}", 0)
                    if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                        page_mapping.__setattr__(f"{attr}_level{g_level}", 0)
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            bit_val = 1
            # if attr == 'x':
            #     bit_val = 0
            pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize)
            page_mapping.__setattr__(f"{attr}_level{pt_leaf_level}", bit_val)

    def randomize_gstage_pt_attrs(
        self,
        attr,
        page_mapping,
        paging_mode_g,
        paging_mode_vs,
        final_pagesize_vs,
        final_pagesize_gleaf,
        final_pagesize_gnonleaf,
        address_size_leaf,
        address_mask_leaf,
        address_size_nonleaf,
        address_mask_nonleaf,
    ):
        (addr_size_leaf, addr_mask_leaf) = (address_size_leaf, address_mask_leaf)
        (addr_size_nonleaf, addr_mask_nonleaf) = (
            address_size_nonleaf,
            address_mask_nonleaf,
        )
        # if page_mapping.v_nonleaf == 0:
        map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode_g)
        base_attr = attr.split("_")[0]
        # For most bits, clearing it will generate a fault, e.g. v=0, w=0, r=0, x=0. But, for g-bit common case is to set it to 0
        insignificant_value = 1
        # if base_attr in ['g', 'x']:
        if base_attr in ["g", "n", "pbmt"]:
            insignificant_value = 0
        if base_attr in ["u"]:
            # If current mode is super mode, then significant bit is 1, else 0
            if self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
                insignificant_value = 0

        attr_value = page_mapping.__getattribute__(attr)
        if attr_value is None or attr_value == insignificant_value:
            return (
                addr_size_leaf,
                addr_mask_leaf,
                addr_size_nonleaf,
                addr_mask_nonleaf,
            )

        # If _nonleaf_ specified then we need to find which vs-level to randomize the attribute for
        if "_nonleaf_" in attr:
            # If vs-stage is disabled, then randomization should be taken care at the g-stage map itself
            if paging_mode_vs == RV.RiscvPagingModes.DISABLE:
                page_mapping.__setattr__(f"{base_attr}_nonleaf", attr_value)
                (addr_size_leaf, addr_mask_leaf) = self.randomize_pt_attrs(
                    attr=base_attr,
                    page_mapping=page_mapping,
                    paging_mode=paging_mode_g,
                    final_pagesize=final_pagesize_gleaf,
                    address_size=addr_size_leaf,
                    address_mask=addr_mask_leaf,
                )

                return (
                    addr_size_leaf,
                    addr_mask_leaf,
                    addr_size_nonleaf,
                    addr_mask_nonleaf,
                )

            map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode_vs)
            levels_this_page = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs)
            available_pt_levels = map_max_levels - levels_this_page
            log.debug(f"{page_mapping.lin_name} {map_max_levels}, {available_pt_levels}, {final_pagesize_vs}")
            if available_pt_levels == 1:
                # If we only have one option, just take that. This happens when you're at the largest pagesize
                # e.g. SV57 and 256TB combo, you would only have one nonleaf pagetable. See the example in above comment
                possible_pt_levels = map_max_levels - 1
                possible_pt_levels = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs)
                levels_range = list(range(possible_pt_levels, possible_pt_levels + 1))
            else:
                possible_pt_levels = map_max_levels - available_pt_levels
                levels_range = list(range(levels_this_page + 1, map_max_levels))

            # Now pick the lowest number with the higest probability between range [possible_pt_levels, map_max_levels]
            # Pick the lowest level possible by default
            rnd_pt_level = levels_range[0]
            # 5% of times pick other levels
            if self.rng.with_probability_of(0):
                rnd_pt_level = self.rng.random_entry_in(levels_range)
            # rnd_pt_level = self.rng.random_entry_in(list(range(possible_pt_levels, map_max_levels)))
            log.debug(f"{page_mapping.lin_name} final_pagesize_vs: {final_pagesize_vs}, {rnd_pt_level}")
            vslevel_to_randomize = rnd_pt_level
            log.debug(f"{page_mapping.lin_name} levels_range: {levels_range}, vslevel_to_randomize: {vslevel_to_randomize}")
            vs_addr_size = 2 ** ((RV.RiscvPagingModes.index_bits(paging_mode_vs, rnd_pt_level))[1])
            if vs_addr_size > address_size_leaf:
                addr_size_leaf = vs_addr_size
                addr_mask_leaf = 0xFFFFFFFFFFFFFFFF << (common.msb(vs_addr_size)) & 0xFFFFFFFFFFFFFFFF

        elif "_leaf_" in attr:
            # If vs-stage is disabled, then randomization should be taken care at the g-stage map itself
            if paging_mode_vs == RV.RiscvPagingModes.DISABLE:
                g_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_gleaf)
                page_mapping.__setattr__(f"{base_attr}_level{g_leaf_level}", attr_value)
                log.debug(f"Setting {base_attr}: {attr_value} for {page_mapping.lin_name}")
                # (addr_size_leaf, addr_mask_leaf) = self.randomize_pt_attrs(attr=attr[0],
                #                                                  page_mapping=page_mapping,
                #                                                  paging_mode=paging_mode_g,
                #                                                  final_pagesize=final_pagesize_gleaf,
                #                                                  address_size=addr_size_leaf,
                #                                                  address_mask=addr_mask_leaf)

                return (
                    addr_size_leaf,
                    addr_mask_leaf,
                    addr_size_nonleaf,
                    addr_mask_nonleaf,
                )

            vslevel_to_randomize = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs)
            log.debug(f"{page_mapping.lin_name}, vs_level: {vslevel_to_randomize}, attr: {attr}")

        # Let's handle gleaf and gnonleaf, i.e. final decision on the g-stage level
        if "_gnonleaf" in attr:
            # if paging_mode_vs == RV.RiscvPagingModes.DISABLE:
            #     return (addr_size_leaf, addr_mask_leaf, addr_size_nonleaf, addr_mask_nonleaf)
            map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode_g)
            levels_this_page = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_gleaf)
            log.debug(f"{page_mapping.lin_name}, map_max_levels: {map_max_levels}, final_pagesize_gleaf: {final_pagesize_gleaf}, {levels_this_page}")
            # if '_leaf_' in attr:
            #     levels_this_page = RV.RiscvPageSizes.pt_leaf_level(final_pagesize_gleaf)
            available_pt_levels = map_max_levels - levels_this_page
            log.debug(f"{map_max_levels}, {available_pt_levels}, {final_pagesize_gnonleaf}")
            if available_pt_levels == 1:
                # If we only have one option, just take that. This happens when you're at the largest pagesize
                # e.g. SV57 and 256TB combo, you would only have one nonleaf pagetable. See the example in above comment
                possible_pt_levels = map_max_levels - 1
                levels_range = list(range(possible_pt_levels - 1, possible_pt_levels))
            else:
                possible_pt_levels = map_max_levels - available_pt_levels
                levels_range = list(range(levels_this_page + 1, map_max_levels))
            # Now pick the lowest number with the higest probability between range [possible_pt_levels, map_max_levels]
            # Pick the lowest level possible by default
            log.debug(f"{page_mapping.lin_name} levels_range: {levels_range}")
            rnd_pt_level = levels_range[0]
            # Now there's a case when rbd_pt_level is more than available levels for vs-stage. This matters because currently
            # we have gpa=gva, so we need to make sure that the g-stage level is not more than the vs-stage level
            if rnd_pt_level > vslevel_to_randomize:
                # In this case, pick the lowest level available in g-stage
                rnd_pt_level = levels_this_page
            # 5% of times pick other levels
            if self.rng.with_probability_of(0):
                rnd_pt_level = self.rng.random_entry_in(levels_range)

            glevel_to_randomize = rnd_pt_level
            g_addr_size = 2 ** ((RV.RiscvPagingModes.index_bits(paging_mode_g, rnd_pt_level))[1])
            page_info = " ".join(
                [
                    f"{page_mapping.lin_name} levels_range: {levels_range}",
                    f"vs_level_to_randomize: {vslevel_to_randomize}",
                    f"glevel_to_randomize: {glevel_to_randomize}",
                    f"{final_pagesize_gnonleaf}",
                    f"{final_pagesize_gleaf}",
                    f"{g_addr_size:x}",
                ]
            )
            log.debug(page_info)
            # We need to update addr_size and addr_mask for leaf or nonleaf based on the vslevel_to_randomize
            if "_nonleaf_" in attr:
                if g_addr_size > address_size_nonleaf:
                    addr_size_nonleaf = g_addr_size
                    addr_mask_nonleaf = 0xFFFFFFFFFFFFFFFF << (common.msb(g_addr_size)) & 0xFFFFFFFFFFFFFFFF
            else:  # _leaf_
                if g_addr_size > address_size_leaf:
                    addr_size_leaf = g_addr_size
                    addr_mask_leaf = 0xFFFFFFFFFFFFFFFF << (common.msb(g_addr_size)) & 0xFFFFFFFFFFFFFFFF
            log.debug(
                " ".join(
                    [
                        f"{page_mapping.lin_name}, pagesize:{final_pagesize_gnonleaf},",
                        f"levels: {levels_range} level: {glevel_to_randomize}",
                        f"addr_size_nonleaf: {addr_size_nonleaf:x}, addr_mask_nonleaf: {addr_mask_nonleaf:x}",
                    ]
                ),
            )

        elif "_gleaf" in attr:
            # pagesize = final_pagesize_gleaf if '_leaf_' in attr else final_pagesize_gnonleaf
            pagesize = final_pagesize_gnonleaf
            if vslevel_to_randomize == RV.RiscvPageSizes.pt_leaf_level(final_pagesize_vs):
                pagesize = final_pagesize_gleaf
            # pagesize = final_pagesize_gleaf
            glevel_to_randomize = RV.RiscvPageSizes.pt_leaf_level(pagesize)
            log.debug(f"{page_mapping.lin_name}, pagesize: {pagesize}, {attr}, {glevel_to_randomize}, {final_pagesize_gleaf}, {final_pagesize_gnonleaf}")
            # We don't need to update addr_size or addr_mask since this is the last level and pagesize logic would have
            # taken care of it

        # Now construct the final attribute name for page_mapping, so it can be used in pagetables, e.g. v_level2_glevel1
        pt_attr = f"{base_attr}_level{vslevel_to_randomize}_glevel{glevel_to_randomize}"
        log.debug(
            " ".join(
                [
                    f"setting {pt_attr} for {page_mapping.lin_name} with {attr_value},",
                    f"reserving: {addr_size_leaf:x}, {addr_mask_leaf:x}, {addr_size_nonleaf:x}, {addr_mask_nonleaf:x},",
                    f"pt_attr: {pt_attr}",
                ]
            ),
        )
        page_mapping.__setattr__(f"{pt_attr}", attr_value)

        return (addr_size_leaf, addr_mask_leaf, addr_size_nonleaf, addr_mask_nonleaf)

    def randomize_pt_attrs(
        self,
        attr,
        page_mapping,
        paging_mode,
        final_pagesize,
        address_size,
        address_mask,
    ):
        """
        Handle all the randomization of the pagetable attributes including g-stage randomization.
        We need to handle the g-stage randomization here since we fork off g-stage pagetable pages from the vs-stage
        """
        (addr_size, addr_mask) = (address_size, address_mask)

        # # Handle g-stage randomization
        # gstage_attr = False
        # # if attr matches with 'gleaf' or 'gnonleaf' appears then it's a g-stage attribute
        # patterns = [r'gleaf', r'gnonleaf']
        # if any(re.search(pattern, attr) for pattern in patterns):
        #     gstage_attr = True

        return self.pt_attrs_helper(attr, page_mapping, paging_mode, final_pagesize, addr_size, addr_mask)

    def pt_attrs_helper(
        self,
        attr,
        page_mapping,
        paging_mode,
        final_pagesize,
        address_size,
        address_mask,
    ):
        (addr_size, addr_mask) = (address_size, address_mask)
        # if page_mapping.v_nonleaf == 0:
        map_max_levels = RV.RiscvPagingModes.max_levels(paging_mode)
        pt_leaf_level = RV.RiscvPageSizes.pt_leaf_level(final_pagesize)
        # For most bits, clearing it will generate a fault, e.g. v=0. But, for g-bit common case is to set it to 0
        insignificant_value = 1
        leaf_bit_val = 1
        # if attr in ['g', 'x']:
        if attr in ["g", "n", "pbmt"]:
            insignificant_value = 0
        if attr[0] in ["u"]:
            # If current mode is super mode, then significant bit is 1, else 0
            if self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
                insignificant_value = 0
                leaf_bit_val = 0
            # If vs-paging is disabled, then we need to set the U-bit to 1
            if paging_mode == RV.RiscvPagingModes.DISABLE:
                leaf_bit_val = 1
        # if attr[0] in ['x']:
        #     leaf_bit_val = 0
        # First mark the correct value for U-bit since we can't set it at the time-0 since we don't know the priv-mode
        # By default risc-v requires U=0 at nonleaf and U=<if_usermode> at leaf
        # Mark the U-bit for the nonleaf pagetable
        if attr[0] in ["u", "r", "w", "x", "a", "d"]:
            for level in range(pt_leaf_level, map_max_levels):
                if level == pt_leaf_level:
                    # Leaf level U-bit needs to be 1 since all the G-stage translations are treated as user
                    page_mapping.__setattr__(f"{attr}_level{level}", leaf_bit_val)
                else:
                    page_mapping.__setattr__(f"{attr}_level{level}", 0)

        # Need to handle v=0, x=0, r=0 etc cases where we need to transfer thainformation to actual level
        # based on the current pagesize, e.g. v=0 will translate to v_level0=0 for a 4kb page
        attr_value = page_mapping.__getattribute__(attr)
        if attr_value is not None and attr_value != insignificant_value:
            # Make sure we update the correct level based on the pagesize
            page_mapping.__setattr__(f"{attr}_level{pt_leaf_level}", attr_value)
            return (addr_size, addr_mask)

        nonleaf_value = page_mapping.__getattribute__(f"{attr}_nonleaf")
        if nonleaf_value is not None and nonleaf_value != insignificant_value:
            # Based on the current pagesize, select a level to clear the v-bit
            # 4KB and SV57
            # map_max_levels (based on current paging map) = 5
            # available_pt_levels (based on current pagesize) = 5 - 0 = 5 [0,1,2,3,4] => 1:5
            #
            # 2MB and SV57
            # map_max_levels = 5
            # available_pt_levels = 5 - 1 = 4 [1,2,3,4] => 2:5
            #
            # 1GB and SV57
            # map_max_levels = 5
            # available_pt_levels = 5 - 2 = 3 [2,3,4] => 3:5 [page_max_levels-available_pt_levels+1:page_max_levels]
            #
            # 512GB and SV57
            # map_max_levels = 5
            # available_pt_levels = 5 - 3 = 2 [3,4] => 4:5 [page_max_levels-available_pt_levels+1:page_max_levels]
            #
            # 256GB and SV57
            # map_max_levels = 5
            # available_pt_levels = 5 - 4 = 1 [4] => 4:5 [page_max_levels-available_pt_levels+1:page_max_levels] will not work
            # This combo needs a special handling since we only have one non-leaf

            available_pt_levels = map_max_levels - RV.RiscvPageSizes.pt_leaf_level(final_pagesize)
            if available_pt_levels == 1:
                # If we only have one option, just take that. This happens when you're at the largest pagesize
                # e.g. SV57 and 256TB combo, you would only have one nonleaf pagetable. See the example in above comment
                possible_pt_levels = map_max_levels - 1
                levels_range = list(range(possible_pt_levels - 1, map_max_levels))
            else:
                possible_pt_levels = map_max_levels - available_pt_levels
                levels_range = list(range(possible_pt_levels + 1, map_max_levels))
            # rnd_pt_level = self.rng.random_entry_in(list(range(possible_pt_levels, map_max_levels)))
            log.debug(
                " ".join(
                    [
                        f"{page_mapping.lin_name}, {paging_mode}",
                        f"levels_range: {levels_range}",
                        f"{map_max_levels}, {RV.RiscvPageSizes.pt_leaf_level(final_pagesize)},",
                        f"{available_pt_levels}, {possible_pt_levels}",
                        f"{final_pagesize}",
                    ]
                ),
            )
            # Pick the lowest level possible by default
            rnd_pt_level = levels_range[0]
            # 5% of times pick other levels
            if self.rng.with_probability_of(0):
                rnd_pt_level = self.rng.random_entry_in(levels_range)

            addr_size = 2 ** ((RV.RiscvPagingModes.index_bits(paging_mode, rnd_pt_level))[1])
            addr_mask = 0xFFFFFFFFFFFFFFFF << (common.msb(addr_size)) & 0xFFFFFFFFFFFFFFFF

            # Make sure we only update the address size/mask if it's bigger than the current one, otherwise it's already covered by
            # the current address size/mask
            if addr_size < address_size:
                addr_size = address_size
                addr_mask = address_mask
            log.debug(f"rnd_pt for {page_mapping.lin_name}, {attr}: {rnd_pt_level}, {addr_size:x}, {0xffffffffffffffff << (common.msb(addr_size)):x}")
            page_mapping.__setattr__(f"{attr}_level{rnd_pt_level}", nonleaf_value)

        return (addr_size, addr_mask)

    def pick_pagesize(self, specified_pagesizes, paging_mode, page_mapping):
        # Check what pagesizes are specified and pick one and set the address size
        # Generally, we want to use main paging mode, but in virtualization if vs-stage is BARE then use g-mode paging
        # paging_mode = self.featmgr.paging_mode
        valid_pagesizes = RV.RiscvPagingModes.supported_pagesizes(paging_mode)
        # If paging is disabled and valid_pagesizes is empty, then we need to add at least 4kb page
        if not valid_pagesizes:
            valid_pagesizes = [RV.RiscvPageSizes.S4KB]
        valid_fixed_pagesizes_str = [f"{str(x).lower()}page" for x in valid_pagesizes]
        # specified_pagesizes = page_mapping.pagesizes
        log.debug(f"specified_ps: {specified_pagesizes}, {valid_pagesizes}")
        # if not RV.RiscvPageSizes.256TB in specified_pagesizes:
        if "256tb" not in specified_pagesizes:
            # Remove 256TB from the randomized pagesizes
            if RV.RiscvPageSizes.S256TB in valid_pagesizes:
                # Delete 256TB from valid_pagesizes
                valid_pagesizes.remove(RV.RiscvPageSizes.S256TB)
        allowed_pagesizes = dict()
        # Find out if any of the _<pagesize>page specified
        for pagesize in specified_pagesizes:
            matching = [s for s in valid_fixed_pagesizes_str if pagesize in s]
            if matching:
                selected_pagesize = RV.RiscvPageSizes["S" + matching[0][:-4].upper()]
                allowed_pagesizes[selected_pagesize] = RV.RiscvPageSizes.weights(selected_pagesize)

        # If we found any pagesizes then pick one
        log.debug(f"allowed_ps: {allowed_pagesizes}")
        if allowed_pagesizes and any(choice_weight > 0 for choice_weight in allowed_pagesizes.values()):
            log.debug(f"allowed_ps: {allowed_pagesizes}")
            final_pagesize = self.rng.random_choice_weighted(allowed_pagesizes)
            addr_size = RV.RiscvPageSizes.memory(final_pagesize)
        else:
            valid_pagesizes_weighted = {pagesize: RV.RiscvPageSizes.weights(pagesize) for pagesize in valid_pagesizes}
            final_pagesize = self.rng.random_choice_weighted(valid_pagesizes_weighted)
            addr_size = RV.RiscvPageSizes.memory(final_pagesize)
        addr_mask = RV.RiscvPageSizes.address_mask(final_pagesize)

        log.debug(f"final_pagesize: {final_pagesize}, addr_size: {addr_size:016x}, addr_mask: {addr_mask:016x}\n")
        # Force final pagesize to 4kb if switch says so
        has_linked_pages = False
        map_key = "map_os"
        if page_mapping.in_private_map:
            map_key = page_mapping.page_maps[0]
        if self.pool.parsed_page_mapping_exists(page_mapping.lin_name, map_key) and self.pool.get_parsed_page_mapping(page_mapping.lin_name, map_key):
            ppm = self.pool.get_parsed_page_mapping(page_mapping.lin_name, map_key)
            if ppm.has_linked_ppms:
                log.debug(f"Has linked pages: {page_mapping.lin_name}")
                has_linked_pages = True
        if self.featmgr.all_4kb_pages and not has_linked_pages:
            log.debug(f"Forcing 4KB pagesize for {page_mapping.lin_name}")
            final_pagesize = RV.RiscvPageSizes.S4KB
            addr_size = RV.RiscvPageSizes.memory(final_pagesize)
            addr_mask = RV.RiscvPageSizes.address_mask(final_pagesize)

        return (final_pagesize, addr_size, addr_mask)

    def handle_normal_page_mappings(self, page_mapping):
        """
        Randomize the pagesize for the given page mapping. Also calculate memory needed for linear and physical
        addresses associated with the page mapping
        """
        # Randomize the pagesize for the given page mapping
        self.randomize_pagesize(page_mapping=page_mapping)

        # The above function also updates the page_mapping.address_size, so update the size of the linear and physical
        lin_rand_addr = self.pool.get_parsed_addr(key=page_mapping.lin_name)
        lin_rand_addr.size = max(page_mapping.address_size, lin_rand_addr.size)
        phys_rand_addr = self.pool.get_parsed_addr(key=page_mapping.phys_name)
        phys_rand_addr.size = max(page_mapping.address_size, phys_rand_addr.size)
        has_linked_pages = False
        map_key = "map_os"
        if page_mapping.in_private_map:
            map_key = page_mapping.page_maps[0]
        if self.pool.parsed_page_mapping_exists(page_mapping.lin_name, map_key) and self.pool.get_parsed_page_mapping(page_mapping.lin_name, map_key):
            ppm = self.pool.get_parsed_page_mapping(page_mapping.lin_name, map_key)
            if ppm.has_linked_ppms:
                log.debug(f"Has linked pages: {page_mapping.lin_name}")
                has_linked_pages = True
        if self.featmgr.all_4kb_pages and not has_linked_pages:
            log.debug(f"Forcing 4KB pagesize for {page_mapping.lin_name}")
            page_mapping.final_pagesize = RV.RiscvPageSizes.S4KB
            page_mapping.address_size = 0x1000
            page_mapping.address_mask = 0xFFFFFFFFFFFFF000
            lin_rand_addr.size = 0x1000
            phys_rand_addr.size = 0x1000

        # lin_addr_name   = page_mapping.lin_name
        # phys_addr_name  = page_mapping.phys_name
        # phys_addr_attr = self.pool.get_parsed_addr(phys_addr_name)
        # lin_addr_attr  = self.pool.get_parsed_addr(lin_addr_name)
        # phys_addr_c    = addrgen.AddressConstraint(
        #                         address_type=RV.AddressType.PHYSICAL,
        #                         address_qualifiers=[RV.AddressQualifiers.ADDRESS_DRAM],
        #                         address_bits=int(re.findall(r'physical(\d+)',phys_addr_attr.type)[0])
        #                     )
        # lin_addr_c     = addrgen.AddressConstraint(
        #                         address_type=RV.AddressType.LINEAR,
        #                         address_bits=int(re.findall(r'linear(\d+)',lin_addr_attr.type)[0])
        #                     )
        # return tuple([lin_addr_name, addrgen.generate_address(lin_addr_c)]), tuple([phys_addr_name, addrgen.generate_address(phys_addr_c)])

    def process_raw_parsed_page_mappings(self):
        for ppm_inst in self.pool.get_raw_parsed_page_mappings():
            fixed_addr_specified = False
            attrs = ppm_inst.gen_time_proc
            line = ppm_inst.source_line
            pagemap_str = ppm_inst.pagemap_str
            for var_val in attrs:
                var = var_val[0]
                val = var_val[1]
                if re.match(r"lin_name", var):
                    if re.match(r"lin_name", var):
                        # Do additional check if we have linked page_mappings
                        match = re.search(r"(\w+)\+(\w+)", val)
                        if match:
                            # Find the parent parsedpagemap instance
                            parent = match.group(1)
                            map_key = "map_os"
                            if self.featmgr.private_maps:
                                if len(ppm_inst.page_maps) != 0:
                                    map_key = ppm_inst.page_maps[0]
                            parent_ppm_inst = self.pool.get_parsed_page_mapping(parent, map_key)
                            parent_ppm_inst.linked_page_mappings.append(ppm_inst)
                            parent_ppm_inst.has_linked_ppms = True
                            ppm_inst.is_linked = True
                            # The text after + in lin_name has the starting offset for the linked ppm
                            ppm_inst.linked_ppm_offset = int(match.group(2), 16)

                if re.match(r"lin_addr|phys_addr", var):
                    if re.match(r"lin_addr|phys_addr", var):
                        if common.is_number(val):
                            if var == "lin_addr":
                                ppm_inst.lin_name = f"__auto_lin_{val}"
                            elif var == "phys_addr":
                                ppm_inst.phys_name = f"__auto_phys_{val}"
                            fixed_addr_specified = True
                        else:
                            suggestion = ""
                            if val == "&random":
                                suggestion = f"Maybe you should use phys_name={val}\n"
                            raise ValueError(f"{var}={val} must be a number for entry:\n \t{line}\n {suggestion}")
                        if var == "lin_addr":
                            ppm_inst.lin_addr_specified = True
                        elif var == "phys_addr":
                            ppm_inst.phys_addr_specified = True

                if var == "phys_name":
                    if val == "&random":
                        ppm_inst.resolve_priority = 20
                    # Check if phys_name exists in any value instance in ppm
                    exists = any(ppm.phys_name == val for ppm in self.pool.get_parsed_page_mappings().values())
                    if exists:
                        log.debug(f"phys_name {val} already exists in another page mapping, marking as alias case")
                        ppm_inst.alias = True

            if fixed_addr_specified:
                ppm_inst.resolve_priority = 15

            if self.featmgr.private_maps:
                # Make a separate copy of ppm_inst for each map
                if pagemap_str != "":
                    ppm_inst.in_private_map = True
                if len(ppm_inst.page_maps) != 0:
                    for pm in ppm_inst.page_maps[1:]:
                        ppm_copy = copy.deepcopy(ppm_inst)
                        if ppm_copy.is_linked:
                            parent_ppm_inst.linked_page_mappings.append(ppm_copy)
                        ppm_copy.page_maps = [pm]
                        if not ppm_copy.is_linked:
                            ppm_copy.lin_name += f"_{pm}"
                            ppm_copy.phys_name += f"_{pm}"
                            self.pool.add_parsed_page_mapping(ppm_copy)
                    ppm_inst.page_maps = [ppm_inst.page_maps[0]]
                    ppm_inst.lin_name += f"_{ppm_inst.page_maps[0]}"

            if not ppm_inst.is_linked:
                self.pool.add_parsed_page_mapping(ppm_inst)

    def handle_page_mappings(self, parsed_page_mappings=None):
        """
        All the addresses should have been generated before this. So, we just need to create a Page instance for
        each of the page_mappings and create pagetables here
        """
        if parsed_page_mappings is None:
            parsed_page_mappings = self.pool.get_parsed_page_mappings()

        for lin_map_name, parsed_page_mapping in parsed_page_mappings.items():
            if parsed_page_mapping.resolve_priority == 0:
                # Handle maps
                # Everything goes in map_os, map_hyp by default
                page_maps = ["map_os"]
                # Add hypervisor map if we are in virtualized mode
                if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                    page_maps += ["map_hyp"]
                page_maps += parsed_page_mapping.page_maps

                log.debug(f"Page: {lin_map_name}, alias={parsed_page_mapping.alias}")
                # Create the Page instance for this type of page_mapping entry
                p = Page(
                    name=lin_map_name[0],
                    phys_name=parsed_page_mapping.phys_name,
                    pool=self.pool,
                    featmgr=self.featmgr,
                    addrgen=self.addrgen,
                    maps=page_maps,
                    alias=parsed_page_mapping.alias,
                )
                p.lin_addr = self.pool.get_random_addr(p.name).address
                p.size = parsed_page_mapping.address_size
                p.pagesize = parsed_page_mapping.final_pagesize
                if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                    self.update_page_attrs(page=p, parsed_page_mapping=parsed_page_mapping)
                    # p.gstage_vs_leaf_pagesize = parsed_page_mapping.gstage_vs_leaf_final_pagesize
                    # p.gstage_vs_nonleaf_pagesize = parsed_page_mapping.gstage_vs_nonleaf_final_pagesize
                    # p.gstage_vs_leaf_address_size = parsed_page_mapping.gstage_vs_leaf_address_size
                    # p.gstage_vs_nonleaf_address_size = parsed_page_mapping.gstage_vs_nonleaf_address_size
                    # p.gstage_vs_leaf_address_mask = parsed_page_mapping.gstage_vs_leaf_address_mask
                    # p.gstage_vs_nonleaf_address_mask = parsed_page_mapping.gstage_vs_nonleaf_address_mask
                    log.debug(f"set for {p.name} gstage_vs_leaf_final_pagesize: {p.gstage_vs_leaf_pagesize}, gstage_vs_nonleaf_final_pagesize: {p.gstage_vs_nonleaf_pagesize}")
                p.phys_addr = None
                p.phys_addr = self.pool.get_random_addr(p.phys_name).address
                if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                    # p.phys_addr = p.lin_addr
                    p.lin_addr = self.canonicalize_lin_addr(p.phys_addr)
                    self.addrgen.reserve_memory(
                        address_type=RV.AddressType.PHYSICAL,
                        start_address=p.phys_addr,
                        size=p.size,
                    )
                    self.addrgen.reserve_memory(
                        address_type=RV.AddressType.LINEAR,
                        start_address=p.phys_addr,
                        size=p.size,
                    )

                    # Also update the random instance address which is used for printing addresses into the various files
                    rand_addr_inst = self.pool.get_random_addr(p.name)
                    rand_addr_inst.address = p.lin_addr
                # else:
                #     p.phys_addr = self.pool.get_random_addr(p.phys_name).address
                #     log.debug(f'_Handle_page_mappings: {p.phys_name}')
                #     log.debug(f'_Handle_page_mappings: {p.name}, {p.phys_addr:016x}')
                self.pass_parsed_attrs(page=p, parsed_page_mapping=parsed_page_mapping)
                self.pool.add_page(page=p, map_names=page_maps)
                log.debug(f"Handle_page_mappings: {p.name}, {p.phys_addr:016x}")
                log.debug(f"{p}")

                # Handle linked consecutive pages
                if parsed_page_mapping.has_linked_ppms:
                    for linked_ppm in parsed_page_mapping.linked_page_mappings:
                        # Handle maps
                        # Everything goes in map_os, map_hyp by default
                        page_maps = ["map_os"]
                        # Add hypervisor map if we are in virtualized mode
                        if (
                            self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED
                            and self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE
                            and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE
                        ):
                            page_maps += ["map_hyp"]
                        page_maps += linked_ppm.page_maps

                        self.randomize_pagesize(linked_ppm)
                        # Get the start offset for this page
                        offset = linked_ppm.linked_ppm_offset

                        # Check if we have reserved enough memory for the original page
                        if p.size + offset <= parsed_page_mapping.address_size:
                            message = f"Enough memory was not reserved for {parsed_page_mapping.lin_name}. "
                            message += f"This is because {linked_ppm.lin_name} is continuous page to {parsed_page_mapping.lin_name}, which "
                            message += f"needs 0x{offset:x} memory on top of 0x{parsed_page_mapping.address_size:x} specified int the {parsed_page_mapping.lin_name}"
                            raise ValueError(message)
                        p_linked = Page(
                            name=linked_ppm.lin_name,
                            phys_name=linked_ppm.phys_name,
                            pool=self.pool,
                            featmgr=self.featmgr,
                            addrgen=self.addrgen,
                            maps=page_maps,
                        )
                        p_linked.lin_addr = p.lin_addr + offset
                        p_linked.size = offset
                        p_linked.pagesize = linked_ppm.final_pagesize
                        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                            self.update_page_attrs(page=p_linked, parsed_page_mapping=linked_ppm)
                            # p_linked.gstage_vs_leaf_pagesize = linked_ppm.gstage_vs_leaf_final_pagesize
                            # p_linked.gstage_vs_nonleaf_pagesize = linked_ppm.gstage_vs_nonleaf_final_pagesize
                            # p_linked.gstage_vs_nonleaf_address_size = linked_ppm.gstage_vs_nonleaf_address_size
                            # p_linked.gstage_vs_leaf_address_size = linked_ppm.gstage_vs_leaf_address_size
                            # p_linked.gstage_vs_nonleaf_address_mask = linked_ppm.gstage_vs_nonleaf_address_mask
                            # p_linked.gstage_vs_leaf_address_mask = linked_ppm.gstage_vs_leaf_address_mask

                        p_linked.phys_addr = p.phys_addr + offset
                        self.pass_parsed_attrs(page=p_linked, parsed_page_mapping=linked_ppm)
                        self.pool.add_page(page=p_linked, map_names=page_maps)

        # Print the pages in the map
        # map_os = self.pool.get_page_map(map_name="map_os")
        # for _, page in map_os.pages.items():
        #     print(f'name: {page.name}, lin_addr: {page.lin_addr:016x}, phys_addr: {page.phys_addr:016x}')

    def update_page_attrs(self, page, parsed_page_mapping):
        # Copy select attributes from parsed_page_mapping to page
        # Group related attributes together for better maintainability
        gstage_vs_attrs = [
            "gstage_vs_leaf_pagesize",
            "gstage_vs_nonleaf_pagesize",
            "gstage_vs_leaf_address_size",
            "gstage_vs_nonleaf_address_size",
            "gstage_vs_leaf_address_mask",
            "gstage_vs_nonleaf_address_mask",
        ]

        # Generate all level combinations
        levels = range(5)  # 0 to 4
        level_types = ["v", "u", "a", "d", "r", "w", "x", "pbmt", "n"]

        # Generate all level combinations
        level_attrs = []
        for level_type in level_types:
            # Generate single level attributes
            level_attrs.extend([f"{level_type}_level{i}" for i in levels])

            # Generate level-glevel combinations
            level_attrs.extend([f"{level_type}_level{v}_glevel{g}" for v in levels for g in levels])

        # Combine all attributes
        all_attrs = gstage_vs_attrs + level_attrs

        # Copy attributes from parsed_page_mapping to page
        for attr in all_attrs:
            ppm_attr = attr
            if ppm_attr in ["gstage_vs_leaf_pagesize", "gstage_vs_nonleaf_pagesize"]:
                # Replace pagesize with final_pagesize
                ppm_attr = ppm_attr.replace("pagesize", "final_pagesize")

            # Only update page if the parsed_page_mapping has the attribute
            if hasattr(parsed_page_mapping, ppm_attr):
                log.debug(f"{page.name}, attr: {attr}, ppm_attr: {parsed_page_mapping.__getattribute__(ppm_attr)}")
                page.__setattr__(attr, parsed_page_mapping.__getattribute__(ppm_attr))

    def pass_parsed_attrs(self, page, parsed_page_mapping):
        # This method will copy values of page attributes from parsed_page_mapping instance into page instance
        log.debug(f"attrs: {page.attrs}")
        for attr_name in page.attrs.keys():
            # copy each attr
            if hasattr(parsed_page_mapping, attr_name):
                ppm_attr_val = parsed_page_mapping.__getattribute__(attr_name)
                if ppm_attr_val is None:
                    if attr_name == "u":
                        if self.featmgr.priv_mode == RV.RiscvPrivileges.USER:
                            ppm_attr_val = 1
                        else:
                            ppm_attr_val = 0
                page.attrs[attr_name] = ppm_attr_val
                log.debug(f"Setting {attr_name} = {ppm_attr_val} for {page.name}")

    def handle_sections(self, section):
        sections_to_process = ["data", "runtime", "code"] + self.os_data_sections + self.io_sections
        if self.featmgr.c_used:
            sections_to_process += self.c_used_sections

        if section not in sections_to_process:
            return

        if section == "debug_rom":
            # Only reserve and create page tables when test has ;#discrete_debug_test()
            if self.pool.get_parsed_discrete_debug_test() is None:
                return
            # Reserve memory and create page tables via add_section_handler (same pattern as io_htif/maplic/saplic)
            page_size = 0x1000
            size = self.featmgr.debug_rom_size
            size_aligned = max((size + page_size - 1) & ~(page_size - 1), page_size)
            (lin_addr, phys_addr) = self.add_section_handler(
                name="debug_rom",
                size=size_aligned,
                iscode=True,
                start_addr=self.featmgr.debug_rom_address,
            )
            return

        if self.featmgr.wysiwyg and self.pool.random_addr_exists("code"):
            return

        num_runtime_pages = 16
        num_user_runtime_pages = 1 if self.featmgr.priv_mode == RV.RiscvPrivileges.USER else 0
        num_runtime_s_pages = 4 if RV.RiscvPrivileges.SUPER in self.featmgr.supported_priv_modes else 0

        num_code_pages = 128
        if self.featmgr.more_os_pages:
            num_code_pages = 500

        # C text pages
        num_c_text_pages = 30 if self.featmgr.c_used else 0

        # Also add some default super and user code pages
        num_super_code_pages = 8
        num_user_code_pages = 8
        num_machine_code_pages = 8

        # csr pages
        num_machine_csr_pages = 1
        num_super_csr_pages = 1

        # pte pages
        # Each pte machine code entry generates at most ~256 bytes (sv57 worst case: ~58 instructions
        # of page-table walk code + 2 dispatch instructions). With alloc_size=0x1000, that is at most
        # 16 entries per page. Compute the number of pages dynamically so the section never overflows
        # into the adjacent page-table section.
        _pte_entry_count = len(self.pool.get_parsed_read_ptes()) + len(self.pool.get_parsed_write_ptes())
        num_machine_pte_pages = max(1, (_pte_entry_count + 15) // 16)

        if section == "runtime":
            alloc_size = 0x1000
            alloc_addr = self.featmgr.reset_pc

            reserve_page_count = num_runtime_pages + num_user_runtime_pages + num_runtime_s_pages
            if not self.featmgr.randomize_code_location:
                # Allocate .code immediately after .runtime
                # This is useful to avoid far jumps.
                reserve_page_count += (
                    num_code_pages + num_super_code_pages + num_user_code_pages + num_machine_code_pages + num_machine_csr_pages + num_super_csr_pages + num_machine_pte_pages + num_c_text_pages
                )

            # First allocate space for ALL the pages. Then add all the individual pages
            (lin_addr, phys_addr) = self.add_section_handler(
                name="runtime",
                size=alloc_size * reserve_page_count,
                iscode=True,
                phys_name="_section_runtime",
                start_addr=alloc_addr,
                skip_page_map=True,
            )

            alloc_lin_addr = lin_addr + alloc_size
            alloc_phys_addr = phys_addr + alloc_size
            for i in range(1, num_runtime_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section__runtime_{i}",
                    size=alloc_size,
                    iscode=True,
                    phys_name="",
                    start_addr=alloc_phys_addr,
                    skip_page_map=True,
                )
                # increment alloc addresses to the next available address
                alloc_lin_addr = lin_addr + alloc_size
                alloc_phys_addr = phys_addr + alloc_size

            if num_user_runtime_pages:
                # Allocate runtime_user contiguous with runtime
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="runtime_user",
                    size=alloc_size * num_user_runtime_pages,
                    iscode=True,
                    always_user=True,
                    phys_name="",
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                )

                alloc_lin_addr = lin_addr + alloc_size
                alloc_phys_addr = phys_addr + alloc_size
                for i in range(1, num_user_runtime_pages):
                    (lin_addr, phys_addr) = self.add_section_handler(
                        name=f"runtime_user_{i}",
                        size=alloc_size,
                        iscode=True,
                        always_user=True,
                        phys_name="",
                        start_addr=alloc_phys_addr,
                        start_lin_addr=alloc_lin_addr,
                    )
                    alloc_lin_addr = lin_addr + alloc_size
                    alloc_phys_addr = phys_addr + alloc_size
                # increment alloc addresses to the next available address
                alloc_lin_addr = lin_addr + alloc_size

            if num_runtime_s_pages:
                # Allocate runtime_s contiguous with runtime and runtime_user
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="runtime_s",
                    size=alloc_size * num_runtime_s_pages,
                    iscode=True,
                    always_super=True,
                    phys_name="",
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                )
                alloc_lin_addr = lin_addr + alloc_size
                alloc_phys_addr = phys_addr + alloc_size
                for i in range(1, num_runtime_s_pages):
                    (lin_addr, phys_addr) = self.add_section_handler(
                        name=f"runtime_s_{i}",
                        size=alloc_size,
                        iscode=True,
                        always_super=True,
                        phys_name="",
                        start_addr=alloc_phys_addr,
                        start_lin_addr=alloc_lin_addr,
                    )
                    alloc_lin_addr = lin_addr + alloc_size
                    alloc_phys_addr = phys_addr + alloc_size

            self.runtime_end_addr = alloc_lin_addr

        elif section == "code":
            alloc_size = 0x1000
            code_skip_page_map = self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE
            # Randomize code offset with interesting values for cacheline alignment of 64B and in anywhere in
            # the first 5-cachelines
            if self.featmgr.wysiwyg:
                # Allocate code at the reset_pc
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="code",
                    # +1 at the end to account for loader code
                    size=alloc_size
                    * (
                        num_code_pages
                        + num_super_code_pages
                        + num_user_code_pages
                        + num_machine_code_pages
                        + num_machine_csr_pages
                        + num_super_csr_pages
                        + num_machine_pte_pages
                        + num_c_text_pages
                        + 1
                    ),
                    iscode=True,
                    start_addr=self.featmgr.reset_pc,
                    phys_name="__section_code",
                    skip_page_map=code_skip_page_map,
                )
            elif self.featmgr.randomize_code_location:
                # First allocate space for ALL the code pages. Then add all the individual pages
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="code",
                    size=alloc_size
                    * (num_code_pages + num_super_code_pages + num_user_code_pages + num_machine_code_pages + num_machine_csr_pages + num_super_csr_pages + num_machine_pte_pages + num_c_text_pages),
                    iscode=True,
                    phys_name="__section_code",
                    skip_page_map=code_skip_page_map,
                )
            else:
                # Reserve PA for all code pages upfront; sub-sections use contiguous block
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="code",
                    size=alloc_size
                    * (num_code_pages + num_super_code_pages + num_user_code_pages + num_machine_code_pages + num_machine_csr_pages + num_super_csr_pages + num_machine_pte_pages + num_c_text_pages),
                    iscode=True,
                    start_lin_addr=self.runtime_end_addr,
                    phys_name="__section_code",
                    skip_page_map=code_skip_page_map,
                    identity_map=self.featmgr.identity_map_code,
                )
            alloc_lin_addr = lin_addr + alloc_size
            alloc_phys_addr = phys_addr + alloc_size

            for i in range(1, num_code_pages):
                if code_skip_page_map:
                    (lin_addr, phys_addr) = self.add_section_handler(
                        name=f"__section__code_{i}",
                        size=alloc_size,
                        iscode=True,
                        start_addr=alloc_lin_addr,
                        phys_name="",
                        skip_page_map=code_skip_page_map,
                    )
                else:
                    (lin_addr, phys_addr) = self.add_section_handler(
                        name=f"__section__code_{i}",
                        size=alloc_size,
                        iscode=True,
                        start_addr=alloc_phys_addr,
                        start_lin_addr=alloc_lin_addr,
                        phys_name="",
                        skip_page_map=code_skip_page_map,
                    )
                # increment alloc addrs to the next available address
                alloc_lin_addr += alloc_size
                alloc_phys_addr += alloc_size
            # Every call to add_section_handler already adds the sections to the pool
            # self.pool.add_section(section_name="code")

            # Add super pages with name starting code_super
            for i in range(num_super_code_pages):
                page_name = f"code_super_{i}"
                page_phys_name = f"__section_{page_name}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=alloc_size,
                    iscode=True,
                    always_super=True,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    phys_name=page_phys_name,
                )
                # increment alloc addrs to the next available address
                alloc_lin_addr += alloc_size
                alloc_phys_addr += alloc_size
                # Every call to add_section_handler already adds the sections to the pool
                # self.pool.add_section(section_name=page_name)

            # Add user pages with name starting code_user
            for i in range(num_user_code_pages):
                page_name = f"code_user_{i}"
                page_phys_name = f"__section_{page_name}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=alloc_size,
                    iscode=True,
                    always_user=True,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    phys_name=page_phys_name,
                )
                # increment alloc addrs to the next available address
                alloc_lin_addr += alloc_size
                alloc_phys_addr += alloc_size
                # Every call to add_section_handler already adds the sections to the pool
                # self.pool.add_section(section_name=page_name)

            # Add machine code pages
            for i in range(num_machine_code_pages):
                page_name = f"code_machine_{i}"
                page_phys_name = f"__section_{page_name}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=alloc_size,
                    iscode=True,
                    always_user=True,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    phys_name=page_phys_name,
                    skip_page_map=True,
                )
                # Only advance the physical pointer; these sections do not consume VA space.
                alloc_lin_addr += alloc_size
                alloc_phys_addr += alloc_size
                # Every call to add_section_handler already adds the sections to the pool
                # self.pool.add_section(section_name=page_name)

            # CSR Jump Table
            for i in range(num_machine_csr_pages):
                page_name = f"csr_machine_{i}"
                page_phys_name = f"__section_{page_name}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=alloc_size,
                    iscode=True,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    phys_name=page_phys_name,
                    skip_page_map=True,
                )
                alloc_lin_addr += alloc_size
                alloc_phys_addr += alloc_size

            for i in range(num_super_csr_pages):
                page_name = f"csr_super_{i}"
                page_phys_name = f"__section_{page_name}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=alloc_size,
                    iscode=True,
                    always_super=True,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    phys_name=page_phys_name,
                )
                alloc_lin_addr += alloc_size
                alloc_phys_addr += alloc_size

            # PTE pages for machine mode
            for i in range(num_machine_pte_pages):
                page_name = f"pte_machine_{i}"
                page_phys_name = f"__section_{page_name}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=alloc_size,
                    iscode=True,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    phys_name=page_phys_name,
                    skip_page_map=True,
                )
                alloc_lin_addr += alloc_size
                alloc_phys_addr += alloc_size

            # If C code is used, allocate .text section immediately after .code
            # so that jal calls from .code into .text stay within range
            if self.featmgr.c_used:
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="text",
                    size=alloc_size,
                    iscode=True,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    phys_name="__section_text",
                )
                alloc_lin_addr = lin_addr + alloc_size
                alloc_phys_addr = phys_addr + alloc_size
                for i in range(1, num_c_text_pages):
                    (lin_addr, phys_addr) = self.add_section_handler(
                        name=f"__section__text_{i}",
                        size=alloc_size,
                        iscode=True,
                        start_addr=alloc_phys_addr,
                        start_lin_addr=alloc_lin_addr,
                        phys_name="",
                    )
                    alloc_lin_addr += alloc_size
                    alloc_phys_addr += alloc_size

        elif section == "data" or section == "os_data":
            data_page_size = 0x1000
            size = 0x2000
            if self.featmgr.c_used:
                size = 0xF0000
            (lin_addr, phys_addr) = self.add_section_handler(
                name=section,
                size=size,
                iscode=False,
                phys_name=f"__section_{section}",
            )

            alloc_lin_addr = lin_addr + data_page_size
            alloc_phys_addr = phys_addr + data_page_size
            for i in range(1, size // data_page_size):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section_{section}_{i}",
                    size=data_page_size,
                    iscode=False,
                    phys_name="",
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                )
                # increment alloc addrs to the next available address
                alloc_lin_addr = lin_addr + data_page_size
                alloc_phys_addr = phys_addr + data_page_size

        # SUGGESTION tie the size of these sections to the size of the c code compiled sections that more or less use this exclusively.
        elif self.featmgr.c_used and section in self.c_used_sections:

            def get_page_count(section: str):
                page_count = 30
                if section in ["bss"]:
                    if self.featmgr.big_bss:
                        page_count = 3080
                    elif self.featmgr.small_bss:
                        page_count = 200
                elif section in ["c_stack"]:
                    page_count = 400
                elif section in ["rodata"]:
                    page_count = 75
                elif section in self.gcc_cstdlib_sections:
                    page_count = 1
                return page_count

            # C sections: contiguous VA, independent PA per section
            num_total_pages = get_page_count(section)
            page_name = section
            is_code = True if "runtime" in section else False
            phys_page_name = f"__section_{section}"
            (lin_addr, phys_addr) = self.add_section_handler(
                name=page_name,
                size=0x1000 * num_total_pages,
                iscode=is_code,
                phys_name=phys_page_name,
                always_user=not is_code,
                start_lin_addr=self.next_c_section_lin_addr,
            )
            self.next_c_section_lin_addr = lin_addr + 0x1000 * num_total_pages

            # add page mapping for all pages
            alloc_lin_addr = lin_addr + 0x1000
            alloc_phys_addr = phys_addr + 0x1000
            for i in range(1, num_total_pages):
                page_name = f"{section}_{i}"
                phys_page_name = f"__section_{section}_{i}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=0x1000,
                    iscode=is_code,
                    phys_name=phys_page_name,
                    always_user=not is_code,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                )
                alloc_lin_addr = lin_addr + 0x1000
                alloc_phys_addr = phys_addr + 0x1000

        elif "hart_context" == section:
            # handle hart context and stack
            # Acts as hart-local storage for test runtime environment
            # Used in single and multi-process tests
            page_size = 0x1000

            ctx = "hart_context"

            # Compute actual total section size to determine number of pages needed
            total_size = self.writer.runtime.variable_manager.get_hart_context_total_size()
            num_pages = (total_size + page_size - 1) // page_size

            # Allocate full block; sub-pages (skip_linker) fill remainder
            (lin_addr, phys_addr) = self.add_section_handler(
                name=ctx,
                size=page_size * num_pages,
                iscode=False,
                phys_name=f"__section_{ctx}",
            )

            # Additional pages (page table entries only, no linker sections)
            alloc_lin_addr = lin_addr + page_size
            alloc_phys_addr = phys_addr + page_size
            for i in range(1, num_pages):
                page_name = f"__page_{ctx}_{i}"
                phys_page_name = f"__section___page_{ctx}_{i}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=page_size,
                    iscode=False,
                    phys_name=phys_page_name,
                    start_addr=alloc_phys_addr,
                    start_lin_addr=alloc_lin_addr,
                    skip_linker=True,
                )
                alloc_lin_addr = lin_addr + page_size
                alloc_phys_addr = phys_addr + page_size

            # Hart stacks (each is already 1 page)
            for hid in range(self.featmgr.num_cpus):
                stk = f"hart_stack_{hid}"
                self.add_section_handler(
                    name=stk,
                    size=page_size,
                    iscode=False,
                    phys_name=f"__section_{stk}",
                )

        elif section == "io_htif":
            (lin_addr, phys_addr) = self.add_section_handler(
                name="io_htif",
                size=0x10,
                iscode=False,
                identity_map=True,
                start_addr=self.featmgr.io_htif_addr,
            )

        elif section == "maplic":
            page_size = 0x1000
            n_pages = int((self.featmgr.io_maplic_size + (page_size - 1)) / page_size)
            start_addr = self.featmgr.io_maplic_addr
            for i in range(n_pages):
                (lin_addr, phys_addr) = self.add_section_handler(name=f"maplic_{i}", size=self.featmgr.io_maplic_size, iscode=False, identity_map=True, start_addr=start_addr)
                start_addr += page_size

        elif section == "saplic":
            page_size = 0x1000
            n_pages = int((self.featmgr.io_saplic_size + (page_size - 1)) / page_size)
            start_addr = self.featmgr.io_saplic_addr
            for i in range(n_pages):
                (lin_addr, phys_addr) = self.add_section_handler(name=f"saplic_{i}", size=self.featmgr.io_saplic_size, iscode=False, identity_map=True, start_addr=start_addr)
                start_addr += page_size

        elif section == "imsic_mfile":
            start_addr = self.featmgr.io_imsic_mfile_addr
            page_size = 0x1000
            for i in range(self.featmgr.num_cpus):
                (lin_addr, phys_addr) = self.add_section_handler(name=f"imsic_mfile_{i}", size=page_size, iscode=False, identity_map=True, start_addr=start_addr)
                start_addr += self.featmgr.io_imsic_mfile_stride

        elif section == "imsic_sfile":
            start_addr = self.featmgr.io_imsic_sfile_addr
            page_size = 0x1000
            for i in range(self.featmgr.num_cpus):
                (lin_addr, phys_addr) = self.add_section_handler(name=f"imsic_sfile_{i}", size=page_size, iscode=False, identity_map=True, start_addr=start_addr)
                start_addr += self.featmgr.io_imsic_sfile_stride

        elif section == "text":
            pass

        elif section == "selfcheck_data":
            # Selfcheck data section for saving architectural state
            per_hart_size = 8 + SELFCHECK_CHECKSUM_SIZE * self.featmgr.repeat_times * (len(self.pool.discrete_tests) + 2)

            # Total size = per_hart_size * num_cpus, rounded up to 4KB
            total_size = per_hart_size * self.featmgr.num_cpus
            total_size = ((total_size + 0xFFF) // 0x1000) * 0x1000
            num_selfcheck_pages = total_size // 0x1000
            alloc_size = 0x1000

            (lin_addr, phys_addr) = self.add_section_handler(
                name="selfcheck_data",
                size=total_size,
                iscode=False,
                phys_name="__section_selfcheck_data",
            )
            alloc_addr = lin_addr + alloc_size
            for i in range(1, num_selfcheck_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section__selfcheck_data_{i}",
                    size=alloc_size,
                    iscode=False,
                    phys_name="",
                    start_addr=alloc_addr,
                )
                alloc_addr = lin_addr + alloc_size
        elif section == "test_execution_data":
            # Per-hart: test_counter(8) + current_test_ptr(8) + (test_tracker, m_time)[256]
            page_size = 0x1000
            num_pages = (self.featmgr.num_cpus * TEST_EXECUTION_DATA_PER_HART_SIZE + 0xFFF) // page_size
            (lin_addr, phys_addr) = self.add_section_handler(
                name="test_execution_data",
                size=num_pages * page_size,
                iscode=False,
                phys_name="__section_test_execution_data",
                skip_page_map=True,
            )
            alloc_addr = lin_addr + page_size
            for i in range(1, num_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__page_test_execution_data_{i}",
                    size=page_size,
                    iscode=False,
                    phys_name="",
                    start_addr=alloc_addr,
                    skip_page_map=True,
                )
                alloc_addr = lin_addr + page_size
        else:
            log.error(f"Unknown section: {section}")

    def add_section_handler(
        self,
        name,
        size,
        iscode,
        start_addr=None,
        start_lin_addr=None,
        identity_map=False,
        always_super=False,
        always_user=False,
        phys_name="",
        skip_linker=False,
        skip_page_map=False,
    ):
        """
        Add a section with name, size and number of pages information
        Inputs:
          - name
          - size
          - iscode
          - start_addr: Physical address start (when specified, reserves PA)
          - start_lin_addr: Virtual address start (when specified with start_addr, allows VA != PA;
                            when specified alone, fixes VA and generates PA randomly;
                            when paging is disabled/skip_page_map=True, used as PA directly)
          - identity_map: When True, forces VA == PA
          - skip_page_map: When True, don't add to page tables (e.g. M-mode code sections)
        """
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE and self.featmgr.paging_g_mode == RV.RiscvPagingModes.DISABLE:
            skip_page_map = True

        # When identity_map is requested, normalize so that the start_addr-only path handles it
        if identity_map:
            if start_addr is None and start_lin_addr is not None:
                start_addr = start_lin_addr
            start_lin_addr = None

        # Generate address or reserve memory
        lin_addr = None
        phys_addr = None

        if skip_page_map:
            # No page tables: allocate only PA, use it for both linker LMA and VMA
            if start_addr is not None:
                phys_addr = start_addr
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.PHYSICAL,
                    start_address=start_addr,
                    size=size,
                )
                # VMA == PA for skip_page_map sections; reserve linear space too to avoid
                # linker errors from data sections being assigned VMAs that overlap this region.
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.LINEAR,
                    start_address=start_addr,
                    size=size,
                )
            elif start_lin_addr is not None:
                # Paging disabled: use the provided VA as PA (VA == PA)
                phys_addr = start_lin_addr
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.PHYSICAL,
                    start_address=start_lin_addr,
                    size=size,
                )
                # Reserve linear space too to avoid linker errors from VMAs overlapping this region.
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.LINEAR,
                    start_address=start_lin_addr,
                    size=size,
                )
            else:
                phys_addr_bits = min(self.physical_addr_bits, self.pool.get_min_physical_addr_bits_for_page_maps())
                address_mask = common.address_mask_from_size(size)
                phys_addr_c = addrgen.AddressConstraint(
                    type=RV.AddressType.PHYSICAL,
                    qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
                    bits=phys_addr_bits,
                    mask=address_mask,
                    size=size,
                )
                phys_addr = self.addrgen.generate_address(constraint=phys_addr_c)
                if phys_addr is None:
                    raise ValueError(f"Failed to generate address for {name}")
                log.debug(f"phys_addr_constraints, {phys_name}: {phys_addr_c}, phys_addr: {phys_addr:016x}")
            lin_addr = phys_addr
        elif start_addr is not None and start_lin_addr is not None:
            # Both PA and VA explicitly specified (contiguous non-identity-mapped sections)
            phys_addr = start_addr
            lin_addr = start_lin_addr
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.PHYSICAL,
                start_address=start_addr,
                size=size,
            )
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.LINEAR,
                start_address=start_lin_addr,
                size=size,
            )
        elif start_addr is not None:
            # Only PA specified
            phys_addr = start_addr
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.PHYSICAL,
                start_address=start_addr,
                size=size,
            )
            if (self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE) or identity_map:
                # Identity map: VA = PA (paging off, or caller explicitly requests it)
                lin_addr = phys_addr
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.LINEAR,
                    start_address=phys_addr,
                    size=size,
                )
            else:
                # Generate VA independently (non-identity mapping)
                lin_addr_bits = min(self.linear_addr_bits, self.pool.get_min_linear_addr_bits_for_page_maps())
                address_mask = common.address_mask_from_size(size)
                lin_addr_c = addrgen.AddressConstraint(
                    type=RV.AddressType.LINEAR,
                    bits=lin_addr_bits,
                    mask=address_mask,
                    size=size,
                )
                lin_addr_orig = self.addrgen.generate_address(constraint=lin_addr_c)
                lin_addr = self.canonicalize_lin_addr(lin_addr_orig)
        elif start_lin_addr is not None:
            # Only VA specified — fix VA, generate PA randomly
            lin_addr = start_lin_addr
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.LINEAR,
                start_address=start_lin_addr,
                size=size,
            )
            phys_addr_bits = min(self.physical_addr_bits, self.pool.get_min_physical_addr_bits_for_page_maps())
            address_mask = common.address_mask_from_size(size)
            phys_addr_c = addrgen.AddressConstraint(
                type=RV.AddressType.PHYSICAL,
                qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
                bits=phys_addr_bits,
                mask=address_mask,
                size=size,
            )
            phys_addr = self.addrgen.generate_address(constraint=phys_addr_c)
        else:
            # Calculate mask, addr_bits from size
            # Sections are mapped across all page_maps. So we need the min address bits of all page maps for address selection.
            lin_addr_bits = min(self.linear_addr_bits, self.pool.get_min_linear_addr_bits_for_page_maps())
            phys_addr_bits = min(self.physical_addr_bits, self.pool.get_min_physical_addr_bits_for_page_maps())

            # If we are paging_disabled or identity_mapped, we generate physical address and assign it to linear address
            # So, we need to constraint the physical address to the same size as linear address. In addition we cannot use all bits of
            # linear address as then we need to canonicalize it. So we restrict to min of physical and linear address bits-1
            if (self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE) or identity_map:
                phys_addr_bits = min(phys_addr_bits, lin_addr_bits - 1)

            # address_mask = 0xfffffffffffff000
            address_size = size
            address_mask = common.address_mask_from_size(size)

            phys_addr_c = addrgen.AddressConstraint(
                type=RV.AddressType.PHYSICAL,
                qualifiers={RV.AddressQualifiers.ADDRESS_DRAM},
                bits=phys_addr_bits,
                mask=address_mask,
                size=address_size,
            )
            phys_addr = self.addrgen.generate_address(constraint=phys_addr_c)
            if phys_addr is None:
                raise ValueError(f"Failed to generate address for {name}")
            log.debug(f"phys_addr_constraints, {phys_name}: {phys_addr_c}, phys_addr: {phys_addr:016x}")

            if (self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE) or identity_map:
                lin_addr = phys_addr
                self.addrgen.reserve_memory(
                    address_type=RV.AddressType.LINEAR,
                    start_address=phys_addr,
                    size=address_size,
                )
            else:
                lin_addr_c = addrgen.AddressConstraint(
                    type=RV.AddressType.LINEAR,
                    bits=lin_addr_bits,
                    mask=address_mask,
                    size=address_size,
                )
                lin_addr_orig = self.addrgen.generate_address(constraint=lin_addr_c)
                lin_addr = self.canonicalize_lin_addr(lin_addr_orig)
                log.debug(f"lin_addr_constraints: {name}: {lin_addr_c}, lin_addr: {lin_addr:016x}")

        phys_addr_name = phys_name if phys_name else f"{name}_phys"

        # Create Address and Page instances
        addr_type = RV.AddressType.PHYSICAL if skip_page_map else RV.AddressType.LINEAR
        addr_inst = Address(name=name, type=addr_type, address=lin_addr)
        self.pool.add_random_addr(addr_name=name, addr=addr_inst)

        addr_inst = Address(name=phys_addr_name, type=RV.AddressType.PHYSICAL, address=phys_addr)
        self.pool.add_random_addr(addr_name=phys_addr_name, addr=addr_inst)

        # Add the page in all the available maps
        maps_to_add = list(self.pool.get_page_maps().keys())

        p = Page(
            name=name,
            phys_name=phys_addr_name,
            pool=self.pool,
            featmgr=self.featmgr,
            addrgen=self.addrgen,
            maps=maps_to_add,
            no_pbmt_ncio=1,
        )
        p.lin_addr = lin_addr
        p.phys_addr = phys_addr

        if iscode:
            # Mark it executable
            p.attrs["x_level0"] = 1
            # If hypervisor is enabled, then we need to mark it executable in hyp-map
            if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                p.attrs["x_level0_glevel0"] = 1
                p.attrs["x_level1_glevel0"] = 1
                p.attrs["x_level2_glevel0"] = 1
                p.attrs["x_level3_glevel0"] = 1
                p.attrs["x_level4_glevel0"] = 1
        # Always supervisor, except when vs-stage is disabled in 2-stage translation, in that case
        # the u_level0 will go to hyp-map
        if not self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            p.attrs["u_level0"] = 0

        if not always_super and self.featmgr.priv_mode == RV.RiscvPrivileges.USER:
            # Always user for data pages or in user mode
            p.attrs["u_level0"] = 1

        if always_user:
            p.attrs["u_level0"] = 1

        if not skip_page_map:
            self.pool.add_page(page=p, map_names=maps_to_add)

        # Add address as a section so they can be used in the linker script
        log.debug(f"Adding section {name} with lin_addr: {lin_addr:016x}, phys_addr: {phys_addr:016x}")
        if not skip_linker:
            self.pool.add_section(section_name=name)

        return (lin_addr, phys_addr)

    def generate_addr(self):
        """
        Generate all the addresses here
        """
        # page_mappings = {k: v for k,v in sorted(self.pool.get_parsed_page_mapings().items(),key=lambda item : item[1].resolve_priority, reverse=True)}
        for lin_name_map, page_mapping in self.pool.get_parsed_page_mappings().items():
            if page_mapping.resolve_priority == 15:
                self.handle_fixed_page_mappings(page_mapping)

            elif page_mapping.resolve_priority == 20:
                self.handle_random_page_mappings(page_mapping)

            else:
                # We don't need to generate any address for normal page_mappings since addresses are already generated for them
                # We directly create a Page instance after generating all addresses for these types
                self.handle_normal_page_mappings(page_mapping)

        # Now handle random_address entries
        for addr_name, rand_addr in self.pool.get_parsed_addrs().items():
            if addr_name not in self.pool.get_random_addrs():
                log.debug(f"random_addr {addr_name}")
                self.handle_random_addr(random_addr=rand_addr)

    def generate_init_mem(self):
        # page_mappings = self.pool.get_page_mappings()
        for init_mem_name in self.pool.get_parsed_init_mem_addrs():
            log.debug(f"Adding init_mem section {init_mem_name}")
            section_name = self.pool.resolve_canonical_lin_name(init_mem_name, "map_os")
            self.pool.add_section(section_name=section_name)

    def generate_sections(self):
        if not self.featmgr.wysiwyg:
            # We need to explicitely add 'runtime' section since we moved to using '.section .code' for the user tests, which will be in
            # different section than the .runtime
            # .runtime will contain mostly the operating system code
            self.pool.add_parsed_sections(val="runtime", index=0)
        for section in self.os_data_sections:
            self.pool.add_parsed_sections(val=section)
        for section in self.io_sections:
            self.pool.add_parsed_sections(val=section)
        for section in self.pool.get_parsed_sections():
            self.handle_sections(section)

        if self.featmgr.c_used:
            for section_name in self.c_used_sections:
                self.handle_sections(section_name)
