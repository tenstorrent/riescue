# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import re
import logging
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING, cast

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.lib.csr_manager.csr_manager_interface import CsrManagerInterface
from riescue.dtest_framework.lib.dtest_instruction_helper import DtestInstructionHelper
from riescue.dtest_framework.lib.sdtrig import (
    TriggerType,
    build_tdata1_mcontrol6,
    build_tdata1_icount,
    build_tdata1_itrigger,
    build_tdata1_etrigger,
)
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.parser import Parser, ParsedPageMapping
from riescue.riemap.request import Page
from riescue.riemap.result import SpaceResult
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.runtime import Runtime
from riescue.dtest_framework.artifacts import GeneratedFiles

if TYPE_CHECKING:
    from riescue.dtest_framework.runtime.macros import Macros

log = logging.getLogger(__name__)


class AssemblyWriter:
    """
    This handles generating and writing assembly code. It's responsible for writing:

    - Assembly File (.S)
    - Equates Files (.inc)
    - Pagetables Files (.inc)
    - :class:`riescue.dtest_framework.runtime.Runtime` Files (.inc)
    - Linker Script (.ld)


    :param rng: Random number generator
    :param pool: Test pool
    :param run_dir: Path to the directory where the generated code will be written
    :param featmgr: Feature manager
    """

    def __init__(self, rng: RandNum, pool: Pool, run_dir: Path, featmgr: FeatMgr):
        self.rng = rng
        self.pool = pool
        self.featmgr = featmgr
        self.run_dir = run_dir

        self.runtime = Runtime(rng=self.rng, pool=self.pool, featmgr=self.featmgr)

        # output info
        self.testname = self.pool.testname

        if self.featmgr.wysiwyg:
            self.code_offset = 0
        elif self.featmgr.code_offset is not None:
            self.code_offset = self.featmgr.code_offset
        elif self.featmgr.force_alignment:
            self.code_offset = 0
        else:
            self.code_offset = self.rng.random_in_range(0, 0x3F * 5, 16)
        self.single_assembly_file = self.featmgr.single_assembly_file or self.featmgr.linux_mode

        # Lazily-built (lin_name, map_name) -> riemap declaration Page cache, from
        # pool.page_translation; see _get_page_for_addr_name.
        self._page_by_name_and_map: dict[tuple[str, str], Page] | None = None

    def write(self, rasm: Path, generated_files: GeneratedFiles) -> None:
        """
        Generates and writes all files.

        - Always writes assembly file and linker script.
        - Conditionally writes equates, pagetables, and runtime files (.inc files).

        :param rasm: Path to the input RASM file
        :param generated_files: Generated files container
        """

        self.create_assembly_files(rasm, generated_files)
        self.create_linker_script(generated_files.linker_script)
        self.create_c_files(generated_files)

    def _generate_aplic_header(self) -> list[str]:
        aplic_header_content: list[str] = []
        if self.featmgr.io_maplic_addr is not None:
            aplic_header_content.append(f"#define MAPLIC_MMR_BASE_ADDR {hex(self.featmgr.io_maplic_addr)}\n")
        if self.featmgr.io_saplic_addr is not None:
            aplic_header_content.append(f"#define SAPLIC_MMR_BASE_ADDR {hex(self.featmgr.io_saplic_addr)}\n")
        if self.featmgr.io_imsic_mfile_addr is not None:
            aplic_header_content.append(f"#define IMSIC_MFILE_BASE_ADDR {hex(self.featmgr.io_imsic_mfile_addr)}\n")
        if self.featmgr.io_imsic_mfile_stride is not None:
            aplic_header_content.append(f"#define IMSIC_MFILE_STRIDE {hex(self.featmgr.io_imsic_mfile_stride)}\n")
        if self.featmgr.io_imsic_sfile_addr is not None:
            aplic_header_content.append(f"#define IMSIC_SFILE_BASE_ADDR {hex(self.featmgr.io_imsic_sfile_addr)}\n")
        if self.featmgr.io_imsic_sfile_stride is not None:
            aplic_header_content.append(f"#define IMSIC_SFILE_STRIDE {hex(self.featmgr.io_imsic_sfile_stride)}\n")
        if aplic_header_content:
            aplic_header_content = ["#ifndef __RIESCUE_APLIC_MMR_H__\n#define __RIESCUE_APLIC_MMR_H__\n\n"] + aplic_header_content
            aplic_header_content += ["\n#endif\n"]
        return aplic_header_content

    def create_c_files(self, generated_files: GeneratedFiles) -> None:
        """
        Create C files - for example aplic includes and rvmodel_macros.h
        """
        aplic_header_file = self.run_dir / "riescue_aplic_mmr.h"
        aplic_header = "".join(self._generate_aplic_header())
        if aplic_header:
            with open(aplic_header_file, "w") as f:
                f.write(aplic_header + "\n")
            f.close()

        # Stage rvmodel_macros.h into run_dir (resolved by riescued.py)
        assert self.featmgr.rvmodel_macros is not None, "rvmodel_macros must be set before assembly generation"
        shutil.copy(self.featmgr.rvmodel_macros, self.run_dir / "rvmodel_macros.h")

    def create_assembly_files(self, rasm: Path, generated_files: GeneratedFiles) -> None:
        """
        Generates assembly file and any conditional files. E.g equates, pagetables, :class:`Runtime` files.

        Splits input file into headers and test code, generates runtime code then equates.

        :param rasm: Path to the input RASM file
        :param generated_files: Generated files container
        """
        header_section, test_section = self.split_rasm_sections(rasm)
        test_section = self._wrap_discrete_tests(test_section)
        runtime_sections = self.generate_runtime_sections()
        equates_section = self.generate_equates_section()
        data_section = self.generate_data_section()

        # Prepend #include for rvmodel_macros.h so I/O macros are always available
        # This must come before equates so macros are available in the opsys #include
        rvmodel_include: list[str] = ['#include "rvmodel_macros.h"\n']

        if self.pool.init_aplic_interrupts:
            aplic_imsic_assembly = self.generate_aplic_imsic_assembly()
            assembly_content = header_section + rvmodel_include + equates_section + aplic_imsic_assembly + runtime_sections + test_section + data_section
        else:
            assembly_content = header_section + rvmodel_include + equates_section + runtime_sections + test_section + data_section

        # apply find and replace to all code
        all_assembly = "".join(assembly_content)
        with open(generated_files.assembly, "w") as assembly_file:
            assembly_file.write("\n".join(self.find_and_replace_assembly(all_assembly)))

    def split_rasm_sections(self, rasm: Path) -> tuple[list[str], list[str]]:
        """
        Read .rasm file and return a series of sections. Need to find where to split the test code from header code.

        Tests can be split into two sections:

        1. Header Section (header, directives; everything before test section )
        2. Test Section (test code and test data)

        The Test Section starts with the first occurrence of any of:
        - ``.section .code``
        - ``.section .code_super_0``
        - ``.section .code_machine_0``

        This ensures includes are placed before all code sections.

        """
        with open(rasm, "r") as input_file:
            lines = input_file.readlines()

        # find where to split tests, the first occurrence of any code section
        code_section_idx = None
        code_super_0_idx = None
        code_machine_0_idx = None
        for i, line in enumerate(lines):
            if ".section .code" in line and "_" not in line and code_section_idx is None:
                code_section_idx = i
            if ".section .code_super_0" in line and code_super_0_idx is None:
                code_super_0_idx = i
            if ".section .code_machine_0" in line and code_machine_0_idx is None:
                code_machine_0_idx = i

        if code_section_idx is None:
            raise ValueError(f"Couldn't find a .section .code section in input file, {rasm.name}.")

        # Use the earliest (lowest line number) of the found code sections
        test_section_start_idx = code_section_idx
        if code_super_0_idx is not None and code_super_0_idx < test_section_start_idx:
            test_section_start_idx = code_super_0_idx
        if code_machine_0_idx is not None and code_machine_0_idx < test_section_start_idx:
            test_section_start_idx = code_machine_0_idx

        header_section = lines[:test_section_start_idx]
        test_section = lines[test_section_start_idx:]
        return header_section, test_section

    def _wrap_discrete_tests(self, test_section: list[str]) -> list[str]:
        """
        Inject the :py:attr:`~riescue.lib.enums.HookPoint.PRE_DISCRETE_TEST` and
        :py:attr:`~riescue.lib.enums.HookPoint.POST_DISCRETE_TEST` hooks around each discrete test.

        - ``PRE_DISCRETE_TEST`` code is inserted immediately after each discrete test's label
          (``;#discrete_test(test=NAME)`` -> ``NAME:``), so it runs at test entry.
        - ``POST_DISCRETE_TEST`` code is inserted immediately before every ``;#test_passed()`` inside
          a discrete test, so it runs on each pass path.

        ``test_setup`` / ``test_cleanup`` are not discrete tests and are left untouched. This is a
        no-op (returns the input unchanged) when neither hook is registered.

        :param test_section: Test section lines from :meth:`split_rasm_sections`
        :return: Test section lines with hook code woven in
        """
        pre_code = self.featmgr.call_hook(RV.HookPoint.PRE_DISCRETE_TEST)
        post_code = self.featmgr.call_hook(RV.HookPoint.POST_DISCRETE_TEST)
        if not pre_code and not post_code:
            return test_section
        return self._weave_discrete_test_hooks(test_section, pre_code, post_code)

    @staticmethod
    def _weave_discrete_test_hooks(test_section: list[str], pre_code: str, post_code: str) -> list[str]:
        """
        Pure text transform used by :meth:`_wrap_discrete_tests`. Kept separate so the weaving logic
        can be unit tested without constructing an :class:`AssemblyWriter`.
        """
        directive_re = re.compile(r"^;#discrete_test\(")
        passed_re = re.compile(r"^;#test_passed\b")
        cleanup_re = re.compile(r"^test_cleanup\s*:")
        section_re = re.compile(r"^\.section\b")

        result: list[str] = []
        in_region = False  # inside the discrete-test region (excludes setup/cleanup/data)
        pending_label = None  # exact label expected right after a ;#discrete_test directive
        for line in test_section:
            stripped = line.strip()

            # ;#discrete_test(test=NAME): start/continue region; the next NAME: label is the entry point
            if directive_re.match(stripped):
                in_region = True
                compact = stripped.replace(" ", "")  # tolerate `test = NAME`, mirrors the parser
                name_m = re.match(r";#discrete_test\(test=([^),]+)", compact)
                pending_label = name_m.group(1) if name_m else None
                result.append(line)
                continue

            # PRE: right after the test's label
            if pending_label is not None and re.match(rf"^{re.escape(pending_label)}\s*:", stripped):
                result.append(line)
                if pre_code:
                    result.append(pre_code + "\n")
                pending_label = None
                continue

            # Region ends at the reserved test_cleanup label or the first .section (e.g. .data)
            if in_region and (cleanup_re.match(stripped) or section_re.match(stripped)):
                in_region = False

            # POST: before every ;#test_passed() inside a discrete test
            if in_region and post_code and passed_re.match(stripped):
                result.append(post_code + "\n")

            result.append(line)
        return result

    def _emit_direct_csr(self, line: str, csr_name: str, access_type: str) -> str:
        """Emit direct csrr/csrw/csrs/csrc instructions (fallback when csr_manager unavailable)."""
        csr_asm = csr_name.lower()
        if access_type == "read":
            return line + f"\ncsrr t2, {csr_asm}\n"
        if access_type == "set":
            return line + f"\ncsrs {csr_asm}, t2\n"
        if access_type == "clear":
            return line + f"\ncsrc {csr_asm}, t2\n"
        return line + f"\ncsrw {csr_asm}, t2\n"

    def _csr_value_load(self, value: str) -> str:
        """Pick ``li`` (numeric/random_addr/expression) or ``la`` (plain symbol) for a CSR value."""
        base = value.split("+")[0].strip("() ") if "+" in value else value.strip("() ")
        if value.startswith("0x") or value.lstrip("-").isdigit() or self.pool.parsed_random_addr_exists(value) or self.pool.parsed_random_addr_exists(base):
            return "li"
        if not re.fullmatch(r"[A-Za-z_.$][\w.$]*", value):  # expression (e.g. (1<<3)) -> li
            return "li"
        return "la"

    def _expand_csr_rw(self, parsed_line: str) -> str:
        """Expand a ``;#csr_rw(...)`` directive into assembly (the single CSR_RW API path).

        The ``;#csr_rw`` directive and the trigger directives both route here. Returns the
        generated assembly, or "" if the CSR can't be resolved.
        """
        priority = ["user", "super", "machine"]
        match = re.match(r"^;#csr_rw\(([^,]+),\s*([^,)]+)", parsed_line)
        if not match:
            return ""
        csr_spec = match.group(1).strip()
        read_write_set_clear = match.group(2).strip().rstrip(")")
        direct_rw = "false"
        force_machine_rw_line: bool = False
        if "," in parsed_line:
            rest = parsed_line[parsed_line.index(read_write_set_clear) + len(read_write_set_clear) :].strip()
            if rest.startswith(","):
                rest_parts = [p.strip().rstrip(")") for p in rest[1:].split(",")]
                if rest_parts and "=" not in rest_parts[0]:
                    # Positional form: ;#csr_rw(csr, action, direct_rw, force_machine_rw)
                    direct_rw = rest_parts[0].lower()
                    if len(rest_parts) > 1:
                        force_machine_rw_line = rest_parts[1].lower() == "true"
                else:
                    # New format: ;#csr_rw(csr, action, force_machine=true, ...)
                    for p in rest_parts:
                        if "=" in p:
                            split_kv = p.split("=", 1)
                            if split_kv[0].strip() == "force_machine":
                                force_machine_rw_line = split_kv[1].strip().lower() == "true"

        directive_value: int | str | None = None
        directive_bit: int | None = None
        directive_field: str | None = None
        for kv in re.findall(r"(\w+)=(\([^()]*\)|[^,)\s]+)", parsed_line):
            k, v = kv
            if k == "value":
                try:
                    directive_value = int(v, 0)
                except ValueError:
                    directive_value = v
            elif k == "bit":
                try:
                    directive_bit = int(v, 0)
                except (ValueError, TypeError):
                    pass
            elif k == "field":
                directive_field = v

        # For write_subfield/read_subfield, validate field is present
        if read_write_set_clear in ("write_subfield", "read_subfield") and not directive_field:
            raise ValueError(f";#csr_rw({csr_spec}, {read_write_set_clear}) requires field= parameter")
        try:
            parsed_csr_val = self.pool.get_parsed_csr_access(csr_spec, read_write_set_clear, field=directive_field, force_machine_rw=force_machine_rw_line)
        except KeyError:
            log.warning(f"Could not find parsed csr access for {csr_spec}, {read_write_set_clear}, field={directive_field}")
            return ""

        priv_mode = "super" if parsed_csr_val.priv_mode == "supervisor" else parsed_csr_val.priv_mode
        csr_prio = priority.index(priv_mode)
        test_priv_mode = self.featmgr.priv_mode.name.lower()
        priv_mode_prio = priority.index(test_priv_mode)
        no_virtualized_on_hypervisor = parsed_csr_val.hypervisor and self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.priv_mode != RV.RiscvPrivileges.MACHINE

        # Resolve CSR via csr_manager for assembly generation
        csr_mgr: CsrManagerInterface
        os_module = self.runtime.modules.get("os")
        _csr_mgr_raw = getattr(os_module, "csr_manager", None) if os_module is not None else None
        if _csr_mgr_raw is None:
            csr_mgr = CsrManagerInterface(self.rng, feature_discovery=self.featmgr)
            if os_module is not None:
                os_module.csr_manager = csr_mgr
        else:
            csr_mgr = cast(CsrManagerInterface, _csr_mgr_raw)

        csr_config: dict[str, Any] | None
        if re.match(r"0[xX][0-9a-fA-F]+", str(csr_spec)) or (str(csr_spec).isdigit() and not str(csr_spec).startswith("0")):
            addr = int(csr_spec, 0) & 0xFFF
            csr_config = csr_mgr.lookup_csr_by_address(addr)
        else:
            csr_config = csr_mgr.lookup_csr_by_name(str(csr_spec))

        csr_name = list(csr_config.keys())[0] if csr_config else str(csr_spec)

        # Map set_bit/clear_bit to set/clear with value=1<<bit
        access_type = read_write_set_clear
        raw_value = directive_value
        value: int | None
        if isinstance(raw_value, str):
            try:
                value = int(raw_value, 0)
            except ValueError:
                value = None  # symbolic value (label); handled by the syscall value_insn (la/li)
        else:
            value = raw_value
        if read_write_set_clear == "set_bit" and directive_bit is not None:
            access_type = "set"
            value = 1 << directive_bit
        elif read_write_set_clear == "clear_bit" and directive_bit is not None:
            access_type = "clear"
            value = 1 << directive_bit

        subfield = {}
        if read_write_set_clear in ("write_subfield", "read_subfield") and directive_field:
            subfield = {directive_field: str(directive_value or 0)}

        use_syscall = (force_machine_rw_line and test_priv_mode != "machine") or ((priv_mode_prio < csr_prio or no_virtualized_on_hypervisor) and direct_rw != "true")

        if use_syscall:
            value_insn = ""
            if read_write_set_clear in ("set_bit", "clear_bit") and directive_bit is not None:
                value_insn = f"li t2, {1 << directive_bit}"
            elif read_write_set_clear in ("write_subfield", "write", "set", "clear") and directive_value is not None:
                value_insn = f"{self._csr_value_load(str(directive_value))} t2, {directive_value}"
            flag_name = "machine_csr_jump_table_flags" if priv_mode == "machine" or force_machine_rw_line else "super_csr_jump_table_flags"
            sys_call = "0xf0001006" if priv_mode == "super" and not force_machine_rw_line else "0xf0001005"
            # Emit the single jump-table stub source (runtime/macros.py); do not reimplement it here.
            macros_mod = cast("Macros", self.runtime.modules.get("macros"))
            return macros_mod.csr_ecall_stub(parsed_csr_val.csr_id, sys_call, flag_name, value_insn=value_insn) + "\n"

        # Direct access. csr_manager only loads integer values, so a symbolic value
        # (label, e.g. a trigger watchpoint addr) is loaded here via la/li into t2.
        if value is None and isinstance(raw_value, str) and access_type in ("write", "set", "clear"):
            return f"{self._csr_value_load(raw_value)} t2, {raw_value}\n" + self._emit_direct_csr("", csr_name, access_type)

        instr_helper = DtestInstructionHelper()
        if csr_config and access_type in ("read", "write", "set", "clear", "write_subfield", "read_subfield"):
            try:
                return csr_mgr.csr_access(
                    instr_helper,
                    access_type,
                    csr_config,
                    value=value,
                    rs="t2",
                    rd="t2",
                    subfield=subfield,
                )
            except Exception as e:
                log.warning(f"csr_manager.csr_access failed for {csr_name} {access_type}: {e}, falling back to direct")
                return self._emit_direct_csr("", csr_name, access_type)
        return self._emit_direct_csr("", csr_name, access_type)

    def find_and_replace_assembly(self, assembly_content: str) -> list[str]:
        """
        Handle overwritting text from input file to output assembly file. Currently handles:

        - replacing .section .code, with .section .code, "ax"
        - ;#init_memory
        - ;#csr_rw
        - ;#test_passed, ;#test_failed


        :param lines: List of lines from input file
        :return: List of lines with text replaced
        """
        init_mem_sections = self.pool.get_parsed_init_mem_addrs()
        section_code_found = False

        parsed_lines: list[str] = []
        in_pma_hint = False
        trigger_config_idx = 0
        trigger_disable_idx = 0
        trigger_enable_idx = 0
        # Sized from the indices actually used by this test's trigger directives;
        # must match OpSys's trigger_saved_tdata1 allocation (same Pool data).
        num_trigger_shadow_slots = self.pool.trigger_shadow_slot_count()
        for line in assembly_content.split("\n"):
            # Filter out PMA hint directives - they are processed by parser and should not appear in output
            # Handle multi-line directives
            stripped = line.strip()
            if stripped.startswith(";#pma_hint"):
                in_pma_hint = True
                continue
            if in_pma_hint:
                # Continue skipping until we find the closing parenthesis
                if ")" in stripped and not stripped.startswith("#"):
                    in_pma_hint = False
                continue

            # replace .section .code, with .section .code, "ax" or "aw"
            if ".section .code," in line and not section_code_found:
                section_code_found = True
                line = '.section .code, "ax"' if not (self.featmgr.single_assembly_file and not self.featmgr.wysiwyg and len(self.featmgr.hooks) == 0) else '.section .code, "aw"'

            # replace ;#init_memory with .section .lin_name, "aw" or .section .lin_name, "ax"
            if line.startswith(";#init_memory"):
                lin_name_map = Parser.separate_lin_name_map(line)
                maps: list[str] = lin_name_map[1]
                found = False
                # Extract the name after @ so we match exactly (e.g. code_mem42 not code_mem4).
                # Use [^\s:]+ so names with dots (e.g. FADD.D_0_..._lin_aux) are captured fully.
                init_mem_match = re.search(r"@([^\s:]+)", line)
                target_lin_name = init_mem_match.group(1) if init_mem_match else None
                for lin_name in init_mem_sections:
                    if len(maps) == 0:
                        if target_lin_name is not None and lin_name == target_lin_name:
                            section_lin_name = self.pool.resolve_canonical_lin_name(lin_name, "map_os")
                            permissions = "aw"
                            if self.pool.parsed_page_mapping_exists(section_lin_name, "map_os") and self.pool.get_parsed_page_mapping(section_lin_name, "map_os").x:
                                permissions = "ax"
                            print_lin_name = section_lin_name
                            if section_lin_name.startswith("0x"):
                                print_lin_name = self.prefix_hex_lin_name(section_lin_name)
                            line = line + "\n" + f'.section .{print_lin_name}, "{permissions}"\n'
                            break
                    else:
                        for m in maps:
                            lin_name_2 = (line.split(":")[0]).split("@")[1].strip()
                            if lin_name == (lin_name_2 + "_" + m):
                                permissions = "aw"
                                if self.pool.parsed_page_mapping_exists(lin_name, m) and self.pool.get_parsed_page_mapping(lin_name, m).x:
                                    permissions = "ax"
                                print_lin_name = lin_name
                                if lin_name.startswith("0x"):
                                    print_lin_name = self.prefix_hex_lin_name(lin_name)
                                line = line + "\n" + f'.section .{print_lin_name}, "{permissions}"\n'
                                found = True
                                break
                            if found:
                                break

            # replace ;#trigger_config with spec-mandated tselect/tdata1/tdata2 sequence
            parsed_line = line.strip()
            if parsed_line.startswith(";#trigger_config("):
                configs = self.pool.get_parsed_trigger_configs()
                if trigger_config_idx < len(configs) and self.featmgr.is_feature_supported("sdtrig") and self.featmgr.is_feature_enabled("sdtrig"):
                    cfg = configs[trigger_config_idx]

                    # Build tdata1 and determine whether tdata2 is needed
                    if cfg.trigger_type == TriggerType.ICOUNT:
                        tdata1_val = build_tdata1_icount(cfg.count, cfg.action, cfg.priv_mode, cfg.pending)
                        needs_tdata2 = False
                    elif cfg.trigger_type == TriggerType.ITRIGGER:
                        tdata1_val = build_tdata1_itrigger(cfg.action, cfg.priv_mode)
                        needs_tdata2 = True  # tdata2 = interrupt cause bitmask (cfg.addr)
                    elif cfg.trigger_type == TriggerType.ETRIGGER:
                        tdata1_val = build_tdata1_etrigger(cfg.action, cfg.priv_mode)
                        needs_tdata2 = True  # tdata2 = exception cause bitmask (cfg.addr)
                    else:
                        tdata1_val = build_tdata1_mcontrol6(
                            cfg.trigger_type,
                            cfg.action,
                            cfg.size,
                            cfg.chain,
                            cfg.match,
                            cfg.priv_mode,
                        )
                        needs_tdata2 = True

                    # Route every trigger CSR write through the ;#csr_rw API.
                    code = f"# ;#trigger_config(index={cfg.index}, type={cfg.trigger_type.value})\n"
                    code += self._expand_csr_rw(f";#csr_rw(tselect, write, force_machine=true, value={cfg.index})")
                    code += self._expand_csr_rw(";#csr_rw(tdata1, write, force_machine=true, value=0)")
                    if needs_tdata2:
                        # tdata2 = watchpoint addr (mcontrol6) or cause bitmask (i/etrigger);
                        # _expand_csr_rw's value load picks li/la (numeric/random_addr/expr vs label).
                        code += self._expand_csr_rw(f";#csr_rw(tdata2, write, force_machine=true, value={cfg.addr})")
                    # Shadow the armed value into trigger_saved_tdata1[index] so a later
                    # ;#trigger_enable for this index restores exactly what was configured on
                    # the path actually taken at runtime. Materialize tdata1_val in t2 for the
                    # shadow store; the tdata1 write below reloads it via _expand_csr_rw.
                    if cfg.index < num_trigger_shadow_slots:
                        code += f"li t2, 0x{tdata1_val:x}\n"
                        code += f"li x31, trigger_saved_tdata1+{cfg.index * 8}\nsd t2, 0(x31)\n"
                    code += self._expand_csr_rw(f";#csr_rw(tdata1, write, force_machine=true, value=0x{tdata1_val:x})")
                    line = line + "\n" + code
                trigger_config_idx += 1
                parsed_lines.append(line)
                continue

            # replace ;#trigger_disable with tselect + tdata1=0
            if parsed_line.startswith(";#trigger_disable("):
                disables = self.pool.get_parsed_trigger_disable()
                if trigger_disable_idx < len(disables) and self.featmgr.is_feature_supported("sdtrig") and self.featmgr.is_feature_enabled("sdtrig"):
                    cfg = disables[trigger_disable_idx]
                    csr_acc = self.pool.get_parsed_csr_accesses()
                    tselect_acc = csr_acc.get("tselect", {}).get("write_force_machine")
                    tdata1_acc = csr_acc.get("tdata1", {}).get("write_force_machine")
                    if tselect_acc and tdata1_acc:
                        code = f"# ;#trigger_disable(index={cfg.index})\n"
                        code += self._expand_csr_rw(f";#csr_rw(tselect, write, force_machine=true, value={cfg.index})")
                        code += self._expand_csr_rw(";#csr_rw(tdata1, write, force_machine=true, value=0)")
                        line = line + "\n" + code
                trigger_disable_idx += 1
                parsed_lines.append(line)
                continue

            # replace ;#trigger_enable - re-arm tdata1 from the runtime shadow
            if parsed_line.startswith(";#trigger_enable("):
                enables = self.pool.get_parsed_trigger_enable()
                if trigger_enable_idx < len(enables) and self.featmgr.is_feature_supported("sdtrig") and self.featmgr.is_feature_enabled("sdtrig"):
                    cfg = enables[trigger_enable_idx]
                    csr_acc = self.pool.get_parsed_csr_accesses()
                    tdata1_acc = csr_acc.get("tdata1", {}).get("write_force_machine")
                    tselect_acc = csr_acc.get("tselect", {}).get("write_force_machine")
                    if tdata1_acc and tselect_acc:
                        flag_name = "machine_csr_jump_table_flags"
                        sys_call = "0xf0001005"
                        code = f"# ;#trigger_enable(index={cfg.index})\n"
                        # tselect = index
                        code += f"li t2, {cfg.index}\nli x31, {flag_name}\nli t0, {tselect_acc.csr_id}\nsd t0, 0(x31)\nli x31, {sys_call}\necall\n"
                        if cfg.index < num_trigger_shadow_slots:
                            # Re-arm tdata1 with the value ;#trigger_config last stored in the
                            # shadow at runtime. This restores whichever config executed on the
                            # path actually taken, even when this enable is a join point reached
                            # from multiple configs (of possibly different trigger types) via
                            # branches -- a case that cannot be resolved at generation time.
                            code += f"li x31, trigger_saved_tdata1+{cfg.index * 8}\nld t2, 0(x31)\n"
                            code += f"li x31, {flag_name}\n" f"li t0, {tdata1_acc.csr_id}\nsd t0, 0(x31)\n" f"li x31, {sys_call}\necall\n"
                            line = line + "\n" + code
                        else:
                            raise RuntimeError(f"Trigger index {cfg.index} is out of range and was undetected")
                trigger_enable_idx += 1
                parsed_lines.append(line)
                continue


            # replace ;#csr_rw with csrr/csrw instructions or system call to jump table
            if parsed_line.startswith(";#csr_rw"):
                line = line + "\n" + self._expand_csr_rw(parsed_line)

            # replace ;#read_pte with syscall to walk handler
            # An optional trailing "napot_offset=N" (N in 0..15) addresses the Nth 4K
            # sub-page PTE of an Svnapot (64KB) page. The byte offset N*0x1000 is passed
            # in pte_access_flags[3]; the walker applies it to the VA (v-stage walk) or
            # to the intermediate GPA (g-stage walk) depending on whether g_level is set.
            # lin_name is still used for level resolution.
            parsed_line = line.strip()
            if parsed_line.startswith(";#read_pte"):
                pattern = r"^;#read_pte\((?P<lin_name>\w+),\s*(?P<level>\d+|leaf|nonleaf|final)(?:,\s*(?P<g_level>\d+|leaf|nonleaf))?(?:,\s*napot_offset=(?P<napot_offset>\d+))?\)"
                match = re.match(pattern, parsed_line)
                if match:
                    lin_name = match.group("lin_name")
                    level, g_level = self._resolve_pte_levels(lin_name, match.group("level"), match.group("g_level"))
                    napot_offset = match.group("napot_offset")

                    # Generate code to store VA, level, and g_level into pte_access_flags, then ecall
                    g_level_value = g_level if g_level is not None else -1
                    napot_bytes = int(napot_offset) * 0x1000 if napot_offset is not None else 0
                    code_to_replace = "li x31, pte_access_flags\n"
                    code_to_replace += f"li t0, {lin_name}\n"
                    code_to_replace += "sd t0, 0(x31)\n"  # [0] = VA
                    code_to_replace += f"li t0, {level}\n"
                    code_to_replace += "sd t0, 8(x31)\n"  # [1] = level
                    code_to_replace += f"li t0, {g_level_value}\n"
                    code_to_replace += "sd t0, 16(x31)\n"  # [2] = g_level (-1 = no g-stage)
                    code_to_replace += f"li t0, {napot_bytes}\n"
                    code_to_replace += "sd t0, 24(x31)\n"  # [3] = Svnapot sub-page byte offset
                    code_to_replace += "li x31, 0xf0001007\n"
                    code_to_replace += "ecall\n"

                    line = line + "\n" + code_to_replace

            # replace ;#write_pte with syscall to walk handler (see ;#read_pte above
            # for the napot_offset semantics)
            parsed_line = line.strip()
            if parsed_line.startswith(";#write_pte"):
                pattern = r"^;#write_pte\((?P<lin_name>\w+),\s*(?P<level>\d+|leaf|nonleaf|final)(?:,\s*(?P<g_level>\d+|leaf|nonleaf))?(?:,\s*napot_offset=(?P<napot_offset>\d+))?\)"
                match = re.match(pattern, parsed_line)
                if match:
                    lin_name = match.group("lin_name")
                    level, g_level = self._resolve_pte_levels(lin_name, match.group("level"), match.group("g_level"))
                    napot_offset = match.group("napot_offset")

                    # Generate code to store VA, level, and g_level into pte_access_flags, then ecall
                    # t2 should already contain the value to write before the ecall
                    g_level_value = g_level if g_level is not None else -1
                    napot_bytes = int(napot_offset) * 0x1000 if napot_offset is not None else 0
                    code_to_replace = "li x31, pte_access_flags\n"
                    code_to_replace += f"li t0, {lin_name}\n"
                    code_to_replace += "sd t0, 0(x31)\n"  # [0] = VA
                    code_to_replace += f"li t0, {level}\n"
                    code_to_replace += "sd t0, 8(x31)\n"  # [1] = level
                    code_to_replace += f"li t0, {g_level_value}\n"
                    code_to_replace += "sd t0, 16(x31)\n"  # [2] = g_level (-1 = no g-stage)
                    code_to_replace += f"li t0, {napot_bytes}\n"
                    code_to_replace += "sd t0, 24(x31)\n"  # [3] = Svnapot sub-page byte offset
                    code_to_replace += "li x31, 0xf0001008\n"
                    code_to_replace += "ecall\n"

                    line = line + "\n" + code_to_replace

            parsed_line = line.strip()
            if parsed_line.startswith(";#test_passed"):
                # runtime should probably be the one generating this code
                # maybe make a public method on runtime to generate this code.
                replaced_lines = self.runtime.test_passed()

                line = line + "\n\t" + "\n\t".join(replaced_lines)
            if parsed_line.startswith(";#test_failed"):
                replaced_lines = self.runtime.test_failed()
                line = line + "\n\t" + "\n\t".join(replaced_lines)

            parsed_lines.append(line)

        return parsed_lines

    def generate_runtime_sections(self) -> list[str]:
        """
        Generate and write pagetable and all runtime sections.
        This calls all :class:`Runtime` modules to generate code, and should be called before :meth:`generate_equates_section`

        :return: List of strings containing pagetables and runtime sections
        """

        generated_sections: list[str] = []
        # write pagetables into generated section
        if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE or self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            pagetables_assembly = "\n".join(self._generate_pagetable_assembly())

            if self.single_assembly_file:
                generated_sections.append("\n## pagetables ##\n" + pagetables_assembly)
            else:
                pagetables_inc_file = self.run_dir / f"{self.testname}_pagetables.inc"
                with open(pagetables_inc_file, "w") as f:
                    f.write(pagetables_assembly + "\n")
                generated_sections.append(f'\n.include "{pagetables_inc_file.name}"\n')

        # generate runtime includes, adding to start of file
        runtime_sections: list[str] = []
        for runtime_name, runtime_code in self.runtime.generate():
            runtime_assembly = "\n".join(runtime_code)

            if self.single_assembly_file:
                runtime_sections.append(f"\n## {runtime_name} ##\n" + runtime_assembly)
            else:
                include_file = self.run_dir / f"{self.testname}_{runtime_name}.inc"
                with open(include_file, "w") as f:
                    f.write(runtime_assembly + "\n")
                runtime_sections.append(f'#include "{include_file.name}"\n')

        if self.featmgr.c_used:
            runtime_sections.append(
                """
            .balign 16, 0
            __c__stack_addr:
                .dword __c__stack
            """
            )

        # Debug ROM section: ;#discrete_debug_test() body + DRET epilogue
        debug_rom_section = self._generate_debug_rom_section()
        if debug_rom_section:
            runtime_sections.append(debug_rom_section)

        return generated_sections + runtime_sections

    # Assembly emitted when user writes ;#dret in ;#discrete_debug_test() body (restore DPC, return from debug mode)
    _DEBUG_ROM_DRET_ASM = """
    # ;#dret: restore DPC from dscratch0 and return from debug mode
    csrr t0, dscratch0
    csrw dpc, t0
    dret
"""

    def _generate_debug_rom_section(self) -> str:
        """
        Generate .debug_rom section when debug_mode is enabled and ;#discrete_debug_test() is present.
        DCSR is only writable in debug mode: ISS (e.g. whisper_config.json) should reset DCSR to M-mode;
        the prologue here sets DCSR.prv to test_priv so the debug test runs in the same mode as the main test.
        Contains prologue (set DCSR.prv) + user body. The user exits debug mode via the ;#dret directive,
        which expands to restore DPC from dscratch0 and dret (multiple code paths supported).
        """
        if not self.featmgr.debug_mode:
            return ""
        body_lines = self.pool.get_parsed_discrete_debug_test()
        if body_lines is None:
            return ""
        # DCSR.prv (bits 1:0): 0=U, 1=S, 3=M. Set to test privilege so debug test runs in same mode as main test.
        if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
            prv_val = 3
        elif self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
            prv_val = 1
        else:
            prv_val = 0
        prologue = f"""
    # Prologue: set DCSR.prv to test_priv (DCSR only writable in debug mode)
    csrr t0, dcsr
    li t1, 0xfffffffffffffffc
    and t0, t0, t1
    addi t0, t0, {prv_val}
    csrw dcsr, t0
"""
        # Expand ;#dret directive to restore DPC + dret; no automatic epilogue so user can have multiple exit paths
        dret_asm = self._DEBUG_ROM_DRET_ASM.strip()
        body_parts: list[str] = []
        for line in body_lines:
            if line.strip() == ";#dret":
                body_parts.append(dret_asm)
            else:
                body_parts.append(line)
        body = "\n".join(body_parts) if body_parts else "    nop"
        return f"""
.section .debug_rom, "ax"
.globl discrete_debug_test_entry
discrete_debug_test_entry:
{prologue}
{body}
"""

    def generate_aplic_imsic_assembly(self) -> list[str]:
        """
        Generate APLIC/IMSIC related constants
        """
        ret: list[str] = []
        aplic_assembly_inc_content: str = ""
        if self.featmgr.io_maplic_addr is not None:
            aplic_assembly_inc_content += f".equ MAPLIC_MMR_BASE_ADDR, {hex(self.featmgr.io_maplic_addr)}\n"
        if self.featmgr.io_saplic_addr is not None:
            aplic_assembly_inc_content += f".equ SAPLIC_MMR_BASE_ADDR, {hex(self.featmgr.io_saplic_addr)}\n"
        if self.featmgr.io_imsic_mfile_addr is not None:
            aplic_assembly_inc_content += f".equ IMSIC_MFILE_BASE_ADDR, {hex(self.featmgr.io_imsic_mfile_addr)}\n"
        if self.featmgr.io_imsic_mfile_stride is not None:
            aplic_assembly_inc_content += f".equ IMSIC_MFILE_STRIDE, {hex(self.featmgr.io_imsic_mfile_stride)}\n"
        if self.featmgr.io_imsic_sfile_addr is not None:
            aplic_assembly_inc_content += f".equ IMSIC_SFILE_BASE_ADDR, {hex(self.featmgr.io_imsic_sfile_addr)}\n"
        if self.featmgr.io_imsic_sfile_stride is not None:
            aplic_assembly_inc_content += f".equ IMSIC_SFILE_STRIDE, {hex(self.featmgr.io_imsic_sfile_stride)}\n"

        if aplic_assembly_inc_content:
            aplic_assembly_inc = f"{self.testname}_aplic_imsic.inc"
            aplic_assembly_path = self.run_dir / aplic_assembly_inc
            aplic_assembly_inc_content += """

.section .runtime, "ax"

.balign 16, 0

# __set_maplic_eidelivery(interrrupt_delivery)
# interrupt_delivery == 0 => interrupt delivery is disabled
# interrupt_delivery == 1 => interrupt delivery is enabled
# The argument is not checked for correctness and any other
# value may have an undetermined effect
#
.globl __set_maplic_eidelivery
__set_maplic_eidelivery:
    li t0, 0x70
    csrw miselect, t0
    csrw mireg, a0
    ret

# __set_saplic_eidelivery(interrrupt_delivery)
# interrupt_delivery == 0 => interrupt delivery is disabled
# interrupt_delivery == 1 => interrupt delivery is enabled
# The argument is not checked for correctness and any other
# value may have an undetermined effect
#
.globl __set_saplic_eidelivery
__set_saplic_eidelivery:
    li t0, 0x70
    csrw siselect, t0
    csrw sireg, a0
    ret

# __set_maplic_eithreshold(interrrupt_threshold)
#
.globl __set_maplic_eithreshold
__set_maplic_eithreshold:
    li t0, 0x72
    csrw miselect, t0
    csrw mireg, a0
    ret

# __set_saplic_eithreshold(interrrupt_threshold)
#
.globl __set_saplic_eithreshold
__set_saplic_eithreshold:
    li t0, 0x72
    csrw siselect, t0
    csrw sireg, a0
    ret

# __set_maplic_eie(intr_num, intr_val)
# Set the interrupt intr_num to intr_val (0 or 1),
# where 0 indicates that interrupt is disabled
# and 1 enabled. Any other value may have an
# undetermined effect
#
.globl __set_maplic_eie
__set_maplic_eie:
    li t0, 64
    divw t0, a0, t0
    addiw t0, t0, 96
    slliw t0, t0, 1
    csrw miselect, t0
    csrr t1, mireg
    sllw a1, a1, a0
    bset t0, x0, a0
    andn t0, t1, t0
    or a1, a1, t0
    csrw mireg, a1
    ret

# __set_saplic_eie(intr_num, intr_val)
# Set the interrupt intr_num to intr_val (0 or 1),
# where 0 indicates that interrupt is disabled
# and 1 enabled. Any other value may have an
# undetermined effect
#
.globl __set_saplic_eie
__set_saplic_eie:
    li t0, 64
    divw t0, a0, t0
    addiw t0, t0, 96
    slliw t0, t0, 1
    csrw siselect, t0
    csrr t1, sireg
    sllw a1, a1, a0
    bset t0, x0, a0
    andn t0, t1, t0
    or a1, a1, t0
    csrw sireg, a1
    ret


# __set_maplic_eip(intr_num, intr_val)
# Set the interrupt intr_num to intr_val (0 or 1),
# where 0 indicates that interrupt is disabled
# and 1 enabled. Any other value may have an
# undetermined effect
#
.globl __set_maplic_eip
__set_maplic_eip:
    li t0, 64
    divw t0, a0, t0
    addiw t0, t0, 64
    slliw t0, t0, 1
    csrw miselect, t0
    csrr t1, mireg
    sllw a1, a1, a0
    bset t0, x0, a0
    andn t0, t1, t0
    or a1, a1, t0
    csrw mireg, a1
    ret

# __set_saplic_eip(intr_num, intr_val)
# Set the interrupt intr_num to intr_val (0 or 1),
# where 0 indicates that interrupt is disabled
# and 1 enabled. Any other value may have an
# undetermined effect
#
.globl __set_saplic_eip
__set_saplic_eip:
    li t0, 64
    divw t0, a0, t0
    addiw t0, t0, 64
    slliw t0, t0, 1
    csrw siselect, t0
    csrr t1, sireg
    sllw a1, a1, a0
    bset t0, x0, a0
    andn t0, t1, t0
    or a1, a1, t0
    csrw sireg, a1
    ret

# __set_maplic_domaincfg_ie(val)
# Set the IE bit in domaincfg. Valid values
# are 0 or 1.
.globl __set_maplic_domaincfg_ie
__set_maplic_domaincfg_ie:
    la t0, MAPLIC_MMR_BASE_ADDR + 0
    lw t1, 0(t0)
    bclri t1, t1, 8
    slli a1, a1, 8
    or t1, t1, a1
    sw t1, 0(t0)
    ret

# __set_saplic_domaincfg_ie(val)
# Set the IE bit in domaincfg. Valid values
# are 0 or 1.
.globl __set_saplic_domaincfg_ie
__set_saplic_domaincfg_ie:
    la t0, SAPLIC_MMR_BASE_ADDR + 0
    lw t1, 0(t0)
    bclri t1, t1, 8
    slli a1, a1, 8
    or t1, t1, a1
    sw t1, 0(t0)
    ret

# __set_maplic_domaincfg_dm(val)
# Set the DM bit in domaincfg. Valid values
# are 0 or 1.
.globl __set_maplic_domaincfg_dm
__set_maplic_domaincfg_dm:
    la t0, MAPLIC_MMR_BASE_ADDR + 0
    lw t1, 0(t0)
    bclri t1, t1, 2
    slli a0, a0, 2
    or t1, t1, a0
    sw t1, 0(t0)
    ret

# __set_saplic_domaincfg_dm(val)
# Set the DM bit in domaincfg. Valid values
# are 0 or 1.
.globl __set_saplic_domaincfg_dm
__set_saplic_domaincfg_dm:
    la t0, SAPLIC_MMR_BASE_ADDR + 0
    lw t1, 0(t0)
    bclri t1, t1, 2
    slli a0, a0, 2
    or t1, t1, a0
    sw t1, 0(t0)
    ret

# __set_maplic_sourcecfg_sm(source, val)
# Set the SM field in sourcecfg to the given
# value. It's a 3 bit field and no checks are
# made for larger or reserved values.
.globl __set_maplic_sourcecfg_sm
__set_maplic_sourcecfg_sm:
    la t0, MAPLIC_MMR_BASE_ADDR
    slli a0, a0, 2
    add t0, a0, a0
    sw a1, 0(t0)
    ret

# __set_saplic_sourcecfg_sm(source, val)
# Set the SM field in sourcecfg to the given
# value. It's a 3 bit field and no checks are
# made for larger or reserved values.
.globl __set_saplic_sourcecfg_sm
__set_saplic_sourcecfg_sm:
    la t0, SAPLIC_MMR_BASE_ADDR
    slli a0, a0, 2
    add t0, a0, a0
    sw a1, 0(t0)
    ret

# __set_maplic_sourcecfg_childindex(source, val)
.globl __set_maplic_sourcecfg_childindex
__set_maplic_sourcecfg_childindex:
    la t0, MAPLIC_MMR_BASE_ADDR
    slli a0, a0, 2
    add t0, a0, a0
    bset a1, a1, 10 # set deleg
    sw a1, 0(t0)
    ret

# __set_saplic_sourcecfg_childindex(source, val)
.globl __set_saplic_sourcecfg_childindex
__set_saplic_sourcecfg_childindex:
    la t0, SAPLIC_MMR_BASE_ADDR
    slli a0, a0, 2
    add t0, a0, a0
    bset a1, a1, 10 # set deleg
    sw a1, 0(t0)
    ret

# __set_maplic_target_hartindex(target, val)
.globl __set_maplic_target_hartindex
__set_maplic_target_hartindex:
    la t0, MAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    slli a1, a1, 18
    slli t0, t0, 46
    srli t0, t0, 46
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_saplic_target_hartindex(target, val)
.globl __set_saplic_sourcecfg_hartindex
__set_saplic_target_hartindex:
    la t0, SAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    slli a1, a1, 18
    slli t0, t0, 46
    srli t0, t0, 46
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_maplic_target_intpri(target, val)
.globl __set_maplic_target_intpri
__set_maplic_target_intpri:
    la t0, MAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    andi t0, t0, -256
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_saplic_target_intpri(target, val)
.globl __set_saplic_sourcecfg_intpri
__set_saplic_target_intpri:
    la t0, SAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    andi t0, t0, -256
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_maplic_target_guestindex(target, val)
.globl __set_maplic_target_guestindex
__set_maplic_target_guestindex:
    la t0, MAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    li t1, 0xfffc0fff
    and t0, t0, t1
    slli a1, a1, 12
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_saplic_target_guestindex(target, val)
.globl __set_saplic_sourcecfg_guestindex
__set_saplic_target_guestindex:
    la t0, SAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    li t1, 0xfffc0fff
    and t0, t0, t1
    slli a1, a1, 12
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_maplic_target_eiid(target, val)
.globl __set_maplic_target_eiid
__set_maplic_target_eiid:
    la t0, MAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    andi t0, t0, -2048
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_saplic_target_eiid(target, val)
.globl __set_saplic_sourcecfg_eiid
__set_saplic_target_eiid:
    la t0, SAPLIC_MMR_BASE_ADDR + 0x3000
    sh2add a0, a0, t0
    lw t0, 0(a0)
    andi t0, t0, -2048
    or t0, t0, a1
    sw t0, 0(a0)
    ret

# __set_aplic_isr(target, isr)
# Loads the table base via an .os_data pointer (registered by the M-mode trap
# handler) instead of ``la``, which can't reach .data across the >2GiB gap
# in high-VA paged layouts. PA equate because this runs in M-mode setup.
.globl __set_aplic_isr
__set_aplic_isr:
    li t0, trap_handler_m__aplic_isr_table_ptr_pa
    ld t0, 0(t0)
    slli a0, a0, 3
    add a0, a0, t0
    sw a1, 0(a0)
    ret

            """
            with open(aplic_assembly_path, "w") as f:
                f.write(aplic_assembly_inc_content)
            f.close()
            ret.append(f'.include "{aplic_assembly_inc}"\n')

        return ret

    def generate_data_section(self) -> list[str]:
        """
        Generate any data section
        """
        ret: list[str] = []
        if self.featmgr.c_used:
            data_section = """
.section .bss
.size __c__stack_low__, 4096
__c__stack_low__:
.zero 4096
__c__stack:
            """
            ret.append(data_section)

        return ret

    def generate_equates_section(self) -> list[str]:
        """
        Generate the equates section, return all code in a list of strings.

        ..note::

            This *must* be called last, since :class:`Runtime` needs to populate the equates sections
        """
        # generate equates, this needs to be done last but appear first in the file for li instructions to work
        equates_section: list[str] = []

        equates_assembly = "\n".join(self._generate_equates_assembly())
        if self.single_assembly_file:
            equates_section.append("## equates ##\n" + equates_assembly)
        else:
            equates_inc_file = self.run_dir / f"{self.testname}_equates.inc"
            with open(equates_inc_file, "w") as equates_file:
                equates_file.write(equates_assembly + "\n")
            equates_section.append(f'.include "{equates_inc_file.name}"\n')
        return equates_section

    def create_linker_script(self, linker_script: Path) -> None:
        """
        Generates and writes linker script to passed path

        :param linker_script: Path to write linker script to
        """
        with open(linker_script, "w") as linker_file:
            linker_file.write('OUTPUT_ARCH("riscv")\nENTRY(_start)\n')

            # Build section list with resolved names, VMAs, and LMAs
            sections: list[tuple[str, int, int]] = []
            for section_name, section_info in self.pool.get_sections().items():
                vma = section_info.vma
                lma = section_info.lma
                print_section_name = section_name
                if section_name == "code":
                    vma = vma + self.code_offset
                    lma = lma + self.code_offset
                if section_name.startswith("0x"):
                    print_section_name = self.prefix_hex_lin_name(section_name)
                # Clear bit-55 if it's set since the physical address space does not use it
                if lma & (1 << 55):
                    lma = lma & ~(1 << 55)
                log.debug(f"{section_name} -> vma=0x{vma:016x}, lma=0x{lma:016x}")
                sections.append((print_section_name, vma, lma))

            # Group contiguous sections into the same segment.  Two adjacent
            # sections are contiguous when *both* their VMAs and LMAs are
            # exactly one page (4 KB) apart, so the linker can pack them into
            # a single LOAD segment without file-offset gaps.
            PAGE_SIZE = 0x1000
            seg_indices: list[int] = []
            seg_id = 0
            for i, (_, vma, lma) in enumerate(sections):
                if i == 0:
                    seg_indices.append(seg_id)
                else:
                    prev_vma = sections[i - 1][1]
                    prev_lma = sections[i - 1][2]
                    if vma - prev_vma == PAGE_SIZE and lma - prev_lma == PAGE_SIZE:
                        # Contiguous with previous section — same segment
                        seg_indices.append(seg_id)
                    else:
                        seg_id += 1
                        seg_indices.append(seg_id)
            num_segments = seg_id + 1 if sections else 0

            # Emit PHDRS block — one PT_LOAD per segment group, none with
            # FILEHDR/PHDRS so the ELF/program headers are not mapped into
            # any loadable segment.
            linker_file.write("PHDRS\n{\n")
            for sid in range(num_segments):
                linker_file.write(f"\tseg{sid} PT_LOAD;\n")
            linker_file.write("}\n")

            # Emit SECTIONS block — each section assigned to its segment group.
            # Express LMA as (lma + ADDR(.name) - vma) so the LMA tracks any
            # VMA bump ld applies for the section's sh_addralign (raised by
            # .align/.balign/.p2align inside the section) while preserving the
            # intended (lma - vma) delta. A literal AT(lma) would stay pinned
            # and produce a VMA/LMA skew.
            linker_file.write("SECTIONS\n{\n")
            for i, (print_section_name, vma, lma) in enumerate(sections):
                linker_file.write(
                    "\t. = {vma};\n\t .{name} : AT({lma} + ADDR(.{name}) - {vma}) {{ *(."
                    "{name}) }} :seg{sid}\n\n".format(
                        vma=hex(vma),
                        name=print_section_name,
                        lma=hex(lma),
                        sid=seg_indices[i],
                    )
                )
            linker_file.write("}")

    # helper functions:
    def prefix_hex_lin_name(self, n: str) -> str:
        "Helper function to prefix hex linear names with __auto_secname_"
        return f"__auto_secname_{n}"

    def _generate_pagetable_assembly(self) -> list[str]:
        """
        Emit the page-table assembly from the RieMap :class:`AllocationResult`.

        RieMap already built every tree during allocation; RiescueD consumes the
        result and formats it -- it does not rebuild the tables. Emit VS/single-stage
        trees before G-stage trees to match the historical section ordering.

        :return: List of strings containing pagetables assembly
        """
        result = self.pool.allocation_result
        if result is None:
            return []

        pagetable_content: list[str] = []
        spaces = list(result.spaces())
        for space in spaces:
            if not space.is_gstage and space.paging_mode != RV.RiscvPagingModes.DISABLE:
                pagetable_content += self._emit_space_tables(space)
        for space in spaces:
            if space.is_gstage:
                pagetable_content += self._emit_space_tables(space)
        return pagetable_content

    @staticmethod
    def _pagetable_section_name(map_name: str, addr: int) -> str:
        """RiescueD's linker section name for a page-table node. The core yields
        structured records; this is where RiescueD's assembly naming convention lives."""
        return f"__pagetable_{map_name}_0x{addr:016x}"

    def _emit_space_tables(self, space: SpaceResult) -> list[str]:
        """Format one space's built page-table nodes as assembly and register their sections.

        RieMap's ``TableView`` carries the ``Space`` object, not a name -- look up
        RiescueD's own map name from ``pool.page_translation`` (the object-keyed
        ``Translation`` the generator built alongside the ``AllocationResult``)."""
        lines: list[str] = []
        translation = self.pool.page_translation
        map_name = translation.space_names.get(space.space, "unknown") if translation is not None else "unknown"
        for table in space.tables():
            section_name = self._pagetable_section_name(map_name, table.addr)
            lines.append(f'.section .{section_name}, "aw"')
            self.pool.add_section(section_name=section_name, address=table.addr)
            lines.append(f"{section_name}:\n.globl {section_name}")
            entry_size_str = "" if table.entry_size == 0 else f"{table.entry_size}"
            for entry in table.entries:
                offset = table.entry_size * entry.index
                base_addr = (entry.value >> 10) << 12  # PPN of the child table / mapped page
                lines.append(f"    .org 0x{offset:x}")
                lines.append(f"        .{entry_size_str}byte 0x{entry.value:016x}")
                lines.append(f"            #level: {entry.level}: index: 0x{entry.index:x}, base_addr: 0x{base_addr:016x}, value: {entry.value:016x}")
                lines.append(f"            #attrs: {entry.attrs}")
        lines += self._page_debug_comments(space, map_name)
        return lines

    def _page_debug_comments(self, space: SpaceResult, map_name: str) -> list[str]:
        """Per-page debug comments (name -> address, pagesize) for one space, read
        straight from the AllocationResult + the object-keyed Translation (no more
        walker PageMap to iterate -- riemap owns the tree, RiescueD only names it)."""
        lines: list[str] = ["\n#=========================================================="]
        lines.append(f"#printing pagetables for debug: map: {map_name}, base_addr: {space.root_addr:016x}, paging_mode: {space.paging_mode}")
        lines.append("#==========================================================")
        result = self.pool.allocation_result
        translation = self.pool.page_translation
        if result is not None and translation is not None:
            for page, (lin_name, phys_name) in translation.page_names.items():
                if page.space is not space.space:
                    continue
                va, pa = result.address_of(page)
                pagesize = result.page_meta(page).pagesize
                lines.append(f"\n#Page: {lin_name}, 0x{va:016x} -> {phys_name}, 0x{pa:016x}, pagesize:{pagesize}")
        return lines

    def _resolve_pte_levels(
        self,
        lin_name: str,
        level_str: str,
        g_level_str: str | None,
    ) -> tuple[int, int | None]:
        """Resolve symbolic 'leaf'/'nonleaf'/'final' level strings to integer PTE level indices.

        :param lin_name: The linear name of the mapping (used to look up pagesize).
        :param level_str: The level string from the directive: a decimal integer, 'leaf', 'nonleaf', or 'final'.
        :param g_level_str: The g-level string from the directive: a decimal integer, 'leaf', 'nonleaf', or None.
        :returns: Tuple of (level, g_level) as integers (g_level is None when not specified).
        :raises ValueError: On invalid combinations (e.g. 'final' without g_level).
        """
        page = self._get_page_for_addr_name(lin_name)
        page_meta = self.pool.allocation_result.page_meta(page) if page is not None and self.pool.allocation_result is not None else None
        paging_mode = page.space.paging_mode if page is not None else self.featmgr.paging_mode

        if level_str in ("leaf", "nonleaf", "final"):
            if page_meta is None:
                raise ValueError(f";#read_pte/;#write_pte: cannot resolve '{level_str}' — no page found for '{lin_name}'")
            pagesize: RV.RiscvPageSizes = page_meta.pagesize
            leaf_level = RV.RiscvPagingModes.max_levels(paging_mode) - 1 - RV.RiscvPageSizes.pt_leaf_level(pagesize)
            if level_str == "leaf":
                level = leaf_level
            elif level_str == "nonleaf":
                level = leaf_level - 1  # one level above the leaf
            else:  # "final"
                if g_level_str is None:
                    raise ValueError(f";#read_pte/;#write_pte: level='final' requires a g_level argument for '{lin_name}'")
                level = leaf_level + 1
        else:
            level = int(level_str)

        g_level: int | None = None
        if g_level_str is not None:
            if g_level_str in ("leaf", "nonleaf"):
                if page_meta is None:
                    raise ValueError(f";#read_pte/;#write_pte: cannot resolve g_level='{g_level_str}' — no page found for '{lin_name}'")
                g_paging_mode = self.featmgr.paging_g_mode
                if level_str == "final":
                    g_pagesize = page_meta.gstage_vs_leaf_pagesize
                    if g_pagesize is None:
                        raise ValueError(f";#read_pte/;#write_pte: cannot resolve g_level='{g_level_str}' — gstage_vs_leaf_pagesize not set for '{lin_name}'")
                else:  # "leaf", "nonleaf", or integer level
                    walker_level = RV.RiscvPagingModes.max_levels(paging_mode) - 1 - level
                    g_pagesize = page_meta.gstage_node_pagesizes.get(walker_level, page_meta.gstage_vs_nonleaf_pagesize)
                    if g_pagesize is None:
                        raise ValueError(f";#read_pte/;#write_pte: cannot resolve g_level='{g_level_str}' — gstage_vs_nonleaf_pagesize not set for '{lin_name}'")
                g_leaf_level = RV.RiscvPagingModes.max_levels(g_paging_mode) - 1 - RV.RiscvPageSizes.pt_leaf_level(g_pagesize)
                g_level = g_leaf_level if g_level_str == "leaf" else g_leaf_level - 1
            else:
                g_level = int(g_level_str)

        return (level, g_level)

    def _get_page_for_addr_name(self, lin_name: str) -> Page | None:
        """Return the riemap declaration Page for this lin_name, or None if not found.

        RiescueD's own ``(lin_name, map_name) -> Page`` lookup, built lazily from
        ``pool.page_translation`` (``page_names``/``page_source``, the object-keyed
        maps the generator's read-back populated) -- riemap assigns no names of its
        own, so this is entirely RiescueD-side bookkeeping."""
        map_names = self.pool.get_parsed_page_mapping_with_lin_name(lin_name)
        if not map_names:
            return None
        if self._page_by_name_and_map is None:
            self._page_by_name_and_map = {}
            translation = self.pool.page_translation
            if translation is not None:
                for page, (page_lin_name, _phys_name) in translation.page_names.items():
                    source = translation.page_source.get(page)
                    if source is None:
                        continue
                    self._page_by_name_and_map[(page_lin_name, source[0])] = page
        return self._page_by_name_and_map.get((lin_name, map_names[0]))

    def _get_page_mapping_for_addr_name(self, addr_name: str) -> ParsedPageMapping | None:
        """Return a ParsedPageMapping for this equate name if it is a lin_name or phys_name."""
        if self.pool.parsed_page_mapping_with_lin_name_exists(addr_name):
            map_names = self.pool.get_parsed_page_mapping_with_lin_name(addr_name)
            if map_names:
                return self.pool.get_parsed_page_mapping(addr_name, map_names[0])
        for ppm in self.pool.get_parsed_page_mappings().values():
            if ppm.phys_name == addr_name:
                return ppm
        return None

    def _format_page_mapping_comment(self, ppm: ParsedPageMapping) -> str:
        """Format ParsedPageMapping attributes as a ;#page_mapping(...) comment line."""
        pagesize_str = str(ppm.pagesizes) if ppm.pagesizes else "['4kb']"
        page_maps_str = str(ppm.page_maps) if ppm.page_maps else "['map_os']"
        return (
            f"# ;#page_mapping(lin_name={ppm.lin_name}, phys_name={ppm.phys_name}, "
            f"pagesize={pagesize_str}, x={int(ppm.x)}, v={int(ppm.v)}, r={int(ppm.r)}, w={int(ppm.w)}, "
            f"modify_pt={int(ppm.modify_pt)}, modify_leaf_pt={int(ppm.modify_leaf_pt)}, modify_nonleaf_pt={int(ppm.modify_nonleaf_pt)}, g={int(ppm.g)}, a={int(ppm.a) if ppm.a is not None else 1}, "
            f"d={int(ppm.d) if ppm.d is not None else 1}, page_maps={page_maps_str})"
        )

    def _generate_equates_assembly(self) -> list[str]:
        """
        Generate equates and write to filehandle.
        Must be called last, since it relies on :class:`Runtime` to have been generated entirely.
        Must be included at top of assembly files to ensure li instructions will work and don't have relocation issues

        :return: List of strings containing equates assembly
        """
        equates_assembly: list[str] = []
        # Write the configuration
        equates_assembly.append("# Test configuration:")
        data = self.featmgr.get_summary()
        for config_name, config in data.items():
            equates_assembly.append(f".equ {config_name:35}, {int(config)}")

        # FS/VS value-list comments (values 0=Off 1=Initial 2=Clean 3=Dirty)
        equates_assembly.append("")
        equates_assembly.append("# FS/VS field values: 0=Off 1=Initial 2=Clean 3=Dirty")
        equates_assembly.append(f"# FS_RANDOMIZATION_VALUES: {', '.join(str(v) for v in self.featmgr.fs_randomization_values)}")
        equates_assembly.append(f"# VS_RANDOMIZATION_VALUES: {', '.join(str(v) for v in self.featmgr.vs_randomization_values)}")

        # Write random data
        equates_assembly.append("")
        equates_assembly.append("# Test random data:")
        data = self.pool.get_random_data()
        for data_name, data in data.items():
            equates_assembly.append(f".equ {data_name:35}, 0x{data:016x}")

        # Debug ROM address (when debug_mode and debug_rom_address set)
        if self.featmgr.debug_mode and self.featmgr.debug_rom_address is not None:
            equates_assembly.append("\n# Debug ROM (RISC-V Debug Spec Ch.4):")
            equates_assembly.append(f".equ debug_rom{' ':35}, 0x{self.featmgr.debug_rom_address:016x}")
            equates_assembly.append(f".equ debug_rom_address{' ':27}, 0x{self.featmgr.debug_rom_address:016x}")
            if self.featmgr.debug_rom_size is not None:
                equates_assembly.append(f".equ debug_rom_size{' ':30}, 0x{self.featmgr.debug_rom_size:016x}")

        # Write test addresses
        equates_assembly.append("\n# Test addresses:")
        data = self.pool.get_random_addrs()
        for addr_name, addr in data.items():
            if addr_name == "code":
                addr.address = addr.address + self.code_offset
            ppm = self._get_page_mapping_for_addr_name(addr_name)
            if ppm is not None:
                equates_assembly.append(self._format_page_mapping_comment(ppm))
            equates_assembly.append(f".equ {addr_name:35}, 0x{addr.address:016x}")

        # Write PMA region information
        equates_assembly.append("\n# PMA regions:")
        pma_regions = self.pool.pma_regions.consolidated_entries()
        equates_assembly.append(f"# Total PMA regions: {len(pma_regions)}")
        for idx, region in enumerate(pma_regions):
            region_name = region.pma_name if region.pma_name else f"pma_region_{idx}"
            equates_assembly.append(f"# PMA region {idx}: {region_name}")
            base_name = f"{region_name}_base"
            size_name = f"{region_name}_size"
            end_name = f"{region_name}_end"
            equates_assembly.append(f".equ {base_name:35}, 0x{region.pma_address:016x}")
            equates_assembly.append(f".equ {size_name:35}, 0x{region.pma_size:016x}")
            equates_assembly.append(f".equ {end_name:35}, 0x{region.get_end_address():016x}")
            # PMA attributes
            rwx_str = f"{'r' if region.pma_read else '-'}{'w' if region.pma_write else '-'}{'x' if region.pma_execute else '-'}"
            equates_assembly.append(
                f"#   type={region.pma_memory_type}, cacheability={region.pma_cacheability}, "
                f"combining={region.pma_combining}, rwx={rwx_str}, "
                f"amo_type={region.pma_amo_type}, routing={region.pma_routing_to}"
            )

        # Generate PA equates for all named sections.
        # These are used by M-mode runtime code that accesses data with MPRV=0 (bare physical addresses).
        # Always generated (even when VMA == LMA) so runtime equates can reference them unconditionally.
        # Skip hex-addressed sections (0x...) and internal pagetable sections (__pagetable_*) since
        # they are not referenced by name from runtime code.
        equates_assembly.append("\n# Physical address equates for sections:")
        for section_name, section_info in self.pool.get_sections().items():
            if section_name.startswith("0x") or section_name.startswith("__"):
                continue
            pa_equate_name = f"{section_name}_pa"
            equates_assembly.append(f".equ {pa_equate_name:35}, 0x{section_info.lma:016x}")

        # Generate any equate file additions from runtime
        equates_assembly.append(self.runtime.generate_equates())

        # Write exception causes
        equates_assembly.append("\n# Exception causes:")
        for cause_enum in RV.RiscvExcpCauses:
            equates_assembly.append(f".equ {cause_enum.name:35}, {cause_enum.value}")

        equates_assembly.append("\n# Expected Interrupt causes:")
        for interrupt_enum in RV.RiscvInterruptCause:
            equates_assembly.append(f".equ EXPECT_{interrupt_enum.name}, {interrupt_enum.value}")

        equates_assembly.append("\n# XLEN\n.equ XLEN, 64")
        # Also have a special ECALL cause based on the current privilege mode
        # Handle VS mode
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
            equates_assembly.append("\n.equ ECALL            , ECALL_FROM_VS")
        else:
            equates_assembly.append(f"\n.equ ECALL            , ECALL_FROM_{self.featmgr.priv_mode}")
        equates_assembly.append("")
        equates_assembly.append("\n.equ DONT_USE_STACK, 1")
        equates_assembly.append("")

        # Also write needs pma flag based on the commandline
        pma_enabled = 1 if self.featmgr.needs_pma else 0
        equates_assembly.append(f"\n.equ PMA_ENABLED, {pma_enabled}")

        # Add MISA equates
        equates_assembly.append(f"\n.equ MISA_BITS, {self.featmgr.get_misa_bits()}")

        # Add feature-specific equates
        equates_assembly.append("\n# Feature-specific equates:")
        for feature in self.featmgr.feature.list_features():
            enabled = 1 if self.featmgr.feature.is_enabled(feature) else 0
            supported = 1 if self.featmgr.feature.is_supported(feature) else 0
            equates_assembly.append(f".equ FEATURE_{feature.upper():35}, {enabled} # supported={supported}")
        return equates_assembly
