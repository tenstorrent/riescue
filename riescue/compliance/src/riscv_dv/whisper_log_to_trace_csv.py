# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# This file incorporates work covered by the following copyright and  permission notice:
# SPDX-FileCopyrightText: © 2019 Google LLC
#  This code is licensed under the Apache-2.0 License.

# This file incorporates work covered by the following copyright and  permission notice:
# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path

"""
Convert whisper sim log to standard riscv instruction trace format
"""

ix_to_int_reg_name = [
    "zero",
    "ra",
    "sp",
    "gp",
    "tp",
    "t0",
    "t1",
    "t2",
    "s0",
    "s1",
    "a0",
    "a1",
    "a2",
    "a3",
    "a4",
    "a5",
    "a6",
    "a7",
    "s2",
    "s3",
    "s4",
    "s5",
    "s6",
    "s7",
    "s8",
    "s9",
    "s10",
    "s11",
    "t3",
    "t4",
    "t5",
    "t6",
]


def int_reg_name(ix):
    """Given an integer register index, return the corresponding RISCV ABI
    name.
    For example: an index of 0 returns 'zero', and 1 returns 'ra'.
    """
    if ix > 31:
        return "r" + str(ix)
    return ix_to_int_reg_name[ix]


ix_to_fp_reg_name = [
    "ft0",
    "ft1",
    "ft2",
    "ft3",
    "ft4",
    "ft5",
    "ft6",
    "ft7",
    "fs0",
    "fs1",
    "fa0",
    "fa1",
    "fa2",
    "fa3",
    "fa4",
    "fa5",
    "fa6",
    "fa7",
    "fs2",
    "fs3",
    "fs4",
    "fs5",
    "fs6",
    "fs7",
    "fs8",
    "fs9",
    "fs10",
    "fs11",
    "ft8",
    "ft9",
    "ft10",
    "ft11",
]


def fp_reg_name(ix):
    """Given a floating point register index, return the corresponding RISCV
    ABI name.
    For example: an index of 0 returns 'ft0', and 1 returns 'ra'.
    """
    if ix > 31:
        return "f" + str(ix)
    return ix_to_fp_reg_name[ix]


class RiscvInstructionTraceEntry:
    """RISC-V instruction trace entry"""

    def __init__(self):
        self.gpr = []
        self.csr = []
        self.instr = ""
        self.operand = ""
        self.pc = ""
        self.pa = []
        self.stdata = []
        self.binary = ""
        self.instr_str = ""
        self.mode = ""

    def get_trace_string(self):
        """Return a short string of the trace entry"""
        return f"{self.pc},{self.instr},{' '.join(self.gpr)},{' '.join(self.csr)},{' '.join(self.pa)},{' '.join(self.stdata)},{self.binary},{self.mode},{self.instr_str},{self.operand}\n"


# Input data format: 1 line per record unless last char is '+' in
# which case record extends to the next line.  An instruction with
# multiple changes has a multi-line-record (1 line per change with
# each line except last terminated with a '+'). The multi-lines
# are similar except for the resource/add/val fields.
#
# Fields:  rank hart pc       opcode   resource addr val       instr-text
# Example: #4   0    8000000c 00a00193 r        03   0000000a  addi  x3, x0, 0xa
#         #1 0  M 0000000080000000 00000013 r 0000000000000000 0000000000000000 addi     x0, x0, 0
# All values except rank are in hexadecimal
#
def process_whisper_sim_log(whisper_log: Path, csv: Path, full_trace: int = 1) -> None:
    """Process Whisper simulation log.

    Extract instruction and affected register information from whisper simulation
    log and save to CSV file.
    """
    instr_cnt = 0
    records = ["pc,instr,gpr,csr,pa,stdata,binary,mode,instr_str,operand,pad\n"]

    with open(whisper_log, "r") as f:
        changes = []  # list of tuples (resource, address, value)
        for line in f:
            fields = line.split()
            current_rank = ""

            if len(fields) >= 8:
                (rank, hart, mode, pc, opcode, resource, addr, value, instr) = fields[0:9]
                pending = current_rank == rank
                current_rank = rank

                if pending:
                    fields.pop()
                elif fields[-1] == "+":
                    fields.pop()

                mem_addr = []
                store_data = []
                if fields[-1].startswith("["):
                    # Expecting [a] or [a:d] or [a;a;a;...] or [a:d;a:d;...]
                    # where a is an address and d is a data value with an 0x prefix
                    mem_items = fields[-1].strip("[]").replace("0x", "").split(";")
                    mem_items.sort()  # To match other iss tool
                    for x in mem_items:
                        ad = x.split(":")
                        if len(ad) > 0:
                            mem_addr.append(ad[0])
                        if len(ad) > 1:
                            store_data.append(ad[1])

                    fields.pop()

                disas = " ".join(fields[7:])  # instruction disassembly
                operands = " ".join(fields[8:])  # instruction operands
                changes.append((resource, addr, value))

                if not pending:
                    regs = []
                    csrs = []
                    for change in changes:
                        (res, addr_str, val) = change
                        addr = int(addr_str, 16)
                        if res == "r" and addr != 0:
                            regs.append(int_reg_name(addr) + ":" + val)
                        elif res == "f":
                            regs.append(fp_reg_name(addr) + ":" + val)
                        elif res == "v":
                            regs.append("v" + str(addr) + ":" + val)
                        elif res == "c":
                            csrs.append("c" + str(addr) + ":" + val)
                        elif res == "m":
                            if len(store_data) == 0:
                                mem_addr = [addr_str]
                                store_data = [val]

                    record = RiscvInstructionTraceEntry()
                    record.pc = pc
                    record.instr = instr
                    for x in regs:
                        record.gpr.append(x)
                    for x in csrs:
                        record.csr.append(x)
                    for x in mem_addr:
                        record.pa.append(x)
                    for x in store_data:
                        record.stdata.append(x)
                    record.binary = opcode
                    record.mode = mode
                    record.instr_str = disas
                    record.operand = operands

                    changes = []
                    instr_cnt += 1
                    records.append(record.get_trace_string())

    with open(csv, "w") as csv_fd:
        csv_fd.writelines(records)
