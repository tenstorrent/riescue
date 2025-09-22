# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import json
import argparse
import logging
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Any

import riescue.lib.logger as RiescueLogger
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.lib.instr_info.instr_lookup_json import InstrInfoJson
from riescue.dtest_framework.config import FeatMgrBuilder, FeatMgr
from riescue.compliance.lib.tree import Tree
from riescue.compliance.lib.fpgen_intf import FpGenInterface
from riescue.lib.toolchain import Toolchain  # FIXME: make this a top-level import, since it's part of RiesceuD interface

log = logging.getLogger(__name__)


@dataclass
class Resource:
    # Central data structure to store hierarchically the extensions, groups and instructions.
    # FIXME: Rename this structure, since tree has node at every level which are homogeneous.
    tree: Tree = field(default_factory=Tree)
    featmgr: FeatMgr = field(default_factory=FeatMgr)
    toolchain: Toolchain = field(default_factory=Toolchain)

    # Seed management for the framework.
    seed: int = 0

    # Register lengths
    arch: RV.RiscvBaseArch = RV.RiscvBaseArch.ARCH_RV64I
    xlen: RV.Xlen = RV.Xlen.XLEN32
    flen: int = 32  # unused
    vlen: int = 256  # FIXME: this is hard coded to a default value

    # User specified extensions, groups and instrs
    include_extensions: list[str] = field(default_factory=list)
    exclude_extensions: list[str] = field(
        default_factory=lambda: [
            "q_ext",
            "rv_d_zfa",
            "rv_zfh_zfa",
            "rv_q_zfa",
            "rv_zbr",
            "rv64_zbr",
            "rv_f_zfa",
            "rv_zbt",
        ]
    )
    include_groups: list[str] = field(default_factory=list)
    include_instrs: list[str] = field(default_factory=list)

    exclude_groups: list[str] = field(default_factory=list)
    exclude_instrs: list[str] = field(default_factory=list)
    legacy_exclude_instrs: list[str] = field(
        default_factory=lambda: [
            "scall",
            "pause",
            "sbreak",
            "fence.tso",
            "fmv.x.s",
        ]
    )  # NOTE some old test jsons may exclude these by name but riscv-opcodes seems to have removed them.

    # Currently supported features.
    # FIXME: These default "supported" features should probably be an enums / constants. Maybe a separate file
    supported_extensions: list[str] = field(
        default_factory=lambda: [
            "i_ext",
            "m_ext",
            "b_ext",
            "f_ext",
            "zba_ext",
            "zbb_ext",
            "rv32zbc",
            "zbs_ext",
            "zfh_ext",
            "v_ext",
            "a_ext",
            "c_ext",
            "d_ext",
            "rvcd",
            "rv64c",
            "rv32d-zfh",
            "rv_zfbfmin",
        ]
    )
    supported_arch: list[str] = field(default_factory=lambda: ["rv32", "rv64"])
    supported_isss: list[str] = field(default_factory=lambda: ["spike", "whisper"])
    supported_lmuls: list[float] = field(default_factory=lambda: [0.125, 0.25, 0.5, 1, 2, 4, 8])
    supported_sews: list[int] = field(default_factory=lambda: [8, 16, 32, 64])
    int_extensions: list[str] = field(
        default_factory=lambda: [
            "i_ext",
            "b_ext",
            "m_ext",
            "zba_ext",
            "zbb_ext",
            "rv32zbc",
            "zbs_ext",
            "a_ext",
            "c_ext",
            "rv32c",
            "rv64c",
        ]
    )
    fp_extensions: list[str] = field(
        default_factory=lambda: [
            "f_ext",
            "d_ext",
            "q_ext",
            "rv32cf",
            "rvcd",
            "zfh_ext",
            "rv32d-zfh",
            "rv_zfbfmin",
        ]
    )
    vec_extensions: list[str] = field(default_factory=lambda: ["v_ext"])
    rv32_or_rv64_agnostic_extensions: list[str] = field(default_factory=lambda: ["v_ext", "c_ext"])
    conditionally_enabled_extensions: dict[str, list[str]] = field(
        default_factory=lambda: {
            "rv32cf": ["rv32", "c_ext", "f_ext"],
            "rv32zbc": ["rv32"],
            "rvcd": ["c_ext", "d_ext", "rv32", "rv64"],
            "rv32d-zfh": ["zfh_ext", "rv32", "rv64"],
            "rv32c": ["rv32", "c_ext"],
            "rv64c": ["rv64", "c_ext"],
            "rv_zfbfmin": ["rv32", "rv64", "f_ext", "zfh_ext"],
        }
    )

    # Use by default Spike as the ISS for first and whisper for second pass.
    first_pass_iss: str = "spike"  # note: most of the instruction generators expect spike and will error out otherwise. This needs to be fixed in the future
    second_pass_iss: str = "whisper"
    default_first_pass_iss: str = "spike"
    default_second_pass_iss: str = "whisper"

    # Knob to dump the instrs as JSON object for FE TB's
    dump_instrs: bool = False

    force_alignment: bool = False
    big_endian: bool = False
    fe_tb: bool = False

    # Disables second pass if enabled
    disable_pass: bool = False

    # Runs the second-pass test case on both whisper and spike and compare logs.
    compare_iss: bool = False

    # Format in which output is generated.
    output_format: str = "all"

    load_fp_regs: bool = True  # "Switch to load fp regs with load instructions rather than fmv instructions." #FIXME: This should probably be a bool

    # Replicates the instructions "_rpt_cnt" times.
    rpt_cnt: int = 1

    # Pad size for inserting random instruction inside setups.
    pad_size: int = 256

    # FIXME : Allow just 10 as default. Currently setting this to 100 to avoid multiple file generation for floating point bringup.
    max_instr_per_file: int = 1000

    # Maintains the arch state for every instruction post first-pass.
    instr_tracker: dict[str, Any] = field(default_factory=dict)

    use_output_filename: bool = False

    # If 1, combine discrete compliance tests into one
    combine_compliance_tests: bool = False

    # Maintains a copy of the generated instruction classes.
    instr_classes: Optional[Any] = None

    # Maintains per-instruction configurations.
    instr_configs: dict[str, Any] = field(default_factory=dict)

    # Maintains possible configurations for fp instructions
    fp_configs: dict[str, Any] = field(default_factory=dict)

    # Maintains per-test configurations.
    test_configs: dict[str, Any] = field(default_factory=dict)

    testcase_name: str = "bringup"
    testfile: Path = Path("bringup.s")

    repeat_runtime: int = 1

    # Maintains copy of opcode and config files.
    config_files: dict[str, Optional[Path]] = field(default_factory=dict)
    user_config: Optional[Path] = None
    default_config: Path = Path("compliance/tests/configs/default_config.json")
    fp_config: Path = Path("compliance/tests/configs/rv_d_f_zfh.json")

    opcode_files: dict[str, Any] = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)

    # Logger options
    logfile_path: str = ""
    logfile_log_level: str = ""

    info: InstrInfoJson = field(default_factory=InstrInfoJson)
    run_dir: Path = Path(".")

    json_filepath: str = ""

    # Interface connected to fpgen
    fpgen_intf: Optional[FpGenInterface] = None

    # By default, use FPgen to get FP numbers
    fpgen_on: bool = False

    # By default, use the fast mode of FPgen
    fast_fpgen: bool = True

    # By default, do not use vector_bringup mode
    vector_bringup: bool = False

    _rng: Optional[RandNum] = None

    # properties
    @property
    def fpgen_off(self):
        return not self.fpgen_on

    @property
    def rng(self) -> RandNum:
        """
        get instance of ``RandNum``. Need to pass rng instance in ``configure()`` before calling

        :raises ValueError: If RandNum is not configured

        This is required since most legacy code uses rng from resource, instead of passing rng around when needed.
        Ideally rng is passed separately from resource
        """
        if self._rng is None:
            raise ValueError("RandNum is not configured. call configure() first.")
        return self._rng

    # FeatMgr properties
    # These used to be in Resource, but using property to delegate to FeatMgr
    @property
    def num_cpus(self) -> int:
        return self.featmgr.num_cpus

    @property
    def mp_mode(self) -> RV.RiscvMPMode:
        return self.featmgr.mp_mode

    @property
    def parallel_scheduling_mode(self) -> RV.RiscvParallelSchedulingMode:
        return self.featmgr.parallel_scheduling_mode

    @property
    def wysiwyg(self) -> bool:
        return self.featmgr.wysiwyg

    # configuration methods
    def with_rng(self, rng: RandNum):
        if self._rng is not None:
            raise ValueError("RandNum is already configured. Can only set rng once.")
        self._rng = rng
        return self

    # public methods
    # data: [instr_name, rs1_val, rs2_val, rs3_val, rd_val]
    def get_fp_set(self, instr_name, num_bytes, config, size) -> list:

        # connect to the interface, query the database
        if self.fpgen_intf is None:
            raise ValueError("FPgen interface is not configured")
        data = self.fpgen_intf.get_data(instr_name, num_bytes, config, size)
        assert data
        return data[:size]

    def get_config(self, mnemonic):
        if mnemonic in self.instr_configs:
            return self.instr_configs[mnemonic]
        else:
            log.warning(f"Instruction {mnemonic} not found in the config file, may be intentional")
            return None

    def groups(self, extension):
        return self.tree.get_group_names(extension)

    def instrs(self, extension, group):
        return self.tree.get_instr_names(extension, group)

    def get_sim_set(self) -> list[dict[str, Any]]:
        """
        Find the instructions to be simulated. (EXTENSION u GROUP u INSTRS ) - (GROUP u INSTRS)
        FIXME: This should probably return an object / typed class instead of a list of dictionaries. For better debug and type checking.
        """
        self.info.load_data(not_my_xlen=(32 if self.xlen == 64 else 64))

        translated_extensions = []
        translated_extensions.extend(self.info.translate_riescue_extensions_to_riscv_extensions(self.include_extensions))

        excluded_extensions = []
        excluded_extensions.extend(self.info.translate_riescue_extensions_to_riscv_extensions(self.exclude_extensions))

        # filter the included groups
        groups_temp = []
        for group in self.include_groups:
            if "vector_load_store" in group:
                # remove vector_load_store from group string
                group = group.replace("vector_load_store_", "")
                groups_temp.append("vector_load_" + group)
                groups_temp.append("vector_store_" + group)
            else:
                groups_temp.append(group)
        self.include_groups = groups_temp

        # print settings
        log.warning("Included extensions: " + str(translated_extensions))
        log.warning("Excluded extensions: " + str(excluded_extensions))
        log.warning("Included groups: " + str(self.include_groups))
        log.warning("Excluded groups: " + str(self.exclude_groups))
        log.warning("Included instructions: " + str(self.include_instrs))
        log.warning("Excluded instructions: " + str(self.exclude_instrs))

        # Detect any mutual members of self.excluded_instrs and self.legacy_exclude_instrs
        mutual_exclusions = set(self.exclude_instrs) & set(self.legacy_exclude_instrs)
        if mutual_exclusions:
            log.warning(f"Mutual exclusions found between _excluded_instrs and _legacy_exclude_instrs: {mutual_exclusions}")
            log.warning("Removing mutual exclusions from _excluded_instrs")
            self.exclude_instrs = list(set(self.exclude_instrs) - mutual_exclusions)

        # Determine the sim set
        print(f"translated_extensions: {translated_extensions}")
        print(f"include_groups: {self.include_groups}")
        print(f"include_instrs: {self.include_instrs}")
        print(f"excluded_extensions: {excluded_extensions}")
        print(f"exclude_groups: {self.exclude_groups}")
        print(f"exclude_instrs: {self.exclude_instrs}")
        included_instructions = self.info.search_instruction_names(translated_extensions, self.include_groups, self.include_instrs)
        excluded_instructions = self.info.search_instruction_names(
            excluded_extensions,
            self.exclude_groups,
            self.exclude_instrs,
            exclude_rules=True,
        )
        sim_set = set(included_instructions) - set(excluded_instructions)
        included_instructions = list(sim_set)
        included_instructions = self.info.get_instr_info(included_instructions)

        # Turn the included_instructions into a list of objects from its tuples.
        instruction_dictionaries: list[dict[str, Any]] = []

        for instruction in included_instructions:
            # instruction_object has no attribute called 'name', so we need to add it.
            instruction_dictionary: dict[str, Any] = json.loads(instruction[2])
            instruction_dictionary["instruction_id"] = instruction[1]
            instruction_dictionary["name"] = instruction[0]
            name = instruction_dictionary["name"]
            instruction_dictionary["config"] = None

            # Put the values from the key 'variable_fields' into the instruction_dictionary and give them values of None
            for variable_field in instruction_dictionary["variable_fields"]:
                instruction_dictionary[variable_field] = None

            if name.startswith("v"):
                instruction_dictionary["type"] = "Vec"
            elif (name.startswith("f") or name.startswith("c.f")) and not name == "fence":
                instruction_dictionary["type"] = "Float"
            else:
                instruction_dictionary["type"] = "Int"

            instruction_dictionaries.append(instruction_dictionary)

        if len(instruction_dictionaries) == 0:
            raise ValueError("No instructions found.")
        return instruction_dictionaries

    def update_configs(self, instr_configs):
        self.tree.update_configs(instr_configs)

    def generate_test_signature(self, _iter, testcase_num):
        if self.use_output_filename:
            self.output_files.append(f"{self.testcase_name}")
            return f"{self.testcase_name}"

        else:
            self.output_files.append(f"{self.testcase_name}_{testcase_num}_{self.seed}_{_iter}")
            return f"{self.testcase_name}_{testcase_num}_{self.seed}_{_iter}"

    def check_arch(self, arch):
        if arch in self.supported_arch:
            return True
        return False

    def check_extension(self, extension: str):
        if extension in self.supported_extensions:
            return True
        elif extension in self.rv32_or_rv64_agnostic_extensions:
            return True
        return False

    def get_extension_string(self):
        return [ext.split("_", 1)[0] for ext in self.include_extensions]

    def add_extension(self, extension):
        self.tree.add_extension(extension)

    def update_instrs(self, _id: str, instr):
        self.instr_tracker[_id] = instr

    def traverse_tree(self):
        self.tree.traverse_tree()

    def get_instr(self, mnemonic):
        return self.tree.get_instr(mnemonic)

    def get_groups(self, groups):
        return self.tree.get_groups(groups)

    def get_extension_set(self, ext_names):
        mnemonics = set()
        for ext_name in ext_names:
            mnemonics = mnemonics | self.tree.get_extension_set(ext_name)
        return mnemonics

    def get_group_set(self, grp_names):
        mnemonics = set()
        for grp_name in grp_names:
            mnemonics = mnemonics | self.tree.get_group_set(grp_name)
        return mnemonics

    def get_instr_dict(self, mnemonics):
        instrs = dict()
        for mnemonic in mnemonics:
            instrs[mnemonic] = self.get_instr(mnemonic)
        return instrs

    def get_instr_instances(self) -> dict[str, Any]:
        return self.instr_tracker
