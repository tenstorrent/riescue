# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import re
import logging
from typing import Optional, TYPE_CHECKING, Tuple, Union
from dataclasses import dataclass, field
from pathlib import Path

import riescue.lib.common as common
import riescue.lib.enums as RV
from riescue.dtest_framework.lib.pma import PmaInfo

if TYPE_CHECKING:
    from riescue.dtest_framework.pool import Pool

log = logging.getLogger(__name__)


class Parser:
    """
    Parser module parses the .S file for the custom RiESCUE syntaxes and stores
    information into thier relative class instances.

    Currently, we allow these syntaxes:
    - ;#test.
                {name, author, priv, env, secure_mode, arch, category, class, features,
                tags, projects, paging
                }
    - ;#random()
    - ;#random_addr()
    - ;#page_mapping()
    - ;#page_map()
    - ;#test.<headers>

    FIXME: If required parameters are missing from the header, the parser will continue. There should be a validate method and/or better type checking to error handle missing parameters.
    e.g.
    """

    def __init__(self, filename: Path, pool: "Pool"):
        self.filename = filename
        self.reserve_memory = dict()
        self.random_data = dict()
        self.random_addrs = dict()
        self.test_header = ParsedTestHeader()
        self.discrete_tests = dict()
        self.parsed_pte_id = 1
        self.parsed_trigger_id = 1
        self.parsed_csr_id = 1  # Monotonic id for ;#csr_rw ParsedCsrAccess labels/csr_id

        self.pool = pool

        # State for collecting ;#discrete_debug_test() body lines (only one per test file)
        self._collecting_discrete_debug_test = False
        self._discrete_debug_test_body: list[str] = []

    def parse(self):
        with open(self.filename, "r") as file:
            contents = file.readlines()

        for line in contents:
            # If we were collecting ;#discrete_debug_test body, check for terminator
            if self._collecting_discrete_debug_test:
                stripped = line.strip()
                if line.startswith(";#") or stripped.startswith(".section"):
                    self.pool.set_parsed_discrete_debug_test(self._discrete_debug_test_body)
                    self._collecting_discrete_debug_test = False
                    self._discrete_debug_test_body = []
                else:
                    self._discrete_debug_test_body.append(line.rstrip("\n"))
                    continue  # do not process this line as a directive

            if line.startswith(";#discrete_debug_test"):
                self._parse_discrete_debug_test_start(line)
                continue

            # Match the ;#random(), ;#random_addr(), ;#page_mapping() and ;#page_map() syntax
            # ;#random(name=lin1, data_type=32bits, and_mask=0xfffff000, or_mask=0x00000001)
            # ;#random_addr(name=lin1, type=linear32, size=0x1000, and_mask=0xfffff000)
            # ;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1)
            # ;#page_map(name=map1, mode=sv57)
            if line.startswith(";#reserve_memory("):
                self.parse_reserve_memory(line)

            if line.startswith(";#random_data("):
                self.parse_random_data(line)

            if line.startswith(";#random_addr("):
                self.parse_random_addr(line)

            if line.startswith(";#page_mapping("):
                self.parse_page_mappings(line)

            if line.startswith(";#page_map("):
                self.parse_page_maps(line)

            if line.startswith(";#test."):
                self.parse_test_header(line)

            if line.startswith(";#discrete_test") and not line.startswith(";#discrete_debug_test"):
                self.parse_discrete_test(line)

            if line.startswith(";#init_memory"):
                self.parse_init_mem(line)

            if line.startswith(";#vectored_interrupt"):
                self.parse_vectored_interrupt(line)

            if line.startswith(";#custom_handler"):
                self.parse_custom_handler(line)

            if line.startswith(";#vector_delegation"):
                self.parse_vector_delegation(line)

            if line.startswith(";#enable_ext_intr_id"):
                self.parse_enable_ext_intr_id(line)

            if line.startswith(";#set_aplic_attr"):
                self.parse_set_aplic_attr(line)

            if line.startswith(";#pma_hint("):
                self.parse_pma_hint(line)

            if line.strip().startswith(";#trigger_config("):
                self.parse_trigger_config(line.strip())
            if line.strip().startswith(";#trigger_disable("):
                self.parse_trigger_disable(line.strip())
            if line.strip().startswith(";#trigger_enable("):
                self.parse_trigger_enable(line.strip())

            # Only consider strip after all non-riscv directives are removed
            line = line.strip()

            if line.startswith(".section"):
                self.parse_sections(line)
            if line.startswith(";#csr_rw"):
                self.parse_csr_rw(line)
            if line.startswith(";#read_pte"):
                self.parse_read_pte(line)
            if line.startswith(";#write_pte"):
                self.parse_write_pte(line)

        # End of file: flush any remaining ;#discrete_debug_test body
        if self._collecting_discrete_debug_test:
            self.pool.set_parsed_discrete_debug_test(self._discrete_debug_test_body)

    def _parse_discrete_debug_test_start(self, line: str) -> None:
        """Parse ;#discrete_debug_test or ;#discrete_debug_test() and start collecting body lines. Only one per test file."""
        if self._collecting_discrete_debug_test:
            raise ValueError("multiple ;#discrete_debug_test() not allowed; only one per test file")
        if self.pool.get_parsed_discrete_debug_test() is not None:
            raise ValueError("multiple ;#discrete_debug_test() not allowed; only one per test file")
        # Accept ;#discrete_debug_test or ;#discrete_debug_test() (no parameters)
        stripped = line.strip()
        if stripped != ";#discrete_debug_test" and not re.match(r"^;#discrete_debug_test\s*\(\s*\)\s*$", stripped):
            raise ValueError(";#discrete_debug_test() does not take parameters; use ;#discrete_debug_test or ;#discrete_debug_test()")
        self._discrete_debug_test_body = []
        self._collecting_discrete_debug_test = True

    def parse_reserve_memory(self, line):
        pattern = r"^;#(reserve_memory)\((.+)\)"
        match = re.match(pattern, line)
        if match:
            res_mem = ParsedReserveMemory()
            args = match.group(2).replace(" ", "")
            args = args.split(",")
            for arg in args:
                var = arg.split("=")[0]
                val = arg.split("=")[1]
                if re.match(r"(size)", var):
                    val = int(val, 0)
                res_mem.__setattr__(var, val)
            res_mem.name = f"__res_mem_{(res_mem.addr_type)}{res_mem.start_addr}"
            self.reserve_memory[res_mem.name] = [res_mem]
            self.pool.add_parsed_res_mem(res_mem)

    def parse_random_data(self, line):
        pattern = r"^;#(random_data)\((.+)\)"
        match = re.match(pattern, line)
        if match:
            rnd_inst = ParsedRandomData()
            args = match.group(2).replace(" ", "")
            args = args.split(",")
            for arg in args:
                var = arg.split("=")[0]
                val = arg.split("=")[1]
                if not (re.match(r"(name|type)", var)):
                    val = int(val, 0)
                rnd_inst.__setattr__(var, val)
            self.random_data[rnd_inst.name] = [rnd_inst]
            self.pool.add_parsed_data(rnd_inst)

    def parse_random_addr(self, line):
        pattern = r"^;#(random_addr)\((.+)\)"
        match = re.match(pattern, line)
        if match:
            rnd_inst = ParsedRandomAddress()
            args = match.group(2).replace(" ", "")
            args = args.split(",")
            for arg in args:
                var = arg.split("=")[0]
                val = arg.split("=")[1]
                if not (re.match(r"name|type|pma_memory_type|pma_amo_type|pma_cacheability|pma_combining|pma_routing_to", var)):
                    val = int(val, 0)
                if re.match(r"in_pma", var):
                    rnd_inst.pma_info = PmaInfo()
                    rnd_inst.pma_info.__setattr__(var, val)
                if re.match(r"pma_", var):
                    if rnd_inst.pma_info is None:
                        rnd_inst.pma_info = PmaInfo()
                    rnd_inst.pma_info.__setattr__(var, val)
                if re.match(r"(type)", var):
                    match = re.findall(r"([a-z]+)(\d*)", str(val))[0]
                    if len(match) == 2:
                        if match[1] != "":
                            addr_bits = int(match[1])
                            val = match[0]
                            rnd_inst.__setattr__("addr_bits", addr_bits)
                            # print(f'addr_bits: {addr_bits}, val: {val}')
                    elif len(match) == 1:
                        # Only linear|physical was specified
                        val = match[0]
                    # val = int(val, 0)
                rnd_inst.__setattr__(var, val)
            self.random_addrs[rnd_inst.name] = [rnd_inst]
            log.debug(f"Adding random_addr: {rnd_inst.name} to pool")
            self.pool.add_parsed_addr(rnd_inst)

    def parse_page_mappings(self, line):
        pattern = r"^;#(page_mapping)\((.+)\)"
        pagemap_str = ""
        pagesize_str = ""
        gstage_vs_leaf_pagesize_str = ""
        gstage_vs_nonleaf_pagesize_str = ""
        # Also add gstage_vs attributes for v, r, w, x, u, g, a, d, pbmt, n, rsw, reserved
        v_nonleaf_str = ""
        v_leaf_gleaf_str = ""
        v_leaf_gnonleaf_str = ""
        v_nonleaf_gleaf_str = ""
        v_nonleaf_gnonleaf_str = ""
        a_nonleaf_str = ""
        a_leaf_gleaf_str = ""
        a_leaf_gnonleaf_str = ""
        a_nonleaf_gleaf_str = ""
        a_nonleaf_gnonleaf_str = ""
        d_nonleaf_str = ""
        d_leaf_gleaf_str = ""
        d_leaf_gnonleaf_str = ""
        d_nonleaf_gleaf_str = ""
        d_nonleaf_gnonleaf_str = ""
        g_nonleaf_str = ""
        g_nonleaf_str = ""
        g_leaf_gleaf_str = ""
        g_leaf_gnonleaf_str = ""
        g_nonleaf_gleaf_str = ""
        g_nonleaf_gnonleaf_str = ""
        u_nonleaf_str = ""
        u_leaf_gleaf_str = ""
        u_leaf_gnonleaf_str = ""
        u_nonleaf_gleaf_str = ""
        u_nonleaf_gnonleaf_str = ""
        r_nonleaf_str = ""
        r_leaf_gleaf_str = ""
        r_leaf_gnonleaf_str = ""
        r_nonleaf_gleaf_str = ""
        r_nonleaf_gnonleaf_str = ""
        w_nonleaf_str = ""
        w_leaf_gleaf_str = ""
        w_leaf_gnonleaf_str = ""
        w_nonleaf_gleaf_str = ""
        w_nonleaf_gnonleaf_str = ""
        x_nonleaf_str = ""
        x_leaf_gleaf_str = ""
        x_leaf_gnonleaf_str = ""
        x_nonleaf_gleaf_str = ""
        x_nonleaf_gnonleaf_str = ""
        pbmt_nonleaf_str = ""
        pbmt_nonleaf_gleaf_str = ""
        pbmt_nonleaf_gnonleaf_str = ""
        pbmt_leaf_gleaf_str = ""
        pbmt_leaf_gnonleaf_str = ""
        n_nonleaf_str = ""
        n_nonleaf_gleaf_str = ""
        n_nonleaf_gnonleaf_str = ""
        n_leaf_gleaf_str = ""
        n_leaf_gnonleaf_str = ""
        secure_str = ""

        if "pagesize=" in line:
            pagesize_str = re.findall(r" pagesize=\[.*?\]", line)[0]
        if "gstage_vs_leaf_pagesize=" in line:
            gstage_vs_leaf_pagesize_str = re.findall(r"gstage_vs_leaf_pagesize=\[.*?\]", line)[0]
        if "gstage_vs_nonleaf_pagesize=" in line:
            gstage_vs_nonleaf_pagesize_str = re.findall(r"gstage_vs_nonleaf_pagesize=\[.*?\]", line)[0]
        if "v_nonleaf=" in line:
            v_nonleaf_str = re.findall(r"v_nonleaf=(\d+)", line)[0]
        if "v_leaf_gleaf=" in line:
            v_leaf_gleaf_str = re.findall(r"v_leaf_gleaf=(\d+)", line)[0]
        if "v_leaf_gnonleaf=" in line:
            v_leaf_gnonleaf_str = re.findall(r"v_leaf_gnonleaf=(\d+)", line)[0]
        if "v_nonleaf_gleaf=" in line:
            v_nonleaf_gleaf_str = re.findall(r"v_nonleaf_gleaf=(\d+)", line)[0]
        if "v_nonleaf_gnonleaf=" in line:
            v_nonleaf_gnonleaf_str = re.findall(r"v_nonleaf_gnonleaf=(\d+)", line)[0]
        if "a_nonleaf=" in line:
            a_nonleaf_str = re.findall(r"a_nonleaf=(\d+)", line)[0]
        if "a_leaf_gleaf=" in line:
            a_leaf_gleaf_str = re.findall(r"a_leaf_gleaf=(\d+)", line)[0]
        if "a_leaf_gnonleaf=" in line:
            a_leaf_gnonleaf_str = re.findall(r"a_leaf_gnonleaf=(\d+)", line)[0]
        if "a_nonleaf_gleaf=" in line:
            a_nonleaf_gleaf_str = re.findall(r"a_nonleaf_gleaf=(\d+)", line)[0]
        if "a_nonleaf_gnonleaf=" in line:
            a_nonleaf_gnonleaf_str = re.findall(r"a_nonleaf_gnonleaf=(\d+)", line)[0]
        if "d_nonleaf=" in line:
            d_nonleaf_str = re.findall(r"d_nonleaf=(\d+)", line)[0]
        if "d_leaf_gleaf=" in line:
            d_leaf_gleaf_str = re.findall(r"d_leaf_gleaf=(\d+)", line)[0]
        if "d_leaf_gnonleaf=" in line:
            d_leaf_gnonleaf_str = re.findall(r"d_leaf_gnonleaf=(\d+)", line)[0]
        if "d_nonleaf_gleaf=" in line:
            d_nonleaf_gleaf_str = re.findall(r"d_nonleaf_gleaf=(\d+)", line)[0]
        if "d_nonleaf_gnonleaf=" in line:
            d_nonleaf_gnonleaf_str = re.findall(r"d_nonleaf_gnonleaf=(\d+)", line)[0]
        if "g_nonleaf=" in line:
            g_nonleaf_str = re.findall(r"g_nonleaf=(\d+)", line)[0]
        if "g_leaf_gleaf=" in line:
            g_leaf_gleaf_str = re.findall(r"g_leaf_gleaf=(\d+)", line)[0]
        if "g_leaf_gnonleaf=" in line:
            g_leaf_gnonleaf_str = re.findall(r"g_leaf_gnonleaf=(\d+)", line)[0]
        if "g_nonleaf_gleaf=" in line:
            g_nonleaf_gleaf_str = re.findall(r"g_nonleaf_gleaf=(\d+)", line)[0]
        if "g_nonleaf_gnonleaf=" in line:
            g_nonleaf_gnonleaf_str = re.findall(r"g_nonleaf_gnonleaf=(\d+)", line)[0]
        if "u_nonleaf=" in line:
            u_nonleaf_str = re.findall(r"u_nonleaf=(\d+)", line)[0]
        if "u_leaf_gleaf=" in line:
            u_leaf_gleaf_str = re.findall(r"u_leaf_gleaf=(\d+)", line)[0]
        if "u_leaf_gnonleaf=" in line:
            u_leaf_gnonleaf_str = re.findall(r"u_leaf_gnonleaf=(\d+)", line)[0]
        if "u_nonleaf_gleaf=" in line:
            u_nonleaf_gleaf_str = re.findall(r"u_nonleaf_gleaf=(\d+)", line)[0]
        if "u_nonleaf_gnonleaf=" in line:
            u_nonleaf_gnonleaf_str = re.findall(r"u_nonleaf_gnonleaf=(\d+)", line)[0]
        if "w_nonleaf=" in line:
            w_nonleaf_str = re.findall(r"w_nonleaf=(\d+)", line)[0]
        if "w_leaf_gleaf=" in line:
            w_leaf_gleaf_str = re.findall(r"w_leaf_gleaf=(\d+)", line)[0]
        if "w_leaf_gnonleaf=" in line:
            w_leaf_gnonleaf_str = re.findall(r"w_leaf_gnonleaf=(\d+)", line)[0]
        if "w_nonleaf_gleaf=" in line:
            w_nonleaf_gleaf_str = re.findall(r"w_nonleaf_gleaf=(\d+)", line)[0]
        if "w_nonleaf_gnonleaf=" in line:
            w_nonleaf_gnonleaf_str = re.findall(r"w_nonleaf_gnonleaf=(\d+)", line)[0]
        if "r_nonleaf=" in line:
            r_nonleaf_str = re.findall(r"r_nonleaf=(\d+)", line)[0]
        if "r_leaf_gleaf=" in line:
            r_leaf_gleaf_str = re.findall(r"r_leaf_gleaf=(\d+)", line)[0]
        if "r_leaf_gnonleaf=" in line:
            r_leaf_gnonleaf_str = re.findall(r"r_leaf_gnonleaf=(\d+)", line)[0]
        if "r_nonleaf_gleaf=" in line:
            r_nonleaf_gleaf_str = re.findall(r"r_nonleaf_gleaf=(\d+)", line)[0]
        if "r_nonleaf_gnonleaf=" in line:
            r_nonleaf_gnonleaf_str = re.findall(r"r_nonleaf_gnonleaf=(\d+)", line)[0]
        if "x_nonleaf=" in line:
            x_nonleaf_str = re.findall(r"x_nonleaf=(\d+)", line)[0]
        if "x_leaf_gleaf=" in line:
            x_leaf_gleaf_str = re.findall(r"x_leaf_gleaf=(\d+)", line)[0]
        if "x_leaf_gnonleaf=" in line:
            x_leaf_gnonleaf_str = re.findall(r"x_leaf_gnonleaf=(\d+)", line)[0]
        if "x_nonleaf_gleaf=" in line:
            x_nonleaf_gleaf_str = re.findall(r"x_nonleaf_gleaf=(\d+)", line)[0]
        if "x_nonleaf_gnonleaf=" in line:
            x_nonleaf_gnonleaf_str = re.findall(r"x_nonleaf_gnonleaf=(\d+)", line)[0]
        if "pbmt_nonleaf=" in line:
            pbmt_nonleaf_str = re.findall(r"pbmt_nonleaf=(\d+)", line)[0]
        if "pbmt_nonleaf_gleaf=" in line:
            pbmt_nonleaf_gleaf_str = re.findall(r"pbmt_nonleaf_gleaf=(\d+)", line)[0]
        if "pbmt_nonleaf_gnonleaf=" in line:
            pbmt_nonleaf_gnonleaf_str = re.findall(r"pbmt_nonleaf_gnonleaf=(\d+)", line)[0]
        if "pbmt_leaf_gleaf=" in line:
            pbmt_leaf_gleaf_str = re.findall(r"pbmt_leaf_gleaf=(\d+)", line)[0]
        if "pbmt_leaf_gnonleaf=" in line:
            pbmt_leaf_gnonleaf_str = re.findall(r"pbmt_leaf_gnonleaf=(\d+)", line)[0]
        if "n_nonleaf=" in line:
            n_nonleaf_str = re.findall(r"n_nonleaf=(\d+)", line)[0]
        if "n_nonleaf_gleaf=" in line:
            n_nonleaf_gleaf_str = re.findall(r"n_nonleaf_gleaf=(\d+)", line)[0]
        if "n_nonleaf_gnonleaf=" in line:
            n_nonleaf_gnonleaf_str = re.findall(r"n_nonleaf_gnonleaf=(\d+)", line)[0]
        if "n_leaf_gleaf=" in line:
            n_leaf_gleaf_str = re.findall(r"n_leaf_gleaf=(\d+)", line)[0]
        if "n_leaf_gnonleaf=" in line:
            n_leaf_gnonleaf_str = re.findall(r"n_leaf_gnonleaf=(\d+)", line)[0]
        if "secure=" in line:
            secure_str = re.findall(r"secure=(\d+)", line)[0]

        if "page_maps=" in line:
            pagemap_str = re.findall(r"page_maps=\[.*?\]", line)[0]
        line = line.replace(pagesize_str, "")
        line = line.replace(gstage_vs_leaf_pagesize_str, "")
        line = line.replace(gstage_vs_nonleaf_pagesize_str, "")
        line = line.replace(pagemap_str, "")
        match = re.match(pattern, line)
        if match:
            ppm_inst = ParsedPageMapping()
            ppm_inst.__setattr__("source_line", line)
            generate_time_process_args = []
            args = match.group(2).replace(" ", "")
            args = args.split(",")
            args = list(filter(None, args))
            for arg in args:
                var = arg.split("=")[0]
                val = arg.split("=")[1]

                if var == "lin_name" or var == "phys_name" or var == "lin_addr" or var == "phys_addr":
                    generate_time_process_args.append([var, val])
                elif not (re.match(r"pagesize|g_(.+)|v_(.+)|a_(.+)|d_(.+)|u_(.+)|w_(.+)|r_(.+)|x_(.+)|page_maps|secure", var)):
                    val = int(val, 0)

                ppm_inst.__setattr__(var, val)

            # Refactor following code
            if v_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", v_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.v_nonleaf = bool(int(extracted[0]))

            if a_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", a_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.a_nonleaf = bool(int(extracted[0]))

            if d_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", d_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.d_nonleaf = bool(int(extracted[0]))

            if u_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", u_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.u_nonleaf = bool(int(extracted[0]))

            if w_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", w_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.w_nonleaf = bool(int(extracted[0]))

            if r_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", r_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.r_nonleaf = bool(int(extracted[0]))

            if x_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", x_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.x_nonleaf = bool(int(extracted[0]))

            if v_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", v_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.v_leaf_gleaf = bool(int(extracted[0]))

            if v_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", v_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.v_leaf_gnonleaf = bool(int(extracted[0]))

            if v_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", v_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.v_nonleaf_gleaf = bool(int(extracted[0]))

            if v_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", v_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.v_nonleaf_gnonleaf = bool(int(extracted[0]))

            if a_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", a_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.a_leaf_gleaf = bool(int(extracted[0]))

            if a_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", a_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.a_leaf_gnonleaf = bool(int(extracted[0]))

            if a_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", a_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.a_nonleaf_gleaf = bool(int(extracted[0]))

            if a_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", a_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.a_nonleaf_gnonleaf = bool(int(extracted[0]))

            if d_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", d_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.d_leaf_gleaf = bool(int(extracted[0]))

            if d_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", d_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.d_leaf_gnonleaf = bool(int(extracted[0]))

            if d_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", d_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.d_nonleaf_gleaf = bool(int(extracted[0]))

            if d_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", d_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.d_nonleaf_gnonleaf = bool(int(extracted[0]))

            if g_nonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", g_nonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.g_nonleaf = bool(int(extracted[0]))

            if g_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", g_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.g_leaf_gleaf = bool(int(extracted[0]))

            if g_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", g_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.g_leaf_gnonleaf = bool(int(extracted[0]))

            if g_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", g_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.g_nonleaf_gleaf = bool(int(extracted[0]))

            if g_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", g_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.g_nonleaf_gnonleaf = bool(int(extracted[0]))

            if u_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", u_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.u_leaf_gleaf = bool(int(extracted[0]))

            if u_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", u_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.u_leaf_gnonleaf = bool(int(extracted[0]))

            if u_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", u_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.u_nonleaf_gleaf = bool(int(extracted[0]))

            if u_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", u_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.u_nonleaf_gnonleaf = bool(int(extracted[0]))

            if w_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", w_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.w_leaf_gleaf = bool(int(extracted[0]))

            if w_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", w_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.w_leaf_gnonleaf = bool(int(extracted[0]))

            if w_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", w_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.w_nonleaf_gleaf = bool(int(extracted[0]))

            if w_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", w_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.w_nonleaf_gnonleaf = bool(int(extracted[0]))

            if r_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", r_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.r_leaf_gleaf = bool(int(extracted[0]))

            if r_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", r_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.r_leaf_gnonleaf = bool(int(extracted[0]))

            if r_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", r_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.r_nonleaf_gleaf = bool(int(extracted[0]))

            if r_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", r_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.r_nonleaf_gnonleaf = bool(int(extracted[0]))

            if x_leaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", x_leaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.x_leaf_gleaf = bool(int(extracted[0]))

            if x_leaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", x_leaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.x_leaf_gnonleaf = bool(int(extracted[0]))

            if x_nonleaf_gleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", x_nonleaf_gleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.x_nonleaf_gleaf = bool(int(extracted[0]))

            if x_nonleaf_gnonleaf_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", x_nonleaf_gnonleaf_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.x_nonleaf_gnonleaf = bool(int(extracted[0]))

            if pbmt_nonleaf_str != "":
                extracted = re.findall(r"(\d+)", pbmt_nonleaf_str)[0]
                ppm_inst.pbmt_nonleaf = int(extracted)

            if pbmt_nonleaf_gleaf_str != "":
                extracted = re.findall(r"(\d+)", pbmt_nonleaf_gleaf_str)[0]
                ppm_inst.pbmt_nonleaf_gleaf = int(extracted)

            if pbmt_nonleaf_gnonleaf_str != "":
                extracted = re.findall(r"(\d+)", pbmt_nonleaf_gnonleaf_str)[0]
                ppm_inst.pbmt_nonleaf_gnonleaf = int(extracted)

            if pbmt_leaf_gleaf_str != "":
                extracted = re.findall(r"(\d+)", pbmt_leaf_gleaf_str)[0]
                ppm_inst.pbmt_leaf_gleaf = int(extracted)

            if pbmt_leaf_gnonleaf_str != "":
                extracted = re.findall(r"(\d+)", pbmt_leaf_gnonleaf_str)[0]
                ppm_inst.pbmt_leaf_gnonleaf = int(extracted)

            if n_nonleaf_str != "":
                extracted = re.findall(r"(\d+)", n_nonleaf_str)[0]
                ppm_inst.n_nonleaf = int(extracted)

            if n_nonleaf_gleaf_str != "":
                extracted = re.findall(r"(\d+)", n_nonleaf_gleaf_str)[0]
                ppm_inst.n_nonleaf_gleaf = int(extracted)

            if n_nonleaf_gnonleaf_str != "":
                extracted = re.findall(r"(\d+)", n_nonleaf_gnonleaf_str)[0]
                ppm_inst.n_nonleaf_gnonleaf = int(extracted)

            if n_leaf_gleaf_str != "":
                extracted = re.findall(r"(\d+)", n_leaf_gleaf_str)[0]
                ppm_inst.n_leaf_gleaf = int(extracted)

            if n_leaf_gnonleaf_str != "":
                extracted = re.findall(r"(\d+)", n_leaf_gnonleaf_str)[0]
                ppm_inst.n_leaf_gnonleaf = int(extracted)

            if pagesize_str != "":
                # Extract actual sizes from "pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])"
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"\[(.*?)\]", pagesize_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.pagesizes = extracted

            if gstage_vs_leaf_pagesize_str != "":
                # Extract actual sizes from "pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])"
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"\[(.*?)\]", gstage_vs_leaf_pagesize_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.gstage_vs_leaf_pagesizes = extracted

            if gstage_vs_nonleaf_pagesize_str != "":
                # Extract actual sizes from "pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])"
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"\[(.*?)\]", gstage_vs_nonleaf_pagesize_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.gstage_vs_nonleaf_pagesizes = extracted

            if secure_str != "":
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"(\d+)", secure_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.secure = bool(int(extracted[0]))

                # set secure flag for parsed addresses that's included in page mapping
                if ppm_inst.secure:
                    parsed_addrs = self.pool.get_parsed_addrs()
                    for lin_addr, phys_addr in generate_time_process_args:
                        if lin_addr in parsed_addrs:
                            parsed_addr = parsed_addrs[lin_addr]
                            parsed_addr.secure = True
                            self.pool.add_parsed_addr(parsed_addr, force_overwrite=True)
                        if phys_addr in parsed_addrs:
                            parsed_addr = parsed_addrs[phys_addr]
                            parsed_addr.secure = True
                            self.pool.add_parsed_addr(parsed_addr, force_overwrite=True)

            if pagemap_str != "":
                # Extract actual sizes from "pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])"
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"\[(.*?)\]", pagemap_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.page_maps = extracted

            ppm_inst.__setattr__("pagemap_str", pagemap_str)
            ppm_inst.__setattr__("gen_time_proc", generate_time_process_args)
            self.pool.add_raw_parsed_page_mapping(ppm_inst)

    def parse_page_maps(self, line):
        pattern = r"^;#(page_map)\((.+)\)"
        match = re.match(pattern, line)
        if match:
            pm_inst = ParsedPageMap()
            args = match.group(2).replace(" ", "")
            args = args.split(",")
            for arg in args:
                var = arg.split("=")[0]
                val = arg.split("=")[1]
                if re.match(r"name", var):
                    pm_inst.name = val
                if re.match(r"mode", var):
                    pm_inst.mode = val
            self.pool.add_parsed_page_map(pm_inst)

    def parse_test_header(self, line):
        pattern = r";#test.(\w*)(.*)"
        match = re.match(pattern, line)
        if match is None:
            raise ValueError(f"Invalid test header line: {line}")
        var = match.group(1).strip()
        val = match.group(2)
        self.test_header.__setattr__(var, val)
        self.pool.add_test_header(self.test_header)

    def parse_discrete_test(self, line):
        pattern = r"^;#(discrete_test)\((.+)\)"
        match = re.match(pattern, line)
        if match:
            arg = match.group(2).replace(" ", "")
            testname = arg.split("=")[1]
            self.discrete_tests[testname] = testname
            self.pool.add_parsed_discrete_test(testname)

    @staticmethod
    def separate_lin_name_map(line: str) -> list[Union[str, list[str]]]:
        lin_name_maps: str = re.findall(r"@(.+)", line)[0]
        lin_name_maps_sep = lin_name_maps.split(":")
        ret: list[Union[str, list[str]]] = []
        if len(lin_name_maps_sep) == 1:
            ret.append(lin_name_maps.strip())
            ret.append([])
        else:
            ret.append(lin_name_maps_sep[0].strip())
            maps = lin_name_maps_sep[1].split(",")
            for i in range(0, len(maps)):
                maps[i] = maps[i].strip()
            ret.append(maps)
        return ret

    def parse_init_mem(self, line):
        lin_name = re.findall(r"@(.+)", line)[0]
        lin_name_maps = self.separate_lin_name_map(line)
        # FIXME: this should be a stronger type instead of a tuple of str + list
        if len(lin_name_maps[1]) == 0:
            lin_name = lin_name_maps[0]
            if not isinstance(lin_name, str):
                raise Exception(f"separate_lin_name_map should have returned a string as the first element when len(lin_name_maps[1]) == 0, but got {type(lin_name)}")
            self.pool.add_parsed_init_mem_addr(lin_name)
        else:
            if not isinstance(lin_name_maps[0], str):
                raise Exception(f"separate_lin_name_map should have returned a string as the first element when len(lin_name_maps[1]) != 0, but got {type(lin_name_maps[0])}")
            lin_name = lin_name_maps[0].strip()
            maps = lin_name_maps[1]
            for m in maps:
                self.pool.add_parsed_init_mem_addr(f"{lin_name}_{m.strip()}")

    def parse_sections(self, line):
        section_name = re.findall(r".section .(\w+)", line)[0]
        self.pool.add_parsed_sections(section_name)

    def parse_vectored_interrupt(self, line):
        "Format is #;vectored_interrupt(INDEX, LABEL) where INDEX is an integer or a named interrupt bit (e.g. SSI)"
        pattern = r"^;#vectored_interrupt\((?P<index>\w*),\s*(?P<label>\w*)\)"
        match = re.match(pattern, line)
        if match:
            index = match.group("index")
            label = match.group("label")
            try:
                index = int(index, 0)
            except ValueError:
                pass  # Cast fails on non-integer values, will stay a string

            if isinstance(index, int):
                vectored_interrupt = ParsedVectoredInterrupt.from_interrupt_index(label=label, index=index)
            else:
                vectored_interrupt = ParsedVectoredInterrupt.from_interrupt_name(label=label, name=index)
            log.warning(f"parsed {vectored_interrupt}")
            self.pool.add_parsed_vectored_interrupt(vectored_interrupt)

    def parse_custom_handler(self, line):
        """Format: ;#custom_handler(NUM, LABEL) for per-discrete_test custom interrupt handler."""
        pattern = r"^;#custom_handler\((?P<num>\d+),\s*(?P<label>\w+)\)"
        match = re.match(pattern, line.strip())
        if match:
            vector_num = int(match.group("num"), 0)
            label = match.group("label")
            parsed = ParsedCustomHandler(vector_num=vector_num, label=label)
            self.pool.add_parsed_custom_handler(parsed)

    def parse_vector_delegation(self, line):
        """Format: ;#vector_delegation(NUM, MODE) where MODE is 'supervisor' or 'machine'."""
        pattern = r"^;#vector_delegation\((?P<num>\d+),\s*(?P<mode>\w+)\)"
        match = re.match(pattern, line.strip())
        if match:
            vector_num = int(match.group("num"), 0)
            mode = match.group("mode").lower()
            if mode not in ("supervisor", "machine", "super", "m"):
                # Error out instead of warning
                raise ValueError(f";#vector_delegation: unknown mode '{mode}', use supervisor or machine")
            if mode == "super":
                mode = "supervisor"
            elif mode == "m":
                mode = "machine"
            parsed = ParsedVectorDelegation(vector_num=vector_num, delegate_to_supervisor=(mode == "supervisor"))
            self.pool.add_parsed_vector_delegation(parsed)

    def set_aplic_attr_max_aplic_irq(self, n_intr):
        self.pool.max_aplic_irq = n_intr

    def parse_set_aplic_attr(self, line):
        set_aplic_attr_args = {
            "max_aplic_irq": self.set_aplic_attr_max_aplic_irq,
        }
        args = re.search(r"^;#set_aplic_attr\((.*?)\)", line)
        pattern = r"(\w+)=([^,]+)"
        if args:
            var_val_pairs = re.findall(pattern, args.group(1))
            for var_val in var_val_pairs:
                var = var_val[0].strip()
                try:
                    func = set_aplic_attr_args[var]
                except Exception:
                    log.warning(f"illegal argument {var} in set_aplic_attr directive - ignored")
                    continue
                val = int(var_val[1].strip())
                func(val)

    def enable_ext_intr_arg_intr(self, val):
        self.pool.ext_aplic_interrupts[val] = dict()
        self.pool.ext_aplic_interrupts[val]["isr"] = None
        self.pool.ext_aplic_interrupts[val]["eiid"] = None
        self.pool.ext_aplic_interrupts[val]["hart"] = None
        self.pool.ext_aplic_interrupts[val]["source_mode"] = None
        self.pool.ext_aplic_interrupts[val]["state"] = None
        self.pool.ext_aplic_interrupts[val]["mode"] = None

    def enable_ext_intr_arg_isr(self, intr_num, val):
        if self.pool.ext_aplic_interrupts[intr_num]["isr"] is not None:
            log.warning("duplicate isr in enable_ext_intr_id directive - ignored")
        else:
            self.pool.ext_aplic_interrupts[intr_num]["isr"] = val

    def enable_ext_intr_arg_eiid(self, intr_num, val):
        if self.pool.ext_aplic_interrupts[intr_num]["eiid"] is not None:
            log.warning("duplicate eiid in enable_ext_intr_id directive - ignored")
        else:
            self.pool.ext_aplic_interrupts[intr_num]["eiid"] = int(val)

    def enable_ext_intr_arg_hart(self, intr_num, val):
        if self.pool.ext_aplic_interrupts[intr_num]["hart"] is not None:
            log.warning("duplicate hart in enable_ext_intr_id directive - ignored")
        else:
            self.pool.ext_aplic_interrupts[intr_num]["hart"] = int(val)

    def enable_ext_intr_arg_source_mode(self, intr_num, val):
        if self.pool.ext_aplic_interrupts[intr_num]["source_mode"] is not None:
            log.warning("duplicate source_mode in enable_ext_intr_id directive - ignored")
        else:
            if val == "inactive":
                self.pool.ext_aplic_interrupts[intr_num]["source_mode"] = 0
            elif val == "detached":
                self.pool.ext_aplic_interrupts[intr_num]["source_mode"] = 1
            elif val == "edge1":
                self.pool.ext_aplic_interrupts[intr_num]["source_mode"] = 4
            elif val == "edge0":
                self.pool.ext_aplic_interrupts[intr_num]["source_mode"] = 5
            elif val == "level1":
                self.pool.ext_aplic_interrupts[intr_num]["source_mode"] = 6
            elif val == "level0":
                self.pool.ext_aplic_interrupts[intr_num]["source_mode"] = 7
            else:
                log.warning("unrecognized source_mode in enable_ext_intr_id directive - ignored")

    def enable_ext_intr_arg_mode(self, intr_num, val):
        if self.pool.ext_aplic_interrupts[intr_num]["mode"] is not None:
            log.warning("duplicate mode in enable_ext_intr_id directive - ignored")
        else:
            if val != "m" and val != "s":
                log.warning("unrecognized mode - ignored")
            else:
                self.pool.ext_aplic_interrupts[intr_num]["mode"] = val

    def enable_ext_intr_arg_state(self, intr_num, val):
        if self.pool.ext_aplic_interrupts[intr_num]["state"] is not None:
            log.warning("duplicate state in enable_ext_intr_id directive - ignored")
        else:
            if val == "enabled":
                self.pool.ext_aplic_interrupts[intr_num]["state"] = 1
            elif val == "disabled":
                self.pool.ext_aplic_interrupts[intr_num]["state"] = 0
            else:
                log.warning("unrecognized state in enable_ext_intr_id directive - ignored")

    def parse_enable_ext_intr_id(self, line):
        enable_ext_intr_args = {
            "intr": self.enable_ext_intr_arg_intr,
            "isr": self.enable_ext_intr_arg_isr,
            "source_mode": self.enable_ext_intr_arg_source_mode,
            "eiid": self.enable_ext_intr_arg_eiid,
            "hart": self.enable_ext_intr_arg_hart,
            "state": self.enable_ext_intr_arg_state,
        }
        self.pool.init_aplic_interrupts = True
        args = re.search(r"^;#enable_ext_intr_id\((.*?)\)", line)
        pattern = r"(\w+)=([^,]+)"
        if not args:
            return

        var_val_pairs = re.findall(pattern, args.group(1))
        intr_num_found = False
        intr_num = 1
        for var_val in var_val_pairs:
            var = var_val[0].strip()
            if var == "intr":
                intr_num_found = True
                intr_num = int(var_val[1].strip())
                self.enable_ext_intr_arg_intr(intr_num)

        if not intr_num_found:
            log.error("No interrupt number in the enable_ext_intr_id directive")
            return

        for var_val in var_val_pairs:
            var = var_val[0].strip()
            if var == "intr":
                continue
            val = var_val[1].strip()
            try:
                func = enable_ext_intr_args[var]
            except Exception:
                log.warning(f"illegal argument {var} in enable_ext_intr_id directive - ignored")
                continue
            func(intr_num, val)

    def parse_csr_rw(self, line: str) -> None:
        """
        Parse ;#csr_rw directive. Supports:
        - Old: ;#csr_rw(csr_name, action, direct_rw [, force_machine_rw])
        - New: ;#csr_rw(csr, action [, field=, value=, bit=, force_machine=])
        """
        pattern = r"^;#csr_rw\((.+)\)"
        match = re.match(pattern, line.strip())
        if not match:
            return

        inner = match.group(1).strip()
        parts = [p.strip() for p in re.split(r",\s*(?![^\[]*\])", inner)]

        if len(parts) < 2:
            return

        csr_name = parts[0]
        read_write_set_clear = parts[1]
        field, value, bit = None, None, None

        # Old format: 3-4 args, 3rd and 4th are direct_rw and force_machine (no =)
        if len(parts) >= 3 and "=" not in parts[2]:
            force_machine_rw_str = parts[3] if len(parts) > 3 else ""
            force_machine_rw = force_machine_rw_str.lower() == "true" if force_machine_rw_str else False
        else:
            # New format: parse key=value from remaining parts
            force_machine_rw = False
            for p in parts[2:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k == "force_machine":
                        force_machine_rw = v.lower() == "true"
                    elif k == "field":
                        field = v
                    elif k == "value":
                        try:
                            value = int(v, 0)
                        except ValueError:
                            value = v
                    elif k == "bit":
                        try:
                            bit = int(v, 0)
                        except (ValueError, TypeError):
                            bit = None

        if not csr_name or not read_write_set_clear:
            return

        hypervisor = False
        priv_mode = "user"
        csr_lower = csr_name.lower() if isinstance(csr_name, str) else ""
        if csr_lower.startswith("m"):
            priv_mode = "machine"
        elif any(csr_lower.startswith(p) for p in ("s", "h", "v")):
            priv_mode = "supervisor"
        if csr_lower.startswith("h"):
            hypervisor = True

        label = f"csr_access_{csr_name}_{priv_mode}_key_{self.parsed_csr_id}_{read_write_set_clear}"

        csr_access = ParsedCsrAccess(
            csr_name=csr_name,
            priv_mode=priv_mode,
            read_write_set_clear=read_write_set_clear,
            label=label,
            csr_id=self.parsed_csr_id,
            force_machine_rw=force_machine_rw,
            hypervisor=hypervisor,
            field=field,
            value=value,
            bit=bit,
        )
        self.pool.add_parsed_csr_access(csr_access)
        self.parsed_csr_id += 1

    def parse_read_pte(self, line):
        pattern = r"^;#read_pte\((?P<lin_name>\w+),\s*(?P<paging_mode>\w+),\s*(?P<level>\d+)\)"
        match = re.match(pattern, line)
        if match:
            lin_name = match.group("lin_name")
            paging_mode = match.group("paging_mode")
            level = int(match.group("level"))

            label = f"read_pte_{lin_name}_{paging_mode}_level_{level}_key_{self.parsed_pte_id}"
            read_pte = ParsedReadPte(lin_name=lin_name, paging_mode=paging_mode, level=level, label=label, pte_id=self.parsed_pte_id)
            self.pool.add_parsed_read_pte(read_pte)
            self.parsed_pte_id += 1

    def parse_write_pte(self, line):
        pattern = r"^;#write_pte\((?P<lin_name>\w+),\s*(?P<paging_mode>\w+),\s*(?P<level>\d+)\)"
        match = re.match(pattern, line)
        if match:
            lin_name = match.group("lin_name")
            paging_mode = match.group("paging_mode")
            level = int(match.group("level"))

            label = f"write_pte_{lin_name}_{paging_mode}_level_{level}_key_{self.parsed_pte_id}"
            write_pte = ParsedWritePte(lin_name=lin_name, paging_mode=paging_mode, level=level, label=label, write_pte_id=self.parsed_pte_id)
            self.pool.add_parsed_write_pte(write_pte)
            self.parsed_pte_id += 1

    def parse_pma_hint(self, line: str) -> None:
        """
        Parse ;#pma_hint directive.

        Syntax examples:
        ;#pma_hint(name=test, combinations=[{memory_type=memory, cacheability=cacheable, rwx=rwx}])
        ;#pma_hint(name=test, memory_types=[memory, io], cacheability=[cacheable, noncacheable])
        """
        pattern = r"^;#(pma_hint)\((.+)\)"
        match = re.match(pattern, line)
        if not match:
            return

        hint = ParsedPmaHint()
        args_str = match.group(2)

        # Parse arguments
        args = self._parse_directive_args(args_str)

        for key, value in args.items():
            if key == "name":
                hint.name = value
            elif key == "combinations":
                hint.combinations = self._parse_combinations(value)
            elif key == "memory_types":
                hint.memory_types = self._parse_list(value)
            elif key == "cacheability":
                hint.cacheability = self._parse_list(value)
            elif key == "combining":
                hint.combining = self._parse_list(value)
            elif key == "rwx_combos":
                hint.rwx_combos = self._parse_list(value)
            elif key == "amo_types":
                hint.amo_types = self._parse_list(value)
            elif key == "routing":
                hint.routing = self._parse_list(value)
            elif key == "adjacent":
                hint.adjacent = self._parse_bool(value)
            elif key == "min_regions":
                hint.min_regions = int(value, 0)
            elif key == "max_regions":
                hint.max_regions = int(value, 0)
            elif key == "size":
                hint.size = int(value, 0)

        if not hint.name:
            log.warning(f"PMA hint without name, skipping: {line}")
            return

        log.debug(f"Parsed PMA hint: {hint.name}")
        self.pool.add_parsed_pma_hint(hint)

    def parse_trigger_config(self, line: str) -> None:
        """
        Parse ;#trigger_config(index=N, type=execute|load|store|load_store, addr=SYM, action=breakpoint [, size=1|2|4|8] [, chain=0|1])
        """
        pattern = r"^;#trigger_config\((.+)\)"
        match = re.match(pattern, line.strip())
        if not match:
            return
        args = self._parse_directive_args(match.group(1))
        index = int(args.get("index", 0))
        trigger_type = args.get("type", "execute").lower()
        addr = args.get("addr", "")
        action = args.get("action", "breakpoint").lower()
        size = int(args.get("size", 4))
        chain = int(args.get("chain", 0))
        if not addr:
            log.warning(";#trigger_config requires addr= parameter")
            return
        # Add ParsedCsrAccess for tselect, tdata1, tdata2 (force_machine_rw) so jump table has entries
        csr_ids = []
        for csr_name in ("tselect", "tdata1", "tdata2"):
            label = f"trigger_config_{self.parsed_trigger_id}_{csr_name}_write"
            acc = ParsedCsrAccess(
                csr_name=csr_name,
                priv_mode="machine",
                read_write_set_clear="write",
                label=label,
                csr_id=self.parsed_csr_id,
                force_machine_rw=True,
                hypervisor=False,
            )
            if csr_name not in self.pool.parsed_csr_accesses or "write" not in self.pool.parsed_csr_accesses[csr_name]:
                self.pool.add_parsed_csr_access(acc)
                self.parsed_csr_id += 1
            csr_ids.append(self.pool.parsed_csr_accesses[csr_name]["write"].csr_id)
        cfg = ParsedTriggerConfig(
            index=index,
            trigger_type=trigger_type,
            addr=addr,
            action=action,
            size=size,
            chain=chain,
            trigger_id=self.parsed_trigger_id,
            csr_ids=(csr_ids[0], csr_ids[1], csr_ids[2]),
        )
        self.pool.add_parsed_trigger_config(cfg)
        self.parsed_trigger_id += 1

    def parse_trigger_disable(self, line: str) -> None:
        """Parse ;#trigger_disable(index=N)"""
        pattern = r"^;#trigger_disable\((.+)\)"
        match = re.match(pattern, line.strip())
        if not match:
            return
        args = self._parse_directive_args(match.group(1))
        index = int(args.get("index", 0))
        cfg = ParsedTriggerDisable(index=index, trigger_id=self.parsed_trigger_id)
        self.pool.add_parsed_trigger_disable(cfg)
        self.parsed_trigger_id += 1

    def parse_trigger_enable(self, line: str) -> None:
        """Parse ;#trigger_enable(index=N)"""
        pattern = r"^;#trigger_enable\((.+)\)"
        match = re.match(pattern, line.strip())
        if not match:
            return
        args = self._parse_directive_args(match.group(1))
        index = int(args.get("index", 0))
        cfg = ParsedTriggerEnable(index=index, trigger_id=self.parsed_trigger_id)
        self.pool.add_parsed_trigger_enable(cfg)
        self.parsed_trigger_id += 1

    def _parse_directive_args(self, args_str: str) -> dict:
        """
        Parse directive arguments into dictionary.
        Handles nested brackets for lists and dictionaries.
        """
        args = {}
        args_str = args_str.strip()

        if not args_str:
            return args

        # Track bracket depth
        depth = 0
        current_key = None
        current_value = ""
        in_string = False

        i = 0
        while i < len(args_str):
            char = args_str[i]

            # Handle string literals (if we add support)
            if char == '"' or char == "'":
                in_string = not in_string
                current_value += char
                i += 1
                continue

            if in_string:
                current_value += char
                i += 1
                continue

            # Track bracket depth
            if char in "[{":
                depth += 1
                current_value += char
            elif char in "]}":
                depth -= 1
                current_value += char
            elif char == "=" and depth == 0:
                # Start of value
                current_key = current_value.strip()
                current_value = ""
            elif char == "," and depth == 0:
                # End of current argument
                if current_key:
                    args[current_key] = current_value.strip()
                current_key = None
                current_value = ""
            else:
                current_value += char

            i += 1

        # Add last argument
        if current_key:
            args[current_key] = current_value.strip()

        return args

    def _parse_list(self, value: str) -> list[str]:
        """Parse list value like [memory, io]"""
        value = value.strip()
        if not value.startswith("[") or not value.endswith("]"):
            return [value]  # Single value, return as list

        # Remove brackets
        value = value[1:-1].strip()
        if not value:
            return []

        # Split by comma, but handle nested brackets
        items = []
        current_item = ""
        depth = 0

        for char in value:
            if char == "[":
                depth += 1
                current_item += char
            elif char == "]":
                depth -= 1
                current_item += char
            elif char == "," and depth == 0:
                items.append(current_item.strip())
                current_item = ""
            else:
                current_item += char

        if current_item:
            items.append(current_item.strip())

        return items

    def _parse_combinations(self, value: str) -> list[dict]:
        """Parse combinations list like [{memory_type=memory, rwx=rwx}, ...]"""
        value = value.strip()
        if not value.startswith("[") or not value.endswith("]"):
            return []

        # Remove outer brackets
        value = value[1:-1].strip()
        if not value:
            return []

        combinations = []
        current_combo = ""
        depth = 0

        for char in value:
            if char == "{":
                depth += 1
                current_combo += char
            elif char == "}":
                depth -= 1
                current_combo += char
                if depth == 0:
                    # Parse this combination
                    combo_dict = self._parse_combo_dict(current_combo)
                    if combo_dict:
                        combinations.append(combo_dict)
                    current_combo = ""
            elif depth == 0 and char in (",", " ", "\t"):
                # Skip whitespace and commas between combinations
                continue
            else:
                current_combo += char

        return combinations

    def _parse_combo_dict(self, combo_str: str) -> dict:
        """Parse combination dictionary like {memory_type=memory, rwx=rwx}"""
        combo = {}
        combo_str = combo_str.strip()

        if not combo_str.startswith("{") or not combo_str.endswith("}"):
            return combo

        # Remove braces
        combo_str = combo_str[1:-1].strip()
        if not combo_str:
            return combo

        # Split by comma, but handle nested structures
        pairs = []
        current_pair = ""
        depth = 0

        for char in combo_str:
            if char in "[{":
                depth += 1
                current_pair += char
            elif char in "]}":
                depth -= 1
                current_pair += char
            elif char == "," and depth == 0:
                pairs.append(current_pair.strip())
                current_pair = ""
            else:
                current_pair += char

        if current_pair:
            pairs.append(current_pair.strip())

        # Parse each key=value pair
        for pair in pairs:
            if "=" in pair:
                key, val = pair.split("=", 1)
                combo[key.strip()] = val.strip()

        return combo

    def _parse_bool(self, value: str) -> bool:
        """Parse boolean value"""
        value = value.strip().lower()
        return value in ("true", "1", "yes")

    def process(self):
        """
        Process parsed data and into their respective class instances
        """
        pass


@dataclass
class ParsedRandomData:
    name: str = ""
    type: str = ""
    and_mask: int = 0xFFFFFFFFFFFFF000
    or_mask: int = 0x0


@dataclass
class ParsedReserveMemory:
    addr: str = ""
    addr_type: str = ""
    size: int = 0x1000
    and_mask: int = 0xFFFFFFFFFFFFF000
    name: str = ""
    start_addr: int = 0


@dataclass
class ParsedRandomAddress:
    name: str = ""
    address: Optional[int] = None
    type: RV.AddressType = RV.AddressType.NONE
    io: bool = False
    addr_bits: Optional[int] = None
    size: int = 0x1000
    align: int = 0xFFFFFFFFFFFFF000
    and_mask: int = 0xFFFFFFFFFFFFFFFF  # -1
    or_mask: int = 0
    in_pma: bool = False
    pma_info: Optional[PmaInfo] = None
    secure: bool = False
    resolve_priority: int = 10


@dataclass
class ParsedPageMapping:
    lin_name: str = ""
    phys_name: str = ""
    in_private_map: bool = False
    v: bool = True
    v_leaf: bool = True
    v_nonleaf: bool = True
    v_level0: bool = True
    v_level1: bool = True
    v_level2: bool = True
    v_level3: bool = True
    v_level4: bool = True
    v_nonleaf_gnonleaf: bool = True
    v_nonleaf_gleaf: bool = True
    v_leaf_gnonleaf: bool = True
    v_leaf_gleaf: bool = True
    a: Optional[bool] = None
    a_nonleaf: bool = True
    a_nonleaf_gnonleaf: Optional[bool] = None
    a_nonleaf_gleaf: Optional[bool] = None
    a_leaf_gnonleaf: Optional[bool] = None
    a_leaf_gleaf: Optional[bool] = None
    d: Optional[bool] = None
    d_nonleaf: bool = True
    d_nonleaf_gnonleaf: Optional[bool] = None
    d_nonleaf_gleaf: Optional[bool] = None
    d_leaf_gnonleaf: Optional[bool] = None
    d_leaf_gleaf: Optional[bool] = None
    r: bool = True
    r_nonleaf: bool = True
    r_nonleaf_gnonleaf: Optional[bool] = None
    r_nonleaf_gleaf: Optional[bool] = None
    r_leaf_gnonleaf: Optional[bool] = None
    r_leaf_gleaf: Optional[bool] = None
    w: bool = False
    w_nonleaf: Optional[bool] = None
    w_nonleaf_gnonleaf: Optional[bool] = None
    w_nonleaf_gleaf: Optional[bool] = None
    w_leaf_gnonleaf: Optional[bool] = None
    w_leaf_gleaf: Optional[bool] = None
    x: bool = True
    x_nonleaf: Optional[bool] = None
    x_nonleaf_gnonleaf: Optional[bool] = None
    x_nonleaf_gleaf: Optional[bool] = None
    x_leaf_gnonleaf: Optional[bool] = None
    x_leaf_gleaf: Optional[bool] = None
    u: Optional[bool] = None
    u_nonleaf: Optional[bool] = None
    u_nonleaf_gnonleaf: Optional[bool] = None
    u_nonleaf_gleaf: Optional[bool] = None
    u_leaf_gnonleaf: Optional[bool] = None
    u_leaf_gleaf: Optional[bool] = None
    u_level0_glevel1: bool = False
    g: bool = False
    g_nonleaf: bool = False
    g_level0: bool = False
    g_level1: bool = False
    g_level2: bool = False
    g_level3: bool = False
    g_level4: bool = False
    g_nonleaf_gnonleaf: bool = False
    g_nonleaf_gleaf: bool = False
    g_leaf_gnonleaf: bool = False
    g_leaf_gleaf: bool = False
    rsw: int = 0
    rsw_level0: int = 0
    rsw_level1: int = 0
    rsw_level2: int = 0
    rsw_level3: int = 0
    rsw_level4: int = 0
    reserved: int = 0
    reserved_level0: int = 0
    reserved_level1: int = 0
    reserved_level2: int = 0
    reserved_level3: int = 0
    reserved_level4: int = 0
    pbmt: int = 0
    pbmt_nonleaf: int = 0
    pbmt_level0: int = 0
    pbmt_level1: int = 0
    pbmt_level2: int = 0
    pbmt_level3: int = 0
    pbmt_level4: int = 0
    pbmt_nonleaf_gnonleaf: int = 0
    pbmt_nonleaf_gleaf: int = 0
    pbmt_leaf_gnonleaf: int = 0
    pbmt_leaf_gleaf: int = 0
    n: Optional[int] = None
    n_nonleaf: int = 0
    n_level0: int = 0
    n_level1: int = 0
    n_level2: int = 0
    n_level3: int = 0
    n_level4: int = 0
    n_nonleaf_gnonleaf: int = 0
    n_nonleaf_gleaf: int = 0
    n_leaf_gnonleaf: int = 0
    n_leaf_gleaf: int = 0
    secure: bool = False
    page_maps: list[str] = field(default_factory=list)
    pagesizes: list[str] = field(default_factory=list)
    gstage_vs_leaf_pagesizes: list[str] = field(default_factory=list)
    gstage_vs_nonleaf_pagesizes: list[str] = field(default_factory=list)

    _4kb: bool = True
    _64kb: bool = False
    _2mb: bool = False
    _1gb: bool = False
    _512gb: bool = False
    _256tb: bool = False
    _4kbpage: bool = False
    _64kbpage: bool = False
    _2mbpage: bool = False
    _1gbpage: bool = False
    _512gbpage: bool = False
    _256tbpage: bool = False
    final_pagesize: int = 0
    modify_pt: bool = False
    address_size: int = 0x1000
    address_mask: int = 0xFFFFFFFFFFFFF000
    linked_page_mappings: list["ParsedPageMapping"] = field(default_factory=list)
    linked_ppm_offset: int = 0x0

    # Internal use members
    resolve_priority: int = 0
    lin_addr_specified: bool = False
    phys_addr_specified: bool = False
    is_linked: bool = False
    has_linked_ppms: bool = False
    alias: bool = False  # Used to mark if physical address is an alias for another address


@dataclass
class ParsedPageMap:
    name: str = ""
    mode: str = "testmode"  # Can take sv57, sv48, sv39, disabled, testmode


@dataclass
class ParsedTestHeader:
    priv: str = ""
    env: str = ""
    secure_mode: str = ""
    cpus: str = ""
    paging: str = ""
    paging_g: str = ""
    arch: str = ""
    group: str = ""
    features: str = ""
    mp: str = ""
    mp_mode: str = ""
    opts: str = ""
    parallel_scheduling_mode: str = ""


@dataclass
class ParsedVectoredInterrupt:
    interrupt_index = {"SSI": 1, "MSI": 3, "STI": 5, "MTI": 7, "SEI": 9, "MEI": 11, "COI": 13}  # static interrupt vector table

    index: int
    name: str
    label: str

    @classmethod
    def from_interrupt_index(cls, label: str, index: int):
        "Returns a instance from an interrupt index"
        for k, v in cls.interrupt_index.items():
            if v == index:
                return cls(index=index, name=k, label=label)
        return cls(index=index, name=f"platform_{index}", label=label)

    @classmethod
    def from_interrupt_name(cls, label: str, name: str):
        "Returns a instance from an interrupt name"
        index = cls.interrupt_index.get(name, None)
        if index is None:
            raise ValueError(f"No interrupt bit named {name}. Supported names are [{', '.join(i for i in cls.interrupt_index)}]")
        return cls(index=index, name=name, label=label)


@dataclass
class ParsedCsrAccess:
    csr_name: str
    priv_mode: str
    csr_id: int
    read_write_set_clear: str
    label: str
    hypervisor: bool
    force_machine_rw: bool = False
    # Extended params for write_subfield, read_subfield, set_bit, clear_bit
    field: Optional[str] = None
    value: Optional[Union[int, str]] = None
    bit: Optional[int] = None


@dataclass
class ParsedReadPte:
    lin_name: str
    paging_mode: str
    level: int
    pte_id: int
    label: str


@dataclass
class ParsedWritePte:
    lin_name: str
    paging_mode: str
    level: int
    write_pte_id: int
    label: str


@dataclass
class ParsedPmaHint:
    """Parsed PMA hint from ;#pma_hint directive"""

    name: str = ""
    combinations: list[dict] = field(default_factory=list)
    memory_types: list[str] = field(default_factory=list)
    cacheability: list[str] = field(default_factory=list)
    combining: list[str] = field(default_factory=list)
    rwx_combos: list[str] = field(default_factory=list)
    amo_types: list[str] = field(default_factory=list)
    routing: list[str] = field(default_factory=list)
    adjacent: bool = False
    min_regions: Optional[int] = None
    max_regions: Optional[int] = None
    size: Optional[int] = None


@dataclass
class ParsedTriggerConfig:
    """Parsed ;#trigger_config directive."""

    index: int
    trigger_type: str  # execute, load, store, load_store
    addr: str  # symbol or hex literal
    action: str = "breakpoint"
    size: int = 4
    chain: int = 0
    trigger_id: int = 0
    csr_ids: Tuple[int, int, int] = (0, 0, 0)  # tselect, tdata1, tdata2


@dataclass
class ParsedTriggerDisable:
    """Parsed ;#trigger_disable directive."""

    index: int
    trigger_id: int = 0


@dataclass
class ParsedTriggerEnable:
    """Parsed ;#trigger_enable directive. Re-enables previously configured trigger."""

    index: int
    trigger_id: int = 0


@dataclass
class ParsedCustomHandler:
    """Parsed ;#custom_handler(NUM, LABEL) for per-discrete_test custom interrupt handler."""

    vector_num: int
    label: str


@dataclass
class ParsedVectorDelegation:
    """Parsed ;#vector_delegation(NUM, MODE). Voyager2 sets where each vector is handled."""

    vector_num: int
    delegate_to_supervisor: bool
