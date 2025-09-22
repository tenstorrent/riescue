# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.floating_point.base import FpSetup, FloatComponent, do_load_fp_regs
from riescue.compliance.lib.instr_setup.utils import FpLoadUtil
from riescue.compliance.config import Resource


class FpRegRegCompareSetup(FpLoadUtil, FpSetup, FloatComponent):

    def __init__(self, resource_db):
        FpLoadUtil.__init__(self, resource_db)
        FpSetup.__init__(self, resource_db)
        FloatComponent.__init__(self, resource_db)

    def pre_setup(self, instr):
        rs1 = instr.srcs[0]
        rs2 = instr.srcs[1]
        rd = self.get_random_reg(instr.reg_manager)

        self.size_name = "d"
        self.num_bytes = 8
        if ".s" in instr.name:
            self.size_name = "s"
            self.num_bytes = 4
        elif ".h" in instr.name:
            self.size_name = "h"
            self.num_bytes = 2

        if do_load_fp_regs(self.resource_db):
            self.load_fp_regs_nanbox_in_double(self.num_bytes, [rs1.name, rs2.name], instr)
        else:
            temp_reg = self.get_random_reg(instr.reg_manager)
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs2.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs2.name}, {temp_reg}")

        self.write_pre(f"{instr.label}: {instr.name} {rd},{rs1.name},{rs2.name}")
