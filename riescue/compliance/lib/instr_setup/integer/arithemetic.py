# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.base import InstrSetup


class RegRegSetup(InstrSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        self._pre_setup_instrs = []
        rs1 = instr.srcs[0]
        rs2 = instr.srcs[1]
        rd = instr.dests[0]
        self._asm_instr = f"\t{instr.name} {rd.name},{rs1.name},{rs2.name}"
        self.write_pre(f"\tli {rs1.name}, {str(hex(rs1.value))}")
        self.write_pre(f"\tli {rs2.name}, {str(hex(rs2.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class RegImmSetup(InstrSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rs1 = instr.srcs[0]
        imm = instr.imms[0]
        rd = instr.dests[0]
        self._asm_instr = f"\t{instr.name} {rd.name},{rs1.name},{str(hex(imm.value))}"
        self.write_pre(f"\tli {rs1.name},{str(hex(rs1.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class SingleRegSetup(InstrSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        for src in instr.srcs:
            if src.is_initialized:
                setup_instr = "\tli " + src.name + "," + str(hex(src.value))
                self._pre_setup_instrs.append(setup_instr)
                setup_instr = instr.label + ":\n\t" + instr.name + " " + instr.dests[0].name + "," + ",".join([name for name in [src.name for src in instr.srcs]])
        self._pre_setup_instrs.append(setup_instr)

    def post_setup(self, modified_arch_state, instr):
        mod_gprs = modified_arch_state[2].split(";")
        gprs = self.get_gprs(mod_gprs)

        for gpr, value in gprs.items():
            result_reg = self.get_random_reg(instr.reg_manager)
            setup_instr = "\tli {},{}".format(result_reg, "0x" + value)
            self._post_setup_instrs.append(setup_instr)
            self.write_post(f"\tbne {result_reg}, {gpr}, 1f")

        if self.j_pass_ok():
            self.write_post(";#test_passed()")
        else:
            self.write_post("\tj 2f")
        self.write_post("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_post(";#test_failed()")
        self.write_post("\t2:\n")
