# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import re
import io
import logging
from pathlib import Path

import riescue.dtest_framework.lib.addrgen as addrgen
import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.lib.address import Address
from riescue.lib.numgen import NumGen
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.parser import PmaInfo, PmpInfo, ParsedPageMapping, ParsedRandomAddress, ParsedRandomData
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.lib.page_map import Page, PageMap
from riescue.dtest_framework.config import CpuConfig, Memory

from riescue.dtest_framework.runtime import Runtime, formatted_line_generator

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
        self.numgen = NumGen(self.rng)
        self.numgen.default_genops()

        self.run_dir = run_dir
        self.runtime = Runtime(rng=self.rng, pool=self.pool, run_dir=self.run_dir, featmgr=self.featmgr)

        # Set MISA bits based on enabled features
        self.misa_bits = self.featmgr.get_misa_bits()

        # Output files
        self.os_inc = self.run_dir / f"{self.pool.testname}_os.inc"
        self.scheduler_inc = self.run_dir / f"{self.pool.testname}_scheduler.inc"
        self.syscalls_inc = self.run_dir / f"{self.pool.testname}_syscalls.inc"
        self.excp_inc = self.run_dir / f"{self.pool.testname}_trap_handler.inc"
        self.loader_inc = self.run_dir / f"{self.pool.testname}_loader.inc"
        self.macros_inc = self.run_dir / f"{self.pool.testname}_macros.inc"
        self.equates_inc = self.run_dir / f"{self.pool.testname}_equates.inc"
        self.pagetables_inc = self.run_dir / f"{self.pool.testname}_pagetables.inc"
        self.hypervisor_inc = self.run_dir / f"{self.pool.testname}_hypervisor.inc"
        self.linker_script = self.run_dir / f"{self.pool.testname}.ld"

        # Default sections
        self.os_code_sections = ["text"]
        self.os_data_sections = ["os_data", "os_stack"]
        self.io_sections = []  # IO sections to be added
        if self.featmgr.io_htif_addr is not None:
            self.io_sections.append("io_htif")  # If unset, don't want to make it a section (and add to linker); this effectively places io_htif after OS code

        self.c_used_sections = [
            "bss",
            "sbss",
            "sdata",
            "c_text",
            "rela.c_text",
            "c_stack",
            "rodata",
            "c_data",
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

        memory = featmgr.memory

        # Setup PMAs and PMP regions
        for range in memory.dram_ranges:
            # Setup default PMAs for DRAM
            # FIXME: Did we really mean to only create a PMA for the last dram range?
            self.pool.pma_dram_default = PmaInfo(pma_address=range.start, pma_size=range.size, pma_memory_type="memory")

            # Also add to pmp regions
            self.pool.pmp_regions.add_region(base=range.start, size=range.size)

        for range in memory.secure_ranges:
            # Since this secure region, we need to set bit-55 to 1
            start_addr = range.start | 0x0080000000000000
            self.pool.pmp_regions.add_region(base=start_addr, size=range.size)

            # Setup pmas
            self.pool.pma_regions[f"sec_{range.start:x}"] = PmaInfo(pma_address=range.start, pma_size=range.size, pma_memory_type="memory")
        self.addrgen = addrgen.AddrGen(self.rng, memory, self.featmgr.addrgen_limit_indices, self.featmgr.addrgen_limit_way_predictor_multihit)

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

    def generate(self, file_in, assembly_out):
        # Need better name for .s files (riescue source?)
        # .S can be assembly
        filehandle = None
        with open(assembly_out, "w") as f:
            filehandle = f
            # The order of calling following functions is very important, please do not change
            # unless you know what you are doing
            self.generate_data()
            self.add_page_maps()
            self.generate_sections()
            self.initialize_page_maps()
            self.handle_res_mem()
            self.generate_addr()
            self.handle_page_mappings()
            self.generate_init_mem()

            use_single_assembly_file = self.featmgr.single_assembly_file or bool(self.featmgr.linux_mode)
            pagetable_includes = ""
            if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE or self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                if use_single_assembly_file:
                    with io.StringIO() as f:
                        self.create_pagetables(f)
                        pagetable_includes = "## pagetables ##\n" + f.getvalue()
                else:
                    with open(self.pagetables_inc, "w") as pt_file_handle:
                        self.create_pagetables(pt_file_handle)

            if not use_single_assembly_file:
                self.generate_runtime(testname=self.pool.testname)
            self.write_test(
                file_in,
                output_file=filehandle,
                single_assembly_file=use_single_assembly_file,
                pagetable_includes=pagetable_includes,
            )
            self.generate_linker_script()

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
            lin_addr = common.str_to_int(page_mapping.lin_addr)
        else:
            lin_addr_name = page_mapping.lin_name

        if page_mapping.phys_addr_specified:
            phys_addr_name = page_mapping.phys_name
            phys_addr = common.str_to_int(page_mapping.phys_addr)
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
        self.pool.add_page(page=p, map_names=page_maps)

        # return tuple([lin_addr_name, lin_addr]), tuple([phys_addr_name, phys_addr])

    def handle_random_page_mappings(self, page_mapping):
        """
        Handle syntax like:
        ;#page_mapping(lin_addr=lin1, phys_addr=&random, v=1, r=1, w=1)
        """

        lin_addr_name = page_mapping.lin_name
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

    def handle_random_addr(self, random_addr):
        """
        Generate addresses for left over random_addrs
        """
        addr_name = random_addr.name
        addr_type = random_addr.type

        phys_address_size = address_size = random_addr.size
        phys_address_mask = address_mask = random_addr.and_mask
        secure = False

        if self.pool.parsed_page_mapping_exists(addr_name) and self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
            parsed_page_mapping = self.pool.get_parsed_page_mapping(addr_name)
            address_mask = parsed_page_mapping.address_mask
            phys_address_size = RV.RiscvPageSizes.memory(parsed_page_mapping.final_pagesize)
            phys_address_mask = RV.RiscvPageSizes.address_mask(parsed_page_mapping.final_pagesize)
            # If g-stage s enabled, this physical address becomes GPA. It means that the alignment of
            # this address should be at least the size of gstage_vs_leaf pagesize
            if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                phys_address_mask = min(phys_address_mask, parsed_page_mapping.gstage_vs_leaf_address_mask)
                phys_address_size = max(phys_address_size, parsed_page_mapping.gstage_vs_leaf_address_size)

            # also update address_bits/mask for the physical address, if fixed address is not proivded in the page_mapping
            if self.pool.parsed_random_addr_exists(addr_name=parsed_page_mapping.phys_name):
                phys_random_addr = self.pool.get_parsed_addr(parsed_page_mapping.phys_name)
                phys_random_addr.size = address_size
                phys_random_addr.and_mask &= address_mask

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
            )
            log.debug(f"Adding addr: {addr_name}, addr_c: {address_contstaint}")
            address = self.addrgen.generate_address(constraint=address_contstaint)
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
        for attr in ["v", "a", "d", "g", "u", "x", "w", "r"]:
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
        # For most bits, clearing it will generate a fault, e.g. v=0, w=0, r=0, x=0. But, for g-bit common case is to set it to 0
        significant_bit_value = 0
        # if attr[0] in ['g', 'x']:
        if attr[0] in ["g"]:
            significant_bit_value = 1
        if attr[0] in ["u"]:
            # If current mode is super mode, then significant bit is 1, else 0
            if self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
                significant_bit_value = 1

        if not page_mapping.__getattribute__(attr) == significant_bit_value:
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
                page_mapping.__setattr__(f"{attr[0]}_nonleaf", significant_bit_value)
                (addr_size_leaf, addr_mask_leaf) = self.randomize_pt_attrs(
                    attr=attr[0],
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
                page_mapping.__setattr__(f"{attr[0]}_level{g_leaf_level}", significant_bit_value)
                log.debug(f"Setting {attr[0]}: {significant_bit_value} for {page_mapping.lin_name}")
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
        pt_attr = f"{attr[0]}_level{vslevel_to_randomize}_glevel{glevel_to_randomize}"
        log.debug(
            " ".join(
                [
                    f"setting {pt_attr} for {page_mapping.lin_name} with {significant_bit_value},",
                    f"reserving: {addr_size_leaf:x}, {addr_mask_leaf:x}, {addr_size_nonleaf:x}, {addr_mask_nonleaf:x},",
                    f"pt_attr: {pt_attr}",
                ]
            ),
        )
        page_mapping.__setattr__(f"{pt_attr}", significant_bit_value)

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
        significant_bit_value = 0
        leaf_bit_val = 1
        # if attr in ['g', 'x']:
        if attr in ["g"]:
            significant_bit_value = 1
        if attr[0] in ["u"]:
            # If current mode is super mode, then significant bit is 1, else 0
            if self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
                significant_bit_value = 1
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
        if page_mapping.__getattribute__(attr) == significant_bit_value:
            # Make sure we update the correct level based on the pagesize
            page_mapping.__setattr__(f"{attr}_level{pt_leaf_level}", significant_bit_value)
            return (addr_size, addr_mask)

        if page_mapping.__getattribute__(f"{attr}_nonleaf") == significant_bit_value:
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
            page_mapping.__setattr__(f"{attr}_level{rnd_pt_level}", significant_bit_value)

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
        if self.pool.parsed_page_mapping_exists(page_mapping.lin_name) and self.pool.get_parsed_page_mapping(page_mapping.lin_name):
            ppm = self.pool.get_parsed_page_mapping(page_mapping.lin_name)
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
        if self.pool.parsed_page_mapping_exists(page_mapping.lin_name) and self.pool.get_parsed_page_mapping(page_mapping.lin_name):
            ppm = self.pool.get_parsed_page_mapping(page_mapping.lin_name)
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

    def handle_page_mappings(self, parsed_page_mappings=None):
        """
        All the addresses should have been generated before this. So, we just need to create a Page instance for
        each of the page_mappings and create pagetables here
        """
        if parsed_page_mappings is None:
            parsed_page_mappings = self.pool.get_parsed_page_mappings()

        for lin_name, parsed_page_mapping in parsed_page_mappings.items():
            if parsed_page_mapping.resolve_priority == 0:
                # Handle maps
                # Everything goes in map_os, map_hyp by default
                page_maps = ["map_os"]
                # Add hypervisor map if we are in virtualized mode
                if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.paging_mode == RV.RiscvPagingModes.DISABLE and self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                    page_maps += ["map_hyp"]
                page_maps += parsed_page_mapping.page_maps

                log.debug(f"Page: {lin_name}, alias={parsed_page_mapping.alias}")
                # Create the Page instance for this type of page_mapping entry
                p = Page(
                    name=lin_name,
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
        level_types = ["v", "u", "a", "d", "r", "w", "x"]

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

    def create_pagetables(self, pt_file_handle):
        # Create pagetables here

        # Handle the vs-stage pagetables first since we need to know the guest physical
        # address to generate the g-stage pagetables
        for map in self.pool.get_page_maps().values():
            if not map.g_map and map.paging_mode != RV.RiscvPagingModes.DISABLE:
                map.create_pagetables(self.rng)
                map.print_pagetables(file_handle=pt_file_handle)

        # Now that the vs-stage pagetables are generated, handle the g-stage pagetables
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            for map in self.pool.get_page_maps().values():
                if map.g_map:
                    map.create_pagetables(self.rng)
                    map.print_pagetables(file_handle=pt_file_handle)

    def handle_sections(self, section):
        sections_to_process = ["data", "text", "code"] + self.os_data_sections + self.io_sections
        if self.featmgr.c_used:
            sections_to_process += self.c_used_sections

        if section not in sections_to_process:
            return

        if self.featmgr.wysiwyg and self.pool.random_addr_exists("text"):
            return

        num_text_pages = 16
        num_user_text_pages = 1 if self.featmgr.priv_mode == RV.RiscvPrivileges.USER else 0

        num_code_pages = 128
        if self.featmgr.more_os_pages:
            num_code_pages = 500
        # Also add some default super and user code pages
        num_super_code_pages = 8
        num_user_code_pages = 8
        num_machine_code_pages = 8

        if section == "text":
            alloc_size = 0x1000
            alloc_addr = self.featmgr.reset_pc

            reserve_page_count = num_text_pages + num_user_text_pages
            if not self.featmgr.randomize_code_location:
                # Allocate .code immediately after .text
                # This is useful to avoid far jumps.
                reserve_page_count += num_code_pages + num_super_code_pages + num_user_code_pages + num_machine_code_pages

            # First allocate space for ALL the pages. Then add all the individual pages
            (lin_addr, phys_addr) = self.add_section_handler(
                name="text",
                size=alloc_size * reserve_page_count,
                iscode=True,
                always_super=True,
                phys_name="_section_text",
                start_addr=alloc_addr,
            )

            alloc_addr = lin_addr + alloc_size
            for i in range(1, num_text_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section__text_{i}",
                    size=alloc_size,
                    iscode=True,
                    always_super=True,
                    phys_name="",
                    start_addr=alloc_addr,
                )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + alloc_size

            if num_user_text_pages:
                # Allocate text page for user space OS routines.
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="text_user",
                    size=alloc_size * num_user_text_pages,
                    iscode=True,
                    always_user=True,
                    phys_name="",
                    start_addr=alloc_addr,
                )

                alloc_addr = lin_addr + alloc_size
                for i in range(1, num_user_text_pages):
                    (lin_addr, phys_addr) = self.add_section_handler(
                        name=f"text_user_{i}",
                        size=alloc_size,
                        iscode=True,
                        always_user=True,
                        phys_name="",
                        start_addr=alloc_addr,
                    )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + alloc_size

            self.text_end_addr = alloc_addr

        if section == "code":
            alloc_size = 0x1000
            # Randomize code offset with interesting values for cacheline alignment of 64B and in anywhere in
            # the first 5-cachelines
            self.code_offset = None
            if self.featmgr.code_offset is not None:
                self.code_offset = self.featmgr.code_offset
            elif self.featmgr.force_alignment:
                self.code_offset = 0
            else:
                self.code_offset = self.rng.random_in_range(0, 0x3F * 5, 2)

            if self.featmgr.randomize_code_location:
                # First allocate space for ALL the code pages. Then add all the individual pages
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="code",
                    size=alloc_size * (num_code_pages + num_super_code_pages + num_user_code_pages + num_machine_code_pages),
                    iscode=True,
                    phys_name="__section_code",
                    identity_map=True,
                )
            else:
                # Allocate .code immediately after .text. .text already reserved the space for .code
                (lin_addr, phys_addr) = self.add_section_handler(
                    name="code",
                    size=alloc_size,
                    iscode=True,
                    start_addr=self.text_end_addr,
                    phys_name="__section_code",
                    identity_map=True,
                )
            alloc_addr = lin_addr + alloc_size

            for i in range(1, num_code_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section__code_{i}",
                    size=alloc_size,
                    iscode=True,
                    start_addr=alloc_addr,
                    phys_name="",
                    identity_map=True,
                )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + alloc_size
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
                    start_addr=alloc_addr,
                    phys_name=page_phys_name,
                    identity_map=True,
                )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + alloc_size
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
                    start_addr=alloc_addr,
                    phys_name=page_phys_name,
                    identity_map=True,
                )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + alloc_size
                # Every call to add_section_handler already adds the sections to the pool
                # self.pool.add_section(section_name=page_name)

            # Add user pages with name starting code_user
            for i in range(num_machine_code_pages):
                page_name = f"code_machine_{i}"
                page_phys_name = f"__section_{page_name}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=alloc_size,
                    iscode=True,
                    always_user=True,
                    start_addr=alloc_addr,
                    phys_name=page_phys_name,
                    identity_map=True,
                )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + alloc_size
                # Every call to add_section_handler already adds the sections to the pool
                # self.pool.add_section(section_name=page_name)

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
                identity_map=True,
            )

            alloc_addr = lin_addr + data_page_size
            for i in range(1, size // data_page_size):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section_{section}_{i}",
                    size=data_page_size,
                    iscode=False,
                    phys_name="",
                    identity_map=True,
                    start_addr=alloc_addr,
                )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + data_page_size

        # SUGGESTION tie the size of these sections to the size of the c code compiled sections that more or less use this exclusively.
        elif self.featmgr.c_used and section in self.c_used_sections:
            num_total_pages = 30
            if section in ["bss"]:
                if self.featmgr.big_bss:
                    num_total_pages = 3080
                elif self.featmgr.small_bss:
                    num_total_pages = 200
                else:
                    num_total_pages = 2200
            elif section in ["c_stack"]:
                num_total_pages = 400
            elif section in ["rodata"]:
                num_total_pages = 75
            elif section in self.gcc_cstdlib_sections:
                num_total_pages = 1
            page_name = section
            is_code = True if "text" in section else False
            phys_page_name = f"__section_{section}"
            (lin_addr, phys_addr) = self.add_section_handler(
                name=page_name,
                size=0x1000 * num_total_pages,
                iscode=is_code,
                phys_name=phys_page_name,
                always_user=not is_code,
                identity_map=True,
            )

            # add several more pages
            for i in range(1, num_total_pages):
                page_name = f"{section}_{i}"
                phys_page_name = f"__section_{section}_{i}"
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=page_name,
                    size=0x1000,
                    iscode=is_code,
                    phys_name=phys_page_name,
                    always_user=not is_code,
                    identity_map=True,
                    start_addr=lin_addr + 0x1000,
                )

        elif section == "os_stack":
            if self.featmgr.more_os_pages:
                # FIXME: This really isnt supported downstream. The loader code only supports stack size of 0x1000.
                # These extra pages can only be used by the last hart. Also by name shoudln't this be num_cpus+32?
                num_stack_pages = 32
            else:
                num_stack_pages = self.featmgr.num_cpus
            stack_page_size = 0x1000

            # First make an allocation for the full stack, then the individual pages
            (lin_addr, phys_addr) = self.add_section_handler(
                name=section + "_end",
                size=stack_page_size * num_stack_pages,
                iscode=False,
                phys_name="",
                identity_map=True,
            )
            stack_top_addr = lin_addr + (stack_page_size * num_stack_pages)

            alloc_addr = lin_addr + stack_page_size
            for i in range(1, num_stack_pages):
                (lin_addr, phys_addr) = self.add_section_handler(
                    name=f"__section__{section}_{i}",
                    size=stack_page_size,
                    iscode=False,
                    phys_name="",
                    identity_map=True,
                    start_addr=alloc_addr,
                )
                # increment alloc_addr to the next available address
                alloc_addr = lin_addr + stack_page_size

            # Mark start (lowest address) of stack through equates.
            # Do not use add_section_handler as it can create overlapping page tables if the stack is just before another section.
            # The stack start address would be the same as the next section start address. Directly inject into pool skipping page tables.
            addr_inst = Address(name=section, type=RV.AddressType.LINEAR, address=alloc_addr)
            self.pool.add_random_addr(addr_name=section, addr=addr_inst)

            addr_inst = Address(name=f"{section}_phys", type=RV.AddressType.PHYSICAL, address=alloc_addr)
            self.pool.add_random_addr(addr_name=f"{section}_phys", addr=addr_inst)

        elif section == "io_htif":
            (lin_addr, phys_addr) = self.add_section_handler(
                name="io_htif",
                size=0x10,
                iscode=False,
                identity_map=True,
                start_addr=self.featmgr.io_htif_addr,
            )
            # Every call to add_section_handler already adds the sections to the pool
            # self.pool.add_section(section_name="io_htif")

        else:
            pass

    def add_section_handler(
        self,
        name,
        size,
        iscode,
        start_addr=None,
        identity_map=False,
        always_super=False,
        always_user=False,
        phys_name="",
    ):
        """
        Add a section with name, size and number of pages information
        Inputs:
          - name
          - size
          - iscode
        """
        # Generate address or reserve memory
        lin_addr = None
        phys_addr = None

        if start_addr is not None:
            # No need to generate addresses
            lin_addr = start_addr
            phys_addr = start_addr
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.LINEAR,
                start_address=start_addr,
                size=size,
            )
            self.addrgen.reserve_memory(
                address_type=RV.AddressType.PHYSICAL,
                start_address=start_addr,
                size=size,
            )
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
        addr_inst = Address(name=name, type=RV.AddressType.LINEAR, address=lin_addr)
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

        self.pool.add_page(page=p, map_names=maps_to_add)

        # Add address as a section so they can be used in the linker script
        log.debug(f"Adding section {name} with lin_addr: {lin_addr:016x}, phys_addr: {phys_addr:016x}")
        self.pool.add_section(section_name=name)

        return (lin_addr, phys_addr)

    def generate_addr(self):
        """
        Generate all the addresses here
        """
        # page_mappings = {k: v for k,v in sorted(self.pool.get_parsed_page_mapings().items(),key=lambda item : item[1].resolve_priority, reverse=True)}
        for lin_name, page_mapping in self.pool.get_parsed_page_mappings().items():
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
            self.pool.add_section(section_name=init_mem_name)

    def generate_sections(self):
        # We need to explicitely add 'text' section since we moved to using '.section .code' for the user tests, which will be in
        # different section than the .text
        # .text will contain mostly the operating system code
        self.pool.add_parsed_sections(val="text", index=0)
        for section in self.os_data_sections:
            self.pool.add_parsed_sections(val=section)
        for section in self.io_sections:
            self.pool.add_parsed_sections(val=section)
        for section in self.pool.get_parsed_sections():
            self.handle_sections(section)

        if self.featmgr.c_used:
            for section_name in self.c_used_sections:
                self.handle_sections(section_name)

    def get_input_test_lines(self, input_file):
        text = []
        code = []
        data = []

    def write_test(self, filename, output_file, single_assembly_file=False, pagetable_includes=""):
        lines = []
        with open(filename, "r") as input_file:
            lines = input_file.readlines()

        # Randomize starting of the code section to an offset from 0...128, increment of 4
        code_offset = 0
        # if self.featmgr.randomize_code_offset:
        #     code_offsets = list(range(0, 129, 4))
        #     code_offset = self.rng.random_entry_in(code_offsets)
        code_replace = f"""
        .section .code, "ax"
        # .org 0x{code_offset:x}
        """

        if self.featmgr.wysiwyg:
            loader_pos = [i for i, line in enumerate(lines) if ".section .text" in line][0]
        else:
            code_lines = [i for i, line in enumerate(lines) if ".section .code," in line]
            if len(code_lines) == 0:
                raise ValueError('No ".section .code" found in .s file')
            loader_pos = code_lines[0]
            lines[loader_pos] = code_replace

        main_idx = -1
        for idx, line in enumerate(lines):
            if ".section .code_super_0" in line:
                main_idx = idx
                break

        if main_idx == -1:
            main_idx = loader_pos

        os_code = None

        # Include or inline the include files
        if not single_assembly_file:
            if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and not self.featmgr.wysiwyg and not self.featmgr.linux_mode:
                lines.insert(main_idx, f'.include "{self.hypervisor_inc.name}"\n')
            if not self.featmgr.wysiwyg:
                lines.insert(main_idx, f'.include "{self.excp_inc.name}"\n')
                lines.insert(main_idx, f'.include "{self.syscalls_inc.name}"\n')
            lines.insert(main_idx, f'.include "{self.loader_inc.name}"\n')
            if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE or self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                lines.insert(main_idx, f'.include "{self.pagetables_inc.name}"\n')
            lines.insert(main_idx, f'.include "{self.equates_inc.name}"\n')
            lines.insert(main_idx, f'.include "{self.macros_inc.name}"\n')
        else:
            runtime_module_code = {module_name: module_code for module_name, module_code in self.runtime_module_generator()}
            if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and not self.featmgr.wysiwyg and not self.featmgr.linux_mode:
                lines.insert(main_idx, f"## hypervisor ##\n{runtime_module_code['hypervisor']}")
            if not self.featmgr.wysiwyg and not self.featmgr.linux_mode:
                lines.insert(main_idx, f"## trap_handler ##\n{runtime_module_code['trap_handler']}")
                lines.insert(main_idx, f"## syscalls ##\n{runtime_module_code['syscalls']}")
            lines.insert(main_idx, f"## loader ##\n{runtime_module_code['loader']}")
            lines.insert(main_idx, f"## macros ##\n{runtime_module_code['macros']}")
            os_code = runtime_module_code["os"]

        if not self.featmgr.wysiwyg:
            data_lines = [i for i, line in enumerate(lines) if ".section .data" in line]
            if len(data_lines) == 0:
                raise ValueError('No ".section .data" defined in .s file. Try including at the end')
            os_pos = data_lines[0]

        if not self.featmgr.wysiwyg:
            if not single_assembly_file:
                lines.insert(os_pos, f'.include "{self.scheduler_inc.name}"\n')
                lines.insert(os_pos, f'.include "{self.os_inc.name}"\n')
            else:
                if os_code is None:
                    raise ValueError("Got os_code==None, somehow variable os_code wassn't initialized to runtime.opsys code")
                lines.insert(os_pos, "## os ##\n" + os_code)
                lines.insert(os_pos, "## test_scheduler ##\n" + runtime_module_code["scheduler"])

        init_mem_sections = []
        for val in self.pool.get_parsed_init_mem_addrs():
            init_mem_sections.append(val)

        for i, line in enumerate(lines):
            if line.startswith(";#init_memory"):
                for lin_name in init_mem_sections:
                    if "@" + lin_name in line:
                        permissions = "aw"
                        if self.pool.parsed_page_mapping_exists(lin_name) and self.pool.get_parsed_page_mapping(lin_name).x:
                            permissions = "ax"
                        print_lin_name = lin_name
                        if lin_name.startswith("0x"):
                            print_lin_name = self.prefix_hex_lin_name(lin_name)
                        lines[i] = line + line.replace(line, f'.section .{print_lin_name}, "{permissions}"\n')
                        break

        if single_assembly_file:
            output_file.write("## equates ##\n")
            self.generate_equates(output_file)
        else:
            with open(self.equates_inc, "w") as equates_inc_file:
                self.generate_equates(equates_inc_file)

        # Add pagetables if includes inlined
        if single_assembly_file:
            if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE or self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                lines.append("## pagetables ##\n" + pagetable_includes)

        # Write .S file
        for line in formatted_line_generator(lines):
            output_file.write(line)

    def generate_equates(self, filehandle):
        output_file = filehandle
        # Write the configuration
        output_file.write("# Test configuration:\n")
        data = self.featmgr.get_summary()
        for config_name, config in data.items():
            output_file.write(f".equ {config_name:35}, {int(config)}\n")

        # Write random data
        output_file.write("\n")
        output_file.write("# Test random data:\n")
        data = self.pool.get_random_data()
        for data_name, data in data.items():
            output_file.write(f".equ {data_name:35}, 0x{data:016x}\n")

        # Write test addresses
        output_file.write("\n# Test addresses:\n")
        data = self.pool.get_random_addrs()
        for addr_name, addr in data.items():
            if addr_name == "code":
                addr.address = addr.address + self.code_offset
            output_file.write(f".equ {addr_name:35}, 0x{addr.address:016x}\n")

        # Generate any equate file additions from runtime
        output_file.write(self.runtime.generate_equates())

        # Write exception causes
        output_file.write("\n# Exception causes:\n")
        for cause_enum in RV.RiscvExcpCauses:
            output_file.write(f".equ {cause_enum.name:35}, {cause_enum.value}\n")

        output_file.write("\n# Expected Interrupt causes:\n")
        for interrupt_enum in RV.RiscvInterruptCause:
            output_file.write(f".equ EXPECT_{interrupt_enum.name}, {interrupt_enum.value}\n")

        output_file.write("\n# XLEN\n.equ XLEN, 64\n")
        # Also have a special ECALL cause based on the current privilege mode
        # Handle VS mode
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
            output_file.write("\n.equ ECALL            , ECALL_FROM_VS\n")
        else:
            output_file.write(f"\n.equ ECALL            , ECALL_FROM_{self.featmgr.priv_mode}\n")
        deleg_to_super = 1 if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.SUPER else 0
        deleg_to_machine = 1 if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.MACHINE else 0
        output_file.write(f"\n.equ OS_DELEG_EXCP_TO_SUPER, {deleg_to_super}")
        output_file.write(f"\n.equ OS_DELEG_EXCP_TO_MACHINE, {deleg_to_machine}")
        output_file.write("\n")
        output_file.write("\n.equ DONT_USE_STACK, 1")
        output_file.write("\n")

        # Also write needs pma flag based on the commandline
        pma_enabled = 1 if self.featmgr.needs_pma else 0
        output_file.write(f"\n.equ PMA_ENABLED, {pma_enabled}\n")

        # Add MISA equates
        output_file.write(f"\n.equ MISA_BITS, {self.misa_bits}\n")

        # Add feature-specific equates
        output_file.write("\n# Feature-specific equates:\n")
        for feature in self.featmgr.feature.list_features():
            enabled = 1 if self.featmgr.feature.is_enabled(feature) else 0
            supported = 1 if self.featmgr.feature.is_supported(feature) else 0
            output_file.write(f".equ FEATURE_{feature.upper():35}, {enabled} # supported={supported}\n")

    def generate_linker_script(self):
        # Create the linker script file <testname>.ld
        with open(self.linker_script, "w") as linker_file:
            linker_file.write('OUTPUT_ARCH("riscv")\nENTRY(_start)\n')
            linker_file.write("SECTIONS\n{\n")
            for section_name, address in self.pool.get_sections().items():
                log.debug(f"{section_name} -> 0x{address:016x}")
                print_section_name = section_name
                if section_name == "code":
                    address = address + self.code_offset
                if section_name.startswith("0x"):
                    print_section_name = self.prefix_hex_lin_name(section_name)
                # Clear bit-55 if it's set since there physical address space does not use it
                if address & (1 << 55):
                    address = address & ~(1 << 55)
                linker_file.write(
                    "\t. = {}\n\t {} : {} \n\n".format(
                        str(hex(address) + ";"),
                        "." + print_section_name,
                        "{ *(." + print_section_name + ") }",
                    )
                )
            linker_file.write("}")

    # can these both be removed? could instead just use the module_generator
    def generate_runtime(self, **kwargs):
        """
        Generate runtime code in various .inc files
        """
        self.runtime.generate(**kwargs)

    def runtime_module_generator(self):
        """
        Generate runtime code in various .inc files
        """
        for m in self.runtime.module_generator():
            yield m

    def prefix_hex_lin_name(self, n):
        return f"__auto_secname_{n}"
