#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import yaml
import json
import copy
import pathlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Union


@dataclass
class InstrEntry:
    """
    Class to represent an instruction entry in the source instruction dictionary.
    This includes all parsed entries from the "Instructions" subdictionary

    "Instruction" entries are encoded as a key and tuple of (``instruction_id``, ``encoded_string``)
    The ``encoded_string`` is a JSON string that contains the instruction configuration.

    ..code-block:: JSON

        "add": [
                0,
                "{\"encoding\": \"0000000----------000-----0110011\", \"extension\": [\"rv_i\"], \"mask\": \"0xfe00707f\",  \
                \"match\": \"0x33\", \"variable_fields\": [\"rd\", \"rs1\", \"rs2\"],  \
                \"group\": \"rv32i_compute_register_register\", \"opcode\": \"0x33\"}"
            ],

    ..note::
        This ``InstrEntry`` class was added to help with type checking and parsing the instruction entries.
        This is assuming the encoded JSON strings are escaped to save on space, but there wasn't any documented reason this is using encoded strings.

        It seems to trade off space for speed, where there's less memory overhead for the instructions and they get lazily loaded.
        This object aims to add type checking and wrap that lazy loading.

    """

    name: str
    instruction_id: int
    encoding: str
    extension: list[str]
    mask: str
    match: str
    variable_fields: list[str]
    group: str
    opcode: str
    extra_fields: dict[str, str]  #: Additional fields that are extension specific. E.g. mop for vector
    type: str = "Int"

    @classmethod
    def from_entry(cls, instr_name: str, entry: list[Union[int, str]]) -> "InstrEntry":
        """
        classmethod to lazily load the instruction configuration from the instruction JSON.

        Assumes entry is correctly formatted as [instruction_id, encoded_string].

        :param instr_name: The name of the instruction.
        :param entry: The entry from the instruction dictionary.
        """

        instr_id, instr_blob = entry
        if not isinstance(instr_blob, str) or not isinstance(instr_id, int):
            raise ValueError(f"Expected entry to be list of length 2: {entry} {type(entry)}. Expected a list of [instruction_id, encoded_string].")
        instruction_entry = json.loads(instr_blob)

        if instr_name.startswith("v"):
            instr_type = "Vec"
        elif (instr_name.startswith("f") or instr_name.startswith("c.f")) and not instr_name == "fence":
            instr_type = "Float"
        else:
            instr_type = "Int"

        # any fields that are extension specific
        known_fields = {"encoding", "extension", "mask", "match", "variable_fields", "group", "opcode"}
        fields = {key: value for key, value in instruction_entry.items() if key not in known_fields}

        return cls(
            name=instr_name,
            instruction_id=instr_id,
            encoding=instruction_entry["encoding"],
            extension=instruction_entry["extension"],
            mask=instruction_entry["mask"],
            match=instruction_entry["match"],
            variable_fields=instruction_entry["variable_fields"],
            group=instruction_entry["group"],
            opcode=instruction_entry["opcode"],
            extra_fields=fields,
            type=instr_type,
        )

    def to_dict(self) -> dict[str, Union[str, int, list[str], None]]:
        "Used for backwards compatibility with the old code."
        instr_dict: dict[str, Union[str, int, list[str], None]] = {
            "name": self.name,
            "instruction_id": self.instruction_id,
            "encoding": self.encoding,
            "extension": self.extension,
            "mask": self.mask,
            "match": self.match,
            "variable_fields": self.variable_fields,
            "group": self.group,
            "opcode": self.opcode,
            "type": self.type,
            "config": None,
        }
        for variable_field in self.variable_fields:
            instr_dict[variable_field] = None
        instr_dict.update(self.extra_fields)

        return instr_dict


# Simpler and more easily read and updated instruction info source.
class InstrInfoJson:
    isa_info_path = pathlib.Path(__file__).parent.resolve()
    instr_dict_filename = "instr_dict.yaml"  # Filename for the YAML file from riscv-opcodes.
    instr_query_dict_filename = "instr_query_dict.json"  # Filename for the JSON file that will be used to query the instructions.
    groups_filename = "groups_to_instruction_names.json"  # Filename for the YAML file from riscv-opcodes.
    supplementary_dict_filename = "supplementary_instr_dict.yaml"  # Filename for the YAML file derived from riscv-opcodes.
    # Things like segmented LS instructions that aren't explicitly in the YAML file. Also proprietary instructions.
    riscv_extensions = [
        "rv_zbt",
        "rv_zfbfmin",
        "rv_zvfbfmin",
        "rv_zvfbfwma",
        "rv_zifencei",
        "rv64_zbb",
        "rv64_zbs",
        "rv_h",
        "rv_zcmp",
        "rv_zba",
        "rv64_a",
        "rv64_zkne",
        "rv32_zkne",
        "rv_zbkc",
        "rv_v",
        "rv_zvkned",
        "rv_zknhb",
        "rv32_zkn",
        "rv_zbkb",
        "rv_zbc",
        "rv_zksed",
        "rv_c",
        "rv_zfh",
        "rv_zvbb",
        "rv_zvbc",
        "rv_zvkg",
        "rv64_zknd",
        "rv_a",
        "rv32_zknh",
        "rv_svinval",
        "Xsfvqdotq",
        "rv_zicbo",
        "rv64_f",
        "rv64_zkn",
        "rv_zkn",
        "rv_q",
        "rv64_zks",
        "rv64_i",
        "rv_i",
        "rv64_d",
        "rv_f",
        "rv_zbkx",
        "rv_zknh",
        "rv64_zk",
        "rv_d",
        "rv32_c",
        "rv_zicsr",
        "rv64_zknh",
        "rv_d_zfh",
        "rv_zawrs",
        "rv32_zk",
        "rv32_zks",
        "rv32_c_f",
        "rv_zcb",
        "rv_c_d",
        "rv_m",
        "rv64_zba",
        "rv64_c",
        "rv_zks",
        "rv_system",
        "rv64_q",
        "rv_zksh",
        "rv64_zfh",
        "rv_zbb",
        "rv32_zknd",
        "rv_zk",
        "rv_zcmt",
        "rv64_zbkb",
        "rv_s",
        "rv64_m",
        "rv_q_zfh",
        "rv_zbs",
        "rv64_h",
        "rv64_zcb",
        "rv_f_zfa",
        "rv_zbr",
        "rv64_zbr",
        "rv_d_zfa",
        "rv_q_zfa",
        "rv_zfh_zfa",
    ]  # What the riscv foundation calls the extensions in riscv-opcodes.

    translation_from_riescue_to_riscv_extensions = OrderedDict(
        [
            ("i_ext", ["rv_i", "rv64_i"]),
            ("m_ext", ["rv_m", "rv64_m"]),
            ("f_ext", ["rv_f", "rv64_f"]),
            ("rv_zbt", ["rv_zbt"]),
            ("rv_d_zfa", ["rv_d_zfa"]),
            ("rv_zfh_zfa", ["rv_zfh_zfa"]),
            ("rv_zbr", ["rv_zbr"]),
            ("rv64_zbr", ["rv64_zbr"]),
            ("rv_q_zfa", ["rv_q_zfa"]),
            ("rv_f_zfa", ["rv_f_zfa"]),
            ("q_ext", ["rv_q", "rv64_q", "rv_q_zfh", "rv_q_zfa"]),
            ("zba_ext", ["rv_zba", "rv64_zba"]),
            ("zbb_ext", ["rv_zbb", "rv64_zbb"]),
            ("zbs_ext", ["rv_zbs", "rv64_zbs"]),
            ("zfh_ext", ["rv_zfh", "rv64_zfh", "rv_d_zfh", "rv_q_zfh", "rv_zfh_zfa"]),
            ("a_ext", ["rv_a", "rv64_a"]),
            ("d_ext", ["rv_d", "rv64_d"]),
            ("v_ext", ["rv_v"]),
            ("c_ext", ["rv_c", "rv64_c", "rv_c_d"]),
            ("rv32cf", ["rv32_c_f"]),
            ("rv32zbc", ["rv_zbc"]),
            ("rvcd", ["rv_c_d"]),
            ("Xsfvqdotq_ext", ["Xsfvqdotq"]),
            ("rv32d-zfh", ["rv_d_zfh"]),
            ("rv32c", ["rv32_c"]),
            ("rv64c", ["rv64_c", "rv_c"]),
            ("rv_zfbfmin", ["rv_zfbfmin"]),
            ("rv_zvbb", ["rv_zvbb"]),
            ("rv_zvfbfmin", ["rv_zvfbfmin"]),
            ("rv_zvbc", ["rv_zvbc"]),
            ("rv_zvkg", ["rv_zvkg"]),
            ("rv_zvfbfwma", ["rv_zvfbfwma"]),
            ("rv_znkned", ["rv_zvkned"]),
            ("rv_zvknhb", ["rv_zvknhb"]),
        ]
    )
    not_my_xlen = 32

    def __init__(self):
        self.instr_query_dict = dict()
        self.instr_query_dict["Instructions"] = dict()
        self.instr_query_dict["Extensions_To_Instruction_Names"] = dict()
        self.instr_query_dict["Extensions_To_Groups"] = dict()
        self.instr_query_dict["Groups_To_Extensions"] = dict()
        self.instr_query_dict["Groups_To_Instruction_Names"] = dict()

    def augment_data_with_groups(self, instr_dict_raw, groups_to_instruction_names):
        local_instr_dict_raw = copy.deepcopy(instr_dict_raw)
        for ext, groups in groups_to_instruction_names.items():
            for group, instrs in groups.items():
                for instr in instrs:
                    instr_name = instr.replace(".", "_")
                    if instr_name in local_instr_dict_raw:
                        local_instr_dict_raw[instr_name].update({"group": group})

        return local_instr_dict_raw

    def from_instr_record_lookup_slice_of_encoding(self, instr_dict, msb, lsb):
        numbits = msb - lsb + 1
        encoding = instr_dict["encoding"]
        length = len(encoding)
        tmsb = length - msb
        tlsb = length - lsb

        field = encoding[tmsb - 1 : tlsb]
        assert len(field) == numbits

        if "-" in field or "_" in field:
            return None
        else:
            return int(field, 2)

    def extract_constants(self, instr_record):
        constant_bit_positions = OrderedDict(
            [("opcode", {"ext": "all", "bits": [(6, 0), (2, 0), (1, 0)]}), ("24..20", {"ext": "v_v", "bits": [(24, 20)]}), ("mop", {"ext": "v_v", "bits": [(27, 26)]})]
        )

        constant_vals = OrderedDict()

        for key, value in constant_bit_positions.items():
            if value["ext"] == "v_v" and not any("v_v" in extension for extension in instr_record["extension"]):
                continue
            for bitpair in value["bits"]:
                msb = bitpair[0]
                lsb = bitpair[1]
                constant = self.from_instr_record_lookup_slice_of_encoding(instr_record, msb, lsb)
                if constant is not None:
                    constant_vals[key] = hex(constant)
                    break

        return constant_vals

    def augment_data_with_constants(self, instr_dict_raw):
        local_instr_dict_raw = copy.deepcopy(instr_dict_raw)
        for key, value in local_instr_dict_raw.items():
            constant_vals = self.extract_constants(value)
            value.update(constant_vals)

        return local_instr_dict_raw

    def translate_riescue_extensions_to_riscv_extensions(self, extensions=None) -> list:
        if extensions is None:
            return []

        translation_from_riescue_to_riscv_extensions = copy.deepcopy(InstrInfoJson.translation_from_riescue_to_riscv_extensions)

        # With xlen as a string, remove elements of the values of translation_from_riescue_to_riscv_extensions that contain xlen.
        xlen_string = str(InstrInfoJson.not_my_xlen)
        applicable_extensions = []
        for ext in extensions:
            value = translation_from_riescue_to_riscv_extensions.get(ext, None)
            if value is None:
                assert False, f"Extension {ext} not found in translation_from_riescue_to_riscv_extensions"

            for item in value:
                if xlen_string in item:
                    value.remove(item)
                    continue

            applicable_extensions.extend(value)

        return applicable_extensions

    def rebuild_json(self):
        "Rebuilds JSON from yaml"

        groups_to_instruction_names = dict()
        with open(self.isa_info_path / self.instr_dict_filename) as f:
            instr_dict_raw = yaml.load(f, Loader=yaml.FullLoader)
        with open(self.isa_info_path / self.supplementary_dict_filename) as f:
            supplementary_dict_raw = yaml.load(f, Loader=yaml.FullLoader)
        with open(self.isa_info_path / self.groups_filename) as f:
            groups_to_instruction_names = yaml.load(f, Loader=yaml.FullLoader)

        assert isinstance(instr_dict_raw, dict)
        assert isinstance(supplementary_dict_raw, dict)
        assert isinstance(groups_to_instruction_names, dict)

        instr_dict_raw.update(supplementary_dict_raw)
        instr_dict_raw = self.augment_data_with_groups(instr_dict_raw, groups_to_instruction_names)
        instr_dict_raw = self.augment_data_with_constants(instr_dict_raw)

        groups_to_instruction_names["none"] = dict()
        riescue_extensions_list = [key for key in self.translation_from_riescue_to_riscv_extensions.keys()]
        list_with_repeats = self.translate_riescue_extensions_to_riscv_extensions(extensions=riescue_extensions_list)
        applicable_extensions = [key for key in OrderedDict.fromkeys(list_with_repeats).keys()]

        # Populate the instructions table
        # Fill the Instructions sub dictionary with instruction names as keys and their indices and blobs as values.
        self.instr_query_dict["Instructions"] = OrderedDict([(key.replace("_", "."), (index, json.dumps(value))) for index, (key, value) in enumerate(instr_dict_raw.items())])

        # Populate the tables letting us query instruction names by extension and group.
        for instr_name, (instr_id, instr_blob) in self.instr_query_dict["Instructions"].items():
            instr = json.loads(instr_blob)
            extensions = instr["extension"]
            group = instr.get("group", "none")

            for extension in extensions:
                if extension in applicable_extensions:
                    if extension not in self.instr_query_dict["Extensions_To_Groups"]:
                        self.instr_query_dict["Extensions_To_Groups"][extension] = {group}
                    else:
                        self.instr_query_dict["Extensions_To_Groups"][extension].add(group)
                    if group not in self.instr_query_dict["Groups_To_Extensions"]:
                        self.instr_query_dict["Groups_To_Extensions"][group] = {extension}
                    else:
                        self.instr_query_dict["Groups_To_Extensions"][group].add(extension)
                    if extension not in self.instr_query_dict["Extensions_To_Instruction_Names"]:
                        self.instr_query_dict["Extensions_To_Instruction_Names"][extension] = [instr_name]
                    else:
                        self.instr_query_dict["Extensions_To_Instruction_Names"][extension].append(instr_name)
            if group not in self.instr_query_dict["Groups_To_Instruction_Names"]:
                self.instr_query_dict["Groups_To_Instruction_Names"][group] = [instr_name]
            else:
                self.instr_query_dict["Groups_To_Instruction_Names"][group].append(instr_name)

        # transform some sets into lists
        for key, value in self.instr_query_dict["Extensions_To_Groups"].items():
            self.instr_query_dict["Extensions_To_Groups"][key] = list(value)
        for key, value in self.instr_query_dict["Groups_To_Extensions"].items():
            self.instr_query_dict["Groups_To_Extensions"][key] = list(value)

        # Dump the data to a json file
        with open(self.isa_info_path / self.instr_query_dict_filename, "w") as f:
            json.dump(self.instr_query_dict, f, indent=4)

    def load_data(self, not_my_xlen=32):
        InstrInfoJson.not_my_xlen = not_my_xlen
        # Check for the existence of instr_query_dict.json in isa info path
        # If it exists, load the data from it.
        if (self.isa_info_path / self.instr_query_dict_filename).exists():
            with open(self.isa_info_path / self.instr_query_dict_filename, "r") as f:
                self.instr_query_dict = json.load(f)
        else:
            self.rebuild_json()

    def search_instructions_by_extension(self, extension_names: list[str], exclude_rules: bool = False) -> list[str]:
        assert self.instr_query_dict["Extensions_To_Instruction_Names"], "Extensions table is empty."
        instrs = list()
        for extension_name in extension_names:
            if extension_name not in self.instr_query_dict["Extensions_To_Instruction_Names"]:
                assert exclude_rules or False, f"Extension {extension_name} not found in the database."
            instrs.extend(self.instr_query_dict["Extensions_To_Instruction_Names"].get(extension_name, []))
        return instrs

    def search_instructions_by_groups(self, group_names: list[str], exclude_rules: bool = False) -> list[str]:
        # Check that self.instr_query_dict["Groups"] is not an empty dictionary.
        assert self.instr_query_dict["Groups_To_Instruction_Names"], "Groups table is empty."
        instrs = list()
        for group_name in group_names:
            if group_name not in self.instr_query_dict["Groups_To_Instruction_Names"]:
                assert exclude_rules or False, f"Group {group_name} not found in the database."
            instrs.extend(self.instr_query_dict["Groups_To_Instruction_Names"].get(group_name, []))
        return instrs

    def filter_instruction_names(self, instruction_names: list[str], exclude_rules: bool = False) -> list[str]:
        # Check that self.instr_query_dict["Instructions"] is not an empty dictionary.
        assert self.instr_query_dict["Instructions"], "Instructions table is empty."
        instrs = list()
        unique_instr_names = list(set(instruction_names))

        for instr_name in unique_instr_names:
            if instr_name not in self.instr_query_dict["Instructions"]:
                assert exclude_rules or False, f"Instruction {instr_name} not found in the database."

            instrs.append(instr_name)
        return instrs

    def search_instruction_names(self, extension_names: list[str], group_names: list[str], instruction_names: list[str], sorted: bool = False, exclude_rules: bool = False) -> list[str]:
        instr_names_set: set[str] = set()
        instr_names_set.update(self.search_instructions_by_extension(extension_names, exclude_rules))
        instr_names_set.update(self.search_instructions_by_groups(group_names, exclude_rules))
        instr_names_set.update(self.filter_instruction_names(instruction_names, exclude_rules))
        instr_names: list[str] = list(instr_names_set)
        if sorted:
            instr_names.sort()
        return instr_names

    def get_instr_info(self, instruction_names: list[str]) -> list[InstrEntry]:
        """
        Consuming code should use InstrEntry objects instead of dictionaries if possible.
        This function is provided for backwards compatibility with the old code.
        """
        instrs: list[InstrEntry] = list()
        for instr_name in instruction_names:
            instr = InstrEntry.from_entry(instr_name, self.instr_query_dict["Instructions"][instr_name])
            instrs.append(instr)
        return instrs
