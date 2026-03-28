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

# Mnemonics for which the ``m`` resource ``value`` field is the store data. Whisper's ``[a:d]``
# bracket on the same line is not reliable for Sv57 (``d`` may be a walk / GPA artifact).
_WHISPER_SCALAR_STORE_MNEMONICS: frozenset[str] = frozenset({"sb", "sh", "sw", "sd", "fsw", "fsd"})


def _whisper_instr_is_scalar_store(instr: str) -> bool:
    t = instr.strip().lower()
    if t.startswith("c."):
        t = t[2:]
    return t in _WHISPER_SCALAR_STORE_MNEMONICS


def _whisper_store_stdata_for_post_setup(instr: str, val_hex: str) -> str:
    """Narrow ``m`` line ``value`` to the width actually written.

    ``StoreSetup.post_setup`` expands ``stdata`` into per-byte ``lbu`` checks. Using a full
    XLEN ``value`` for ``sh``/``sb`` zero-fills high bytes in the expectation, but only the
    low 2/1 bytes were stored — upper bytes still hold ``init_memory`` contents.
    """
    t = instr.strip().lower()
    if t.startswith("c."):
        t = t[2:]
    v = int(val_hex, 16)
    if t == "sb":
        n = v & 0xFF
        w = 2
    elif t == "sh":
        n = v & 0xFFFF
        w = 4
    elif t == "sw":
        n = v & 0xFFFFFFFF
        w = 8
    elif t == "sd":
        n = v & 0xFFFFFFFFFFFFFFFF
        w = 16
    elif t == "fsw":
        n = v & 0xFFFFFFFF
        w = 8
    elif t == "fsd":
        n = v & 0xFFFFFFFFFFFFFFFF
        w = 16
    else:
        return val_hex
    return f"{n:x}".zfill(w)


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
        return f"{self.pc},{self.instr},{';'.join(self.gpr)},{';'.join(self.csr)},{' '.join(self.pa)},{' '.join(self.stdata)},{self.binary},{self.mode},{self.instr_str},{self.operand}\n"


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
def process_whisper_sim_log(
    whisper_log: Path,
    csv: Path,
    full_trace: int = 1,
    *,
    narrow_scalar_store_stdata: bool = False,
) -> None:
    """Process Whisper simulation log.

    Extract instruction and affected register information from whisper simulation
    log and save to CSV file.

    Multi-line records (same rank '#N') are merged into a single CSV row so that
    atomic (AMO) instructions, which produce both an 'r' line (GPR destination
    update) and an 'm' line (memory write), are captured in one row with correct
    GPR, pa, and stdata fields.

    SV39 note: when Whisper is run with --traceptw the bracket annotation on
    every memory-touching log line changes from [paddr] to [vaddr:paddr].  We
    intentionally ignore ad[1] (the physical address) in all bracket annotations
    because it is never the stored data value — the actual stored value always
    comes from the 'm'-resource line's val field via the fallback below.

    :param narrow_scalar_store_stdata: If True, narrow ``stdata`` for sb/sh/sw/sd to
        the width actually written (used by compliance second-pass store self-check).
        If False (default), emit full XLEN hex strings as in the Whisper ``m`` line,
        matching unit tests and legacy CSV consumers.
    """
    instr_cnt = 0
    records = ["pc,instr,gpr,csr,pa,stdata,binary,mode,instr_str,operand,pad\n"]

    with open(whisper_log, "r") as f:
        # State accumulated for the current multi-line instruction record.
        current_rank = None
        changes = []  # list of (resource, addr_str, val) for current instruction
        rec_pc = None
        rec_opcode = ""
        rec_mode = ""
        rec_instr = ""
        rec_disas = ""
        rec_operands = ""
        # Memory address / store-data accumulated from bracket annotations.
        # For Whisper these are overridden by the m-resource fallback, but we
        # accumulate them in case a non-Whisper ISS uses the [a:d] bracket format.
        acc_mem_addr = []
        acc_store_data = []

        def emit_record():
            nonlocal instr_cnt
            if rec_pc is None:
                return
            regs = []
            csrs = []
            mem_addr = list(acc_mem_addr)
            store_data = list(acc_store_data)
            for res, addr_str, val in changes:
                addr = int(addr_str, 16)
                if res == "r" and addr != 0:
                    regs.append(int_reg_name(addr) + ":" + val)
                elif res == "f":
                    regs.append(fp_reg_name(addr) + ":" + val)
                elif res == "v":
                    # Whisper logs wide vector registers as colon-separated 64-bit
                    # chunks (e.g. "0x1234:0x5678:...").  Strip the 0x prefixes and
                    # colons so downstream consumers see a single hex string.
                    normalized = val.replace("0x", "").replace(":", "")
                    regs.append("v" + str(addr) + ":" + normalized)
                elif res == "c":
                    csrs.append("c" + str(addr) + ":" + val)
                elif res == "m":
                    # Whisper's stored value is always in the val field of the
                    # 'm' resource line; use it as the authoritative stdata
                    # (overriding anything accumulated from bracket annotations).
                    if len(store_data) == 0:
                        mem_addr = [addr_str]
                        if narrow_scalar_store_stdata and _whisper_instr_is_scalar_store(rec_instr):
                            store_data = [_whisper_store_stdata_for_post_setup(rec_instr, val)]
                        else:
                            store_data = [val]

            record = RiscvInstructionTraceEntry()
            record.pc = rec_pc
            record.instr = rec_instr
            for x in regs:
                record.gpr.append(x)
            for x in csrs:
                record.csr.append(x)
            for x in mem_addr:
                record.pa.append(x)
            for x in store_data:
                record.stdata.append(x)
            record.binary = rec_opcode
            record.mode = rec_mode
            record.instr_str = rec_disas
            record.operand = rec_operands
            instr_cnt += 1
            records.append(record.get_trace_string())

        for line in f:
            fields = line.split()

            if len(fields) >= 8:
                (rank, hart, mode, pc, opcode, resource, addr, value, instr) = fields[0:9]

                if rank != current_rank:
                    # New instruction: flush the previously accumulated record.
                    emit_record()
                    # Reset all per-record state.
                    current_rank = rank
                    changes = []
                    acc_mem_addr = []
                    acc_store_data = []
                    rec_pc = pc
                    rec_opcode = opcode
                    rec_mode = mode
                    rec_instr = instr

                # Pop the continuation marker '+' if present.
                if fields[-1] == "+":
                    fields.pop()

                # Process the optional bracket annotation at the end of the line.
                # Expecting [a] or [a:d] or [a;a;a;...] or [a:d;a:d;...]
                # where a is an address and d is a data value with an 0x prefix.
                #
                # Bug fix (SV39 / --traceptw): Whisper appends [vaddr:paddr] to
                # every memory-touching log line, for ALL resource types (r, m,
                # f, v, ...).  In that format ad[1] is the physical address, not
                # the stored data.  We therefore never treat ad[1] as store_data
                # here; the actual stored value is always captured via the
                # 'm'-resource fallback in emit_record().
                if fields[-1].startswith("["):
                    mem_items = fields[-1].strip("[]").replace("0x", "").split(";")
                    mem_items.sort()  # To match other iss tool
                    for x in mem_items:
                        ad = x.split(":")
                        if len(ad) > 0:
                            acc_mem_addr.append(ad[0])
                        # Intentionally NOT using ad[1] as store_data for any
                        # resource type — see the SV39 note above.
                    fields.pop()

                disas = " ".join(fields[7:])  # instruction disassembly
                operands = " ".join(fields[8:])  # instruction operands
                rec_disas = disas
                rec_operands = operands

                changes.append((resource, addr, value))

        # Flush the final record.
        emit_record()

    with open(csv, "w") as csv_fd:
        csv_fd.writelines(records)
