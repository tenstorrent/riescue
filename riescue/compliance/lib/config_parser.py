# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import json
import copy
from pathlib import Path

from riescue.compliance.config import Resource


class ConfigParser:
    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db
        self._default_config = dict()
        self._user_config = dict()

        THIS_DIR = Path(__file__).resolve().parent
        compliance_lib = Path(__file__).resolve().parent
        riescue_src = compliance_lib.parents[1]
        groups_names_path = riescue_src / "lib/instr_info/groups_to_instruction_names.json"
        with open(groups_names_path) as f:
            group_names_dictionary_unformatted = json.load(f)

        # The extensions are dummy names in the group_names_dictionary, so we need to remove them
        # It is organized like so {'extension_name': {'group_name': ['instruction_name', 'instruction_name', ...]}}
        # We want to remove the extension_name key and just have {'group_name': ['instruction_name', 'instruction_name', ...]}
        self.group_names_to_instruction_names = dict()
        for extension_name in group_names_dictionary_unformatted.keys():
            self.group_names_to_instruction_names.update(group_names_dictionary_unformatted[extension_name])

        """
        Parse the default configuration file specifying all the parameters for the extension, group and instructions
        """
        with open(self.resource_db.default_config) as default_config_file:
            config = json.load(default_config_file)
            self._default_config = config["extensions"]
            self.resource_db.test_configs = config["test"]
            self.parse_default_config()

        if self.resource_db.user_config is not None:
            with open(self.resource_db.user_config) as user_config:
                self._user_config = json.load(user_config)["config"]
                self.override()

        self.resource_db.instr_configs = self.extract_instr_configs()

        # FIXME need to make config excludes part of the normal config file for FP and for Vector
        with open(self.resource_db.fp_config) as fp_config_file:
            self.resource_db.fp_configs = json.load(fp_config_file)

        self.validate_groups_contents_disjoint()
        self.validate_default_config_groups_contents_disjoint()

    def validate_group_name(self, group_name):
        if group_name in self.group_names_to_instruction_names.keys():
            return True
        else:
            return False

    def validate_instruction_name(self, instruction_name):
        for group_name in self.group_names_to_instruction_names.keys():
            if instruction_name in self.group_names_to_instruction_names[group_name]:
                return True
        return False

    def validate_groups_contents_disjoint(self):
        groups = []
        for instructions in self.group_names_to_instruction_names.values():
            instructions_set = set(instructions)
            assert len(instructions_set) == len(instructions), f"Group {instructions} contains duplicate instructions"
            groups.append(instructions_set)

        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                assert groups[i].isdisjoint(groups[j]), f"Groups {groups[i]} and {groups[j]} have overlapping instructions"

        # print(f"groups disjoint check passed")

    def validate_default_config_groups_contents_disjoint(self):
        groups = []
        for ext, ext_cfgs in self._default_config.items():
            for grp, grp_cfgs in ext_cfgs["groups"].items():
                instructions_set = set(grp_cfgs["instrs"].keys())
                assert len(instructions_set) == len(grp_cfgs["instrs"].keys()), f"Group {instructions_set} contains duplicate instructions"
                groups.append(instructions_set)

        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                assert groups[i].isdisjoint(groups[j]), f"Groups {groups[i]} and {groups[j]} have overlapping instructions"

        # print(f"config groups disjoint check passed")

    def parse_default_config(self):

        # Parse extension parameters.
        for ext, ext_cfgs in self._default_config.items():

            # Parse group parameters
            for grp, grp_cfgs in ext_cfgs["groups"].items():
                assert self.validate_group_name(grp), f"Group name {grp} is not valid, refer to opcodes files or lib/instr_info/groups_to_instruction_names.json"

                # Add in the extension config to the group config but dont replace keys already present in the group config
                grp_cfgs["configs"] = copy.deepcopy({**ext_cfgs["configs"], **grp_cfgs["configs"]})

                # Parse instruction parameters
                for instr, instr_cfgs in grp_cfgs["instrs"].items():
                    assert self.validate_instruction_name(instr), f"Instruction name {instr} is not valid, refer to opcodes files or lib/instr_info/groups_to_instruction_names.json"

                    # Add in the group config to the instruction config but dont replace keys already present in the instruction config
                    instr_cfgs["configs"] = copy.deepcopy({**grp_cfgs["configs"], **instr_cfgs["configs"]})

    def override(self):

        # Parse extension parameters.
        if "test" in self._user_config:
            self.resource_db.test_configs = self._user_config["test"]

        for ext, ext_cfgs in self._default_config.items():

            # If user has defined an extension override, deep copy the extension config
            ext_specified = False
            if ext in self._user_config:
                ext_specified = True
                # Old ext level configs were already represented in groups etc if they weren't overridden

            for grp, grp_cfgs in ext_cfgs["groups"].items():
                assert self.validate_group_name(grp), f"Group name {grp} is not valid, refer to opcodes files or lib/instr_info/groups_to_instruction_names.json"

                previous_group_entries = set(grp_cfgs["configs"].keys())
                previous_values = dict(grp_cfgs["configs"])

                # Replace the extension level config fields in the previously established group configs
                if ext_specified:
                    grp_cfgs["configs"] = copy.deepcopy(
                        {
                            **grp_cfgs["configs"],
                            **self._user_config[ext].get("configs", self._user_config[ext]),
                        }
                    )

                # Override with any group level config fields
                if grp in self._user_config:
                    grp_cfgs["configs"] = copy.deepcopy(
                        {
                            **grp_cfgs["configs"],
                            **self._user_config[grp].get("configs", self._user_config[grp]),
                        }
                    )
                    # We want to start with the group config, which already is supposed to represent the extension config, and then override with the user config
                    # even if ext has changed, we shouldn't throw out everything given by the default config such as group level overrides

                current_group_entries = set(grp_cfgs["configs"].keys())

                new_entries = current_group_entries - previous_group_entries
                possibly_replaced_entries = current_group_entries.intersection(previous_group_entries)
                changed_entries = set()
                for entry in possibly_replaced_entries:
                    if grp_cfgs["configs"][entry] != previous_values[entry]:
                        changed_entries.add(entry)

                entries_to_inherit = new_entries.union(changed_entries)
                entries_to_inherit_dict = {entry: grp_cfgs["configs"][entry] for entry in entries_to_inherit}

                for instr, instr_cfgs in grp_cfgs["instrs"].items():
                    assert self.validate_instruction_name(instr), f"Instruction name {instr} is not valid, refer to opcodes files or lib/instr_info/groups_to_instruction_names.json"

                    replacements = dict()
                    if instr in self._user_config:
                        replacements = copy.deepcopy(
                            {
                                **entries_to_inherit_dict,
                                **self._user_config[instr].get("configs", self._user_config[instr]),
                            }
                        )
                    else:
                        replacements = copy.deepcopy({**entries_to_inherit_dict})

                    # No need to eliminate keys we aren't specifying in the user config, otherwise that would ignore everything we specified previously but aren't trying to change now
                    instr_cfgs["configs"] = copy.deepcopy({**instr_cfgs["configs"], **replacements})

            # Make the config information accurate for API use that might depend on this
            if ext_specified:
                ext_cfgs["configs"] = copy.deepcopy(
                    {
                        **ext_cfgs["configs"],
                        **self._user_config[ext].get("configs", self._user_config[ext]),
                    }
                )

    def extract_instr_configs(self):

        instr_configs = dict()

        for _, ext_cfgs in self._default_config.items():
            for grp, grp_cfgs in ext_cfgs["groups"].items():
                for instr, instr_cfgs in grp_cfgs["instrs"].items():
                    instr_configs[instr] = instr_cfgs["configs"]

        return instr_configs
