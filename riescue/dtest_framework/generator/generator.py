# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import re
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import copy

import riescue.riemap.addrgen as addrgen
import riescue.riemap.resolve as resolve
import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.rand_mem_breakpoint import apply as apply_rand_mem_breakpoint
from riescue.dtest_framework.runtime.loader import decoy_pma_budget
from riescue.dtest_framework.runtime.rand_mem_breakpoint import apply_icount_park as apply_rand_mem_icount_park
from riescue.dtest_framework.runtime.selfcheck import SELFCHECK_CHECKSUM_SIZE
from riescue.dtest_framework.runtime.test_execution_logger import TEST_EXECUTION_DATA_PER_HART_SIZE
from riescue.lib.address import Address
from riescue.lib.numgen import NumGen
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool, PageMapInfo, PageInfo
from riescue.dtest_framework.parser import PmaInfo, ParsedPageMapping, Parser, ParsedRandomData
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.config.pma_config import MAX_PMA_REGIONS
from riescue.dtest_framework.generator.pt_request_builder import build_page_tables, SectionAddr, SectionSpec, _Placement
from riescue.dtest_framework.lib.pma_generator import PmaGenerator, PmaRandomizer
from riescue.dtest_framework.generator.assembly_writer import AssemblyWriter
from riescue.dtest_framework.artifacts import GeneratedFiles
from riescue.riemap.memory import DramRange, IoRange
from riescue.lib.enums import PmpAttributes

log = logging.getLogger(__name__)


def pma_fixed_region_name(range_name: str) -> str:
    """The PMA region name a fixed memory-map window is published under (``pma_<range name>``)."""
    return f"pma_{range_name}"


@dataclass
class PmaRegionBinding:
    """Provenance for one pre-allocated PMA region: the :class:`PmaInfo` itself, plus
    whether the read-back must register it into ``pool.pma_regions``.

    Decided at the exact moment :meth:`Generator._pre_allocate_pma_regions_for_in_pma`
    resolves an ``in_pma`` address to a region: a brand-new region (never seen by the
    pool before) must be registered once its address is read back
    (``register_on_readback=True``); a reused hint region, an adopted randomized decoy,
    or a region shared with an earlier ``in_pma`` address in this same pre-allocation
    pass is already tracked elsewhere (``pool.pma_regions`` or ``pool.pma_random_regions``)
    and must NOT be registered again, or it would double-count a PMA entry at emission.
    """

    info: PmaInfo
    register_on_readback: bool


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

        # PMA region names owned by fixed memory-map windows; exempt from carve-out mask randomization
        self._pma_fixed_region_names: set[str] = set()

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
        add_selfcheck_section = self.featmgr.selfcheck
        if add_selfcheck_section:
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

        # Memory-map ranges with pma_randomization: false. Named after the range so the test reaches the
        # window through pma_<name>_base/_size/_end, and so decoys never shadow it.
        for range in memory.pma_fixed_ranges:
            self.pool.pma_regions.add_entry(PmaInfo(pma_name=pma_fixed_region_name(range.name), pma_address=range.start, pma_size=range.size, pma_valid=True))
            self._pma_fixed_region_names.add(pma_fixed_region_name(range.name))

        for range in memory.io_ranges + memory.reserved_ranges:
            self.pool.pma_regions.add_region(base=range.start, size=range.size, type="io")
            if isinstance(range, IoRange) and (range.name == "htif" or range in memory.io_ranges):
                self.pool.pmp_regions.add_region(range=range)

        for range in memory.custom_ranges:
            self.pool.pmp_regions.add_region(range=range)

        # Add a catchall PMP entry that covers all memory with RWX permissions.
        # This ensures that after PMP test scenarios restore original CSR values,
        # there's always a valid PMP entry allowing S-mode code execution.
        # The catchall is added as the last entry in pmpcfg0 (entry 1 if only DRAM range exists).
        # Use a large power-of-two size to cover all practical memory addresses
        # size = 2^52 results in pmpaddr = 0x1FFFFFFFFFFFF (NAPOT covering 0x0 to 0x10000000000000)
        catchall_range = DramRange(start=0, size=2**52, permissions=PmpAttributes.R_W_X)
        self.pool.pmp_regions.add_region(range=catchall_range)

        # Set the linear and physical address bits
        self.linear_addr_bits = RV.RiscvPagingModes.linear_addr_bits(self.featmgr.paging_mode)
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
                self.physical_addr_bits = min(RV.RiscvPagingModes.linear_addr_bits(self.featmgr.paging_g_mode), self.physical_addr_bits)
        # Set physical address bits in feature manager
        self.featmgr.physical_addr_bits = self.physical_addr_bits
        log.debug(f"Using physical address bits: {self.physical_addr_bits}")

        # Generate PMA regions from hints and configuration
        self._generate_pma_from_hints(memory)

        # Generate randomized decoy PMA regions (no-op unless enable_pma_randomization)
        self._generate_random_pma_regions(memory)

        # PMA region promised-capacity bookkeeping. The value keeps a PmaInfo reference
        # so id() keys stay live (consolidation copies can be GC'd).
        self._pma_region_promised: dict[int, tuple["PmaInfo", int]] = {}

        # Explicit provenance for every pre-allocated PMA region, keyed by id(PmaInfo): whether
        # the read-back must register it into pool.pma_regions (see PmaRegionBinding). Decided
        # at the exact decision point in _pre_allocate_pma_regions_for_in_pma, not re-derived
        # heuristically at read-back time.
        self._pma_region_bindings: dict[int, PmaRegionBinding] = {}

        # Pre-allocate PMA regions for all in_pma=1 addresses
        # This ensures all PMA regions are determined during initialization
        self._pre_allocate_pma_regions_for_in_pma(memory)

        self.addrgen = addrgen.AddrGen(
            self.rng,
            memory,
            self.featmgr.addrgen_limit_indices,
            self.featmgr.addrgen_limit_way_predictor_multihit,
            excluded_regions=self.pool.pma_random_exclusions,  # decoys: generated addresses must avoid these
        )

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
        max_regions = pma_config.max_regions if pma_config else MAX_PMA_REGIONS
        if total_regions > max_regions:
            log.warning(f"Total PMA regions ({total_regions}) exceeds max_regions limit ({max_regions}). " f"Some regions may not be used.")

    def _generate_random_pma_regions(self, memory) -> None:
        """
        Generate randomized decoy PMA regions when enable_pma_randomization is set.

        Decoys are placed anywhere below 2^physical_addr_bits except fixed/known windows; generated
        addresses avoid them via the AddrGen exclusion list, and the loader emits them at higher
        priority than the memory-map regions so they are live under first-match-wins.

        :param memory: Memory configuration object
        """
        if not self.featmgr.enable_pma_randomization:
            return  # keep zero rng draws when disabled

        blocked = self._fixed_blocked_intervals(memory)
        for region in self.pool.pma_regions.consolidated_entries(merge_named=False):
            if region.pma_name.startswith("pma_"):
                blocked.append((region.pma_address, region.get_end_address()))

        randomizer = PmaRandomizer(self.rng, self.featmgr.pma_random_mask_pct, self.physical_addr_bits)
        self.pool.extend_pma_random_regions(randomizer.generate(self.featmgr.pma_random_regions, blocked))
        log.info(f"Generated {len(self.pool.pma_random_regions)} randomized decoy PMA regions")

    def _fixed_blocked_intervals(self, memory) -> "list[tuple[int, int]]":
        """
        Collect every fixed/known physical [start, end) interval decoys and masks must avoid.

        :param memory: Memory configuration object
        :return: List of blocked intervals (secure/reserved/custom/fixed, reset_pc, IO windows, fixed addrs)
        """
        blocked: list[tuple[int, int]] = []
        for mem_range in memory.secure_ranges + memory.reserved_ranges + memory.custom_ranges + memory.pma_fixed_ranges:
            blocked.append((mem_range.start, mem_range.start + mem_range.size))
        blocked.append((self.featmgr.reset_pc, self.featmgr.reset_pc + 0x100_0000))
        for io_addr, io_size in (
            (self.featmgr.io_htif_addr, 0x1000),
            (self.featmgr.io_imsic_mfile_addr, 0x10_0000),
            (self.featmgr.io_imsic_sfile_addr, 0x10_0000),
            (self.featmgr.io_imsic_vsfile_addr, 0x10_0000),
            (self.featmgr.io_maplic_addr, self.featmgr.io_maplic_size or 0x10_0000),
            (self.featmgr.io_saplic_addr, self.featmgr.io_saplic_size or 0x10_0000),
            (self.featmgr.debug_rom_address, self.featmgr.debug_rom_size or 0x1000),
        ):
            if io_addr is not None:
                blocked.append((io_addr, io_addr + io_size))
        for parsed_addr in self.pool.get_parsed_addrs().values():
            if parsed_addr.fixed_addr is not None:
                blocked.append((parsed_addr.fixed_addr, parsed_addr.fixed_addr + max(parsed_addr.size or 0, 0x1000)))
        for parsed_res_mem in self.pool.get_parsed_res_mems().values():
            start = int(parsed_res_mem.start_addr, 16) if common.is_hex_number(parsed_res_mem.start_addr) else int(parsed_res_mem.start_addr)
            blocked.append((start, start + parsed_res_mem.size))
        for page_mapping in self.pool.get_parsed_page_mappings().values():
            if page_mapping.phys_addr_specified:
                phys_addr = int(page_mapping.phys_addr, 0)
                blocked.append((phys_addr, phys_addr + (page_mapping.phys_address_size or 0x1000)))
        return blocked

    def _apply_carveout_masks(self) -> None:
        """
        Randomly mask named pma_* carve-out regions; force masks on pma_masked=1 regions.

        A masked region still matches its own NAPOT base window (mask bits sit above the size),
        so pages placed inside keep their attributes while whisper's masked-compare matching is
        exercised on every access. Scattered windows are safety-checked against every foreign
        allocation; masked regions are registered with addrgen so later page-table allocation
        avoids their windows. Runs after all test allocations, before writer.write().
        """
        if not self.featmgr.enable_pma_randomization:
            return  # keep zero rng draws when disabled

        carveouts = [
            r for r in self.pool.pma_regions.consolidated_entries(merge_named=False) if r.pma_name.startswith("pma_") and r.pma_name not in self._pma_fixed_region_names and r.pma_address != 0
        ]
        if not carveouts:
            return

        blocked = self._fixed_blocked_intervals(self.featmgr.memory)
        blocked.extend(self.addrgen.allocated_physical_intervals())
        blocked.extend((decoy.pma_address, decoy.get_end_address()) for decoy in self.pool.pma_random_regions)

        randomizer = PmaRandomizer(self.rng, self.featmgr.pma_carveout_mask_pct, self.physical_addr_bits)
        for region in carveouts:
            # Clip the region's own span out of every interval: every mask matches its own base
            # window, so an interval overlapping the span (e.g. the addrgen cluster run that
            # contains the carveout's own allocation) would otherwise veto every candidate mask
            span_start, span_end = region.pma_address, region.get_end_address()
            blocked_other = []
            for start, end in blocked:
                if start < span_start:
                    blocked_other.append((start, min(end, span_start)))
                if end > span_end:
                    blocked_other.append((max(start, span_end), end))
            if region.pma_mask_requested:
                if not randomizer.apply_random_mask(region, blocked_other, force=True):
                    raise ValueError(
                        f"pma_masked region '{region.pma_name}' (base=0x{region.pma_address:x}, size=0x{region.pma_size:x}): "
                        f"no safe pmamask found after {randomizer.MASK_ATTEMPTS} random attempts and a single-bit sweep"
                    )
            else:
                randomizer.apply_random_mask(region, blocked_other)
            if region.pma_mask:
                self.addrgen.exclude_region(region.excluded_region())
                log.info(f"Masked carve-out region {region.pma_name}: mask=0x{region.pma_mask:x}")

    def _pre_allocate_pma_regions_for_in_pma(self, memory) -> None:
        """
        Pre-allocate PMA regions for all addresses with in_pma=1.

        This ensures all PMA regions are determined during initialization.
        First tries to reuse existing PMA hint regions, then creates new ones if needed.

        :param memory: Memory configuration object
        """
        # Get PMA config to check max_regions
        pma_config = None
        if hasattr(self.featmgr, "cpu_config") and self.featmgr.cpu_config:
            pma_config = self.featmgr.cpu_config.pma_config
        max_regions = pma_config.max_regions if pma_config else MAX_PMA_REGIONS

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

        if not self.featmgr.enable_pma_randomization:
            for addr_name, parsed_addr in in_pma_addresses:
                if parsed_addr.pma_masked:
                    # The non-randomized PMA loader emits pmacfg=0 for
                    # pma_valid regions and cannot represent a masked region.
                    raise ValueError(f"random_addr {addr_name}: pma_masked=1 requires --enable_pma_randomization")

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

            # Default pma_size if unspecified; the region must cover the whole allocation
            parsed_addr.pma_info.pma_size = max(parsed_addr.pma_info.pma_size, parsed_addr.size)

            # Try to find matching existing PMA hint region (bound decoys by the page's addressable range)
            matching_region = self._find_matching_pma_region(
                parsed_addr.pma_info,
                max_end=1 << (parsed_addr.addr_bits or self.linear_addr_bits),
                require_masked=bool(parsed_addr.pma_masked),
            )

            # If no hint region matches, check if we've already pre-allocated a region with matching attributes
            if not matching_region:
                # Create a key from PMA attributes to check for sharing (masked requests never share unmasked)
                attr_key = (
                    parsed_addr.pma_info.pma_memory_type,
                    parsed_addr.pma_info.pma_cacheability,
                    parsed_addr.pma_info.pma_combining,
                    parsed_addr.pma_info.pma_read,
                    parsed_addr.pma_info.pma_write,
                    parsed_addr.pma_info.pma_execute,
                    parsed_addr.pma_info.pma_amo_type,
                    parsed_addr.pma_info.pma_routing_to,
                    bool(parsed_addr.pma_masked),
                )
                # Check if we've already pre-allocated a region with these attributes
                if attr_key in pre_allocated_by_attrs:
                    if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                        pass  # VA==PA: every mapped page anchors its own region at its lin address; no sharing
                    else:
                        # Share and grow the region so every sharer fits, incl. coarse-aligned ones (alignment from and_mask)
                        existing_pre_alloc = pre_allocated_by_attrs[attr_key]
                        request_step = parsed_addr.and_mask & -parsed_addr.and_mask if parsed_addr.and_mask else 0x1000
                        existing_pre_alloc.pma_size += max(parsed_addr.pma_info.pma_size, request_step)
                        matching_region = existing_pre_alloc
                        log.debug(f"Sharing pre-allocated region '{existing_pre_alloc.pma_name}' " f"for address {addr_name}, grown to 0x{existing_pre_alloc.pma_size:x}")

            if matching_region:
                # Reuse existing PMA hint region
                log.debug(f"Reusing PMA hint region '{matching_region.pma_name}' " f"for address {addr_name}")
                self._promise_region_capacity(matching_region, parsed_addr.pma_info.pma_size)
                # Store reference to existing region
                self._pre_allocated_pma_regions[addr_name] = matching_region
                # Update parsed_addr to point to existing region
                # Reused hint / adopted decoy / shared-with-an-earlier-member region: it is
                # already tracked in pool.pma_regions or pool.pma_random_regions, so read-back
                # must not register it again (setdefault: a share only ever revisits the SAME
                # object this loop already bound, never overwrites its provenance).
                self._pma_region_bindings.setdefault(id(matching_region), PmaRegionBinding(info=matching_region, register_on_readback=False))
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
                    pma_mask_requested=bool(parsed_addr.pma_masked),
                )

                # Store pre-allocated region
                self._pre_allocated_pma_regions[addr_name] = pma_info
                # Update parsed_addr to point to pre-allocated region
                parsed_addr.pma_info = pma_info
                # Brand new: never seen by pool.pma_regions before, so read-back must register
                # it exactly once when its address is finally chosen.
                self._pma_region_bindings[id(pma_info)] = PmaRegionBinding(info=pma_info, register_on_readback=True)

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
                    pma_info.pma_mask_requested,
                )
                pre_allocated_by_attrs[attr_key] = pma_info

                # Not added to pool.pma_regions yet: its address is chosen later, when the
                # builder resolves the region and the allocation is read back.
                available_slots -= 1
                log.debug(f"Pre-allocated PMA region for {addr_name}: " f"size=0x{pma_info.pma_size:x}, type={pma_info.pma_memory_type}")

        # Log summary
        reused = sum(1 for r in self._pre_allocated_pma_regions.values() if r.pma_address != 0)  # Address != 0 means it's from existing region
        new_regions = len(self._pre_allocated_pma_regions) - reused
        log.info(f"Pre-allocated PMA regions: {reused} reused from hints, {new_regions} new regions. " f"Remaining slots: {available_slots}")

    def _find_matching_pma_region(self, pma_info: "PmaInfo", max_end: Optional[int] = None, require_masked: bool = False) -> Optional["PmaInfo"]:
        """
        Find an existing PMA region that matches the given PMA attributes.

        :param pma_info: PMA info to match against
        :param max_end: Skip decoy regions ending above this bound (page addressability limit)
        :param require_masked: Only match regions that carry (or will carry) a forced pmamask
        :return: Matching PmaInfo if found, None otherwise
        """
        # Check consolidated entries (final PMA regions); named regions stay unmerged so capacity tracking keys stay stable
        for region in self.pool.pma_regions.consolidated_entries(merge_named=False):
            # Masked requests only match masked/to-be-masked regions, and vice versa
            if require_masked != (region.pma_mask != 0 or region.pma_mask_requested):
                continue
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
                if region.pma_size >= pma_info.pma_size and self._pma_region_has_capacity(region, pma_info.pma_size):
                    return region

        # Unmasked randomized decoys are also valid placement targets (masked ones match scattered
        # windows, and decoy masks are not cross-validated against sibling decoys' spans; TODO)
        for region in self.pool.pma_random_regions:
            if region.pma_mask != 0 or require_masked:
                continue
            if max_end is not None and region.get_end_address() > max_end:
                continue  # decoy lies beyond what this page's addr_bits can address
            if region.attrib_matches(pma_info) and region.pma_size >= pma_info.pma_size and self._pma_region_has_capacity(region, pma_info.pma_size):
                return region

        return None

    def _promise_region_capacity(self, region: "PmaInfo", size: int) -> None:
        """Record a promised placement so later matches can't overflow the region."""
        self._pma_region_promised[id(region)] = (region, self._promised_bytes(region) + size)

    def _promised_bytes(self, region: "PmaInfo") -> int:
        entry = self._pma_region_promised.get(id(region))
        return entry[1] if entry else 0

    def _pma_region_has_capacity(self, region: "PmaInfo", size: int) -> bool:
        """Reject regions whose promised placements would overflow them."""
        return region.pma_size - self._promised_bytes(region) >= size

    def _resolved_request_size(self, phys_name: str) -> int:
        """Bytes phys_name's placement will request: directive size grown by any resolved mapping pagesize."""
        size = self.pool.get_parsed_addr(phys_name).size if self.pool.parsed_random_addr_exists(addr_name=phys_name) else 0x1000
        for (_, _), parsed_page_mapping in self.pool.get_parsed_page_mappings().items():
            if parsed_page_mapping.phys_name != phys_name:
                continue
            if parsed_page_mapping.final_pagesize:  # unresolved (0) when paging is disabled
                size = max(size, RV.RiscvPageSizes.memory(parsed_page_mapping.final_pagesize))
            if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                size = max(size, parsed_page_mapping.gstage_vs_leaf_address_size)
        return size

    def _pma_region_demand(self, region: "PmaInfo") -> int:
        """Total bytes all sharers of this pre-allocated region will place (mapping pagesizes resolve after pre-allocation)."""
        return sum(self._resolved_request_size(name) for name, shared in self._pre_allocated_pma_regions.items() if shared is region)

    def _project_decoy_budget(self) -> int:
        """Project how many randomized decoys will survive to be emitted, before RieMap runs.

        ``loader.setup_pma`` truncates decoys to fit only at emission time (deep inside
        ``writer.write()``), long after RieMap already drew page-table frames and pages
        avoiding every decoy in ``pool.pma_random_exclusions``. A decoy that would be
        truncated away at emission time must not still veto placements here, so this
        mirrors that same essential-entry accounting (:func:`decoy_pma_budget`) using the
        entries already known before RieMap runs: the current consolidated PMA count plus
        every ``in_pma`` region this pass pre-allocated but has not yet added to the pool
        (``register_on_readback=True`` bindings whose address is still unresolved).
        """
        if not self.featmgr.enable_pma_randomization:
            return len(self.pool.pma_random_regions)
        randomize = True
        existing = len(self.pool.pma_regions.consolidated_entries(merge_named=not randomize))
        pending_new = len({id(binding.info) for binding in self._pma_region_bindings.values() if binding.register_on_readback and binding.info.pma_address == 0})
        reserved = self.featmgr.user_programmable_pmacfg
        return decoy_pma_budget(reserved, existing + pending_new, self.featmgr.num_pmas)

    def generate(self, file_in: Path, generated_files: GeneratedFiles):
        """
        Generate random data, randomize addresses, create page mappings, reserve memory, and write all files
        """
        # Apply --rand_mem_breakpoint_pct against the pool of addresses supplied
        # via ;#rand_mem_breakpoint_pool directives. No-op when the switch is 0
        # or the pool is empty. Must run before writer.write() so the registered
        # hook + default exception handler are visible to asm emission.
        icount_slot_claimed = apply_rand_mem_breakpoint(self.featmgr, self.pool, self.rng)

        # --rand_mem_icount_park_pct is a separate gate: it only touches the icount trigger slot,
        # needs no address pool and registers no handler, so it applies whether or not the watchpoint
        # feature above rolled true. It stands down when that feature already claimed the slot.
        # Same ordering requirement -- must precede writer.write().
        apply_rand_mem_icount_park(self.featmgr, self.pool, self.rng, icount_slot_claimed=icount_slot_claimed)

        # The order of calling following functions is very important, please do not change
        # unless you know what you are doing
        self.process_raw_parsed_page_mappings()
        self.generate_data()
        self.add_page_maps()
        self.generate_sections()
        self.handle_res_mem()
        self.allocate_via_riemap()
        self.generate_init_mem()
        # allocate_via_riemap already ran (RieMap owns page-table allocation, not
        # writer.write()); carve-out masks are chosen against those already-placed PT
        # intervals, read back into addrgen during allocate_via_riemap's read-back.
        self._apply_carveout_masks()

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

        These are RiescueD's lightweight ``PageMapInfo`` records: name,
        paging mode, and an ``sptbr`` filled from RieMap's
        ``AllocationResult``.
        """
        # Before generating any addresses, create default map_os paging_map
        # If paging is disabled, we still need to add dummy map_os (let's choose the paging mode to be SV39)
        map_os = PageMapInfo(name="map_os", paging_mode=self.featmgr.paging_mode)
        self.pool.add_page_map(map_instance=map_os)

        # If virtualization is enabled and g_stage is not bare, setup map_hyp
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and (self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE or self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE):
            map_hyp = PageMapInfo(name="map_hyp", paging_mode=self.featmgr.paging_g_mode)
            self.pool.add_page_map(map_instance=map_hyp)

        # Handle user defined maps
        for map_name, parsed_map in self.pool.get_parsed_page_maps().items():
            mode = self.featmgr.paging_mode
            if parsed_map.mode != "testmode":
                mode = RV.RiscvPagingModes[parsed_map.mode.upper()]
            map_inst = PageMapInfo(name=map_name, paging_mode=mode)
            self.pool.add_page_map(map_instance=map_inst)

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
            exclude_largest=self._has_vs_nonleaf_constraints(page_mapping) or page_mapping.modify_nonleaf_pt,
            exclude_after_bump=bool(page_mapping.modify_leaf_pt) and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE,
            bump_mode=self.featmgr.paging_g_mode if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE else None,
        )
        log.debug(f"lin_name: {lin_name}, final_pagesize: {final_pagesize}, addr_size: {addr_size:x}, addr_mask: {addr_mask:x}")

        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            exclude_largest_gstage = self._has_gstage_nonleaf_constraints(page_mapping)
            if not page_mapping.gstage_vs_leaf_pagesizes:
                if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                    # VS-stage is Bare: there is no VS-stage pagesize to inherit (pick_pagesize(DISABLE)
                    # forced final_pagesize to 4KB). The single page-table walk is G-stage, so size the
                    # G-stage leaf from the page's OWN specified pagesize via the G-stage mode — otherwise
                    # a 2MB/1GB region only gets a 4KB G-stage leaf and accesses past 4KB fault (guest-page
                    # fault).
                    (
                        gstage_vs_leaf_final_pagesize,
                        gstage_vs_leaf_addr_size,
                        gstage_vs_leaf_addr_mask,
                    ) = self.pick_pagesize(
                        specified_pagesizes=page_mapping.pagesizes,
                        paging_mode=self.featmgr.paging_g_mode,
                        page_mapping=page_mapping,
                        exclude_largest=exclude_largest_gstage or page_mapping.modify_leaf_pt,
                        exclude_after_bump=bool(page_mapping.modify_leaf_pt),
                    )
                else:
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
                    exclude_largest=exclude_largest_gstage or page_mapping.modify_leaf_pt,
                    exclude_after_bump=bool(page_mapping.modify_leaf_pt),
                )
            log.debug(f"lin_name_nonleaf: {lin_name}, specified_pagesizes: {specified_pagesizes}")
            specified_pagesizes = page_mapping.gstage_vs_nonleaf_pagesizes
            # Only the chosen pagesize is used: the size/mask this returns described the
            # (now removed) reservation bump, which pt_request_builder derives from the pagesize
            # itself. ``exclude_after_bump`` still matters -- it keeps the span the builder will
            # ask for one level up allocatable.
            (
                gstage_vs_nonleaf_final_pagesize,
                _gstage_vs_nonleaf_addr_size,
                _gstage_vs_nonleaf_addr_mask,
            ) = self.pick_pagesize(
                specified_pagesizes=specified_pagesizes,
                paging_mode=self.featmgr.paging_g_mode,
                page_mapping=page_mapping,
                exclude_largest=exclude_largest_gstage or page_mapping.modify_nonleaf_pt,
                exclude_after_bump=bool(page_mapping.modify_nonleaf_pt),
            )

        # When VS-stage is disabled but G-stage is active, the only page table
        # walk is G-stage. Override the 4KB fallback from pick_pagesize(DISABLE)
        # with the G-stage leaf pagesize.
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            final_pagesize = gstage_vs_leaf_final_pagesize
            addr_size = gstage_vs_leaf_addr_size
            addr_mask = gstage_vs_leaf_addr_mask

        # Generally, we want to use main paging mode, but in virtualization if vs-stage is BARE then use g-mode paging
        paging_mode = self.featmgr.paging_mode
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
                paging_mode = self.featmgr.paging_g_mode

        # Handle the non-leaf attributes forcing, i.e. v_level1=0 etc. This materializes the
        # concrete {base}_level{n} keys onto the mapping (the translator reads them back as
        # Mapping.pt_nodes); the isolation growth the resolver also returns is DROPPED --
        # attr-aware coloring (PageTableBuilder(color=True)) now separates conflicting siblings.
        for attr in ["v", "a", "d", "g", "u", "x", "w", "r", "n", "pbmt"]:
            self.randomize_pt_attrs(
                attr=attr,
                page_mapping=page_mapping,
                paging_mode=paging_mode,
                final_pagesize=final_pagesize,
            )

        # Handle gstage atrribute randomization. This materializes the concrete
        # {base}_level{vs}_glevel{g} g-stage forcing keys onto the mapping (the translator
        # reads them back as the G-frame mappings' pt_nodes); the isolation growth the
        # resolver also returns (g-stage reservation widening + VS-stage VA isolation) is
        # DROPPED -- attr-aware coloring separates conflicting siblings.
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
                self.randomize_gstage_pt_attrs(
                    attr=attr,
                    page_mapping=page_mapping,
                    paging_mode_g=self.featmgr.paging_g_mode,
                    paging_mode_vs=self.featmgr.paging_mode,
                    final_pagesize_vs=final_pagesize,
                    final_pagesize_gleaf=gstage_vs_leaf_final_pagesize,
                    final_pagesize_gnonleaf=gstage_vs_nonleaf_final_pagesize,
                )

        # modify_pt / modify_nonleaf_pt declare owned PT nodes as pinned
        # Mapping.pt_nodes frames (see pt_request_builder._modify_pt_frames): modify_pt pins
        # its top-level node, modify_nonleaf_pt pins every non-leaf node leaf+1..root, and
        # attr-aware coloring keeps siblings out. The F1 slot read-back still fires (gated on
        # the modify_pt PTE attr, carried in Mapping.attrs).
        #
        # Note: modify_pt with multiple page_maps for the same page remains unsupported -- a
        # page in both sv39 and sv48 maps would need incompatible top-level reservations.

        # At the end, we need to only have the final pagesize set to True in the page mapping
        # log.debug(f'randomize pagesize for page: {page_mapping.lin_name}, {gstage_vs_leaf_final_pagesize}, {gstage_vs_nonleaf_final_pagesize}')
        page_mapping.final_pagesize = final_pagesize
        page_mapping.address_size = addr_size
        page_mapping.address_mask = addr_mask
        if page_mapping.modify_pt == 1:
            page_mapping.phys_address_size = RV.RiscvPageSizes.memory(final_pagesize)
            page_mapping.phys_address_mask = RV.RiscvPageSizes.address_mask(final_pagesize)
        else:
            page_mapping.phys_address_size = page_mapping.address_size
            page_mapping.phys_address_mask = page_mapping.address_mask
        # The ownership span for a rewritten g-stage pointer travels with the
        # page and is applied by pt_request_builder from
        # resolve.gstage_pointer_span, shared with the declared-force path.
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            log.debug(f"adding for {page_mapping.lin_name} gstage_vs_leaf_final_pagesize: {gstage_vs_leaf_final_pagesize}, gstage_vs_nonleaf_final_pagesize: {gstage_vs_nonleaf_final_pagesize}")
            page_mapping.gstage_vs_nonleaf_final_pagesize = gstage_vs_nonleaf_final_pagesize
            page_mapping.gstage_vs_leaf_final_pagesize = gstage_vs_leaf_final_pagesize
            # gstage_vs_leaf_address_size is read by the PMA pre-allocation capacity estimate
            # (_resolved_request_size); the gstage_vs_nonleaf_* pair had no reader at all.
            page_mapping.gstage_vs_leaf_address_size = gstage_vs_leaf_addr_size
            page_mapping.gstage_vs_leaf_address_mask = gstage_vs_leaf_addr_mask

        # For physical addresses, there's a case where we will not be looking at page_mapping.address_size while generating the address
        # So, make sure we update the address_size/mask in the random_address instance as well
        if self.pool.parsed_random_addr_exists(page_mapping.phys_name):
            phys_random_addr = self.pool.get_parsed_addr(page_mapping.phys_name)
            phys_random_addr.size = max(page_mapping.phys_address_size, phys_random_addr.size)
            phys_random_addr.and_mask = min(page_mapping.phys_address_mask, phys_random_addr.and_mask)

    def setup_uwrx_bit(self, attr, page_mapping, final_pagesize, gstage_vs_leaf_final_pagesize, gstage_vs_nonleaf_final_pagesize):
        """Set up the initial G-stage U/R/W/X/A/D level bits (delegates to riemap)."""
        resolve.setup_uwrx_bit(
            attr,
            attrs=page_mapping.__dict__,
            paging_mode=self.featmgr.paging_mode,
            paging_g_mode=self.featmgr.paging_g_mode,
            final_pagesize=final_pagesize,
            gstage_vs_leaf_final_pagesize=gstage_vs_leaf_final_pagesize,
            gstage_vs_nonleaf_final_pagesize=gstage_vs_nonleaf_final_pagesize,
        )

    def randomize_gstage_pt_attrs(
        self,
        attr,
        page_mapping,
        paging_mode_g,
        paging_mode_vs,
        final_pagesize_vs,
        final_pagesize_gleaf,
        final_pagesize_gnonleaf,
    ):
        """Force a G-stage leaf/non-leaf attribute onto the page mapping (delegates to riemap).

        Called purely for its side effect: it materializes the concrete
        ``{base}_level{vs}_glevel{g}`` forcing key onto the mapping (the translator reads
        it back as the G-frame mappings' pt_nodes). The resolver returns nothing: g-stage
        geometry is declared per node (``PTGPage.pagesize`` / the destination ``Page``'s
        pagesize), and sibling isolation is the builder's attr-aware coloring.
        """
        resolve.randomize_gstage_pt_attrs(
            attr=attr,
            attrs=page_mapping.__dict__,
            paging_mode_g=paging_mode_g,
            paging_mode_vs=paging_mode_vs,
            priv_mode=self.featmgr.priv_mode,
            final_pagesize_vs=final_pagesize_vs,
            final_pagesize_gleaf=final_pagesize_gleaf,
            final_pagesize_gnonleaf=final_pagesize_gnonleaf,
        )

    def randomize_pt_attrs(
        self,
        attr,
        page_mapping,
        paging_mode,
        final_pagesize,
    ):
        """Handle single-stage pagetable attribute randomization for one attr.

        Called purely for its side effect: it materializes the concrete
        ``{base}_level{n}`` key onto the mapping (the translator reads it back as
        Mapping.pt_nodes). Sibling isolation is now the builder's attr-aware coloring, so
        no address reservation is computed or returned here.
        """
        self.pt_attrs_helper(attr, page_mapping, paging_mode, final_pagesize)

    def pt_attrs_helper(self, attr, page_mapping, paging_mode, final_pagesize):
        """Force a single-stage attribute onto the page mapping (delegates to riemap)."""
        updated = resolve.pt_attrs(
            attr,
            page_mapping.__dict__,
            paging_mode,
            final_pagesize,
            self.featmgr.priv_mode,
        )
        page_mapping.__dict__.update(updated)

    # Insignificant (default) values for VS-stage nonleaf attributes.
    # Mirrors the logic in randomize_pt_attrs / pt_attrs_helper.
    _VS_NONLEAF_CHECKS = [
        ("v_nonleaf", True),
        ("a_nonleaf", True),
        ("d_nonleaf", True),
        ("r_nonleaf", True),
        ("w_nonleaf", None),  # default is None — any explicit value is significant
        ("x_nonleaf", None),
        ("u_nonleaf", None),
        ("g_nonleaf", False),  # insignificant_value for g is 0 (False)
        ("pbmt_nonleaf", 0),
        ("n_nonleaf", 0),
    ]

    # Insignificant (default) values for G-stage nonleaf attributes.
    # Mirrors the logic in randomize_gstage_pt_attrs.
    _GSTAGE_NONLEAF_CHECKS = [
        ("v_leaf_gnonleaf", True),
        ("v_nonleaf_gnonleaf", True),
        ("a_leaf_gnonleaf", True),
        ("a_nonleaf_gnonleaf", True),
        ("d_leaf_gnonleaf", True),
        ("d_nonleaf_gnonleaf", True),
        ("r_leaf_gnonleaf", True),
        ("r_nonleaf_gnonleaf", True),
        ("w_leaf_gnonleaf", None),
        ("w_nonleaf_gnonleaf", None),
        ("x_leaf_gnonleaf", None),
        ("x_nonleaf_gnonleaf", None),
        ("u_leaf_gnonleaf", None),
        ("u_nonleaf_gnonleaf", None),
        ("g_leaf_gnonleaf", False),
        ("g_nonleaf_gnonleaf", False),
        ("n_leaf_gnonleaf", 0),
        ("n_nonleaf_gnonleaf", 0),
        ("pbmt_leaf_gnonleaf", 0),
        ("pbmt_nonleaf_gnonleaf", 0),
    ]

    @staticmethod
    def _has_significant_constraints(page_mapping, attr_checks) -> bool:
        """Return True if any attribute in attr_checks has a significant (non-default) value."""
        for attr, insignificant in attr_checks:
            val = page_mapping.__getattribute__(attr)
            if insignificant is None:
                # Any explicit (non-None) value is significant
                if val is not None:
                    return True
            else:
                if val is not None and val != insignificant:
                    return True
        return False

    def _has_vs_nonleaf_constraints(self, page_mapping) -> bool:
        return self._has_significant_constraints(page_mapping, self._VS_NONLEAF_CHECKS)

    def _has_gstage_nonleaf_constraints(self, page_mapping) -> bool:
        return self._has_significant_constraints(page_mapping, self._GSTAGE_NONLEAF_CHECKS)

    @staticmethod
    def _page_mapping_exact_addrs(page_mapping) -> list:
        """Exact addresses this page mapping pins (declared ``lin_addr``/``phys_addr``),
        as ints. The chosen pagesize must not exceed the alignment either address
        already satisfies -- otherwise :meth:`pt_request_builder.PageTableRequestBuilder._va_spec`
        / ``_pa_spec`` would align_down the exact address to the pagesize and silently
        move it away from what was declared."""
        addrs = []
        if getattr(page_mapping, "lin_addr_specified", False) and page_mapping.lin_addr:
            addrs.append(int(page_mapping.lin_addr, 0))
        if getattr(page_mapping, "phys_addr_specified", False) and page_mapping.phys_addr:
            addrs.append(int(page_mapping.phys_addr, 0))
        return addrs

    @staticmethod
    def _pagesizes_satisfying_addrs(pagesizes, addrs):
        """``pagesizes`` filtered to those every exact address in ``addrs`` is already
        aligned to. Returns the unfiltered input unchanged if no exact addresses were
        pinned, or if filtering would empty the set (the caller's align_down step fixes
        the address up afterwards rather than being left with no candidate at all)."""
        if not addrs:
            return pagesizes
        if isinstance(pagesizes, dict):
            filtered = {ps: w for ps, w in pagesizes.items() if all(addr & (RV.RiscvPageSizes.memory(ps) - 1) == 0 for addr in addrs)}
        else:
            filtered = [ps for ps in pagesizes if all(addr & (RV.RiscvPageSizes.memory(ps) - 1) == 0 for addr in addrs)]
        return filtered if filtered else pagesizes

    def pick_pagesize(self, specified_pagesizes, paging_mode, page_mapping, exclude_largest=False, exclude_after_bump=False, bump_mode=None):
        # Check what pagesizes are specified and pick one and set the address size
        # Generally, we want to use main paging mode, but in virtualization if vs-stage is BARE then use g-mode paging
        # paging_mode = self.featmgr.paging_mode
        napot_supported = self.featmgr.is_feature_enabled("svnapot")
        valid_pagesizes = RV.RiscvPagingModes.supported_pagesizes(paging_mode, napot_supported=napot_supported)
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
        if exclude_largest:
            # When nonleaf_* constraints are specified, the largest pagesize has its leaf PTE
            # at the root level (pt_leaf_level == max_levels - 1), meaning zero non-leaf PTEs
            # exist in the walk, so nonleaf_* attributes cannot be applied.
            max_levels = RV.RiscvPagingModes.max_levels(paging_mode)
            valid_pagesizes = [ps for ps in valid_pagesizes if RV.RiscvPageSizes.pt_leaf_level(ps) != max_levels - 1]
        if exclude_after_bump:
            # The caller will bump the chosen pagesize via next_pt_level_pagesize and use it
            # as an address size/alignment. The bumped pagesize must not be the root-level
            # pagesize (pt_leaf_level == max_levels - 1), since that alignment exceeds the
            # available physical address space.
            bump_paging_mode = bump_mode if bump_mode is not None else paging_mode
            bump_max_levels = RV.RiscvPagingModes.max_levels(bump_paging_mode)
            valid_pagesizes = [ps for ps in valid_pagesizes if RV.RiscvPageSizes.pt_leaf_level(RV.RiscvPagingModes.next_pt_level_pagesize(bump_paging_mode, ps)) != bump_max_levels - 1]
        # A declared lin_addr/phys_addr pins the address; prefer a pagesize that address
        # already satisfies over a larger one align_down would later move it out from
        # under (see _va_spec/_pa_spec).
        exact_addrs = self._page_mapping_exact_addrs(page_mapping)
        valid_pagesizes = self._pagesizes_satisfying_addrs(valid_pagesizes, exact_addrs)
        allowed_pagesizes = dict()
        # Find out if any of the _<pagesize>page specified
        for pagesize in specified_pagesizes:
            matching = [s for s in valid_fixed_pagesizes_str if pagesize in s]
            if matching:
                selected_pagesize = RV.RiscvPageSizes["S" + matching[0][:-4].upper()]
                allowed_pagesizes[selected_pagesize] = RV.RiscvPageSizes.weights(selected_pagesize)
        allowed_pagesizes = self._pagesizes_satisfying_addrs(allowed_pagesizes, exact_addrs)

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
        if self.featmgr.all_4kb_pages:
            log.debug(f"Forcing 4KB pagesize for {page_mapping.lin_name}")
            final_pagesize = RV.RiscvPageSizes.S4KB
            addr_size = RV.RiscvPageSizes.memory(final_pagesize)
            addr_mask = RV.RiscvPageSizes.address_mask(final_pagesize)

        return (final_pagesize, addr_size, addr_mask)

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
                        # ``lin_name=base+offset`` always OffsetFrom the bare
                        # ``;#random_addr(name=base)`` window (never nests under a
                        # parent page_mapping). The random_addr owns the window;
                        # a base page_mapping(lin_name=base), if any, SameAs that
                        # same recipe.
                        match = re.search(r"(\w+)\+(\w+)", val)
                        if match:
                            parent = match.group(1)
                            offset = int(match.group(2), 16)
                            if not self.pool.parsed_random_addr_exists(addr_name=parent):
                                raise ValueError(f"lin_name={val}: parent {parent!r} must be a " f"random_addr for entry:\n \t{line}")
                            ppm_inst.lin_addr_link = (parent, offset)

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
                    # A shared phys_name means this mapping aliases another's physical
                    # page. "&random" is not a shared name -- it is a placeholder for
                    # "allocate a fresh random page" -- so it never marks an alias.
                    # Offset forms (name+0xN) name a byte in a random_addr window.
                    if val != "&random" and "+" not in val:
                        exists = any(ppm.phys_name == val for ppm in self.pool.get_parsed_page_mappings().values())
                        if exists:
                            log.debug(f"phys_name {val} already exists in another page mapping, marking as alias case")
                            ppm_inst.alias = True

            # phys_name=random_addr+offset → OffsetFrom that bare physical recipe.
            phys_match = re.search(r"^(\w+)\+(\w+)$", ppm_inst.phys_name)
            if phys_match:
                phys_parent = phys_match.group(1)
                phys_offset = int(phys_match.group(2), 16)
                if not self.pool.parsed_random_addr_exists(addr_name=phys_parent):
                    raise ValueError(f"phys_name={ppm_inst.phys_name}: parent {phys_parent!r} must be a " f"random_addr for entry:\n \t{line}")
                ppm_inst.phys_addr_link = (phys_parent, phys_offset)

            if fixed_addr_specified:
                ppm_inst.resolve_priority = 15

            if self.featmgr.private_maps:
                # Make a separate copy of ppm_inst for each map
                if pagemap_str != "":
                    ppm_inst.in_private_map = True
                if len(ppm_inst.page_maps) != 0:
                    for pm in ppm_inst.page_maps[1:]:
                        ppm_copy = copy.deepcopy(ppm_inst)
                        ppm_copy.page_maps = [pm]
                        ppm_copy.lin_name += f"_{pm}"
                        ppm_copy.phys_name += f"_{pm}"
                        self.pool.add_parsed_page_mapping(ppm_copy)
                    ppm_inst.page_maps = [ppm_inst.page_maps[0]]
                    ppm_inst.lin_name += f"_{ppm_inst.page_maps[0]}"

            self.pool.add_parsed_page_mapping(ppm_inst)

    def allocate_via_riemap(self):
        """Allocate every address and page through the riemap builder.

        RiescueD translates its parsed directives into neutral riemap constraints, hands
        them to a :class:`PageTableBuilder` that owns its own registry and allocates in
        any order, then reads the :class:`AllocationResult` back into the pool -- equates
        (``pool.random_addrs``), page objects (``pool.page_maps`` for write-time tree
        building), and PMA region bases. The page-table roots, tree emission and CSR
        programming downstream stay as they were.
        """
        # Pagesize policy stays in RiescueD; resolve it up front so the translator
        # reads the resolved final_pagesize / address_mask off each mapping.
        for ppm in self.pool.get_parsed_page_mappings().values():
            self.randomize_pagesize(ppm)

        # Grow each pre-allocated in_pma region to the demand of its members now that
        # page sizes are resolved (pre-allocation ran in __init__ before sizes were
        # known). Mirrors the eager anchor path so the translator's NAPOT-rounded
        # region is big enough for a member mapped with a large page size.
        for region in {id(r): r for r in self._pre_allocated_pma_regions.values()}.values():
            if region.pma_address == 0:
                region.pma_size = max(region.pma_size, self._pma_region_demand(region))

        # Truncate decoys (and their aligned exclusion list) to the budget loader.setup_pma
        # will actually emit, BEFORE RieMap draws a single address: a decoy that will not
        # survive emission-time truncation must not still veto a page-table placement here
        # (see _project_decoy_budget).
        budget = self._project_decoy_budget()
        if budget < len(self.pool.pma_random_regions):
            log.warning(f"PMA randomization: projecting decoys {len(self.pool.pma_random_regions)} -> {budget} to fit before RieMap allocation")
            self.pool.truncate_pma_random_regions(budget)

        # The builder owns a separate address generator, so it must avoid everything
        # RiescueD has already reserved in its own -- both physical (page-map roots,
        # section LMAs, ;#reserve_memory) and linear (fixed section/code VAs), so no
        # solver-drawn page (esp. a large page) overlaps the code/runtime region in
        # either space.
        extra_reserved = [(RV.AddressType.PHYSICAL, start, end - start) for start, end in self.addrgen.allocated_physical_intervals()]
        extra_reserved += [(RV.AddressType.LINEAR, start, end - start) for start, end in self.addrgen.allocated_linear_intervals()]
        result, translation = build_page_tables(
            self.pool,
            self.featmgr,
            self.rng,
            extra_reserved_spans=extra_reserved,
            pma_region_bindings=self._pma_region_bindings,
        )
        self._readback_allocation(result, translation)

    def _readback_allocation(self, result, translation):
        """Read a built :class:`AllocationResult` back into the pool.

        RieMap allocated everything (roots, page-table nodes, page frames) and built
        every tree; RiescueD consumes the result. It (1) records the intervals RieMap
        reports as occupied in its own space so its remaining passes (init memory,
        carveouts) never land on a page table, (2) publishes each space's root
        for satp/vsatp/hgatp and the ``{map}_sptbr``
        equates, and (3) re-attaches its symbol names to the allocated addresses. The
        page-table trees are emitted later straight from ``result`` (see
        ``AssemblyWriter._generate_pagetable_assembly``); RiescueD never rebuilds them.
        """
        self.pool.allocation_result = result
        self.pool.page_translation = translation

        # Mark every span RieMap placed as occupied in RiescueD's own space. This is
        # the whole reservation -- roots, PT nodes and page frames -- so nothing
        # RiescueD allocates afterwards collides with a page table.
        for start, end in result.physical_intervals():
            self.addrgen.reserve_memory(address_type=RV.AddressType.PHYSICAL, start_address=start, size=end - start)
        for start, end in result.linear_intervals():
            self.addrgen.reserve_memory(address_type=RV.AddressType.LINEAR, start_address=start, size=end - start)

        # Roots: RieMap owns root allocation. Publish each space's root into its pool
        # page map (satp/vsatp/hgatp read it) and as a {map}_sptbr equate. The Space
        # object carries no name of its own -- look up RiescueD's own map name from
        # the translation's object -> name map.
        for space in result.spaces():
            if space.paging_mode == RV.RiscvPagingModes.DISABLE:
                continue
            map_name = translation.space_names.get(space.space)
            if map_name is None:
                continue  # a space RiescueD didn't declare a name for (shouldn't happen)
            page_maps = self.pool.get_page_maps()
            if map_name in page_maps:
                page_maps[map_name].sptbr = space.root_addr
            sptbr_name = f"{map_name}_sptbr"
            self.pool.add_random_addr(addr_name=sptbr_name, addr=Address(name=sptbr_name, address=space.root_addr, type=RV.AddressType.PHYSICAL), allow_duplicate=True)

        # modify_pt PT-node symbols ({lin}__pt_level{N}): a modify_pt test writes its own
        # live page-table entries at runtime through these. RieMap has no knowledge of the
        # read-back -- RiescueD declared, per modify_pt mapping, a pinned PT-node frame at
        # every walk level plus an ordinary leaf window mapping onto that frame
        # (translation.pt_windows). Here we resolve the symbols from the built result: walk
        # the source VA to get each level's live PTE slot address (never hand-computed
        # index*8 -- NAPOT leaves make that wrong), then express the writable VA as
        # ``window_va + (pte_addr - frame_base)`` and the physical slot as ``pte_addr``.
        for src_page, windows in translation.pt_windows.items():
            names = translation.page_names.get(src_page)
            if names is None:
                continue
            lin_name = names[0]
            space_result = result.space(src_page.space)
            src_va, _src_pa = result.address_of(src_page)
            steps, _translated = space_result.walk(src_va)
            step_by_level = {step.level: step for step in steps}
            for level, window_page, frame_page in windows:
                step = step_by_level.get(level)
                if step is None:
                    continue
                window_va, _ = result.address_of(window_page)
                _, frame_base = result.address_of(frame_page)
                lin_addr = window_va + (step.pte_addr - frame_base)
                clean = f"{lin_name}__pt_level{level}"
                self.pool.add_random_addr(addr_name=clean, addr=Address(name=clean, address=lin_addr, type=RV.AddressType.LINEAR), allow_duplicate=True)
                phys_sym = f"{clean}__phys"
                self.pool.add_random_addr(addr_name=phys_sym, addr=Address(name=phys_sym, address=step.pte_addr, type=RV.AddressType.PHYSICAL), allow_duplicate=True)

        # PMA regions the builder placed: record base/size for equates + pmacfg. A
        # custom_region carries no PmaInfo -- its base is fixed and its PMA setup is the
        # consumer's own responsibility -- so it is absent from region_pma entirely.
        # Whether to (re-)register into pool.pma_regions is exactly the provenance decided
        # at pre-allocation time (see PmaRegionBinding): a brand-new region is registered
        # here for the first time; a reused hint, an adopted decoy, or a region shared with
        # an earlier in_pma address is already tracked in the pool and must not be added
        # again, or it would double-count a PMA entry at emission.
        for region, binding in translation.region_pma.items():
            binding.info.pma_address = result.region_base(region)
            binding.info.pma_size = region.size
            if binding.register_on_readback:
                self.pool.pma_regions.add_entry(binding.info)

        # Bare address requests (no page table). Section skip_page_map addresses are
        # also bare pages but are read back in the sections loop below (they carry
        # section metadata), so translation.addr_names only carries a genuine
        # random-addr's Page.
        for page, name in translation.addr_names.items():
            addr_type = translation.addr_types[page]
            addr = result.address(page)
            # A bare linear address is a guest/OS VA: sign-extend it into canonical form
            # (canonicalize_lin_addr is a no-op when paging is disabled). Physical / GPA
            # addresses are left raw -- their high bits zero-extend, not sign-extend.
            if addr_type == RV.AddressType.LINEAR:
                addr = self.canonicalize_lin_addr(addr)
            self.pool.add_random_addr(addr_name=name, addr=Address(name=name, address=addr, type=addr_type), allow_duplicate=True)

        # Pages: one per (map, request). The solver already resolved shared (SameAs),
        # linked (OffsetFrom) and alias addresses, so each page's VA/PA is read
        # straight from the result -- no per-feature handling needed here.
        for va_page, (map_name, ppm) in translation.page_source.items():
            lin_name, phys_name = translation.page_names[va_page]
            va, pa = result.address_of(va_page)
            # A ``name+offset`` page is addressed via its bare random_addr equate --
            # never emitted as an equate of its own (an invalid assembler symbol).
            if "+" not in lin_name:
                self.pool.add_random_addr(addr_name=lin_name, addr=Address(name=lin_name, address=va, type=RV.AddressType.LINEAR), allow_duplicate=True)
                if "+" not in phys_name:
                    self.pool.add_random_addr(addr_name=phys_name, addr=Address(name=phys_name, address=pa, type=RV.AddressType.PHYSICAL), allow_duplicate=True)

            p = PageInfo(name=lin_name, phys_name=phys_name, lin_addr=va, phys_addr=pa, alias=ppm.alias, in_private_map=ppm.in_private_map)
            self.pool.add_page(page=p, map_names=[map_name])

            self._register_gstage_pt_equates(result, va_page, lin_name, pa)

        # Section pages: RieMap allocated + mapped them. Publish each section's VMA
        # into random_addrs (the plain-name equate) and its PA under phys_name, plus
        # a SectionInfo (linker VMA/LMA). Walk in layout order so an anchor's address
        # is resolved before a section that sits OffsetFrom it, and so pool.sections
        # keeps the insertion order the linker's segment grouping relies on.
        resolved: dict = {}
        for spec, page, is_page in translation.sections:
            if is_page:
                vma, pa = result.address_of(page)
            else:
                pa = result.address(page)
                vma = self._section_vma(spec, pa, resolved)
            resolved[spec.name] = (vma, pa)
            name_type = RV.AddressType.PHYSICAL if spec.skip_page_map else RV.AddressType.LINEAR
            self.pool.add_random_addr(addr_name=spec.name, addr=Address(name=spec.name, address=vma, type=name_type), allow_duplicate=True)
            self.pool.add_random_addr(addr_name=spec.phys_name, addr=Address(name=spec.phys_name, address=pa, type=RV.AddressType.PHYSICAL), allow_duplicate=True)
            if not spec.skip_linker:
                self.pool.set_section(spec.name, vma=vma, lma=pa)

    def _register_gstage_pt_equates(self, result, va_page, lin_name, gpa):
        """Register the ``{lin}__vsleaf{N}`` / ``{lin}__vslevel{N}`` equate families for one
        two-stage page.

        A test running under a g-stage needs to name the *guest physical* addresses its own
        VS-stage walk touches: the GPA of its data page (``__vsleaf{leaf}``) and the GPA of
        every VS-stage page-table frame below the root (``__vslevel{L}`` -- the frame the
        level-``L`` pointer PTE targets). Those are the addresses a hypervisor test compares
        ``htval`` against, and the addresses whose g-stage identity leaves it invalidates to
        provoke an implicit-page-table-walk fault. Each is published twice: ``__gpa`` as a
        linear address (it is a GPA -- never sign-extended; guest physical addresses
        zero-extend) and ``__phys`` as the same value physically, because RieMap
        identity-maps every VS-stage frame into the g-stage domain.

        No read-back field is needed for the frames: a table's base is its PTE's address with
        the table's own span masked off, and ``WalkStep.pte_addr`` already carries that. The
        span is derived (index-field width + PTE stride) rather than hardcoded at 4 KiB, so an
        sv32 table (1024 x 4 B) and a future x4 g-stage root (2048 x 8 B = 16 KiB) both work.

        The pair is skipped when either name already exists, so under ``--private_maps`` --
        where the same ``lin_name`` is mapped in several maps -- the first map's frames win
        rather than the last one's. ``allow_duplicate`` would instead make the published value
        depend on dict iteration order.
        """
        # Every early-out is decided from the DECLARATION (the page's own Space) before
        # ``result.space()`` is called: a space with no page table -- a paging-disabled map, and
        # the physical leaf domain a disabled single-stage page lives in -- has no SpaceResult
        # at all, and asking for one raises KeyError.
        if self.featmgr.paging_g_mode == RV.RiscvPagingModes.DISABLE:
            return
        if va_page.space.paging_mode == RV.RiscvPagingModes.DISABLE:
            return
        space_result = result.space(va_page.space)
        if space_result.is_gstage:
            return

        # A linked child's name is an offset expression ("lin7+0x1000"); '+' is not legal in an
        # assembler identifier, so sanitize it the way the pre-refactor generator did.
        base = lin_name.replace("+", "_")
        meta = result.page_meta(va_page)
        leaf_ps = meta.gstage_vs_leaf_pagesize or RV.RiscvPageSizes.S4KB
        nonleaf_ps = meta.gstage_vs_nonleaf_pagesize or RV.RiscvPageSizes.S4KB

        leaf_level = RV.RiscvPageSizes.pt_leaf_level(meta.pagesize)
        self._add_gstage_pt_equate(f"{base}__vsleaf{leaf_level}", gpa & RV.RiscvPageSizes.address_mask(leaf_ps))

        mode = space_result.paging_mode
        va, _pa = result.address_of(va_page)
        steps, _translated = space_result.walk(va)
        stride = RV.RiscvPagingModes.pt_entry_size(mode=mode)
        for step, child in zip(steps, steps[1:]):
            # ``child`` is a PTE inside the frame the level-``step.level`` pointer targets, so
            # that frame's base is the child PTE's address with the frame's own span masked
            # off: (1 << index-field width) slots of ``stride`` bytes -- 4 KiB for every table
            # RieMap builds today, in every mode. Every level here came out of a completed walk,
            # so it is in range; ``index_bits`` raises rather than returning None anyway.
            hi, lo = RV.RiscvPagingModes.index_bits(mode, child.level)
            span = (1 << (hi - lo + 1)) * stride
            frame = child.pte_addr & ~(span - 1)
            self._add_gstage_pt_equate(f"{base}__vslevel{step.level}", frame & RV.RiscvPageSizes.address_mask(nonleaf_ps))

    def _add_gstage_pt_equate(self, name, addr):
        """Publish one ``__gpa`` / ``__phys`` equate pair for an identity-mapped GPA."""
        gpa_name = f"{name}__gpa"
        phys_name = f"{name}__phys"
        if self.pool.random_addr_exists(gpa_name) or self.pool.random_addr_exists(phys_name):
            return
        self.pool.add_random_addr(addr_name=gpa_name, addr=Address(name=gpa_name, address=addr, type=RV.AddressType.LINEAR))
        self.pool.add_random_addr(addr_name=phys_name, addr=Address(name=phys_name, address=addr, type=RV.AddressType.PHYSICAL))

    def _section_vma(self, spec, pa, resolved):
        """The linker VMA of a skip_page_map section: a pinned value, an anchor's
        VMA plus offset, or -- when neither -- the PA (VMA == LMA)."""
        pl = spec.lin
        if pl.exact is not None:
            return pl.exact
        if pl.anchor is not None:
            return resolved[pl.anchor][0] + pl.offset
        return pa

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
        num_super_code_pages = 128
        num_user_code_pages = 128
        num_machine_code_pages = 128

        # csr pages
        num_machine_csr_pages = 1
        num_super_csr_pages = 1

        # pte pages (walk is now inline in syscall handler, no separate page needed)
        num_machine_pte_pages = 0

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
                    size=alloc_size * (num_code_pages + num_super_code_pages + num_user_code_pages + num_machine_code_pages + num_machine_csr_pages + num_super_csr_pages + num_c_text_pages + 1),
                    iscode=True,
                    start_addr=self.featmgr.reset_pc,
                    phys_name="__section_code",
                    skip_page_map=code_skip_page_map,
                )
            elif self.featmgr.randomize_code_location:
                # First allocate space for ALL the code pages. Then add all the individual pages
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="code",
                    size=alloc_size * (num_code_pages + num_super_code_pages + num_user_code_pages + num_machine_code_pages + num_machine_csr_pages + num_super_csr_pages + num_c_text_pages),
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

            # If C code is used, allocate .text section immediately after the test .code pages
            # so that jal calls from .code into .text stay within range (JAL has ±1 MiB limit).
            # This must come before the privileged code pages which can be numerous.
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

            # C sections share one contiguous VA region: the first C section is a free
            # anchor RieMap places, and every following C section (here and in later
            # handle_sections calls, threaded through self.next_c_section_lin_addr) sits
            # OffsetFrom it. The relation-first solver places the whole chain before any
            # unrelated free draw, so nothing lands inside the block -- no explicit
            # up-front region reservation needed.
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
            hc_base_phys = phys_addr

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

            # U=1 alias of hart_context for user-mode access.
            # Same physical pages, fresh VA, U bit set. Used by OS_SETUP_CHECK_EXCP
            # when force_user=1 so VU/U-mode code can write hart context without
            # needing sstatus.SUM=1 on the supervisor-only hart_context mapping.
            if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
                (alias_lin_addr, _) = self.add_section_handler(
                    name="hart_context_user",
                    size=page_size * num_pages,
                    iscode=False,
                    phys_name=f"__section_{ctx}_user",
                    start_addr=hc_base_phys,
                    skip_linker=True,
                    always_user=True,
                )
                alloc_alias_lin_addr = alias_lin_addr + page_size
                alloc_alias_phys_addr = hc_base_phys + page_size
                for i in range(1, num_pages):
                    (alias_lin_addr, _) = self.add_section_handler(
                        name=f"__page_{ctx}_user_{i}",
                        size=page_size,
                        iscode=False,
                        phys_name=f"__section___page_{ctx}_user_{i}",
                        start_addr=alloc_alias_phys_addr,
                        start_lin_addr=alloc_alias_lin_addr,
                        skip_linker=True,
                        always_user=True,
                    )
                    alloc_alias_lin_addr = alias_lin_addr + page_size
                    alloc_alias_phys_addr = alloc_alias_phys_addr + page_size

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
            # The .io_htif section contains two .align 6 directives (64-byte alignment)
            # plus two .dword values (tohost, fromhost), resulting in 0x48 bytes when
            # 64-byte aligned. Reserve 0x80 to safely cover alignment padding.
            (lin_addr, phys_addr) = self.add_section_handler(
                name="io_htif",
                size=0x80,
                iscode=False,
                identity_map=True,
                start_addr=self.featmgr.io_htif_addr,
            )

        elif section == "maplic":
            page_size = 0x1000
            n_pages = int((self.featmgr.io_maplic_size + (page_size - 1)) / page_size)
            start_addr = self.featmgr.io_maplic_addr
            for i in range(n_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"maplic_{i}",
                    size=page_size,
                    iscode=False,
                    identity_map=True,
                    start_addr=start_addr,
                )
                start_addr += page_size

        elif section == "saplic":
            page_size = 0x1000
            n_pages = int((self.featmgr.io_saplic_size + (page_size - 1)) / page_size)
            start_addr = self.featmgr.io_saplic_addr
            for i in range(n_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"saplic_{i}",
                    size=page_size,
                    iscode=False,
                    identity_map=True,
                    start_addr=start_addr,
                )
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
            skip_page_map = False

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
                skip_page_map=skip_page_map,
            )
            alloc_addr = lin_addr + alloc_size
            for i in range(1, num_selfcheck_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section__selfcheck_data_{i}",
                    size=alloc_size,
                    iscode=False,
                    phys_name="",
                    start_addr=alloc_addr,
                    skip_page_map=skip_page_map,
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
        """Declare one section page to RieMap -- allocation + mapping happen there.

        This records a :class:`SectionSpec` describing how RieMap places and
        maps the page, and returns a *symbolic* ``(lin, phys)`` pair. Because a returned
        :class:`SectionAddr` supports ``+``, the existing contiguity threading in
        :meth:`handle_sections` (``addr + size``) is preserved: a fixed ``int`` start
        stays a pinned address, a ``SectionAddr`` becomes an ``OffsetFrom`` an anchor
        section, and ``None`` is a free draw RieMap chooses.

        ``start_addr`` / ``start_lin_addr`` accept an ``int`` (pinned), a
        :class:`SectionAddr` (relative to another section), or ``None`` (free).
        """
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE and self.featmgr.paging_g_mode == RV.RiscvPagingModes.DISABLE:
            skip_page_map = True
        if self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE:
            identity_map = True
        if identity_map:
            if start_addr is None and start_lin_addr is not None:
                start_addr = start_lin_addr
            start_lin_addr = None

        def placement(val):
            if val is None:
                return _Placement()
            if isinstance(val, SectionAddr):
                return _Placement(anchor=val.anchor, offset=val.offset)
            return _Placement(exact=val)

        phys_name_final = phys_name if phys_name else f"{name}_phys"

        if skip_page_map:
            # No translation: a bare physical address (+ linker section). VMA equals
            # the PA unless both a VA and PA were pinned (then VMA is the given lin).
            both = start_addr is not None and start_lin_addr is not None
            phys = placement(start_addr if start_addr is not None else start_lin_addr)
            lin = placement(start_lin_addr) if both else _Placement()
            identity = not both
        elif start_addr is not None and start_lin_addr is not None:
            phys, lin, identity = placement(start_addr), placement(start_lin_addr), False
        elif start_addr is not None:
            phys = placement(start_addr)
            lin, identity = (placement(start_addr), True) if identity_map else (_Placement(), False)
        elif start_lin_addr is not None:
            if identity_map:
                phys, lin, identity = placement(start_lin_addr), placement(start_lin_addr), True
            else:
                phys, lin, identity = _Placement(), placement(start_lin_addr), False
        else:
            phys, lin, identity = _Placement(), _Placement(), bool(identity_map)

        self.pool.section_specs.append(
            SectionSpec(
                name=name,
                phys_name=phys_name_final,
                size=size,
                phys=phys,
                lin=lin,
                iscode=iscode,
                always_super=always_super,
                always_user=always_user,
                skip_page_map=skip_page_map,
                skip_linker=skip_linker,
                identity=identity,
            )
        )
        return (SectionAddr(name, 0), SectionAddr(name, 0))

    def generate_init_mem(self):
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

        sections = self.pool.get_parsed_sections()
        if self.featmgr.c_used:
            sections += self.c_used_sections
        sections = list(dict.fromkeys(sections))
        for section_name in sections:
            self.handle_sections(section_name)
