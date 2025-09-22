# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from textwrap import wrap

from riescue.compliance.lib.immediate import Immediate12
from riescue.compliance.lib.instr_setup.base import InstrSetup


class LdStBaseSetup(InstrSetup):

    def __init__(self, resource_db):
        self._offset = Immediate12("", resource_db=resource_db, value=0, field_name="imm12", field_type="immediate", aligned=8)
        self._offset.randomize()
        self._lin_addr = ""
        self._phy_addr = ""
        super().__init__(resource_db)


class LoadSetup(LdStBaseSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):

        rs1 = instr.srcs[0]
        rd = instr.dests[0]
        mem_addr = hex(self._offset.value) + "(" + rs1.name + ")"
        self.setup_memory(instr.label, "0x1000", "pre_setup")
        self.write_pre(f"\tli {rs1.name},{self._lin_addr}")
        self.write_pre(f"{instr.label}: {instr.name} {rd.name},{mem_addr}")

        random_word = self._rng.random_nbit(32)
        self.write_data(instr, f";#init_memory @{self._lin_addr}")
        self.write_data(instr, f"\t.org {hex(self._offset.value)}")
        self.write_data(instr, f"\t.word {hex(random_word)}")


class StoreSetup(LdStBaseSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rs1 = instr.srcs[0]
        rs2 = instr.srcs[1]

        # Protect against generating illegal offset values FIXME
        if "sd" in instr.name:
            if self._offset.value >= 0x7F9:
                self._offset.value = 0x7F8
        elif "sw" in instr.name:
            if self._offset.value >= 0x7FD:
                self._offset.value = 0x7FC
        elif "sh" in instr.name:
            if self._offset.value >= 0x7FF:
                self._offset.value = 0x7FE
        elif "sb" in instr.name:
            if self._offset.value >= 0x800:
                self._offset.value = 0x7FF

        mem_addr = hex(self._offset.value) + "(" + rs2.name + ")"
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        random_word = self._rng.random_nbit(32)
        self.write_pre(f"\tli {rs1.name},{hex(random_word)}")
        self.write_pre(f"\tli {rs2.name},{self._lin_addr}")
        self.write_pre(f"{instr.label}: {instr.name} {rs1.name},{mem_addr}")

        random_word = self._rng.random_nbit(32)
        self.write_data(instr, f";#init_memory @{self._lin_addr}")
        self.write_data(instr, f"\t.org {hex(self._offset.value)}")
        self.write_data(instr, f"\t.word {hex(random_word)}")

    # FIXME this code needs to be re-checked and possibly simplified
    def post_setup(self, modified_arch_state, instr):
        # FIXME log relevant info when modified_arch_state is None or ""

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
            mem_addr_argument = hex(self._offset.value + byte_number) + "(" + base_addr_reg + ")"

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
            self.write_post("\tli a0, passed_addr")
            self.write_post("\tld a1, 0(a0)")
            self.write_post("\tjalr ra, 0(a1)")
        else:
            self.write_post("\tj 2f")
        self.write_post("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_post("\tli a0, failed_addr")
            self.write_post("\tld a1, 0(a0)")
            self.write_post("\tjalr ra, 0(a1)")
        self.write_post("\t2:\n")
