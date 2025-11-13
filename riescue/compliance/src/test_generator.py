# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

import csv
import re
import logging
from pathlib import Path
from typing import Optional

import riescue.lib.enums as RV
from riescue.compliance.src.riscv_dv import (
    process_whisper_sim_log,
    process_spike_sim_log,
)
from riescue.compliance.lib.testcase import TestCase
from riescue.compliance.config import Resource
from riescue.compliance.lib.riscv_instrs.base import InstrBase

log = logging.getLogger(__name__)


class TestGenerator:
    """Generate RISC-V compliance tests for instruction verification. Manages the generation of test files through two passes,
    processes simulation logs, and creates testcases for RISC-V instruction bringup testing.

    First pass is in the initial test generation while the second pass is for state tracking and self-checking.

    :param resource_db: Resource configuration database containing test parameters
    """

    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db

        # NOTE: don't need OrderedDict unless we are using reversed() or move_to_end()
        # after python 3.7, dictionaries preserve insertion order by default

        self.state_tracker: dict[str, Optional[list[str]]] = {}
        self._testcases: list[TestCase] = []
        self._limited_instrs: list[InstrBase] = []  # Original list of instructions to process.
        self._label_match_re_prog = re.compile(r"[0-9a-fA-F]+ \<(.*?)\>:")

    def process_instrs(self, instructions: dict[str, InstrBase], iteration: int = 1) -> TestCase:
        """
        Generates the first pass and second pass testcases depending on the iteration.
        Testcases are generated for the instructions provided


        :param instructions: Dictionary of instruction instructions to process
        :param iteration: Pass number (1 for first pass, >1 for subsequent passes)

        """
        if iteration == 1:
            log.info("Generating test (first pass)...")
            return self._generate_test(instructions, iteration)
        else:
            log.info("Generating test (second pass)...")
            self._process_log()
            return self._generate_test(instructions, iteration)

    def _generate_test(self, instrs: dict[str, InstrBase], iteration: int) -> TestCase:
        """Generate test files by organizing instructions into testcases.

        Groups instructions by test configuration, creates TestCase objects,
        and writes test files. Handles instruction limits per file and
        test configuration grouping.

        :param instrs: Dictionary of instruction objects to generate tests for
        :param iteration: Current iteration number for test generation
        """

        testcase_num = 1
        self._testcases = []

        if iteration == 1:
            # Running this for now because the code got tangled up with snippets.
            # It is better to request the code when we want to print it rather than cache it here.
            for key, instr in instrs.items():
                instrs[key].first_pass_snippet = instr.get_pre_setup()  # cache the code for later use

            self.limited_instrs = list(instrs.values())  # FIXME: constructor should know about this and create an empty list instead of doing it here.
            # This preserves some state if RiscvTestGenerator is re-used

        # legacy code was iterating over a dictionary of instruction objects for priv/paging modes
        # ultimately this just needs to generate a single TestCase
        signature = self.resource_db.generate_test_path(iteration, testcase_num)
        testcase = TestCase(signature, self.limited_instrs, self.resource_db)
        self._testcases.append(testcase)
        self._write_file(iteration, testcase_num, testcase)
        return testcase

    def _write_header(self) -> list[str]:
        """
        RiescueD parses this information anyways, ensures privilege / paging mode is set correctly.

        This isn't getting parsed anymore, but it's here for additional debug and sanity checking.

        :param paging_mode: Memory paging mode configuration
        :param privilege_mode: CPU privilege level configuration
        :param test_environment: Test execution environment
        :returns: List of header comment lines
        """
        header: list[str] = []
        header.append(";#test.name       sample_test")
        header.append(";#test.author     dkoshiya@tenstorrent.com")
        header.append(";#test.arch       rv64")

        header.append(f";#test.priv       {str(self.resource_db.featmgr.priv_mode).lower()}")
        header.append(f";#test.env        {str(self.resource_db.featmgr.env).lower()}")
        header.append(f";#test.cpus       {self.resource_db.num_cpus}")
        if self.resource_db.num_cpus > 1:
            header.append(f";#test.mp_mode       {self.resource_db.mp_mode}")
            if self.resource_db.mp_mode == RV.RiscvMPMode.MP_PARALLEL:
                header.append(f";#test.parallel_scheduling_mode       {self.resource_db.parallel_scheduling_mode.value}")
        header.append(f";#test.paging     {str(self.resource_db.featmgr.paging_mode).lower()}")
        header.append(";#test.category   arch")
        header.append(";#test.class      vector")
        header.append(";#test.features   ext_v.enable ext_fp.disable")

        return header

    def _process_body(self, iteration: int, testcase_num: int, body: list[str], instrs: list[InstrBase]) -> None:
        """Generate test body content for instructions.

        Processes instruction list to create test body, handling both first and second pass
        iterations. Manages test combining and snippet generation.

        :param iteration: Current iteration number
        :param testcase_num: Current testcase number
        :param body: List to append body content to
        :param instrs: List of instructions to process
        """

        if self.resource_db.combine_compliance_tests:
            body.append("########################")
            body.append("# test1 : all instrs, up to max number per testfile")
            body.append("########################\n")
            body.append(";#discrete_test(test=test{})".format(1))
            body.append("test{}:".format(1))

        if iteration == 1:
            num_instrs = len(instrs)
            for test_num, instr in enumerate(instrs):
                snippet = instr.first_pass_snippet
                if not self.resource_db.combine_compliance_tests:
                    body.append("########################")
                    body.append(f"# test{str(test_num+1)} : {instr.name.upper()}")
                    body.append("########################\n")
                    body.append(";#discrete_test(test=test{})".format(test_num + 1))
                    body.append("test{}:".format(test_num + 1))

                for line in snippet:
                    body.append(line)

                if self.resource_db.combine_compliance_tests:
                    body.append(instr.setup.pass_one_pre_appendix)

                if not self.resource_db.combine_compliance_tests or test_num == (num_instrs - 1):
                    if self.resource_db.wysiwyg:
                        body.append("\n")
                    else:
                        body.append(";#test_passed()")
        else:
            for test_num, instr in enumerate(instrs):
                snippet = instr.first_pass_snippet + instr.get_post_setup(self.state_tracker[instr.label + f"_{testcase_num}"])

                if not self.resource_db.combine_compliance_tests:
                    body.append("########################")
                    body.append(f"# test{str(test_num+1)} : {instr.name.upper()}")
                    body.append("########################\n")
                    body.append(";#discrete_test(test=test{})".format(test_num + 1))
                    body.append("test{}:".format(test_num + 1))

                for line in snippet:
                    body.append(line)

    def _write_file(
        self,
        iteration: int,
        testcase_num: int,
        testcase: TestCase,
    ):
        """
        Generates the complete assembly test file including configuration header,
        test body with instructions, and data section. Handles both WYSIWYG and
        standard test modes.

        Opens and writes complete test file with header, body, and data sections.

        :param iteration: Current iteration number
        :param testcase_num: Current testcase number
        :param testcase: TestCase object containing test data
        :param paging_mode: Memory paging mode configuration
        :param privilege_mode: CPU privilege level configuration
        :param test_environment: Test execution environment
        """

        header = self._write_header()

        body: list[str] = []

        # Keep the first pass as a dummy pass for WYSIWYG mode to extract the
        # state for self checking code
        if self.resource_db.wysiwyg:
            body.append(".section .text\n")

            # Add 0xc001c0de at the beginning for first pass to end the test
            if iteration == 1:
                body.append("""\tli x31,0xc001c0de""")
                # body.append("lui x31, 0xc001c")
                # body.append("addi x31, x31, 0x0de")
        else:
            body.append('.section .code, "ax"\n')
            body.append("test_setup:")
            body.append(";#test_passed()")

        self._process_body(iteration, testcase_num, body=body, instrs=testcase.instrs)

        # Keep the first pass as a dummy pass for WYSIWYG mode to extract the
        # state for self
        if self.resource_db.wysiwyg:
            pass_fail = ""
            if iteration == 1:
                pass_fail = """
                failed:
                    li x31, 0xbaadc0de
                """
            else:
                pass_fail = """
                passed:
                    li x1,0xc001c0de
                    add x31,x31,x1
                failed:
                    li x31, 0xbaadc0de
                """
            end_of_test = f"""
            {pass_fail}
            end:
                lui x31, 0xc001c
                addi x31, x31, 0x0de
            """

            # Add padding at the end of the test
            padding = """
            \tadd  x2, x29, x28
            \tadd  x3, x2, x1
            \tadd  x4, x3, x2
            \tadd  x5, x4, x3
            \tadd  x6, x5, x4
            \tadd  x7, x6, x5
            \tadd  x8, x7, x6
            \tadd  x9, x8, x7
            \tadd  x10, x9, x8
            \tadd  x11, x10, x9
            \tadd  x12, x11, x10
            \tadd  x13, x12, x11
            \tadd  x14, x13, x12
            \tadd  x15, x14, x13
            \tadd  x16, x15, x14
            \tadd  x17, x15, x14
            """
            # Append of the sim for wysiwyg mode
            body.append(end_of_test)

            end_of_test_tohost = """

            # Append tohost end of the test anyways for wysiwyg to keep whisper
            # happy at the end of the sim
            os_write_tohost:
               li gp, 0x1  # bit0: indicated end of test, bits47:1 -> error code (0:success, non-0: fail)
               la x1, tohost

               fence iorw, iorw
               sw gp, 0(x1)
            """
            if not self.resource_db.fe_tb:
                end_of_test_tohost += """
                j os_write_tohost
                # _exit:
                #    j os_write_tohost
                """

            end_of_test_tohost += """
            # Define tohost and fromhost labels for Spike to end the test
            .align 6; .global tohost; tohost: .dword 0;
            .align 6; .global fromhost; fromhost: .dword 0;

            """
            body.append(end_of_test_tohost)

            # Append padding at the end of the test
            for i in range(0, 8):
                body.append(f"cl{i}:")
                body.append(padding)

        else:
            body.append("test_cleanup:")
            body.append(";#test_passed()")

        # Add data section
        body.append(".section .data\n")

        # FIXME: Shouldn't be accessing private data section.
        for instr in testcase.instrs:
            if instr.data_section:
                body = body + instr.data_section

        with open(testcase.testname, "w") as output_file:
            output_file.write("\n".join(header + body))
            output_contents = "\n".join(header + body)
            log.debug(f"Wrote to {testcase.testname}: \n{output_contents}")

    def _process_log(self):
        """Process simulation logs to extract execution state information.

        This writes the test to a CSV file. The path to the CSV file is stored in the ``TestCase`` object.

        :raises ValueError: If unknown ISS type is specified
        """

        for testcase in self._testcases:
            csv_log = Path(testcase.csv_log)
            log = Path(testcase.log)
            if not log.exists():
                log = self.resource_db.run_dir / testcase.log
            if self.resource_db.first_pass_iss == "spike":
                process_spike_sim_log(log.resolve(), csv_log.resolve())
            elif self.resource_db.first_pass_iss == "whisper":
                process_whisper_sim_log(log.resolve(), csv_log.resolve())
            else:
                raise ValueError(f"Unknown ISS {self.resource_db.first_pass_iss}")

        # why do we skip the first testcase?
        # This isn't tests in the plural sense
        for testcase_num, testcase in enumerate(self._testcases, 1):
            print(f"processing {testcase_num}")
            self._process_testcase(testcase, testcase_num)

    def _process_disassembly(self, testcase: TestCase) -> dict[str, str]:
        """Extract address-to-label mappings from disassembly file.

        :param testcase: TestCase object containing disassembly file path
        """

        addr_to_label: dict[str, str] = dict()
        dis = Path(testcase.disassembly)
        if not dis.exists():
            dis = self.resource_db.run_dir / testcase.disassembly
        with open(dis, "r") as dis_file:
            lines = dis_file.readlines()
            for line in lines:
                label_dis = self._label_match_re_prog.search(line)
                if label_dis is not None:
                    addr_to_label[line.split()[0].lstrip("0").rstrip(":")] = label_dis.group(1)

        return addr_to_label

    def _cross_reference_dissassembly_with_log(self, addr_to_label: dict[str, str], csv_log_file: Path) -> dict[str, list[str]]:
        label_to_state: dict[str, list[str]] = dict()

        with open(csv_log_file, "r") as log:
            reader = csv.reader(log)
            for line in reader:
                addr = line[0].lstrip("0")
                label = addr_to_label.pop(addr, None)
                if label is not None and "_" in label:
                    label_to_state[label] = line

        return label_to_state

    def _store_states(
        self,
        label_to_state: dict[str, list[str]],
        testcase: TestCase,
        testcase_num: int,
    ):
        """Store state information for testcase.

        :param label_to_state: Dictionary of labels to state information
        :param testcase: TestCase object to store state for
        :param testcase_num: Testcase number
        """
        for instr in testcase.instrs:
            # Sometimes state is None
            self.state_tracker[instr.label + f"_{testcase_num}"] = label_to_state.pop(instr.label, None)

    def _process_testcase(self, testcase: TestCase, testcase_num: int):
        """Process testcase to extract state information.

        :param testcase: TestCase object to process
        :param testcase_num: Testcase number
        """
        addr_to_label = self._process_disassembly(testcase)
        addr_to_label_log = testcase.signature.with_suffix(".addr_to_label.log")
        with open(addr_to_label_log, "w") as log:
            for addr, label in addr_to_label.items():
                log.write(f"{addr}: {label}\n")
        label_to_state = self._cross_reference_dissassembly_with_log(addr_to_label, testcase.csv_log)

        label_to_state_log_path = testcase.signature.with_suffix(".label_to_state.log")
        with open(label_to_state_log_path, "w") as log:
            for label, state in label_to_state.items():
                log.write(f"{label}: {state}\n")

        label_to_state_log = testcase.signature.with_suffix(".label_to_state.log")
        with open(label_to_state_log, "w") as log:
            for label, state in label_to_state.items():
                log.write(f"{label}: {state}\n")
        self._store_states(label_to_state, testcase, testcase_num)
