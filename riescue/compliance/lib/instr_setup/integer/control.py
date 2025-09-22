# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.base import InstrSetup


class DestRegSrcImmSetup(InstrSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        imm = instr.imms[0]
        rd = instr.dests[0]
        self._asm_instr = f"\t{instr.name} {rd.name}, {str(hex(imm.value))}"
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class DestRegSrcImmSrcPCSetup(InstrSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        imm = instr.imms[0]
        rd = instr.dests[0]
        self._asm_instr = f"\t{instr.name} {rd.name}, {str(hex(imm.value))}"

        self.pc_reg = self.get_random_reg(instr.reg_manager)
        self.imm_reg = self.get_random_reg(instr.reg_manager)

        self.write_pre(f"\tauipc {self.pc_reg}, {0x0}")
        self.write_pre(f"\taddi {self.pc_reg}, {self.pc_reg}, 8")
        self.write_pre(self._asm_instr)
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"sub {self.imm_reg}, {rd.name}, {self.pc_reg}")
