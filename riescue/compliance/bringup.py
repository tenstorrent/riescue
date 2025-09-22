# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
import logging
import re
from pathlib import Path
from typing import Optional, Union

from .base import BaseMode
from riescue.compliance.src.riscv_instr_generator import InstrGenerator
from riescue.compliance.src.riscv_instr_organizer import InstrOrganizer
from riescue.compliance.src.riscv_test_generator import TestGenerator
from riescue.compliance.config import BringupCfg
from riescue.compliance.src.comparator import Comparator
from riescue.compliance.src.riscv_instr_builder import InstrBuilder
from riescue.riescued import RiescueD
from riescue.lib.toolchain import Spike, Whisper

log = logging.getLogger(__name__)


class BringupMode(BaseMode):
    """
    Runs RiescueC Test Plan generation flow.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not isinstance(self.cfg, BringupCfg):
            raise RuntimeError(f"cfg must be an instance of BringupCfg, not {type(self.cfg)}")
        self.resource_db = self.cfg.build(rng=self.rng, run_dir=self.run_dir)
        log.info("Selected configuration:")
        log.info(f"PRIV_MODE:     {self.resource_db.featmgr.priv_mode}")
        log.info(f"TEST_ENV:      {self.resource_db.featmgr.env}")
        log.info(f"PAGING_MODE:   {self.resource_db.featmgr.paging_mode}")
        log.info(f"PAGING_G_MODE: {self.resource_db.featmgr.paging_g_mode}")

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        bringup_args = parser.add_argument_group("bringup", "bringup mode")
        bringup_args.add_argument("--json", "-js", type=Path, help="JSON File specifying the compliance args")
        bringup_args.add_argument("--cpuconfig", type=Path, default=Path("dtest_framework/lib/config.json"), help="Path to cpu feature configuration to pass to riescued")
        bringup_args.add_argument("--output_file", "-o", type=str, help="Output Filename. The output is generated as <output_filename>{.s/.dis/.log/.S}")
        bringup_args.add_argument("--default_config", "-dcfg", type=Path, help="JSON File specifying default configuration")
        bringup_args.add_argument("--user_config", "-ucfg", type=Path, help="JSON File specifying user-defined configuration")
        bringup_args.add_argument("--fp_config", "-fcfg", type=Path, help="JSON File specifying default floating point instruction configuration")
        bringup_args.add_argument("--dump_instrs", action="store_true", help="Switch to dump the instruction fields as JSON")
        bringup_args.add_argument("--disable_pass", action="store_true", help="Disables the second pass for the compliance run")
        bringup_args.add_argument("--first_pass_iss", type=str, help="Provide Target ISS for the first pass")
        bringup_args.add_argument("--second_pass_iss", type=str, help="Provide Target ISS for the second pass")
        bringup_args.add_argument("--rpt_cnt", type=int, help="Each instruction will have rpt_cnt instances in the test")
        bringup_args.add_argument("--max_instrs_per_file", type=int, help="Max instrs in the test file during the first pass. Doesn't include second pass or runtime instructions")
        bringup_args.add_argument("--compare_iss", action="store_true", help="Run second pass testcase on both ISS targets (i.e whisper and spike) and compares the logs")
        bringup_args.add_argument("--repeat_runtime", "-repeat_runtime", type=int, help="--repeat_times passthrough. Run each discrete test these many times. Only use this with --disable_pass")
        bringup_args.add_argument("--output_format", "-op_fmt", type=str, help="Format in which output is generated")
        bringup_args.add_argument("--load_fp_regs", "-lfpr", action="store_false", help="Switch to load fp regs with load instructions rather than fmv instructions.")
        bringup_args.add_argument("--combine_compliance_tests", "-cct", type=int, help="When set compliance tests will be combined into a single discrete test per file.")
        bringup_args.add_argument("--exclude_instrs", "-exclude_instrs", type=str, help='Specify instructions to exclude. e.g.--exclude_instrs "add,sub"')
        bringup_args.add_argument("--instrs", "-instrs", type=str, help='Specify instructions to be run. e.g. --instrs "add,sub"')
        bringup_args.add_argument("--groups", "-groups", type=str, help='Specify groups to be run. e.g. --groups "rv64i_load_store,rv32f_single_precision_reg_reg"')

        features_args = parser.add_argument_group("bringup - features", "features mode")
        features_args.add_argument("--rv_zfbfmin_experimental", "-rze", action="store_true", help="Experimental mode for to enable rv_zfbfmin, adds options to riescue-d call")
        features_args.add_argument("--rv_zvbb_experimental", "-rvz", action="store_true", help="Experimental mode for to enable rv_zvbb, adds options to riescue-d call")
        features_args.add_argument("--rv_zvfbfmin_experimental", "-rvf", action="store_true", help="Experimental mode for to enable rv_zvfbfmin, adds options to riescue-d call")
        features_args.add_argument("--rv_zvfbfwma_experimental", "-rvw", action="store_true", help="Experimental mode for to enable rv_zvfbfwma, adds options to riescue-d call")
        features_args.add_argument("--rv_zvbc_experimental", "-rvb", action="store_true", help="Experimental mode for to enable rv_zvbc, adds options to riescue-d call")
        features_args.add_argument("--rv_zvkg_experimental", "-rvk", action="store_true", help="Experimental mode for to enable rv_zvkg, adds options to riescue-d call")
        features_args.add_argument("--rv_zvknhb_experimental", "-rvn", action="store_true", help="Experimental mode for to enable rv_zvknhb, adds options to riescue-d call")
        features_args.add_argument("--vector_bringup", "-vb", action="store_true", help="Mode for generating special constraints for vector bringup")
        features_args.add_argument(
            "--experimental_compiler", type=str, help="Path to experimental compiler to pass to RiescueD for *_experimental features. Defaults to EXPERIMENTAL_COMPILER environment variable."
        )
        features_args.add_argument(
            "--experimental_objdump", type=str, help="Path to experimental objdump to pass to RiescueD for *_experimental features. Defaults to EXPERIMENTAL_OBJDUMP environment variable."
        )

        fpgen_args = parser.add_argument_group("bringup - fpgen", "fpgen mode")
        fpgen_args.add_argument(
            "--fpgen_on", action="store_true", help="Turn on FPgen, randomly generate floating point numbers using fpgen database. Setting environment variable FPGEN_ENABLED also sets this to true"
        )
        fpgen_args.add_argument("--fast_fpgen", "-ffp", action="store_true", help="Fpgen returns entries in order (doesn't count the number of qualified entries)")

        deprecated_args = parser.add_argument_group("bringup - deprecated", "deprecated arguments")
        deprecated_args.add_argument("--privilege_mode", type=str, help="Deprecated argument. Use --test_priv_mode instead.")

    def run(self) -> None:
        """
        Generate, compile, and simualte the test case
        """

        # Parse opcodes and generate the tree for all the extensions.
        self.instr_generator = InstrGenerator(self.resource_db)

        # Performs shuffling on instrs generated before test generation.
        self.instr_organizer = InstrOrganizer(self.resource_db)  # Literally just shuffles a dictionary's items and then rebuilds the dictionary.

        # Forms instruction class templates from the instruction records. TODO replace with a generic instruction class
        self.instr_builder = InstrBuilder(self.resource_db)

        # Instantiate the Riescue-D test generator
        self.test_generator = TestGenerator(self.resource_db)

        # Get the instruction records, dictionaries of things like name, opcode, variable fields, etc.
        sim_instrs = self.resource_db.get_sim_set()

        # Turn the instruction records into classes that can be crossed with the configurations, instantiated and generated, just missing configuration and label at this point.
        sim_classes = self.instr_builder.build_classes(sim_instrs)

        self.resource_db.instr_classes = sim_classes  # Old code did this through cross talk between and so could escape user's notice.

        # Generate the instructions by determining the configration, assigning the configuration, and the calling the setup methods.
        self.instr_generator.generate_instructions_with_config_and_repeat_combinations(sim_classes)

        # Generate the riescue_d testcase for the first pass
        instrs = self.resource_db.get_instr_instances()

        log.info(f"Generated {len(instrs)} instructions")

        self.test_generator.process_instrs(instrs, iteration=1)
        testfiles = self.test_generator.testfiles()
        # Run the First pass
        for testfile in testfiles:
            self.rd_run_iss(Path(testfile), self.resource_db.first_pass_iss)

        if self.resource_db.disable_pass:
            log.warning("Second pass is disabled, skipping second pass")
            return

        # Parse the first pass log and generate the second pass testcase.
        self.test_generator.process_instrs(instrs, iteration=2)
        testfiles = self.test_generator.testfiles()
        testcases = self.test_generator.testcases()

        if self.resource_db.compare_iss:
            # Comparator for invoking riescue-d framework
            self.comparator = Comparator(self.resource_db)
            for _, testcase in testcases.items():
                self.rd_run_iss(Path(testcase.testname), ["whisper", "spike"])
                self.comparator.compare_logs(testcase)
        else:
            for testfile in testfiles:
                self.rd_run_iss(Path(testfile), self.resource_db.second_pass_iss)
                self.clean_up(testfile, output_format=self.resource_db.output_format)

    # helper methods
    def get_iss(self, iss_name: str) -> Union[Spike, Whisper]:
        if iss_name == "whisper":
            iss = self.resource_db.toolchain.whisper
        elif iss_name == "spike":
            iss = self.resource_db.toolchain.spike
        else:
            raise ValueError(f"Invalid ISS: {iss_name}")
        if iss is None:
            raise ValueError(f"No ISS {iss_name} configured in toolchain")
        return iss

    def rd_run_iss(self, file: Path, iss: Union[str, list[str]]) -> RiescueD:
        "Shortcut for running RiescueD and ISS on the test"
        if isinstance(iss, str):
            iss = [iss]
        rd = RiescueD(file, run_dir=self.run_dir, seed=self.seed, toolchain=self.resource_db.toolchain)
        generator = rd.generate(self.resource_db.featmgr)
        rd.build(self.resource_db.featmgr, generator)
        for simulator in iss:
            rd.simulate(self.resource_db.featmgr, iss=self.get_iss(simulator))
        return rd

    def find_config(self, file: Optional[str]) -> Optional[Path]:
        """
        If absolute use that, otherwise use relative to riescue directory
        """
        if file is None:
            return None
        filepath = Path(file)
        riescue_relative = self.package_path / file

        if filepath.is_absolute():
            return filepath
        elif riescue_relative.exists():
            return riescue_relative
        elif filepath.exists():
            return filepath
        else:
            raise FileNotFoundError(f"Couldn't find config file {file}. Tried {filepath} and {riescue_relative}.")

    def clean_up(self, output_file: str, output_format: str):
        if output_format == "binary":
            output_dir = Path(self.run_dir)
            if not output_dir.exists():
                output_dir.mkdir()
            (testname, testnum, seed, iteration) = re.findall(r"(.*)_(\d+)_(\d+)_(\d+).s", output_file)[0]
            output_file = f"{testname}_{testnum}_{seed}"

            files_to_remove = [
                f"{output_file}_1.S",
                f"{output_file}_1.s",
                f"{output_file}_1.dis",
                f"{output_file}_1_equates.inc",
                f"{output_file}_1.ld",
                f"{output_file}_1.o",
                f"{output_file}_2.S",
                f"{output_file}_2.s",
                f"{output_file}_2.dis",
                f"{output_file}_2_equates.inc",
                f"{output_file}_2.ld",
                f"{output_file}_2.o",
                f"{output_file}_1_excp.inc",
                f"{output_file}_1_loader.inc",
                f"{output_file}_1_os.inc",
                f"{output_file}_2_excp.inc",
                f"{output_file}_2_loader.inc",
                f"{output_file}_2_os.inc",
                f"{output_file}_1",
                f"{output_file}_1_spike_csv.log",
                f"{output_file}_1_spike.log",
                f"{output_file}_2_whisper.log",
            ]

            for file in files_to_remove:
                file_path = Path(file)
                if file_path.exists():
                    file_path.unlink()

            output_file_2_path = Path(f"{output_file}_2")
            if output_file_2_path.exists():
                destination = output_dir / f"{testname}_{testnum}"
                output_file_2_path.rename(destination)
