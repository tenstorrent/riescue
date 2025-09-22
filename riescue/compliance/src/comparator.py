# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from riescue.compliance.src.riscv_dv.spike_log_to_trace_csv import process_spike_sim_log
from riescue.compliance.config import Resource
import re
import csv


class Comparator:

    def __init__(self, resource_db):
        self.resource_db = resource_db
        self.spike_state_tracker = dict()
        self.whisper_state_tracker = dict()

    def compare_logs(self, testcase):
        spike_logs = testcase.get_spike_logs()
        process_spike_sim_log(spike_logs[0], spike_logs[1])
        whisper_logs = testcase.get_whisper_logs()
        self.process_testcase(testcase, spike_logs[1], iss="spike")
        self.process_testcase(testcase, whisper_logs[1], iss="whisper")
        print("WHISPER")
        print(self.whisper_state_tracker)
        print("SPIKE")
        print(self.spike_state_tracker)

    def process_testcase(self, testcase, log, iss):
        for instr in testcase.instrs:
            addr = self.search_label(instr.label, testcase.disassembly)
            modified_arch_state = self.extract_state(addr, log)
            if iss == "spike":
                self.spike_state_tracker[instr.label] = modified_arch_state
            else:
                self.whisper_state_tracker[instr.label] = modified_arch_state

    def search_label(self, label, disassembly_file):
        with open(disassembly_file, "r") as dis_file:
            lines = dis_file.readlines()
            for line in lines:
                label_dis = re.findall(r"\<(\w+)\>", line)
                if len(label_dis):
                    if label == label_dis[0]:
                        return line.split()[0]

    def extract_state(self, addr, csv_log):
        with open(csv_log) as log:
            reader = csv.reader(log)
            for line in reader:
                # print(line)
                # FIXME: Sometimes add was None, is that expected?
                if addr:
                    if addr.lstrip("0") in line[0]:
                        return line
