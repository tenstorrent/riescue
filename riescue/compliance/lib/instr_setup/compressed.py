# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import struct
from textwrap import wrap


from riescue.compliance.config import Resource
from riescue.compliance.lib.instr_setup import InstrSetup, LoadSetup, StoreSetup
from riescue.compliance.lib.instr_setup.utils import generate_a_fp_value
from riescue.compliance.lib.riscv_imm_constraint_database import imm_constraints


class ConstraintDBAccessComponent:
    def get_constrained_imm_value(self, resource_db: Resource, instr_name: str, imm_name: str):
        self.constraints = imm_constraints(resource_db)
        if instr_name in self.constraints.data:
            return self.constraints.data[instr_name][imm_name]["value_generator"]()

        return None

    def get_sole_imm_value(self, resource_db: Resource, instr_name: str):
        self.constraints = imm_constraints(resource_db)
        return self.constraints.get_sole_value_generator(instr_name)()


class C1NopSetup(InstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)

    def post_setup(self, modified_arch_state, instr):
        # Since this is a nop, there isn't supposed to be any modified state.
        if not self.resource_db.wysiwyg:
            if self.j_pass_ok():
                self.write_post(";#test_passed()")
        else:
            temp_reg = self.get_random_reg(instr.reg_manager)
            self.write_post(f"\tadd x31,x31,{temp_reg}")


class CBC1CompIntRegImmSetup(ConstraintDBAccessComponent, InstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd_rs1_p = self.get_operand(instr, "rd_rs1_p")
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_rs1_p.name}, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rd_rs1_p.name}, {str(hex(rd_rs1_p.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C1CompIntRegImmSetup(ConstraintDBAccessComponent, InstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd_rs1_n0 = self.get_operand(instr, "rd_rs1_n0")
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_rs1_n0.name}, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rd_rs1_n0.name}, {str(hex(rd_rs1_n0.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C1CompIntRegImmSrxiSetup(ConstraintDBAccessComponent, InstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd_rs1_p = self.get_operand(instr, "rd_rs1_p")
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_rs1_p.name}, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rd_rs1_p.name}, {str(hex(rd_rs1_p.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C1CompIntRegImmSlliSetup(ConstraintDBAccessComponent, InstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd_rs1_n0 = self.get_operand(instr, "rd_rs1_n0")
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_rs1_n0.name}, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rd_rs1_n0.name}, {str(hex(rd_rs1_n0.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C1CompIntRegImmAddiwSetup(ConstraintDBAccessComponent, InstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd_rs1_n0 = self.get_operand(instr, "rd_rs1_n0")
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_rs1_n0.name}, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rd_rs1_n0.name}, {str(hex(rd_rs1_n0.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C0CompIntRegImmSetup(ConstraintDBAccessComponent, InstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd_p = self.get_operand(instr, "rd_p")
        sp_init_value = self.resource_db.rng.get_rand_bits(64)
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_p.name}, sp, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli sp, {hex(sp_init_value)}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C2CompIntRegRegMoveSetup(InstrSetup):
    def pre_setup(self, instr):
        rd = self.get_operand(instr, "rd")
        if rd is None:
            rd = self.get_operand(instr, "rd_n0")
        c_rs2_n0 = self.get_operand(instr, "c_rs2_n0")
        assert rd is not None
        assert c_rs2_n0 is not None

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd.name}, {c_rs2_n0.name}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {c_rs2_n0.name}, {str(hex(c_rs2_n0.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C1CompIntConGenSetup(ConstraintDBAccessComponent, InstrSetup):
    def pre_setup(self, instr):
        rd = self.get_operand(instr, "rd")
        if rd is None:
            rd = self.get_operand(instr, "rd_n0")
            assert rd is not None

        imm = self.get_sole_imm_value(self.resource_db, instr.name)
        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd.name}, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C1CompIntConGenSetupLUI(ConstraintDBAccessComponent, InstrSetup):
    def pre_setup(self, instr):
        rd_n2 = self.get_operand(instr, "rd_n2")
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_n2.name}, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C1CompIntImmSetup(ConstraintDBAccessComponent, InstrSetup):
    def pre_setup(self, instr):
        sp_init_value = self.resource_db.rng.get_rand_bits(64)
        imm = self.get_sole_imm_value(self.resource_db, instr.name)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} sp, {imm}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli sp, {hex(sp_init_value)}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class C2CompIntRegRegSetup(InstrSetup):
    def pre_setup(self, instr):
        rd_rs1 = self.get_operand(instr, "rd_rs1")
        if rd_rs1 is None:
            rd_rs1 = self.get_operand(instr, "rd_rs1_n0")
            assert rd_rs1 is not None
        c_rs2_n0 = self.get_operand(instr, "c_rs2_n0")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_rs1.name}, {c_rs2_n0.name}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rd_rs1.name}, {str(hex(rd_rs1.value))}")
        self.write_pre(f"\tli {c_rs2_n0.name}, {str(hex(c_rs2_n0.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class CAC1CompIntRegRegSetup(InstrSetup):
    def pre_setup(self, instr):
        rd_rs1_p = self.get_operand(instr, "rd_rs1_p")
        rs2_p = self.get_operand(instr, "rs2_p")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_rs1_p.name}, {rs2_p.name}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rd_rs1_p.name}, {str(hex(rd_rs1_p.value))}")
        self.write_pre(f"\tli {rs2_p.name}, {str(hex(rs2_p.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(self._asm_instr)


class DoublePrecisionComponent:
    def setup_csrs(self):
        if self.resource_db.wysiwyg:
            mstatus_fsvs_set = "0x2200"
            self.write_pre(f"\tli x1, {mstatus_fsvs_set}")
            self.write_pre("\tcsrrs x0, mstatus, x1")

    def float_to_hex(self, value: float) -> str:
        return hex(struct.unpack("<Q", struct.pack("<d", value))[0])


class LSBoilerplateComponent:
    def init_memory_boilerplate(self, instr, random_word):
        self.write_data(instr, f";#init_memory @{self._lin_addr}")
        self.write_data(instr, f"\t.org {hex(self.offset)}")
        self.write_data(instr, f"\t.dword {random_word}")


class C0DoubleLoadRegBasedSetup(
    ConstraintDBAccessComponent,
    LSBoilerplateComponent,
    DoublePrecisionComponent,
    LoadSetup,
):
    def pre_setup(self, instr):
        rd_p = self.get_operand(instr, "rd_p")
        rs1_p = self.get_operand(instr, "rs1_p")
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        mem_addr = f"{hex(self.offset)}({rs1_p.name})"
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_p.name}, {mem_addr}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rs1_p.name}, {self._lin_addr}")
        self.setup_csrs()
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = generate_a_fp_value(self.resource_db, 8, num_format="fp")
        self.init_memory_boilerplate(instr, random_word)

    def post_setup(self, modified_arch_state, instr):
        mod_gprs = modified_arch_state[2].split(";")
        gprs = self.get_gprs(mod_gprs)
        result_reg = self.get_random_reg(instr.reg_manager, "Float")

        for gpr, value in gprs.items():
            self.write_post(f"\tli x1,{self._lin_addr}")
            self.write_post(f"\tfld {result_reg}, {hex(self.offset)}(x1)")
            self.write_post(f"\tfmv.x.d x2, {result_reg}")
            self.write_post(f"\tfmv.x.d x3, {gpr}")

            if self.resource_db.wysiwyg:
                temp_reg = self.get_random_reg(instr.reg_manager)
                self.write_post(f"\tsub {temp_reg}, x2, x3")
                self.write_post(f"\tadd x31,x31,{temp_reg}")
            else:
                self.write_post("\tbne x2, x3, 1f")

        if self.j_pass_ok():
            self.write_post(";#test_passed()")
        else:
            self.write_post("\tj 2f\n")
        self.write_post("\t1:\n")
        if not self.resource_db.wysiwyg:
            self.write_post(";#test_failed()")
        self.write_post("\t2:\n")


class CStoreComponent:
    def post_setup(self, modified_arch_state, instr):
        # FIXME log relevant info when modified_arch_state is None or ""
        if modified_arch_state is None:
            raise Exception(f"Problem with instr: {instr.label}. modified_arch_state is None")

        """This code assumes *spike* is returning a semicolon separated string of addresses, byte by byte"""
        mod_mem_loc = modified_arch_state[4]
        mod_mem_vals = modified_arch_state[5]
        byte_values = mod_mem_vals.split(";")

        result_reg1 = self.get_random_reg(instr.reg_manager)
        result_reg2 = self.get_random_reg(instr.reg_manager)
        base_addr_reg = self.get_random_reg(instr.reg_manager)

        """Spike instead decided to provide one address and one full word"""
        if len(byte_values) == 1:
            byte_values_temp = wrap(byte_values[0], 2)

            byte_values = byte_values_temp
            """Byte sequence needs to be least significant byte first"""
            if not self.resource_db.big_endian:
                byte_values = byte_values_temp[::-1]

        for byte_number, byte_value in enumerate(byte_values):
            mem_addr_argument = hex(self.offset + byte_number) + "(" + base_addr_reg + ")"

            self.write_post(f'\tli {result_reg1}, {"0x"+byte_value}')
            self.write_post(f"\tli {base_addr_reg}, {self._lin_addr}")
            self.write_post(f"\tlbu {result_reg2}, {mem_addr_argument}")

            if self.resource_db.wysiwyg:
                temp_reg = self.get_random_reg(instr.reg_manager)
                self.write_post(f"\tsub {temp_reg},{result_reg1},{result_reg2}")
                self.write_post(f"\tadd x31,x31,{temp_reg}")
            else:
                self.write_post(f"\tbne {result_reg1}, {result_reg2}, 1f")

        if self.j_pass_ok():
            self.write_post(";#test_passed()")
        else:
            self.write_post("\tj 2f\n")
        self.write_post("\t1:\n")
        if not self.resource_db.wysiwyg:
            self.write_post(";#test_failed()")
        self.write_post("\t2:\n")


class C0DoubleStoreRegBasedSetup(
    ConstraintDBAccessComponent,
    LSBoilerplateComponent,
    DoublePrecisionComponent,
    CStoreComponent,
    StoreSetup,
):
    def pre_setup(self, instr):
        rs1_p = self.get_operand(instr, "rs1_p")
        rs2_p = self.get_operand(instr, "rs2_p")
        temp_reg = self.get_random_reg(instr.reg_manager)
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        mem_addr = f"{hex(self.offset)}({rs1_p.name})"
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rs2_p.name}, {mem_addr}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rs1_p.name}, {self._lin_addr}")
        self.write_pre(f'\tli {temp_reg}, {generate_a_fp_value(self.resource_db, 8, num_format="fp")}')
        self.setup_csrs()
        self.write_pre(f"\tfmv.d.x {rs2_p.name}, {temp_reg}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = generate_a_fp_value(self.resource_db, 8, num_format="fp")
        self.init_memory_boilerplate(instr, random_word)


class C0CompIntRegRegStoreSetup(LSBoilerplateComponent, ConstraintDBAccessComponent, CStoreComponent, StoreSetup):
    def pre_setup(self, instr):
        rs1_p = self.get_operand(instr, "rs1_p")
        rs2_p = self.get_operand(instr, "rs2_p")
        temp_reg = self.get_random_reg(instr.reg_manager)
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        mem_addr = f"{hex(self.offset)}({rs1_p.name})"
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rs2_p.name}, {mem_addr}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rs1_p.name}, {self._lin_addr}")
        self.write_pre(f"\tli {rs2_p.name}, {hex(rs2_p.value)}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = self._rng.random_nbit(64)

        self.init_memory_boilerplate(instr, random_word)


class C0CompIntRegRegLoadSetup(LSBoilerplateComponent, ConstraintDBAccessComponent, LoadSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd_p = self.get_operand(instr, "rd_p")
        rs1_p = self.get_operand(instr, "rs1_p")
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        mem_addr = f"{hex(self.offset)}({rs1_p.name})"
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_p.name}, {mem_addr}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {rs1_p.name}, {self._lin_addr}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = self._rng.random_nbit(64)

        self.init_memory_boilerplate(instr, random_word)


class CI_C2_CompIntSPLoadSetup(LSBoilerplateComponent, ConstraintDBAccessComponent, LoadSetup):
    def pre_setup(self, instr):
        rd_n0 = self.get_operand(instr, "rd_n0")
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd_n0.name}, {hex(self.offset)}(sp)\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli sp, {self._lin_addr}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = self._rng.random_nbit(64)

        self.init_memory_boilerplate(instr, random_word)


class CSS_C2_CompIntSPStoreSetup(LSBoilerplateComponent, ConstraintDBAccessComponent, CStoreComponent, StoreSetup):
    def pre_setup(self, instr):
        c_rs2 = self.get_operand(instr, "c_rs2")
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {c_rs2.name}, {hex(self.offset)}(sp)\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli {c_rs2.name}, {hex(c_rs2.value)}")
        self.write_pre(f"\tli sp, {self._lin_addr}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = self._rng.random_nbit(64)

        self.init_memory_boilerplate(instr, random_word)


class JumpComponent:
    def jump_sled(self, instr, num_fullsize, num_compressed):
        safe_label = instr.label.replace(".", "_")

        for instr_count in range(num_fullsize):
            self.write_pre(f"\tj jump_{safe_label}_end")

        if num_compressed > 0:
            self.write_pre("\t.option rvc")
            for instr_count in range(num_compressed):
                self.write_pre(f"\tc.j jump_{safe_label}_end")
            if num_compressed % 2 == 1:
                self.write_pre(f"\tc.j jump_{safe_label}_end")
            self.write_pre("\t.option norvc")

    def jump_custom_sled_w_target(self, instr, num_fullsize, num_compressed, post_execution_label):
        num_compressed += num_fullsize * 2

        self.write_pre("\t.option rvc")
        if num_compressed > 2:
            num_phase_one = num_compressed // 2 + num_compressed % 2
            num_phase_two = num_compressed - num_phase_one

            assert (num_phase_one + num_phase_two) == num_compressed
            for instr_count in range(num_phase_one):
                self.write_pre("\tc.nop")
            for instr_count in range(num_phase_two):
                self.write_pre(f"\tc.j {post_execution_label}")

        else:
            self.write_pre(f"\tc.j {post_execution_label}")

        if num_compressed % 2 == 1:
            self.write_pre(f"\tc.j {post_execution_label}")

        self.write_pre("\t.option norvc")

    def compute_full_and_c_distribution(self, pad_size_in_bytes, backwards):
        num_fullsize = abs(pad_size_in_bytes // 4)
        remainder = abs(pad_size_in_bytes % 4)
        num_compressed = remainder // 2
        remainder = remainder % 2

        offset_adjustment = 0
        if remainder > 0:
            if not backwards:
                offset_adjustment += 1
            elif backwards:
                offset_adjustment -= 1
            num_compressed += 1

        return tuple((num_fullsize, num_compressed, offset_adjustment))


class CompressedJumpSetup(JumpComponent, ConstraintDBAccessComponent, InstrSetup):
    def pre_setup(self, instr):
        dot_free_label = instr.label.replace(".", "_")
        post_execution_label = f"jump_{dot_free_label}_post"
        self.branch_backwards = False
        defacto_pad_size = 8

        if instr.name in ["c.jr", "c.jalr"]:
            c_rs1_n0 = self.get_operand(instr, "c_rs1_n0")
            rs1_n0 = self.get_operand(instr, "rs1_n0")
            if c_rs1_n0 is None:
                c_rs1_n0 = rs1_n0

            self.branch_backwards = self.resource_db.rng.random_entry_in([True, False])
            (
                self.num_fullsize,
                self.num_compressed,
                self.offset_adjustment,
            ) = self.compute_full_and_c_distribution(pad_size_in_bytes=defacto_pad_size, backwards=self.branch_backwards)
            self.offset = (-1 if self.branch_backwards else 1) * defacto_pad_size + self.offset_adjustment  # FIXME this offset needs to be an implicit constraint of c.jr and c.jalr

            self.write_pre(f"la {c_rs1_n0.name}, {instr.label}")  # {self.offset}')
            self.write_pre(f"addi {c_rs1_n0.name}, {c_rs1_n0.name}, {self.offset}")
            self.instr_call = f"{instr.name} {c_rs1_n0.name}"
        else:
            self.offset = self.get_sole_imm_value(self.resource_db, instr.name)
            if self.offset < 0:
                self.branch_backwards = True

            (
                self.num_fullsize,
                self.num_compressed,
                offset_adjustment,
            ) = self.compute_full_and_c_distribution(pad_size_in_bytes=self.offset, backwards=self.branch_backwards)
            self.offset += offset_adjustment

            self.instr_call = f"{instr.name} {instr.label} + {self.offset}"

        self.write_pre(f"\tj {instr.label}")

        if self.branch_backwards:
            self.jump_custom_sled_w_target(instr, self.num_fullsize, self.num_compressed, post_execution_label)

        self._asm_instr = f"\t.option rvc\n" f"\t{self.instr_call}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        if not self.branch_backwards:
            self.jump_custom_sled_w_target(instr, self.num_fullsize, self.num_compressed, post_execution_label)

        if self.resource_db.wysiwyg:
            self.write_pre("\tli x31, 0xbaadc0de")
        else:
            self.write_pre(";#test_failed()")

        self.write_pre(f"jump_{dot_free_label}_post:")
        self.write_pre("\tnop")

    def post_setup(self, modified_arch_state, instr):
        if self.j_pass_ok():
            self.write_post(";#test_passed()")


class CompressedBranchSetup(ConstraintDBAccessComponent, InstrSetup):
    def jump_sled(self, instr, num_fullsize, num_compressed):
        safe_label = instr.label.replace(".", "_")

        for instr_count in range(num_fullsize):
            self.write_pre(f"\tj jump_{safe_label}_end")

        if num_compressed > 0:
            self.write_pre("\t.option rvc")
            for instr_count in range(num_compressed):
                self.write_pre(f"\tc.j jump_{safe_label}_end")
            if num_compressed % 2 == 1:
                self.write_pre(f"\tc.j jump_{safe_label}_end")
            self.write_pre("\t.option norvc")

    def pre_setup(self, instr):
        rs1_p = self.get_operand(instr, "rs1_p")
        branch_toggle = self._rng.random_nbit(1)
        if branch_toggle == 0:
            self.write_pre(f"\tmv {rs1_p.name}, x0")
        else:
            self.write_pre(f"\tli {rs1_p.name}, {hex(rs1_p.value)}")

        self.write_pre(f"\tj {instr.label}")

        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        self.pad_size = 0
        self.pad_size = abs(self.offset)
        num_fullsize = self.pad_size // 4
        remainder = self.pad_size % 4
        num_compressed = remainder // 2
        remainder = remainder % 2
        if remainder > 0:
            if self.offset > 0:
                self.offset += 1
            elif self.offset < 0:
                self.offset -= 1
            num_compressed += 1

        if self.offset <= 0:
            self.jump_sled(instr, num_fullsize, num_compressed)

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rs1_p.name}, {instr.label} + {self.offset}\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        if self.offset > 0:
            self.jump_sled(instr, num_fullsize, num_compressed)

        safe_label = instr.label.replace(".", "_")
        self.write_pre(f"jump_{safe_label}_end:")
        self.write_pre("\tnop")

    def post_setup(self, modified_arch_state, instr):
        if self.j_pass_ok():
            self.write_post(";#test_passed()")


class CompDoubleSPLoadSetup(
    ConstraintDBAccessComponent,
    LSBoilerplateComponent,
    DoublePrecisionComponent,
    LoadSetup,
):
    def pre_setup(self, instr):
        rd = self.get_operand(instr, "rd")
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)

        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {rd.name}, {hex(self.offset)}(sp)\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli sp, {self._lin_addr}")
        self.setup_csrs()
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = generate_a_fp_value(self.resource_db, 8, num_format="fp")
        self.init_memory_boilerplate(instr, random_word)

    def post_setup(self, modified_arch_state, instr):
        mod_gprs = modified_arch_state[2].split(";")
        gprs = self.get_gprs(mod_gprs)
        result_reg = self.get_random_reg(instr.reg_manager, "Float")

        for gpr, value in gprs.items():
            self.write_post(f"\tli x1,{self._lin_addr}")
            self.write_post(f"\tfld {result_reg}, {hex(self.offset)}(x1)")
            self.write_post(f"\tfmv.x.d x2, {result_reg}")
            self.write_post(f"\tfmv.x.d x3, {gpr}")

            if self.resource_db.wysiwyg:
                temp_reg = self.get_random_reg(instr.reg_manager)
                self.write_post(f"\tsub {temp_reg}, x2, x3")
                self.write_post(f"\tadd x31,x31,{temp_reg}")
            else:
                self.write_post("\tbne x2,x3,failed")
                self.write_post("\tbne x2, x3, 1f")

        if self.j_pass_ok():
            self.write_post(";#test_passed()")
        else:
            self.write_post("\tj 2f\n")
        self.write_post("\t1:\n")
        if not self.resource_db.wysiwyg:
            self.write_post(";#test_failed()")
        self.write_post("\t2:\n")


class CompDoubleSPStoreSetup(
    ConstraintDBAccessComponent,
    LSBoilerplateComponent,
    DoublePrecisionComponent,
    CStoreComponent,
    StoreSetup,
):
    def pre_setup(self, instr):
        c_rs2 = self.get_operand(instr, "c_rs2")
        self.offset = self.get_sole_imm_value(self.resource_db, instr.name)
        temp_reg = self.get_random_reg(instr.reg_manager)

        self.setup_memory(instr.label, "0x1000", "pre_setup")

        self._asm_instr = f"\t.option rvc\n" f"\t{instr.name} {c_rs2.name}, {hex(self.offset)}(sp)\n" f"\tc.nop\n" f"\t.option norvc\n"
        self.write_pre(f"\tli sp, {self._lin_addr}")
        self.write_pre(f'\tli {temp_reg}, {generate_a_fp_value(self.resource_db, 8, num_format="fp")}')
        self.setup_csrs()
        self.write_pre(f"\tfmv.d.x {c_rs2.name}, {temp_reg}")
        self.write_pre(f"{instr.label}:")
        self.write_pre(self._asm_instr)

        random_word = generate_a_fp_value(self.resource_db, 8, num_format="fp")
        self.init_memory_boilerplate(instr, random_word)
