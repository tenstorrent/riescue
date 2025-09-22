# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from textwrap import wrap

from riescue.compliance.lib.instr_setup import LdStBaseSetup


class AtomicMemoryOperationsSetup(LdStBaseSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd = self.get_operand(instr, "rd")
        rs1 = self.get_operand(instr, "rs1")
        rs2 = self.get_operand(instr, "rs2")
        aq = self.get_operand(instr, "aq")
        rl = self.get_operand(instr, "rl")

        aqrl_value = (aq.value << 1) | rl.value

        aqrl_suffix = ""
        if aqrl_value == 0b11:
            aqrl_suffix = ".aqrl"
        elif aqrl_value == 0b01:
            aqrl_suffix = ".rl"
        elif aqrl_value == 0b10:
            aqrl_suffix = ".aq"
        else:
            aqrl_suffix = ""

        if instr.name.endswith("w"):
            self._offset.value = self._offset.value & (0xFFF ^ 0b11)
        else:
            self._offset.value = self._offset.value & (0xFFF ^ 0b111)

        mem_addr = "(" + rs1.name + ")"
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        if instr.name.endswith("w"):
            random_word = self._rng.random_nbit(32)
            self.write_pre(f"\tli {rs2.name},{hex(random_word)}")
        else:  # FIXME when we are loading 64 bit values we should avoid the assembler truncating them
            random_word = self._rng.random_nbit(32)
            self.write_pre(f"\tli {rs2.name},{hex(random_word)}")
            bits_written = 32
            while bits_written < 64:
                random_word = self._rng.random_nbit(12)
                self.write_pre(f"\tslli {rs2.name}, {rs2.name}, {hex(12)}")
                self.write_pre(
                    f"\taddi {rs2.name}, {rs2.name}, {hex(random_word & 0b11111111111)}"
                )  # FIXME the compiler rejects arguments greater than 11 bits but the standard allows 12 bits. What is going on here?
                bits_written += 12

        self.write_pre(f"\tli {rs1.name},{self._lin_addr}")
        self.write_pre(f"\taddi {rs1.name}, {rs1.name}, {hex(self._offset.value)}")
        self.write_pre(f"{instr.label}: {instr.name}{aqrl_suffix} {rd.name}, {rs2.name}, {mem_addr}")

        random_word, size_name = (self._rng.random_nbit(32), "word") if instr.name.endswith("w") else (self._rng.random_nbit(64), "dword")
        self.write_data(instr, f";#init_memory @{self._lin_addr}")
        self.write_data(instr, f"\t.org {hex(self._offset.value)}")
        self.write_data(instr, f"\t.{size_name} {hex(random_word)}")

    def post_setup(self, modified_arch_state, instr):
        """This code assumes *spike* is returning a semicolon separated string of addresses, byte by byte"""
        mod_mem_loc = modified_arch_state[4]
        mod_mem_vals = modified_arch_state[5]
        byte_values = mod_mem_vals.split(";")

        result_reg1 = self.get_random_reg(instr.reg_manager)
        result_reg2 = self.get_random_reg(instr.reg_manager)
        base_addr_reg = self.get_random_reg(instr.reg_manager)

        mod_gprs = modified_arch_state[2].split(";")
        gprs = self.get_gprs(mod_gprs)

        """Check the modified gprs"""
        for gpr, value in gprs.items():
            self.write_post(f'\tli {result_reg1},{"0x"+value}')
            if self.resource_db.wysiwyg:
                temp_reg = self.get_random_reg(instr.reg_manager)
                self.write_post(f"\tsub {temp_reg},{gpr},{result_reg1}")
                self.write_post(f"\tadd x31,x31,{temp_reg}")
            else:
                self.write_post(f"\tbne {result_reg1}, {gpr}, 1f")

        self.write_post("\tj 2f")
        self.write_post("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_post("\tli a0, failed_addr")
            self.write_post("\tld a1, 0(a0)")
            self.write_post("\tjalr ra, 0(a1)")
        self.write_post("\t2:\n")

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
