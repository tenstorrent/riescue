from riescue.compliance.lib.riscv_instrs import InstrBase

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


class Group:
    """
    Class for storing group related information.
        Attributes:
            name    : Identifier for groups ['compute_register_register','single_precision_compute']
            instrs  : Dictionary to store Instr objects defined underneath the group.
    """

    def __init__(self, name):
        self._name = name
        self._instrs = dict()

    @property
    def name(self) -> str:
        return self._name

    @property
    def instrs(self) -> dict:
        return self._instrs

    @property
    def mnemonics(self) -> list:
        return list(self._instrs.keys())

    def add_instr(self, instr: InstrBase) -> None:
        self._instrs[instr.name] = instr

    def get_instr(self, mnemonic: str) -> InstrBase:
        return self._instrs[mnemonic]

    def check_instr(self, mnemonic: str) -> bool:
        if mnemonic in self._instrs:
            return True
        return False

    def __str__(self):
        return ",".join(list(self._instrs.keys()))
