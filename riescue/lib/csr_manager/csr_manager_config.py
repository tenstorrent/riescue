# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
from riescue.lib.rand import RandNum


class FieldConfig:
    "CSR and field Config"

    def __init__(self, config_dict: dict):
        self.field_config = config_dict


class CsrConfig:

    def __init__(self, config_dict: dict):
        self.config = config_dict
        self.Fields = {}

    def add_subfield(self, field_name: str, config_dict: dict):
        field_config = FieldConfig(config_dict)
        field_name_config = {field_name: field_config}
        self.Fields.update(field_name_config)


# Following are the functions of the class CsrManager
# 1)parse :- Converts XLSX to json
# 2)build :- Creates class objects for each CSR out of the csr json file specified
# 3)UserAPIs  :- Some handly user APIs to perform functions like filter csrs according to the requirement,
#                 and give out instructions to access a csr
class CsrManager:
    def __init__(self, rng: RandNum):
        self.rng = rng
        self.CSR_Reg = {}
        self.utils = CsrManagerUtils()
        self.Instruction_helper = None

    def create_csr_config(self, csr_name: str, config_dict: dict):
        csr_config = CsrConfig(config_dict)
        csr_name_config = {csr_name: csr_config}
        self.CSR_Reg.update(csr_name_config)
        return csr_config

    def build(self, override_json_path=Path(__file__).parent / "csr_config.json"):
        with open(override_json_path, "r") as json_file:
            input_json = json.load(json_file)

        for csr, csr_config in input_json.items():
            sub_fields = csr_config.pop("sub-fields")
            csr_obj = self.create_csr_config(csr, csr_config)
            for field, field_config in sub_fields.items():
                csr_obj.add_subfield(field, field_config)

    # User APIs
    def lookup_csrs(self, match: dict, exclude: dict = {}):
        csr_config_dict = self.utils.utils_get_csrs(self.CSR_Reg, match, exclude)
        return csr_config_dict

    def get_random_csr(self, match: dict, exclude: dict = {}):
        csr_configs = self.lookup_csrs(match, exclude)
        random_csr = self.rng.random_entry_in(list(csr_configs))
        config = csr_configs[random_csr]
        return {random_csr: config}

    def csr_access(self, instruction_helper, access_type, csr_config, value=None, imm=None, rs="", rd="", subfield: dict = {}):
        reg1 = instruction_helper.get_random_gpr_reserve("int")
        reg2 = instruction_helper.get_random_gpr_reserve("int")

        if value is None:
            value = self.rng.get_rand_bits(64)
        if imm is None:
            imm = self.rng.get_rand_bits(5)

        csr_name = list(csr_config.keys())[0]

        if len(rd) == 0:
            rd = reg2

        if len(rs) == 0:
            rs = reg1

        if access_type == "write_subfield":

            csr_config_ = list(csr_config.values())[0]
            csr_name = list(csr_config.keys())[0]
            subfield_name = list(subfield.keys())[0]
            value = list(subfield.values())[0]

            if subfield_name in csr_config_.Fields:
                field_config_ = csr_config_.Fields[subfield_name]
                field_range = field_config_.field_config["fields-range"]
                and_mask, or_mask = self.utils.utils_and_or_mask(field_range, int(value))
                instruction = f"li  {reg1}, {and_mask}\n"
                instruction += self.utils.utils_access_csr(inst="csrr", csr=csr_name, rd=reg2)
                instruction += f"and {reg2}, {reg2}, {reg1}\n"
                instruction += f"li {reg1}, {or_mask}\n"
                instruction += f"or {reg2}, {reg2}, {reg1}\n"
                instruction += self.utils.utils_access_csr(inst="csrrw", rs=reg2, rd="x0", csr=csr_name, value=None)
            else:
                print("Error : sub field not found")

        if access_type == "write":
            instruction = self.utils.utils_access_csr(inst="csrrw", rs=rs, rd=rd, csr=csr_name, value=value)

        if access_type == "set":
            instruction = self.utils.utils_access_csr(inst="csrrs", rs=rs, rd=rd, csr=csr_name, value=value)

        if access_type == "clear":
            instruction = self.utils.utils_access_csr(inst="csrrc", rs=rs, rd=rd, csr=csr_name, value=value)

        if access_type == "write_imm":
            instruction = self.utils.utils_access_csr(inst="csrrwi", imm=imm, rd=rd, csr=csr_name)

        if access_type == "clear_imm":
            instruction = self.utils.utils_access_csr(inst="csrrci", imm=imm, rd=rd, csr=csr_name)

        if access_type == "set_imm":
            instruction = self.utils.utils_access_csr(inst="csrrsi", imm=imm, rd=rd, csr=csr_name)

        if access_type == "read":
            instruction = self.utils.utils_access_csr(inst="csrr", rd=rd, csr=csr_name)

        regs = [reg1, reg2]
        instruction_helper.unreserve_regs(regs)

        return instruction


# Helper function for CsrManager
class CsrManagerUtils:

    def __init__(self):
        pass

    def utils_get_csrs(self, CSR_Reg: dict, match: dict, exclude: dict):

        matching_csrs = {}
        for key, nested_dict in CSR_Reg.items():
            is_matching = True
            for k, v in match.items():
                if k in nested_dict.config:
                    if nested_dict.config[k] != v:
                        is_matching = False
                        break
                else:
                    is_matching = False
                    break

            for k, v in exclude.items():
                if k in nested_dict.config:
                    if nested_dict.config[k] == v:
                        is_matching = False
                        break

            if is_matching:
                matching_csrs.update({key: nested_dict})

        assert matching_csrs, "No CSR with given constraint found"
        return matching_csrs

    def utils_access_csr(self, inst: str, rd: str, csr: str, value=None, imm="", rs=""):

        instruction = ""
        if inst == "csrrw" or inst == "csrrs" or inst == "csrrc":
            assert value is not None or len(rs) != 0, "Neither Value nor source register specified along with csrr w/s/c instruction"
            if value is not None:
                instruction = f"li {rs}, {value}\n"
            instruction += f"{inst} {rd}, {csr}, {rs}\n"
            return instruction
        elif inst == "csrrwi" or inst == "csrrsi" or inst == "csrrci":
            return f"{inst} {rd}, {csr}, {imm}\n"
        elif inst == "csrr":
            return f"{inst} {rd}, {csr}\n"
        else:
            assert False, f"No csr access instruction named : {inst} found"

    def utils_and_or_mask(self, subfield_range: str, value):

        if ":" not in subfield_range:
            subfield_range = f"{subfield_range}:{subfield_range}"

        higher, lower = map(int, subfield_range.split(":"))

        # Create a mask with ones in the specified range (x to y)
        and_mask = ((1 << (higher - lower + 1)) - 1) << lower

        # Clear the bits in the range
        and_mask = ~and_mask

        # Set the new bits in the range
        or_mask = value << lower

        return and_mask, or_mask
