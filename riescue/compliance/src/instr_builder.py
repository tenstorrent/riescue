# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Union

from riescue.compliance.lib.riscv_instrs import RiscvFpInstr, RiscvVecInstr, RiscvIntInstr
from riescue.lib.instr_info.instr_lookup_json import InstrEntry


class InstrBuilder:
    """
    Converts ``InstrEntry`` objects into dynamic instruction classes.
    This is done by creating a new class for each instruction type (Int, Float, Vec) with the appropriate fields.

    .. note::
        This class is static because it doesn't need to store any state.

    Useage:

    .. code-block:: python

        from riescue.compliance.src.instr_builder import InstrBuilder
        from riescue.lib.instr_info.instr_lookup_json import InstrEntry

        instruction_entries = [
            InstrEntry(name="add", type="Int", fields={"rd": "x1", "rs1": "x2", "rs2": "x3"}),
            InstrEntry(name="sub", type="Int", fields={"rd": "x4", "rs1": "x5", "rs2": "x6"})
        ]
        instr_classes = InstrBuilder.build_classes(instruction_entries)
        print(instr_classes)

    """

    @staticmethod
    def build_dynamic_classes(instruction_entries: list[InstrEntry]) -> list[type[Union[RiscvIntInstr, RiscvFpInstr, RiscvVecInstr]]]:
        """
        Builds dynamic instruction class templates for each instruction entry based on the instruction type.
        Classes still need to be instantiated.

        :param instruction_entries: List of instruction entries
        :return: List of instruction templates (dynamic classes)
        """

        instr_classes: list[type[Union[RiscvIntInstr, RiscvFpInstr, RiscvVecInstr]]] = []

        for instr_entry in instruction_entries:
            instr_dict = instr_entry.to_dict()
            instr_type = instr_dict["type"]
            mnemonic = instr_dict["name"]

            if not isinstance(mnemonic, str):
                raise ValueError(f"Invalid mnemonic: {mnemonic} {type(mnemonic)} (expected a str)")

            if instr_type == "Int":
                new_class = type(mnemonic, (RiscvIntInstr,), instr_dict)
            elif instr_type == "Float":
                new_class = type(mnemonic, (RiscvFpInstr,), instr_dict)
            elif instr_type == "Vec":
                new_class = type(mnemonic, (RiscvVecInstr,), instr_dict)
            else:
                raise ValueError(f"Unsupported Instruction Type: {instr_type}")
            instr_classes.append(new_class)

        return instr_classes
