# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from riescue.compliance.lib.common import format_e
from riescue.compliance.lib.riscv_instrs import RiscvFpInstr, RiscvVecInstr, RiscvIntInstr
from riescue.compliance.config import Resource


class EncodingBuilder:
    """
    Dynamically creates class depending on encoding fields.
    Provides a factory to create encoding objects for instructions
        Attributes:
            encoding_classes : Dictionary (Encoding : Encoding Class)
    """

    def __init__(self):
        """Constructor for encoding builder module"""
        self.encoding_classes = []

    def augment_members(self, fmt):
        """Modify the data members/methods to be added to the class being
        created"""
        return fmt

    def build_formats(self, encodings):
        """Parses the encodings dictionary from reader and creates dynamic
        classes for each encoding. E.g class R_FORMAT etc"""
        for typ, fmt in encodings.items():
            members = self.augment_members(fmt)
            base_class = tuple()
            encoding_class = type(typ + "_FORMAT", base_class, members)
            self.encoding_classes.append(encoding_class)

    def Factory(self, EncType):
        """Factory pattern to return object of the encoding specified
        by EncType argument"""
        fmt_obj = self.encoding_classes[format_e[EncType]]()
        return fmt_obj


class InstrBuilder:
    """
    Instruction Builder module to create dynamic instruction classes. The input
    to module is a dict {instruction:[fields]} created using reader module.
        Attributes:
            instr_classes   : dictionary of instruction classes
                              {mnemonic : Instruction class}
    """

    def __init__(self, resource_db: Resource) -> None:
        self._instr_classes = {}
        self.resource_db = resource_db

    def build_classes(self, instruction_dicts):
        self._instr_classes = {}

        for instr_dict in instruction_dicts:
            instr_type = instr_dict["type"]
            mnemonic = instr_dict["name"]

            if instr_type == "Int":
                self._instr_classes.update({mnemonic: type(mnemonic, (RiscvIntInstr,), instr_dict)})
            elif instr_type == "Float":
                self._instr_classes.update({mnemonic: type(mnemonic, (RiscvFpInstr,), instr_dict)})
            elif instr_type == "Vec":
                self._instr_classes.update({mnemonic: type(mnemonic, (RiscvVecInstr,), instr_dict)})
            else:
                raise ValueError(f"Unsupported Instruction Type: {instr_type}")

        return self._instr_classes
