# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import csv
import re
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

import riescue.lib.enums as RV
from riescue.compliance.src.riscv_dv import (
    process_whisper_sim_log,
    process_spike_sim_log,
)
from riescue.compliance.lib.testcase import TestCase
from riescue.compliance.config import Resource

log = logging.getLogger(__name__)


class TestGenerator:
    """Generate RISC-V compliance tests for instruction verification. Manages the generation of test files through two passes,
    processes simulation logs, and creates testcases for RISC-V instruction bringup testing.

    First pass is in the initial test generation while the second pass is for state tracking and self-checking.

    :param resource_db: Resource configuration database containing test parameters
    """

    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db
        self.state_tracker: OrderedDict[str, Optional[list[str]]] = OrderedDict()
        self._testcases: OrderedDict[str, TestCase] = OrderedDict()
        self.label_match_re_prog = re.compile(r"[0-9a-fA-F]+ \<(.*?)\>:")

    def process_instrs(self, mnemonics: dict[str, Any], iteration: int = 1):
        """
        Generates the first pass and second pass testcases depending on the iteration.
        Testcases are generated for the mnemonics provided


        :param mnemonics: Dictionary of instruction mnemonics to process
        :param iteration: Pass number (1 for first pass, >1 for subsequent passes)

        """
        if iteration == 1:
            log.info("Generating test (first pass)...")
            self.generate_test(mnemonics, iteration)
        else:
            log.info("Generating test (second pass)...")
            self.process_log()
            self.generate_test(mnemonics, iteration)

    def write_header(self, paging_mode, privilege_mode, test_environment):
        """
        RiescueD parses this information anyways, ensures privilege / paging mode is set correctly.


        :param paging_mode: Memory paging mode configuration
        :param privilege_mode: CPU privilege level configuration
        :param test_environment: Test execution environment
        :returns: List of header comment lines
        """
        header = []
        header.append(";#test.name       sample_test")
        header.append(";#test.author     dkoshiya@tenstorrent.com")
        header.append(";#test.arch       rv64")
        header.append(f";#test.priv       {privilege_mode}")
        header.append(f";#test.env        {test_environment}")
        header.append(f";#test.cpus       {self.resource_db.num_cpus}")
        if self.resource_db.num_cpus > 1:
            header.append(f";#test.mp_mode       {self.resource_db.mp_mode}")
            if self.resource_db.parallel_scheduling_mode != "" and self.resource_db.mp_mode == "parallel":
                header.append(f";#test.parallel_scheduling_mode       {self.resource_db.parallel_scheduling_mode}")
        header.append(f";#test.paging     {paging_mode}")
        header.append(";#test.category   arch")
        header.append(";#test.class      vector")
        header.append(";#test.features   ext_v.enable ext_fp.disable")

        return header

    def process_body(self, iteration, testcase_num, body, instrs):
        """Generate test body content for instructions.

        Processes instruction list to create test body, handling both first and second pass
        iterations. Manages test combining and snippet generation.

        :param iteration: Current iteration number
        :param testcase_num: Current testcase number
        :param body: List to append body content to
        :param instrs: List of instructions to process
        """

        if self.resource_db.combine_compliance_tests == 1:
            body.append("########################")
            body.append("# test1 : all instrs, up to max number per testfile")
            body.append("########################\n")
            body.append(";#discrete_test(test=test{})".format(1))
            body.append("test{}:".format(1))

        if iteration == 1:
            num_instrs = len(instrs)
            for test_num, instr in enumerate(instrs):
                snippet = instr.first_pass_snippet
                if self.resource_db.combine_compliance_tests == 0:
                    body.append("########################")
                    body.append(f"# test{str(test_num+1)} : {instr.name.upper()}")
                    body.append("########################\n")
                    body.append(";#discrete_test(test=test{})".format(test_num + 1))
                    body.append("test{}:".format(test_num + 1))

                for line in snippet:
                    body.append(line)

                if self.resource_db.combine_compliance_tests == 1:
                    body.append(instr.setup._pass_one_pre_appendix)

                if (self.resource_db.combine_compliance_tests == 0) or (test_num == (num_instrs - 1)):
                    if self.resource_db.wysiwyg:
                        body.append("\n")
                    else:
                        body.append(";#test_passed()")
        else:
            for test_num, instr in enumerate(instrs):
                snippet = instr.first_pass_snippet + instr.get_post_setup(self.state_tracker[instr.label + f"_{testcase_num}"])

                if self.resource_db.combine_compliance_tests == 0:
                    body.append("########################")
                    body.append(f"# test{str(test_num+1)} : {instr.name.upper()}")
                    body.append("########################\n")
                    body.append(";#discrete_test(test=test{})".format(test_num + 1))
                    body.append("test{}:".format(test_num + 1))

                for line in snippet:
                    body.append(line)

    def write_file(
        self,
        iteration: int,
        testcase_num: int,
        testcase: TestCase,
        paging_mode: str,
        privilege_mode: str,
        test_environment: str,
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

        header = self.write_header(paging_mode, privilege_mode, test_environment)

        body = []

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

        self.process_body(iteration, testcase_num, body=body, instrs=testcase.instrs)

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
            if instr._data_section:
                body = body + instr._data_section

        with open(testcase.testname, "w") as output_file:
            output_file.write("\n".join(header + body))
            output_contents = "\n".join(header + body)
            log.debug(f"Wrote to {testcase.testname}: \n{output_contents}")

    def get_pre_setup_snippets(self, instrs: dict[str, Any], iteration: int):
        """Generate pre-setup code snippets for first pass instructions.

        Extracts and caches the pre-setup code snippets from instructions
        during the first iteration pass.

        :param instrs: Dictionary of instructions to process
        :param iteration: Current iteration number
        """
        if iteration == 1:
            for key, instr in instrs.items():
                instrs[key].first_pass_snippet = instr.get_pre_setup()

    def generate_test(self, instrs: dict[str, Any], iteration: int):
        """Generate test files by organizing instructions into testcases.

        Groups instructions by test configuration, creates TestCase objects,
        and writes test files. Handles instruction limits per file and
        test configuration grouping.

        :param instrs: Dictionary of instruction objects to generate tests for
        :param iteration: Current iteration number for test generation
        """

        instrs_per_file: list[Any] = []
        instr_cnt_per_file = 0
        testcase_num = 1
        instr_cnt = 0
        self._testcases = OrderedDict()
        max_instr_per_file = self.resource_db.max_instr_per_file  # min(self.resource_db.max_instr_per_file, len(instrs.items()))

        if iteration == 1:
            self.get_pre_setup_snippets(
                instrs, iteration
            )  # Running this for now because the code got tangled up with snippets. It is better to request the code when we want to print it rather than cache it here.

            self.limited_instrs = instrs.values()  # FIXME: constructor should know about this and create an empty list instead of doing it here.
            # This preserves some state if RiscvTestGenerator is re-used

        # Wrangle the mode(s) we actually did end up using after randomly choosing one combination in the config manager.
        test_config_to_instrs: OrderedDict[str, list[Any]] = OrderedDict()
        # FIXME if instructions are were stored already sorted by test config, they wouldn't require reorganization here. That wasn't done in the interest of making as few changes as possible.
        for instr in self.limited_instrs:
            privilege_mode = instr.test_config["privilege_mode"]
            paging_mode = instr.test_config["paging_mode"]
            test_config_string = paging_mode + "_" + privilege_mode
            if test_config_string not in test_config_to_instrs:
                test_config_to_instrs[test_config_string] = []
            test_config_to_instrs[test_config_string].append(instr)

        for test_config_string, _instrs in test_config_to_instrs.items():
            instr_cnt = 0

            for instr in _instrs:
                instrs_per_file.append(instr)
                instr_cnt_per_file += 1
                instr_cnt += 1

                if instr_cnt_per_file == max_instr_per_file or instr_cnt == len(_instrs):
                    signature = str(self.resource_db.generate_test_path(iteration, testcase_num))
                    testcase = TestCase(signature, instrs_per_file, self.resource_db)
                    self._testcases[signature] = testcase
                    # FIXME: These should be strongly typed
                    privilege_mode = instr.test_config["privilege_mode"]
                    paging_mode = instr.test_config["paging_mode"]
                    test_environment = instr.test_config["test_environment"]
                    self.write_file(
                        iteration,
                        testcase_num,
                        testcase,
                        paging_mode,
                        privilege_mode,
                        test_environment,
                    )
                    testcase_num += 1
                    instr_cnt_per_file = 0
                    instrs_per_file = []

    def process_log(self):
        """Process simulation logs to extract execution state information.

        :raises ValueError: If unknown ISS type is specified
        """

        for _, testcase in self._testcases.items():
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

        for testcase_num, testcase in enumerate(self._testcases.values(), 1):
            self.process_testcase(testcase, testcase_num)

    def process_disassembly(self, testcase: TestCase) -> dict[str, str]:
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
                label_dis = self.label_match_re_prog.search(line)
                if label_dis is not None:
                    addr_to_label[line.split()[0].lstrip("0").rstrip(":")] = label_dis.group(1)

        return addr_to_label

    def cross_reference_dissassembly_with_log(self, addr_to_label: dict[str, str], csv_log_file_name: str) -> dict[str, list[str]]:
        label_to_state: dict[str, list[str]] = dict()
        csv_log_file_path = Path(csv_log_file_name)
        if not csv_log_file_path.exists():
            csv_log_file_path = self.resource_db.run_dir / csv_log_file_name

        with open(csv_log_file_path, "r") as log:
            reader = csv.reader(log)
            for line in reader:
                addr = line[0].lstrip("0")
                label = addr_to_label.pop(addr, None)
                if label is not None and "_" in label:
                    label_to_state[label] = line

        return label_to_state

    def store_states(
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

    def process_testcase(self, testcase: TestCase, testcase_num: int):
        """Process testcase to extract state information.

        :param testcase: TestCase object to process
        :param testcase_num: Testcase number
        """
        addr_to_label = self.process_disassembly(testcase)
        label_to_state = self.cross_reference_dissassembly_with_log(addr_to_label, testcase.csv_log)
        self.store_states(label_to_state, testcase, testcase_num)

    def testfiles(self) -> list[str]:
        """Get list of generated testfiles.

        :returns: List of testfile names
        """
        return [testcase.testname for _, testcase in self._testcases.items()]

    def testcases(self) -> OrderedDict[str, TestCase]:
        """Get dictionary of generated testcases.

        :returns: Dictionary of testcase signatures to TestCase objects
        """
        return self._testcases
