# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import numpy as np

from riescue.compliance.lib.instr_setup.floating_point.base import FpSetup, FloatComponent, do_load_fp_regs
from riescue.compliance.lib.instr_setup.floating_point.arithmetic import FpRegRegSetup
from riescue.compliance.lib.instr_setup.utils import FpLoadUtil, generate_a_fp_value


class FpConvertMove(FpLoadUtil, FpSetup, FloatComponent):

    def __init__(self, resource_db):
        FpLoadUtil.__init__(self, resource_db)
        FpSetup.__init__(self, resource_db)
        FloatComponent.__init__(self, resource_db)

    def pre_setup(self, instr):
        self.setup_memory(instr.label, "0x1000", "pre_setup")
        integer_reg = self.get_random_reg(instr.reg_manager)
        integer_value = self._rng.random_nbit(32)

        if ".l" in instr.name:
            integer_value = self._rng.random_nbit(64)

        rs1 = instr.srcs[0]
        rd = None

        self.size_name = "d"
        self.num_bytes = 8
        if ".s." in instr.name:
            self.size_name = "s"
            self.num_bytes = 4
        elif ".d." in instr.name:
            self.size_name = "d"
            self.num_bytes = 8
        elif ".h." in instr.name:
            self.size_name = "h"
            self.num_bytes = 2
        elif instr.name.endswith("d"):
            self.size_name = "d"
            self.num_bytes = 8
        elif instr.name.endswith("s"):
            self.size_name = "s"
            self.num_bytes = 4
        elif instr.name.endswith("h"):
            self.size_name = "h"
            self.num_bytes = 2
        elif ".w.x" in instr.name:
            self.size_name = "s"
            self.num_bytes = 4
        elif ".x.w" in instr.name:
            self.size_name = "s"
            self.num_bytes = 4

        temp_reg = self.get_random_reg(instr.reg_manager)

        if any([instr.name.startswith(stem) for stem in ["fcvt.w.", "fcvt.wu.", "fcvt.l", "fcvt.lu"]]):
            if do_load_fp_regs(self.resource_db):
                self.load_fp_regs_nanbox_in_double(self.num_bytes, [rs1.name], instr)
            else:
                self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, self.num_bytes)}")
                self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")
            self.write_pre(f"{instr.label}: {instr.name} {integer_reg},{rs1.name},{self.get_rounding_mode(instr)}")

        elif any([instr.name.startswith(stem) for stem in ["fcvt.s.", "fcvt.d.", "fcvt.h"]]) and any([instr.name.endswith(stem) for stem in [".w", ".wu", ".l", ".lu"]]):
            self.write_pre(f"\tli {integer_reg},{hex(integer_value)}")

            if not any(frag in instr.name for frag in ["d.w", "d.wu"]):
                self.write_pre(f"{instr.label}: {instr.name} {rs1.name},{integer_reg},{self.get_rounding_mode(instr)}")
            else:
                self.write_pre(f"{instr.label}: {instr.name} {rs1.name},{integer_reg}")
        elif any([instr.name.startswith(stem) for stem in ["fcvt.s.", "fcvt.h", "fcvt.bf16"]]) and any([instr.name.endswith(stem) for stem in [".s", ".d", ".h", ".bf16"]]):
            # source size is either 8, 4 or 2 bytes
            source_size = 8 if instr.name.endswith("d") else 4 if instr.name.endswith("s") else 2
            destination_size = 8 if instr.name.startswith("fcvt.d") else 4 if instr.name.startswith("fcvt.s") else 2
            self.size_name = "d" if source_size == 8 else "s" if source_size == 4 else "h"
            self.num_bytes = destination_size

            rd = instr.dests[0]
            if do_load_fp_regs(self.resource_db):
                self.load_fp_regs_nanbox_in_double(
                    source_size, [rs1.name], instr
                )  # TODO FIXME not using the value of rs1.value because the register class doesn't produce an integer for hwords instead of a decimal.
            else:
                # depending on the source size use numpy to convert the float to the correct size
                if source_size == 8:
                    rs1.value = np.float64(rs1.value)
                elif source_size == 4:
                    rs1.value = np.float32(rs1.value)
                elif source_size == 2 and "h" in instr.name:
                    rs1.value = np.float16(rs1.value)
                elif source_size == 2 and "bf16" in instr.name:
                    rs1.value = np.float16(rs1.value)
                self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, source_size)}")
                self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")
            if any(substr in instr.name for substr in [".s.d", ".h.s", ".h.d", "bf16"]):  # Roundings are done when downcasting.
                self.write_pre(f"{instr.label}: {instr.name} {rd.name}, {rs1.name}, {self.get_rounding_mode(instr)}")
            else:  # No rounding is done when upcasting.
                self.write_pre(f"{instr.label}: {instr.name} {rd.name}, {rs1.name}")
        elif instr.name in ["fcvt.d.s", "fcvt.d.h"]:
            rd = instr.dests[0]
            if do_load_fp_regs(self.resource_db):
                self.load_fp_regs_nanbox_in_double(8, [rs1.name], instr)
            else:
                self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, 8)}")
                self.write_pre(f"\tfmv.d.x {rs1.name}, {temp_reg}")
            self.write_pre(f"{instr.label}: {instr.name} {rd.name}, {rs1.name}")
        elif instr.name in ["fmv.x.w", "fmv.x.d", "fmv.x.s", "fmv.x.h"]:
            rd = instr.dests[0]
            if do_load_fp_regs(self.resource_db):
                self.load_fp_regs_nanbox_in_double(self.num_bytes, [rs1.name], instr)
            else:
                self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, self.num_bytes)}")
                self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")
            self.write_pre(f"{instr.label}: {instr.name} {rd.name}, {rs1.name}")
        elif instr.name in ["fmv.w.x", "fmv.d.x", "fmv.s.x", "fmv.h.x"]:
            rd = instr.dests[0]
            integer_value = int(generate_a_fp_value(self.resource_db, self.num_bytes, num_format="fp"), 16)
            self.write_pre(f"\tli {rs1.name}, {hex(integer_value)}")
            self.write_pre(f"{instr.label}: {instr.name} {rd.name}, {rs1.name}")
        elif instr.name in ["fsgnj.s", "fsgnjn.s", "fsgnjx.s", "fsgnj.d", "fsgnjn.d", "fsgnjx.d"]:
            rs2 = instr.srcs[1]
            rd = instr.dests[0]
            if do_load_fp_regs(self.resource_db):
                self.load_fp_regs_nanbox_in_double(self.num_bytes, [rs1.name, rs2.name], instr)
            else:
                self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs1.value, self.num_bytes)}")
                self.write_pre(f"\tfmv.{self.size_name}.x {rs1.name}, {temp_reg}")
                self.write_pre(f"\tli {temp_reg}, {self.float_to_hex(rs2.value, self.num_bytes)}")
                self.write_pre(f"\tfmv.{self.size_name}.x {rs2.name}, {temp_reg}")
            self.write_pre(f"{instr.label}: {instr.name} {rd.name},{rs1.name},{rs2.name}")

    def post_setup(self, modified_arch_state, instr):
        if any([instr.name.startswith(stem) for stem in ["fcvt.w.", "fcvt.wu.", "fcvt.l.", "fcvt.lu.", "fmv.x."]]):
            FpSetup.post_setup(self, modified_arch_state, instr)
        else:
            FpRegRegSetup.post_setup(self, modified_arch_state, instr)
