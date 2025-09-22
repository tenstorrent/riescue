# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import json
import riescue.lib.common as common


class Tree:

    def __init__(self):
        self._extensions = dict()

    def reset(self):
        self._extensions = dict()

    def add_extension(self, extension) -> None:
        self._extensions[extension.name] = extension

    def get_extension_set(self, ext_name):  # -> set[Any]:
        extension = self._extensions[ext_name]
        mnemonics = set()
        for grp_name, group in extension.groups.items():
            mnemonics = mnemonics | set(group.mnemonics)
        return mnemonics

    def get_group_set(self, grp_name):
        if self.get_group(grp_name) is not None:
            return set(self.get_group(grp_name).keys())
        else:
            return set()

    def update_instrs(self, instrs):
        for instr in instrs:
            for ext_name, extension in self._extensions.items():
                for grp_name, group in extension.groups.items():
                    if instr.name in group.instrs.items():
                        group.instrs.update({instr.name: instr})

    def get_group(self, grp_name):
        # print(self._extensions.items())
        for name, extension in self._extensions.items():
            # print(extension.groups)
            if grp_name in extension.groups:
                # print(grp_name)
                return extension.get_group(grp_name).instrs

        return None

    def get_groups(self, groups):
        instrs = dict()
        for group in groups:
            for name, extension in self._extensions.items():
                if group in extension.groups:
                    instrs = {**instrs, **extension.get_group(group).instrs}
        return instrs

    def get_instr(self, mnemonic):
        for ext_name, extension in self._extensions.items():
            for grp_name, group in extension.groups.items():
                if mnemonic in group.instrs:
                    return group.instrs[mnemonic]

    def get_sim_set(self, ext_names, grp_names, mnemonics):
        sim_set = set()
        for ext_name in ext_names:
            ext_set = self.get_extension_set(ext_name)
            grp_set = set()
            instr_set = set()
            for grp_name in grp_names:
                if self._extensions[ext_name].check_group(grp_name):
                    grp_set = grp_set | self.get_group_set(grp_name)
                    for mnemonic in mnemonics:
                        if self._extensions[ext_name].groups[grp_name].check_instr(mnemonic):
                            instr_set.add(mnemonic)
                        else:
                            instr_set = instr_set | self.get_group_set(grp_name)

            if len(instr_set):
                sim_set = sim_set | instr_set
            elif len(grp_set):
                sim_set = sim_set | grp_set
            else:
                sim_set = sim_set | ext_set
        return sim_set

    def get_group_names(self, extension):
        return list(self._extensions[extension].groups.keys())

    def get_instr_names(self, extension, group):
        return list(self._extensions[extension].groups[group].instrs.keys())

    def traverse_tree(self):
        ext_dict = dict()
        for ext_name, extension in self._extensions.items():
            grp_dict = dict()
            for grp_name, group in extension.groups.items():
                instrs_dict = dict()
                for mnemonic, instr in group.instrs.items():
                    field_dict = dict()
                    for key, field in instr.get_operands().items():
                        if field != "":
                            if key == "funct3":
                                field_dict[key] = {"val": instr.funct3}
                            elif key == "funct7":
                                field_dict[key] = {"val": instr.funct7}
                            elif key == "opcode":
                                field_dict[key] = {"val": instr.opcode}
                            elif key == "24..20":  # Fixme, We don't know if this is lumop, sumop or any of the other aliases in different extensions for this slice.
                                field_dict[key] = {"val": getattr(instr, "24..20")}
                            elif key == "mop":
                                field_dict[key] = {"val": instr.mop}
                            else:
                                field_dict[key] = {"type": field.field_type, "size": field.size}
                    instrs_dict[mnemonic] = field_dict
                grp_dict[grp_name] = instrs_dict
            ext_dict[ext_name] = grp_dict

        with open("data.json", "w") as output_file:
            json.dump(ext_dict, output_file, ensure_ascii=False, indent=4)

    def update_configs(self, instr_configs):
        for ext_name, extension in self._extensions.items():
            for grp_name, group in extension.groups.items():
                for mnemonic, instr in group.instrs.items():
                    instr.config_manager.update_config(instr_configs[mnemonic])
