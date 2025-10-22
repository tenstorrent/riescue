# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import struct
import logging
from textwrap import wrap

import numpy as np

from riescue.compliance.lib.riscv_imm_constraint_database import imm_constraints
from riescue.compliance.config import Resource

log = logging.getLogger(__name__)


def float_to_hex(value, num_bytes: int, reg_size=8) -> str:
    byte_mask = None

    # converts a floating point number to a hex string
    if num_bytes == 8:
        byte_mask = 0xFFFFFFFFFFFFFFFF
        _value = np.float64(value)
        _value = struct.unpack("<Q", struct.pack("<d", _value))[0]

    if num_bytes == 4:
        byte_mask = 0xFFFFFFFF
        _value = np.float32(value)
        _value = struct.unpack("<L", struct.pack("<f", _value))[0]

    if num_bytes == 2:
        byte_mask = 0xFFFF
        _value = np.float16(value)
        _value = struct.unpack("<H", struct.pack("<e", _value))[0]

    if reg_size == num_bytes:
        return hex(_value)

    if reg_size < num_bytes:
        assert False, f"Register size {reg_size} is smaller than the number of bytes {num_bytes} in the floating point number {value}"

    # Make a bit mask for reg_size worth bytes, and set the lower order bits to the value
    if reg_size == 8:
        _value = (0xFFFFFFFFFFFFFFFF ^ byte_mask) | _value  # all bits for bytes above num_bytes should be 1
    elif reg_size == 4:
        _value = (0xFFFFFFFF ^ byte_mask) | _value
    elif reg_size == 2:
        _value = (0xFFFF ^ byte_mask) | _value

    return hex(_value)


# FIXME if there is a rate of 20 instructions per file, while using this function, spike throws an error like invalid address "0xff000".
#       It is not clear if this address is coming from dtest, but the stopgap is to reduce the number of floating point instructions
#       per file.
def setup_memory_boilerplate(a_instr_label: str) -> list:
    # FIXME if the label isn't capitalized or otherwise heavily altered, the dtest API gets confused and uses another page with a similar name.
    #       This is in the case this instruction is a store instruction.
    blah_lin_addr = a_instr_label.upper() + "_lin_aux"
    blah_phys_addr = a_instr_label.upper() + "_phy_aux"
    size = "0x1000"
    temp = []

    temp.append(f";#random_addr(name={blah_lin_addr}, type=linear, size={size}, and_mask=0xfffffffffffff000)")
    temp.append(f";#random_addr(name={blah_phys_addr}, type=physical, size={size}, and_mask=0xfffffffffffff000)")
    temp.append(f";#page_mapping(lin_name={blah_lin_addr}, phys_name={blah_phys_addr}, v=1, r=1, w=1, a=1, d=1)")

    return blah_lin_addr, blah_phys_addr, temp


def init_memories_boilerplate(random_words, num_bytes: int, a_lin_addr: str, offsets) -> list:
    size_name = "dword"
    chop_func = lambda word: word

    if num_bytes == 4:
        size_name = "word"
        chop_func = lambda word: "0x" + word[-8:] if len(word) > 8 else word
    elif num_bytes == 2:
        size_name = "half"
        chop_func = lambda word: "0x" + word[-4:] if len(word) > 4 else word

    temp = []
    temp.append(f";#init_memory @{a_lin_addr}")

    for offset, random_word in zip(offsets, random_words):
        random_word = chop_func(random_word)
        if not random_word.startswith("0x"):
            random_word = "0x" + random_word
        temp.append(f"\t.org {hex(offset)}")
        temp.append(f"\t.{size_name} {random_word}")

    return temp


def nanbox_value(value: str, num_bytes: int, max_bytes: int = 8) -> str:
    value_tmp = value.lstrip("0x")
    value_copy = "".join("0" for digit in range(num_bytes * 2 - len(value_tmp))) + value_tmp
    if (len(value_copy) / 2) == max_bytes:
        return value_copy
    else:
        return "".join("ff" for byte in range(max_bytes - num_bytes)) + value_copy


def init_float_nanbox_in_double(random_words, num_bytes: int, a_lin_addr: str, offsets) -> list:
    temp = []
    temp.append(f";#init_memory @{a_lin_addr}")

    for offset, fp_value in zip(offsets, random_words):
        temp.append(f"\t.org {hex(offset)}")
        fp_nanboxed = nanbox_value(fp_value, num_bytes)
        temp.append(f"\t.dword 0x{fp_nanboxed}")

    return temp


def generate_a_fp_value(resource_db: Resource, num_bytes: int, num_format: str, force_generate: bool = False) -> str:
    if force_generate or resource_db.fpgen_off or "bf" in num_format:
        force_generate = False  # Restore the default value
        sign = None
        exponent = None
        significand = None
        exponent_size = None
        significand_size = None
        exponent_mask = None
        significand_mask = None

        if num_format == "fp":
            if num_bytes == 8:
                significand_size = 52
                exponent_size = 11
                exponent_mask = 0x7FF
                significand_mask = 0xFFFFFFFFFFFFF
            elif num_bytes == 4:
                significand_size = 23
                exponent_size = 8
                exponent_mask = 0xFF
                significand_mask = 0x7FFFFF
            elif num_bytes == 2:
                significand_size = 10
                exponent_size = 5
                exponent_mask = 0x1F
                significand_mask = 0x3FF
        elif num_format == "bf":
            if num_bytes == 2:
                significand_size = 7
                exponent_size = 8
                exponent_mask = 0xFF
                significand_mask = 0x7F
            else:
                assert False, f"Unsupported number of bytes {num_bytes} for bf16"
        else:
            assert False, f"Unsupported floating point format {num_format}"

        rng = resource_db.rng
        sign = rng.random_entry_in([0, 1])
        exponent = rng.random_in_range(0, 2**exponent_size - 1) & exponent_mask
        significand = rng.random_in_range(0, 2**significand_size - 1) & significand_mask
        value = sign << (exponent_size + significand_size) | (exponent << significand_size) | significand

        return hex(value)
    else:
        precision_str = "f16"
        if num_bytes == 4:
            precision_str = "f32"
        elif num_bytes == 8:
            precision_str = "f64"
        tmp_config = {"precision": precision_str}
        fp_temp = resource_db.get_fp_set("any", num_bytes, tmp_config, 1)[0]

        return fp_temp[1]


class FpLoadUtil:

    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db

    def generate_fp_values(self, instr, num_bytes: int) -> list:
        assert num_bytes in [
            2,
            4,
            8,
        ], f"Unsupported number of bytes {num_bytes} for floating point value generation"
        fp_values = None
        if "bf16" in instr.name and num_bytes == 2:
            fp_values = [generate_a_fp_value(self.resource_db, num_bytes, num_format="bf", force_generate=True) for offset in self._aux_offsets]
        elif self.resource_db.fpgen_off or "bypass" in instr.name:
            fp_values = [generate_a_fp_value(self.resource_db, num_bytes, num_format="fp", force_generate=True) for offset in self._aux_offsets]
        else:
            try:
                fp_temp = self.resource_db.get_fp_set(instr.name, num_bytes, instr.config, 1)[0]
                fp_values = fp_temp[1 : len(self._aux_offsets) + 1]
            except Exception:
                log.warning(f"Didn't find database floating point values for instruction {instr.name}")
                fp_values = [generate_a_fp_value(self.resource_db, num_bytes, num_format="fp", force_generate=True) for offset in self._aux_offsets]

        return fp_values

    def load_fp_regs_nanbox_in_double(self, a_num_bytes, a_fp_reg_names, a_instr, a_values=None, a_postfix=None):
        postfix_label = "" if a_postfix is None else ("_" + a_postfix)
        a_postfix = None
        (
            self._aux_lin_addr,
            self._aux_phys_addr,
            temp_mem_setups,
        ) = setup_memory_boilerplate(a_instr.label + postfix_label)
        self._aux_offsets = []

        for setup in temp_mem_setups:
            self._pre_setup_instrs.append(setup)

        load_instr = "fld"
        data_bytes = 8
        for data_index in range(len(a_fp_reg_names)):
            self._aux_offsets.append(data_index * data_bytes)

        temp_reg = self.get_random_reg(a_instr.reg_manager)
        self.write_pre(f"\tli {temp_reg}, {self._aux_lin_addr}")
        for offset, fp_reg_name in zip(self._aux_offsets, a_fp_reg_names):
            self.write_pre(f"\t{load_instr} {fp_reg_name}, {hex(offset)}({temp_reg})")

        # Check the properties of the value
        check_instr_suffix = "d" if a_num_bytes == 8 else "s" if a_num_bytes == 4 else "h"
        integer_reg = self.get_random_reg(a_instr.reg_manager)

        fp_numbers = None
        if a_values is not None:
            fp_numbers = a_values
        else:
            fp_numbers = self.generate_fp_values(a_instr, a_num_bytes)
        mem_inits_temp = init_float_nanbox_in_double(fp_numbers, a_num_bytes, self._aux_lin_addr, self._aux_offsets)
        a_values = None

        for init in mem_inits_temp:
            self.write_data(a_instr, init)


class LSBoilerplateComponent:
    def init_memory_boilerplate(self, instr, random_word, num_bytes):
        size_name = "dword"
        if num_bytes == 4:
            size_name = "word"
        elif num_bytes == 2:
            size_name = "half"

        self.write_data(instr, f";#init_memory @{self._lin_addr}")
        self.write_data(instr, f"\t.org {hex(self.offset)}")
        self.write_data(instr, f"\t.{size_name} {random_word}")


class CStoreComponent:
    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db

    def post_setup(self, modified_arch_state, instr):
        # FIXME log relevant info when modified_arch_state is None or ""
        if modified_arch_state is None:
            print("Problem with instr: " + instr.label)
            assert False

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


class ConstraintDBAccessComponent:
    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db

    def get_constrained_imm_value(self, instr_name: str, imm_name: str):
        self.constraints = imm_constraints(self.resource_db)
        if instr_name in self.constraints.data:
            return self.constraints.data[instr_name][imm_name]["value_generator"]()

        return None

    def get_sole_imm_value(self, instr_name: str):
        self.constraints = imm_constraints(self.resource_db)
        return self.constraints.get_sole_value_generator(instr_name)()


class Offset:
    def __init__(self, offset, size, initial_value):
        self.offset = offset
        self.size = size
        self.initial_value = initial_value

    # print method
    def __str__(self):
        return f"Offset(offset={self.offset}, size={self.size}, initial_value={self.initial_value})"


class Page:
    def __init__(self, label, size, number):
        self.label = label
        self.size = size
        self.lin_addr = f"vreg_inits_{number}_" + label + "_lin"
        self.phy_addr = f"vreg_inits_{number}_" + label + "_phy"
        self.offsets = []

    def get_setup(self) -> str:
        return (
            f";#random_addr(name={self.lin_addr}, type=linear, size={self.size}, and_mask=0xfffffffffffff000)\n"
            f";#random_addr(name={self.phy_addr}, type=physical, size={self.size}, and_mask=0xfffffffffffff000)\n"
            f";#page_mapping(lin_name={self.lin_addr}, phys_name={self.phy_addr}, v=1, r=1, w=1, a=1, d=1)\n"
        )

    def add_offsets(self, offsets):
        self.offsets.extend(offsets)

    def get_inits(self) -> str:
        _temp_inits = f";#init_memory @{self.lin_addr}\n"

        for offset in self.offsets:
            _size = offset.size
            _initial_value = offset.initial_value
            _element_size = _size // len(_initial_value)
            _size_label = "dword" if _element_size == 64 else "word" if _element_size == 32 else "hword" if _element_size == 16 else "byte"
            _temp_inits += f"\t.org {offset.offset}\n"
            _temp_inits += f"\t.{_size_label} "

            for _value in _initial_value:
                _temp_inits += f"{_value}, "

            _temp_inits = _temp_inits[:-2] + "\n"

        return _temp_inits
