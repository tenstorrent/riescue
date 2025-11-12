# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

import re
import logging
from pathlib import Path

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.parser import Parser
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.runtime import Runtime
from riescue.dtest_framework.artifacts import GeneratedFiles

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

        if self.featmgr.code_offset is not None:
            self.code_offset = self.featmgr.code_offset
        elif self.featmgr.force_alignment:
            self.code_offset = 0
        else:
            self.code_offset = self.rng.random_in_range(0, 0x3F * 5, 2)
        self.single_assembly_file = self.featmgr.single_assembly_file or self.featmgr.linux_mode

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

    def create_assembly_files(self, rasm: Path, generated_files: GeneratedFiles) -> None:
        """
        Generates assembly file and any conditional files. E.g equates, pagetables, :class:`Runtime` files.

        Splits input file into headers and test code, generates runtime code then equates.

        :param rasm: Path to the input RASM file
        :param generated_files: Generated files container
        """
        header_section, test_section = self.split_rasm_sections(rasm)
        runtime_sections = self.generate_runtime_sections()
        equates_section = self.generate_equates_section()
        assembly_content = header_section + equates_section + runtime_sections + test_section

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

        The Test Section starts with ``.section .code_super_0`` or ``.section .code`` (or ``.section .text`` if WYSIWYG is enabled)

        """
        with open(rasm, "r") as input_file:
            lines = input_file.readlines()

        if self.featmgr.wysiwyg:
            test_section_start = ".section .text"
            missing_help_message = f"{test_section_start} is requried for wysiwyg tests"
        else:
            test_section_start = ".section .code"
            missing_help_message = f"{test_section_start} is requried for test code"

        # find where to split tests
        test_section_start_idx = None
        code_super_0_idx = None
        for i, line in enumerate(lines):
            if test_section_start in line:
                test_section_start_idx = i
            if ".section .code_super_0" in line:
                code_super_0_idx = i
        if test_section_start_idx is None:
            raise ValueError(f"Couldn't find a {test_section_start} section in input file, {rasm.name}. {missing_help_message}")

        # if there's a code_super_0 section, use that instead of test_section_start
        if code_super_0_idx is not None:
            test_section_start_idx = code_super_0_idx

        header_section = lines[:test_section_start_idx]
        test_section = lines[test_section_start_idx:]
        return header_section, test_section

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
        priority = ["user", "super", "machine"]
        init_mem_sections = self.pool.get_parsed_init_mem_addrs()
        section_code_found = False

        parsed_lines: list[str] = []
        for line in assembly_content.split("\n"):
            # replace .section .code, with .section .code, "ax"
            if ".section .code," in line and not section_code_found:
                section_code_found = True
                line = '.section .code, "ax"'

            # replace ;#init_memory with .section .lin_name, "aw" or .section .lin_name, "ax"
            if line.startswith(";#init_memory"):
                lin_name_map = Parser.separate_lin_name_map(line)
                maps = lin_name_map[1]
                found = False
                for lin_name in init_mem_sections:
                    if len(maps) == 0:
                        if "@" + lin_name in line:
                            permissions = "aw"
                            if self.pool.parsed_page_mapping_exists(lin_name, "map_os") and self.pool.get_parsed_page_mapping(lin_name, "map_os").x:
                                permissions = "ax"
                            print_lin_name = lin_name
                            if lin_name.startswith("0x"):
                                print_lin_name = self.prefix_hex_lin_name(lin_name)
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

            # replace ;#csr_rw with csrr/csrw instructions or system call to jump table
            parsed_line = line.strip()
            if parsed_line.startswith(";#csr_rw"):
                pattern = r"^;#csr_rw\((?P<csr_name>\w*),\s*(?P<read_write_set_clear>\w*),\s*(?P<direct_rw>\w*)\)"
                match = re.match(pattern, parsed_line)
                if match:
                    csr_name = match.group("csr_name")
                    read_write_set_clear = match.group("read_write_set_clear")
                    direct_rw = match.group("direct_rw").lower()

                    # get parsed csr val
                    parsed_csr_val = self.pool.get_parsed_csr_access(csr_name, read_write_set_clear)
                    priv_mode = "super" if parsed_csr_val.priv_mode == "supervisor" else parsed_csr_val.priv_mode
                    csr_prio = priority.index(priv_mode)
                    priv_mode_prio = priority.index(self.featmgr.priv_mode.name.lower())
                    if priv_mode_prio >= csr_prio or direct_rw == "true":
                        if read_write_set_clear == "read":
                            line = line + f"\ncsrr t2, {csr_name}\n"
                        elif read_write_set_clear == "set":
                            line = line + f"\ncsrs {csr_name}, t2\n"
                        elif read_write_set_clear == "clear":
                            line = line + f"\ncsrc {csr_name}, t2\n"
                        else:
                            line = line + f"\ncsrw {csr_name}, t2\n"
                    else:
                        flag_name = "machine_csr_jump_table_flags" if priv_mode == "machine" else "super_csr_jump_table_flags"
                        code_to_replace = f"li x31, {flag_name}\n"
                        code_to_replace += f"li t0, {parsed_csr_val.csr_id}\n"
                        code_to_replace += "sd t0, 0(x31)\n"

                        sys_call = "0xf0001006" if priv_mode == "super" else "0xf0001005"
                        code_to_replace += f"li x31, {sys_call}\n"
                        code_to_replace += "ecall\n"

                        line = line + "\n" + code_to_replace

            # replace ;#read_leaf_pte with syscall to jump table
            parsed_line = line.strip()
            if parsed_line.startswith(";#read_leaf_pte"):
                pattern = r"^;#read_leaf_pte\((?P<lin_name>\w+),\s*(?P<paging_mode>\w+)\)"
                match = re.match(pattern, parsed_line)
                if match:
                    lin_name = match.group("lin_name")
                    paging_mode = match.group("paging_mode")

                    # Find the parsed leaf PTE entry using the tuple key
                    try:
                        leaf_pte = self.pool.get_parsed_leaf_pte(lin_name, paging_mode)
                        pte_id = leaf_pte.pte_id

                        # Generate code to call the syscall
                        code_to_replace = "li x31, machine_leaf_pte_jump_table_flags\n"
                        code_to_replace += f"li t0, {pte_id}\n"
                        code_to_replace += "sd t0, 0(x31)\n"
                        code_to_replace += "li x31, 0xf0001007\n"
                        code_to_replace += "ecall\n"

                        line = line + "\n" + code_to_replace
                    except KeyError:
                        # If not found, skip replacement (shouldn't happen in normal flow)
                        pass

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
                runtime_sections.append(f'.include "{include_file.name}"\n')
        return generated_sections + runtime_sections

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

    # helper functions:
    def prefix_hex_lin_name(self, n: str) -> str:
        "Helper function to prefix hex linear names with __auto_secname_"
        return f"__auto_secname_{n}"

    def _generate_pagetable_assembly(self) -> list[str]:
        """
        Generate pagetables text, return it as a list of strings.

        :param pt_file_handle: Filehandle to write pagetables to
        :return: List of strings containing pagetables assembly
        """
        # Create pagetables here
        pagetable_content: list[str] = []
        # Handle the vs-stage pagetables first since we need to know the guest physical
        # address to generate the g-stage pagetables
        for map in self.pool.get_page_maps().values():
            if not map.g_map and map.paging_mode != RV.RiscvPagingModes.DISABLE:
                map.create_pagetables(self.rng)
                pagetable_content.append("\n".join(map.generate_pagetables_assembly()))

        # Now that the vs-stage pagetables are generated, handle the g-stage pagetables
        if self.featmgr.paging_g_mode != RV.RiscvPagingModes.DISABLE:
            for map in self.pool.get_page_maps().values():
                if map.g_map:
                    map.create_pagetables(self.rng)
                    pagetable_content.append("\n".join(map.generate_pagetables_assembly()))
        return pagetable_content

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

        # Write random data
        equates_assembly.append("")
        equates_assembly.append("# Test random data:")
        data = self.pool.get_random_data()
        for data_name, data in data.items():
            equates_assembly.append(f".equ {data_name:35}, 0x{data:016x}")

        # Write test addresses
        equates_assembly.append("\n# Test addresses:")
        data = self.pool.get_random_addrs()
        for addr_name, addr in data.items():
            if addr_name == "code":
                addr.address = addr.address + self.code_offset
            equates_assembly.append(f".equ {addr_name:35}, 0x{addr.address:016x}")

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
        deleg_to_super = 1 if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.SUPER else 0
        deleg_to_machine = 1 if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.MACHINE else 0
        equates_assembly.append(f"\n.equ OS_DELEG_EXCP_TO_SUPER, {deleg_to_super}")
        equates_assembly.append(f"\n.equ OS_DELEG_EXCP_TO_MACHINE, {deleg_to_machine}")
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
