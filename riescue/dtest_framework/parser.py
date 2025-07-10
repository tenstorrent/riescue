# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import re
import logging
from typing import List, Optional
from dataclasses import dataclass, field
from pathlib import Path

import riescue.lib.common as common
import riescue.lib.enums as RV
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
    """

    def __init__(self, filename: Path, pool: Pool):
        self.filename = filename
        self.reserve_memory = dict()
        self.random_data = dict()
        self.random_addrs = dict()
        self.page_mappings = dict()
        self.test_header = ParsedTestHeader()
        self.discrete_tests = dict()

        self.pool = pool

    def parse(self):
        with open(self.filename, "r") as file:
            contents = file.readlines()

        for line in contents:
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

            if line.startswith(";#discrete_test"):
                self.parse_discrete_test(line)

            if line.startswith(";#init_memory"):
                self.parse_init_mem(line)

            if line.startswith(";#vectored_interrupt"):
                self.parse_vectored_interrupt(line)

            # Only consider strip after all non-riscv directives are removed
            line = line.strip()

            if line.startswith(".section"):
                self.parse_sections(line)

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
            log.info(f"Adding random_addr: {rnd_inst.name} to pool")
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
        if "secure=" in line:
            secure_str = re.findall(r"secure=(\d+)", line)[0]

        if "page_maps=" in line:
            pagemap_str = re.findall(r"page_maps=\[.*?\]", line)[0]
        line = line.replace(pagesize_str, "")
        line = line.replace(gstage_vs_leaf_pagesize_str, "")
        line = line.replace(gstage_vs_nonleaf_pagesize_str, "")
        line = line.replace(pagemap_str, "")
        match = re.match(pattern, line)
        fixed_addr_specified = False
        if match:
            ppm_inst = ParsedPageMapping()
            args = match.group(2).replace(" ", "")
            args = args.split(",")
            args = list(filter(None, args))
            for arg in args:
                # print(f'arg: {arg}')
                var = arg.split("=")[0]
                val = arg.split("=")[1]
                if re.match(r"lin_name", var):
                    # Do additional check if we have linked page_mappings
                    match = re.search(r"(\w+)\+(\w+)", val)
                    if match:
                        # Find the parent parsedpagemap instance
                        parent = match.group(1)
                        parent_ppm_inst = self.pool.get_parsed_page_mapping(parent)
                        parent_ppm_inst.linked_page_mappings.append(ppm_inst)
                        parent_ppm_inst.has_linked_ppms = True
                        ppm_inst.is_linked = True
                        # The text after + in lin_name has the starting offset for the linked ppm
                        ppm_inst.linked_ppm_offset = int(match.group(2), 16)

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

                # Match all the gstage_vs_* attributes
                if not (re.match(r"lin_name|phys_name|lin_addr|phys_addr|pagesize|g_(.+)|v_(.+)|a_(.+)|d_(.+)|u_(.+)|w_(.+)|r_(.+)|x_(.+)|page_maps|secure", var)):
                    val = int(val, 0)

                # if (re.match(r'pagesize', var)):
                #     # pagesize argument looks like pagesize=['4kb', '2mb', '1gb']
                #     # Match everything between [] from args an process entire list here and remove the
                #     # entire pagesize argument with value from args
                #     pagesizes = re.search(r'\[(.+)\]', line).group(1).replace(' ', '')
                #     print(f'var: {val}')

                #     extract = re.findall(r"pagesize=\[.*?\]", line)[0]
                #     line = line.replace(extract, "")
                #     extract = extract.replace('[', "")
                #     extract = extract.replace(']', "")

                #     print(f'extract: {extract}')

                #     # ps = re.findall("\'(.*?)\'", val)[0]
                #     # This is very stupid logic, sorry about that
                #     # The first time we hit pagesize=['4kb', as the arg, we need to collect all
                #     # the elements from the pagesize array defined in the line
                #     # At the same time, we also need to remove the entire pagesize=[...] from the line
                #     # so that we don't have next arg values like '2mb'|'1gb' etc without var
                #     pagesizes_to_set = list()
                #     for _pagesize in pagesizes.split(','):
                #         # We still get quotes around the value, so remove them
                #         pagesize = re.sub('\W+', '', _pagesize)
                #         # setattr(ppm_inst, f'_{pagesize}', True)
                #         pagesizes_to_set.append(pagesize)
                #         for item in args:
                #             if pagesize in item:
                #                 args.pop(args.index(item))
                #     if var == 'pagesize':
                #         ppm_inst.pagesizes = pagesizes_to_set
                #     elif var == 'page_maps':
                #         ppm_inst.page_maps = pagesizes_to_set

                # if (re.match(r'page_maps', var)):
                #     # pagesize argument looks like pagesize=['4kb', '2mb', '1gb']
                #     # Match everything between [] from args an process entire list here and remove the
                #     # entire pagesize argument with value from args
                #     pagesizes = re.search(r'\[(.+)\]', line).group(1).replace(' ', '')
                #     print(f'pagemap: {pagesizes}')
                #     # This is very stupid logic, sorry about that
                #     # The first time we hit pagesize=['4kb', as the arg, we need to collect all
                #     # the elements from the pagesize array defined in the line
                #     # At the same time, we also need to remove the entire pagesize=[...] from the line
                #     # so that we don't have next arg values like '2mb'|'1gb' etc without var
                #     pagesizes_to_set = list()
                #     for _pagesize in pagesizes.split(','):
                #         # We still get quotes around the value, so remove them
                #         pagesize = re.sub('\W+', '', _pagesize)
                #         # setattr(ppm_inst, f'_{pagesize}', True)
                #         pagesizes_to_set.append(pagesize)
                #         for item in args:
                #             if pagesize in item:
                #                 args.pop(args.index(item))
                #     if var == 'pagesize':
                #         ppm_inst.pagesizes = pagesizes_to_set
                #     elif var == 'page_maps':
                #         ppm_inst.page_maps = pagesizes_to_set

                if var == "phys_name":
                    if val == "&random":
                        ppm_inst.resolve_priority = 20
                    # Check if phys_name exists in any value instance in ppm
                    exists = any(ppm.phys_name == val for ppm in self.pool.get_parsed_page_mappings().values())
                    if exists:
                        log.info(f"phys_name {val} already exists in another page mapping, marking as alias case")
                        ppm_inst.alias = True
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
                # Also find the corrosponding physical address instance and mark it secure there
                phys_name = ppm_inst.phys_name
                phys_inst = self.pool.get_parsed_addr(phys_name)
                phys_inst.secure = True

            if pagemap_str != "":
                # Extract actual sizes from "pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])"
                # Remove spaces, quotes and convert to python list
                extracted = re.findall(r"\[(.*?)\]", pagemap_str)[0].replace(" ", "").replace("'", "").split(",")
                ppm_inst.page_maps = extracted

            if fixed_addr_specified:
                ppm_inst.resolve_priority = 15

            if not ppm_inst.is_linked:
                self.page_mappings[ppm_inst.lin_name] = [ppm_inst]
                self.pool.add_parsed_page_mapping(ppm_inst)

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

    def parse_init_mem(self, line):
        lin_name = re.findall(r"@(.+)", line)[0]
        self.pool.add_parsed_init_mem_addr(lin_name)

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


@dataclass
class ParsedReserveMemory:
    addr: str = ""
    addr_type: str = ""
    size: int = 0x1000
    and_mask: int = 0xFFFFFFFFFFFFF000
    name: str = ""
    start_addr: int = 0


@dataclass
class PmpInfo:
    start_addr: int = 0
    size: int = 0
    secure: bool = False


@dataclass
class PmaInfo:
    pma_name: str = ""
    pma_valid: bool = False  # TODO: Evaluate need for this valid.
    pma_read: bool = True
    pma_write: bool = True
    pma_execute: bool = True
    pma_memory_type: str = "memory"  # 'io' | 'memory' | 'ch0' | 'ch1'
    pma_amo_type: str = "arithmetic"  # 'none' | 'logical' | 'swap' | 'arithmetic'
    pma_cacheability: str = "cacheable"  # 'cacheable' | 'noncacheable'
    pma_combining: str = "noncombining"  # 'combining' | 'noncombining'
    pma_routing_to: str = "coherent"  # 'coherent' | 'noncoherent'
    pma_address: int = 0
    pma_size: int = 0

    _memory_type_map = {"memory": 0, "io": 1, "ch0": 2, "ch1": 3}

    _amo_type_map = {"none": 0, "logical": 1, "swap": 2, "arithmetic": 3}

    _cacheability_map = {"cacheable": 1, "noncacheable": 0}

    _combining_map = {"combining": 1, "noncombining": 0}

    _routing_to_map = {"coherent": 1, "noncoherent": 0}

    def generate_pma_value(self):
        # pmacfg CSR format looks like this
        # 2:0 - Permission, 0: read, 1: write, 2: execute
        # 4:3 - memory type, 0: memory, 1: io, 2: ch0, 3: ch1
        # 6:5 - amo type, 0: none, 1: logical, 2: swap, 3: arithmetic
        # 7 (memory) - cacheability, 1: cacheabl1, 0: noncacheable
        # 7 (io) - combining, 1: combining, 0: noncombining
        # 8 - routing to, 1: coherent, 0: noncoherent
        # 11:9 - reserved 0
        # 51:12 - address
        # 57:52 - reserved 0
        # 63:58 - size (if 0, then pma is invalid)
        pma_value = 0
        if not self.pma_valid:
            pma_value |= self.pma_read << 0
            pma_value |= self.pma_write << 1
            if self.pma_execute:
                pma_value |= self.pma_execute << 2
            pma_value |= self._memory_type_map[self.pma_memory_type] << 3
            pma_value |= self._amo_type_map[self.pma_amo_type] << 5
            if self.pma_memory_type == "memory":
                pma_value |= self._cacheability_map[self.pma_cacheability] << 7
            else:  # io
                pma_value |= self._combining_map[self.pma_combining] << 7
            pma_value |= self._routing_to_map[self.pma_routing_to] << 8
            pma_value |= (self.pma_address >> 12) << 12
            pma_value |= (common.msb(self.pma_size) + 1) << 58
            # print(f'pma_size: bits: {common.msb(self.pma_size)}, size: {self.pma_size:x}')

        return pma_value


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
    n: bool = False
    secure: bool = False
    page_maps: List[str] = field(default_factory=list)
    pagesizes: List[str] = field(default_factory=list)
    gstage_vs_leaf_pagesizes: List[str] = field(default_factory=list)
    gstage_vs_nonleaf_pagesizes: List[str] = field(default_factory=list)

    _4kb: bool = True
    _2mb: bool = False
    _1gb: bool = False
    _512gb: bool = False
    _256tb: bool = False
    _4kbpage: bool = False
    _2mbpage: bool = False
    _1gbpage: bool = False
    _512gbpage: bool = False
    _256tbpage: bool = False
    final_pagesize: int = 0
    modify_pt: bool = False
    address_size: int = 0x1000
    address_mask: int = 0xFFFFFFFFFFFFF000
    linked_page_mappings: List["ParsedPageMapping"] = field(default_factory=list)
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
    paging_g: str = "disable"
    arch: str = ""
    group: str = ""
    features: str = ""
    mp: str = ""
    mp_mode: str = ""
    opts: str = ""


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
