# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import re
from itertools import product, starmap
from collections import namedtuple, OrderedDict
from typing import TYPE_CHECKING

from riescue.compliance.config import Resource
from riescue.compliance.lib.common import lmul_map

if TYPE_CHECKING:
    from riescue.compliance.lib.riscv_instrs.base import InstrBase


class ConfigManager:

    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db
        self.rng = self.resource_db.rng
        self.config_tracker = dict()
        # Used for printing in header, really just for consistency/debugging
        self.paging_mode = self.resource_db.featmgr.paging_mode.name.lower()
        self.privilege_mode = self.resource_db.featmgr.priv_mode.name.lower()
        self.test_environment = self.resource_db.featmgr.env.name.lower()

    def generate_named_config(self, **configs):
        # Config is dynamic and can't be statically typed. If possible, having some known keys would be helpful for static type checking.
        Config = namedtuple("Config", configs.keys())
        return starmap(Config, product(*configs.values()))

    """
    #   Assumes that one number in mnemonic and it corresponds to eew for a vector load/store instruction.
    """

    def determine_eew(self, mnemonic):

        numbers_in_name = re.findall(r"\d+", mnemonic)
        if len(numbers_in_name) == 0:
            # This instruction has no eew encoded in the name
            return -1
        else:
            return int(numbers_in_name[-1], 10)

    def determine_nf(self, mnemonic):
        index = mnemonic.find("seg")

        if index < 0:
            return None
        else:
            return int(mnemonic[index + 3])

    def determine_emul(self, eew, sew, lmul):
        emul = (eew / sew) * lmul
        return emul

    def check_instruction_eew_against_config(self, mnemonic, config):

        lmul_numerical_value = lmul_map[config.vlmul]
        sew = int(config.vsew, 10)
        eew = self.determine_eew(mnemonic)
        # This instructions has no, eew, so just use sew to make the rest of the logic work usefully.
        if eew < 8:
            eew = sew
        emul = self.determine_emul(eew, sew, lmul_numerical_value)
        elen = 64  # FIXME this is hard-coded and might not should be

        nf = self.determine_nf(mnemonic)
        if nf is not None:
            if (nf * emul) > 8:
                return False

        # Using definition of vill in spike/riscv/processor.cc
        if eew > (min(emul, 1) * elen) or sew > (min(lmul_numerical_value, 1) * elen):
            return False

        if emul > 8 or emul < 0.125:
            return False
        else:
            return True

    def check_fp_instruction_against_config(self, mnemonic, config):
        reg_str_list = [("rs1sign", "rs1type"), ("rs2sign", "rs2type"), ("rs3sign", "rs3type"), ("rdsign", "rdtype")]

        for r_sign_str, r_type_str in reg_str_list:
            r_sign = getattr(config, r_sign_str, None)
            r_type = getattr(config, r_type_str, None)
            if r_type is not None:
                if r_sign is not None:
                    if r_type not in self.resource_db.fp_configs[mnemonic][r_sign]:
                        return False
                else:
                    if r_type not in self.resource_db.fp_configs[mnemonic]["Neg"] + self.resource_db.fp_configs[mnemonic]["Pos"]:
                        return False

        return True

    def check_privilege_paging_combo(self, privilege_mode, paging_mode):
        if privilege_mode in ["machine", "any"] and paging_mode not in ["disable", "any"]:
            return False

        return True

    def generate_instruction_objects(self, instruction_class: type[InstrBase], rpt: int) -> list[InstrBase]:
        mnemonic = instruction_class.name
        test_config_to_global_config = OrderedDict()
        configs = self.resource_db.get_config(mnemonic)
        # TODO handle other test level configs on a generic basis FIXME#
        # Only once, just choose one paging mode and privilege mode combo

        test_config_string = self.paging_mode + "_" + self.privilege_mode
        test_config_to_global_config[test_config_string] = {"paging_mode": self.paging_mode, "privilege_mode": self.privilege_mode, "test_environment": self.test_environment}
        instrs_temp = []
        if configs:
            # Temmporary fix
            # Keep only one option for every new attribute in f_ext
            # TODO: Remove it when there is a better way to restrict the number of configurations
            configs.pop("rdsign", None)
            configs.pop("rdtype", None)
            # Randomly discard the configuration for a source register when there are 3 of them
            if "rs3sign" in configs:
                discard_rs_idx = self.rng.randint(1, 3)
                configs.pop(f"rs{discard_rs_idx}sign", None)
                configs.pop(f"rs{discard_rs_idx}type", None)
            if "vset_instr" in configs:
                attr = "vset_instr"
                rand_idx = self.rng.randint(0, len(configs[attr]) - 1)
                configs[attr] = [configs[attr][rand_idx]]
            named_configs = list(self.generate_named_config(**configs))
            self.rng.shuffle(named_configs)
            fp_picked = False
            for config in named_configs:
                # skip the config combination if it is impossible
                if hasattr(config, "vlmul"):
                    if not self.check_instruction_eew_against_config(mnemonic, config):
                        continue
                if hasattr(config, "rs1sign") or hasattr(config, "rs2sign") or hasattr(config, "rs3sign"):
                    if fp_picked or not self.check_fp_instruction_against_config(mnemonic, config):
                        continue
                    else:
                        fp_picked = True
                config_string = "_".join(list(config)) + "_" + test_config_string

                _id = f"{mnemonic}_{str(rpt)}_{config_string}"
                instr = instruction_class(resource_db=self.resource_db, name=mnemonic, label=_id)
                instr.resource_db = self.resource_db
                instr.config = config
                instr.test_config = test_config_to_global_config[test_config_string]
                instrs_temp.append(instr)
        else:
            _id = f"{mnemonic}_{str(rpt)}" + "_" + test_config_string
            instr = instruction_class(resource_db=self.resource_db, name=mnemonic, label=_id)
            instr.resource_db = self.resource_db
            instr.test_config = test_config_to_global_config[test_config_string]
            instrs_temp.append(instr)

        return instrs_temp
