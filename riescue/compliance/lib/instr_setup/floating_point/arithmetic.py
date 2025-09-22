# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.utils import FpLoadUtil
from riescue.compliance.lib.instr_setup.floating_point import FpSetup, FloatComponent
from riescue.compliance.lib.instr_setup.floating_point.base import do_load_fp_regs
from riescue.compliance.config import Resource


class FpRegRegSetup(FpLoadUtil, FpSetup, FloatComponent):

    def __init__(self, resource_db):
        FpLoadUtil.__init__(self, resource_db)
        FpSetup.__init__(self, resource_db)
        FloatComponent.__init__(self, resource_db)

    def pre_setup(self, instr):
        rs1 = instr.srcs[0]
        rs2 = instr.srcs[1]
        rd = instr.dests[0]

        self.size_name = "d"
        self.num_bytes = 8
        if ".s" in instr.name:
            self.size_name = "s"
            self.num_bytes = 4
        elif ".h" in instr.name:
            self.size_name = "h"
            self.num_bytes = 2

        if self.do_load_fp_regs():
            self.load_fp_regs_nanbox_in_double(self.num_bytes, [rs1.name, rs2.name], instr)
        else:
            temp_reg = self.get_random_reg(instr.reg_manager)
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs2.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs2.name}, {temp_reg}")

        if instr.config is not None and instr.config.frm is not None:
            self.write_pre(f"{instr.label}:\n\t{instr.name} {rd.name},{rs1.name},{rs2.name},{self.get_rounding_mode(instr)}")
        else:
            self.write_pre(f"{instr.label}:\n\t{instr.name} {rd.name},{rs1.name},{rs2.name}")

    def post_setup(self, modified_arch_state, instr):
        if modified_arch_state is None:
            print("Problem with :" + instr.label)
        mod_gprs = modified_arch_state[2].split(";")
        gprs = self.get_gprs(mod_gprs)
        filtered_gprs = dict()
        for gpr, value in gprs.items():
            if gpr.startswith("f"):
                filtered_gprs[gpr] = value
        gprs = filtered_gprs

        if self.do_load_fp_regs():
            fprs = []
            values = []
            value_mask = None

            mnemonic_suffix = ""
            if self.num_bytes == 8:
                mnemonic_suffix = "d"
                value_mask = 0xFFFFFFFFFFFFFFFF
            elif self.num_bytes == 4:
                mnemonic_suffix = "s"
                value_mask = 0xFFFFFFFF
            elif self.num_bytes == 2:
                mnemonic_suffix = "h"
                value_mask = 0xFFFF

            for gpr, value in gprs.items():
                # value_num = int(value, 16)
                # value_num = value_num & value_mask
                # fp_class = fp_classify(value_num, self.num_bytes)
                # if "nan" in fp_class[1]:
                #     continue

                fprs.append(self.get_random_reg(instr.reg_manager, "Float"))
                values.append(value)

            if len(fprs) > 0:
                self.load_fp_regs_nanbox_in_double(self.num_bytes, fprs, instr, values, "post")
            result_reg = self.get_random_reg(instr.reg_manager, "Int")
            one_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.write_post(f"\tli {one_reg}, 0x1")

            for fpr, (gpr, value) in zip(fprs, gprs.items()):
                self.write_post(f"\tfeq.{mnemonic_suffix} {result_reg}, {fpr}, {fpr}")  # NaN compared with itself is False, so skip the test if we see this
                self.write_post(f"\tbne {result_reg}, {one_reg}, 3f")
                self.write_post(f"\tfeq.{mnemonic_suffix} {result_reg}, {fpr}, {gpr}")
                if self.resource_db.wysiwyg:
                    self.write_post(f"\tsub {result_reg}, {one_reg}, {result_reg}")  # FIXME has a problem if the result should be a NaN
                    self.write_post(f"\tadd x31, x31, {result_reg}")
                else:
                    self.write_post(f"\tbne {result_reg}, {one_reg}, 1f")
        else:
            result_gpr1 = self.get_random_reg(instr.reg_manager, "Int")
            result_gpr2 = self.get_random_reg(instr.reg_manager, "Int")
            result_gpr3 = self.get_random_reg(instr.reg_manager, "Int")

            if self.num_bytes == 4:
                self.write_post(f"\tli {result_gpr3}, 0xffffffff")
            elif self.num_bytes == 2:
                self.write_post(f"\tli {result_gpr3}, 0xffff")

            for gpr, value in gprs.items():
                self.write_post(f"\tli {result_gpr1}, 0x{value}")
                if self.num_bytes != 8:
                    self.write_post(f"\tand {result_gpr1}, {result_gpr1}, {result_gpr3}")
                self.write_post(f"\tfmv.x.{self.size_name} {result_gpr2}, {gpr}")
                if self.num_bytes != 8:
                    self.write_post(f"\tand {result_gpr2}, {result_gpr2}, {result_gpr3}")

                if self.resource_db.wysiwyg:
                    self.write_post(f"\tsub {result_gpr1}, {result_gpr1}, {result_gpr2}")
                    self.write_post(f"\tadd x31, x31, {result_gpr1}")
                else:
                    self.write_post(f"\tbne {result_gpr1}, {result_gpr1}, 1f")

        if self.j_pass_ok():
            self.write_post("\t3:")
            self.write_post("\tli a0, passed_addr")
            self.write_post("\tld a1, 0(a0)")
            self.write_post("\tjalr ra, 0(a1)")
        else:
            self.write_post("\t3:")
            self.write_post("\tj 2f")
        self.write_post("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_post("\tli a0, failed_addr")
            self.write_post("\tld a1, 0(a0)")
            self.write_post("\tjalr ra, 0(a1)")
        self.write_post("\t2:\n")


class FpRegRegRegSetup(FpRegRegSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rs1 = instr.srcs[0]
        rs2 = instr.srcs[1]
        rs3 = instr.srcs[2]
        rd = instr.dests[0]

        self.size_name = "d"
        self.num_bytes = 8
        if ".s" in instr.name:
            self.size_name = "s"
            self.num_bytes = 4
        elif ".h" in instr.name:
            self.size_name = "h"
            self.num_bytes = 2

        if self.do_load_fp_regs():
            self.load_fp_regs_nanbox_in_double(self.num_bytes, [rs1.name, rs2.name, rs3.name], instr)
        else:
            temp_reg = self.get_random_reg(instr.reg_manager)
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs2.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs2.name}, {temp_reg}")
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs3.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs3.name}, {temp_reg}")

        self.write_pre(f"{instr.label}: {instr.name} {rd.name}, {rs1.name}, {rs2.name}, {rs3.name}, {self.get_rounding_mode(instr)}")


class FpSqrtSetup(FpRegRegSetup):

    def __init__(self, resource_db: Resource):
        super().__init__(resource_db)

    def pre_setup(self, instr):

        rs1 = instr.srcs[0]
        rd = instr.dests[0]

        self.size_name = "d"
        self.num_bytes = 8
        if ".s" in instr.name:
            self.size_name = "s"
            self.num_bytes = 4
        elif ".h" in instr.name:
            self.size_name = "h"
            self.num_bytes = 2

        if self.do_load_fp_regs():
            self.load_fp_regs_nanbox_in_double(self.num_bytes, [rs1.name], instr)
        else:
            temp_reg = self.get_random_reg(instr.reg_manager)
            self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, self.num_bytes)}")
            self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")

        self.write_pre(f"{instr.label}: {instr.name} {rd.name},{rs1.name},{self.get_rounding_mode(instr)}")
