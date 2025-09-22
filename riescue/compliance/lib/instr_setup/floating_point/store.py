# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.floating_point.base import FloatComponent, do_load_fp_regs
from riescue.compliance.lib.instr_setup.base import InstrSetup
from riescue.compliance.lib.instr_setup.utils import (
    FpLoadUtil,
    ConstraintDBAccessComponent,
    LSBoilerplateComponent,
    CStoreComponent,
    nanbox_value,
    generate_a_fp_value,
)


class FloatStoreRegBasedSetup(FpLoadUtil, ConstraintDBAccessComponent, LSBoilerplateComponent, FloatComponent, CStoreComponent, InstrSetup):
    def __init__(self, resource_db):
        FpLoadUtil.__init__(self, resource_db)
        ConstraintDBAccessComponent.__init__(self, resource_db)
        FloatComponent.__init__(self, resource_db)
        CStoreComponent.__init__(self, resource_db)
        InstrSetup.__init__(self, resource_db)

    def pre_setup(self, instr):
        rs1 = self.get_operand(instr, "rs1")
        rs2 = self.get_operand(instr, "rs2")
        temp_reg = self.get_random_reg(instr.reg_manager)
        self.offset = self.get_sole_imm_value(instr.name)
        if self.resource_db.force_alignment:
            self.offset = (self.offset // 4) * 4

        num_bytes, size_name = (8, "d")
        if "w" in instr.name:
            num_bytes, size_name = (4, "w")
        elif "h" in instr.name:
            num_bytes, size_name = (2, "h")

        mem_addr = f"{hex(self.offset)}({rs1.name})"
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t{instr.name} {rs2.name}, {mem_addr}\n"
        self.setup_csrs(temp_reg)
        if do_load_fp_regs(self.resource_db):
            self.load_fp_regs_nanbox_in_double(num_bytes, [rs2.name], instr)
        else:
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs2.value, num_bytes)}")
            self.write_pre(f"\tfmv.{size_name}.x {rs2.name}, {temp_reg}")
        self.write_pre(f"\tli {rs1.name}, {self._lin_addr}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = "0x" + nanbox_value(generate_a_fp_value(self.resource_db, num_bytes, num_format="fp"), num_bytes)
        self.init_memory_boilerplate(instr, random_word, 8)
