# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

FpGenInterface connects Riescue and FpGen

    - Riescue uses __init__() and configure() to initialize the FpGenInterface instance
    - get_data() is the interface function for Riescue

    - fpgen_access() access the fpgen api through get_source_operands_for_result()

"""

import sys
import logging
from pathlib import Path

import riescue.lib.common as common


logger = logging.getLogger(__name__)
try:
    # FIXME: FPGEN should be a module that's installed if it's a real dependency
    # FIXME: Remove the sys.path appends
    fpgen_path = Path(__file__).parents[3] / "fpgen"
    fpgen_srcs = fpgen_path / "src"
    sys.path.append(str(fpgen_path))
    sys.path.append(str(fpgen_srcs / "common"))
    sys.path.append(str(fpgen_srcs / "generator"))
    from fpgen.src.pyfpgen.fpGenApi import FpGenApi  # noqa: E402

    fpgen_api_module = FpGenApi

except Exception:
    logger.warning("fpgen unavailable, skipping")
    fpgen_api_module = None


class FpGenInterface:

    def __init__(self):
        """
        Set the name of and path to the database
        Declare the class variables
        """
        self._db_paths = []

        # create a seed variable
        self._seed = 0

        self._fpgen_api = None

    def configure(self, seed, fast_fpgen):
        """
        Instantiate the FpGenApi
        """
        if fpgen_api_module is None:
            logger.warning("FPgen is not available")
            return

        # use the seed from riescue
        self._seed = seed
        self._fpgen_api = fpgen_api_module(self._seed, self._db_paths, fast_fpgen)

    def get_data(self, instr_name, num_bytes, config_in, size):
        """
        Parse the configuration from Riescue,
        form the configuration to be passed to FpGen

        Call fpgen_access
        """
        config_out = {"instruction": instr_name}

        if num_bytes == 2:
            config_out["Precision"] = "f16"
        elif num_bytes == 4:
            config_out["Precision"] = "f32"
        else:
            config_out["Precision"] = "f64"

        reg_str_list = [("Rs1", "rs1sign", "rs1type"), ("Rs2", "rs2sign", "rs2type"), ("Rs3", "rs3sign", "rs3type"), ("Rd", "rdsign", "rdtype")]
        for reg_str, r_sign_str, r_type_str in reg_str_list:
            r_sign = getattr(config_in, r_sign_str, None)
            if r_sign and r_sign != "any":
                if reg_str in config_out:
                    config_out[reg_str].append(r_sign)
                else:
                    config_out[reg_str] = [r_sign]
            r_type = getattr(config_in, r_type_str, None)
            if r_type and r_type != "any":
                if reg_str in config_out:
                    config_out[reg_str].append(r_type)
                else:
                    config_out[reg_str] = [r_type]

            # translation from riescue config to fpgen config for registers
            # add different attributes here
            if reg_str in config_out:
                for i, val in enumerate(config_out[reg_str]):
                    if val == "Inf":
                        config_out[reg_str][i] = "Infinity"

        # The following attributes are not currently available in FPgen
        # NOTE: Disable these to generate fewer hash keys in fpgen and improve performance
        # frm = getattr(config_in, "frm", None)
        # if frm and frm != "any":
        #     config_out["rounding"] = frm

        # dynamic_rm = getattr(config_in, "dynamic_rm", None)
        # if dynamic_rm and dynamic_rm != "any":
        #     config_out["dynamic_rm"] = dynamic_rm

        # exce = getattr(config_in, "exception", None)
        # if exce and exce != "any":
        #     config_out["exception"] = exce

        config_out["query_size"] = size

        return self.fpgen_access(instr_name, num_bytes, config_out)

    ##############################################################
    # The functions below should not be accessed by other classes
    ##############################################################

    def fpgen_access(self, instr_name, num_bytes, config):
        """
        Access FpGen though function get_source_operands_for_result()

        Convert the returned data from string to hex number type
        """
        if self._fpgen_api is None:
            logger.warning("FPgen is not available")
            return []
        data = list(self._fpgen_api.get_source_operands_for_result(config))
        data_convert = []

        # convert hex str to fp numbers
        for entry in data:
            temp_entry = [entry[0]]
            for val_hex_str in entry[1:]:
                if val_hex_str is not None:
                    val_num = int(val_hex_str, 16)
                    # num_bytes*2+2 is the length of the hex str including "0x"
                    temp_entry.append("{0:#0{1}x}".format(val_num, num_bytes * 2 + 2))
                else:
                    temp_entry.append(None)
            data_convert.append(temp_entry)

        # data_convert: [[instr_name, rs1_val, rs2_val, rs3_val, rd_val]]
        return data_convert
