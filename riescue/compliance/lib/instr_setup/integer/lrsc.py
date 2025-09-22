# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from .load_store import LdStBaseSetup


class LoadReservedSetup(LdStBaseSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        rd = self.get_operand(instr, "rd")
        rs1 = self.get_operand(instr, "rs1")
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

        random_word = self._rng.random_nbit(32) if instr.name.endswith("w") else self._rng.random_nbit(64)
        self.write_pre(f"\tli {rs1.name},{self._lin_addr}")
        self.write_pre(f"\taddi {rs1.name}, {rs1.name}, {hex(self._offset.value)}")
        self.write_pre(f"{instr.label}: {instr.name}{aqrl_suffix} {rd.name}, {mem_addr}")

        random_word, size_name = (self._rng.random_nbit(32), "word") if instr.name.endswith("w") else (self._rng.random_nbit(64), "dword")
        self.write_data(instr, f";#init_memory @{self._lin_addr}")
        self.write_data(instr, f"\t.org {hex(self._offset.value)}")
        self.write_data(instr, f"\t.{size_name} {hex(random_word)}")
