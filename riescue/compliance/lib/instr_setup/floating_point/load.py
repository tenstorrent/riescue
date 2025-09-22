# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.floating_point.arithmetic import FpRegRegSetup
from riescue.compliance.lib.instr_setup.utils import LSBoilerplateComponent, ConstraintDBAccessComponent, nanbox_value, generate_a_fp_value


class FpLoadSetup(LSBoilerplateComponent, ConstraintDBAccessComponent, FpRegRegSetup):

    def __init__(self, resource_db):
        ConstraintDBAccessComponent.__init__(self, resource_db)
        FpRegRegSetup.__init__(self, resource_db)

    def pre_setup(self, instr):
        rs1 = self.get_operand(instr, "rs1")
        rd = self.get_operand(instr, "rd")
        self.offset = self.get_sole_imm_value(instr.name)  # FIXME: Is this missing some inheritence?
        if self.resource_db.force_alignment:
            self.offset = (self.offset // 4) * 4
        mem_addr = f"{hex(self.offset)}({rs1.name})"

        if "ld" in instr.name:
            self.size_name = "d"
            self.num_bytes = 8
        elif "lw" in instr.name:
            self.size_name = "w"
            self.num_bytes = 4
        elif "lh" in instr.name:
            self.size_name = "h"
            self.num_bytes = 2

        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self.write_pre(f"\tli {rs1.name}, {self._lin_addr}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(f"\t{instr.name} {rd.name}, {mem_addr}")

        random_word = "0x" + nanbox_value(generate_a_fp_value(self.resource_db, self.num_bytes, num_format="fp"), self.num_bytes)
        self.init_memory_boilerplate(instr, random_word, 8)
