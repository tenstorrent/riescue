# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
import logging
import re
from pathlib import Path
from typing import Optional, Union

from .base import BaseMode
from riescue.compliance.src.riscv_instr_generator import InstrGenerator
from riescue.compliance.src.riscv_test_generator import TestGenerator
from riescue.compliance.config import ResourceBuilder, Resource
from riescue.compliance.src.comparator import Comparator
from riescue.compliance.src.riscv_instr_builder import InstrBuilder
from riescue.riescued import RiescueD
from riescue.lib.toolchain import Spike, Whisper, Toolchain
from riescue.lib.rand import RandNum

log = logging.getLogger(__name__)


class BringupMode(BaseMode):
    """
    Runs RiescueC Test Plan generation flow.
    """

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
        bringup_args.add_argument("--include_extensions", type=str, help='Specify extensions to include. e.g.--include_extensions "i_ext,m_ext"')
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

    def run(
        self,
        bringup_test_json: Path,
        seed: int,
        toolchain: Toolchain,
        cl_args: Optional[argparse.Namespace] = None,
    ) -> Path:
        """
        Top level wrapper to generate and simulate a test
        """
        cfg = self.configure(seed=seed, bringup_test_json=bringup_test_json, cl_args=cl_args)
        return self.generate(cfg, toolchain)

    def configure(
        self,
        seed: int,
        bringup_test_json: Path,
        cl_args: Optional[argparse.Namespace] = None,
    ) -> Resource:
        """
        Configure the :class:`Resource` object for use in :py:meth:`generate`

        Since :class:`Resource` gets constructed with :class:`Toolchain` + Whisper, need to make sure that tpp;cu
        """

        resource_builder = ResourceBuilder()
        resource_builder.with_bringup_test_json(bringup_test_json)
        if cl_args is not None:
            resource_builder.with_args(cl_args)
        return resource_builder.build(seed, self.run_dir)

    def generate(self, cfg: Resource, toolchain: Toolchain) -> Path:
        """
        Generate, compile, and return ELF test file. Returned test will have been simulated on ISS, otherwise an exception will be raised.

        :param cfg: :class:`Resource` object for use in :py:meth:`generate`
        :return: Path to the generated ELF test file
        """
        resource = cfg
        rng = RandNum(resource.seed)
        resource.with_rng(rng)
        log.info(f"Running Bringup mode with seed {resource.seed} and selected configuration:")
        log.info(f"PRIV_MODE:     {resource.featmgr.priv_mode}")
        log.info(f"TEST_ENV:      {resource.featmgr.env}")
        log.info(f"PAGING_MODE:   {resource.featmgr.paging_mode}")
        log.info(f"PAGING_G_MODE: {resource.featmgr.paging_g_mode}")

        # Parse opcodes and generate the tree for all the extensions.
        self.instr_generator = InstrGenerator(resource)

        # Forms instruction class templates from the instruction records. TODO replace with a generic instruction class
        self.instr_builder = InstrBuilder(resource)

        # Instantiate the Riescue-D test generator
        self.test_generator = TestGenerator(resource)

        # Get the instruction records, dictionaries of things like name, opcode, variable fields, etc.
        sim_instrs = resource.get_sim_set()

        # Turn the instruction records into classes that can be crossed with the configurations, instantiated and generated, just missing configuration and label at this point.
        sim_classes = self.instr_builder.build_classes(sim_instrs)

        resource.instr_classes = sim_classes  # Old code did this through cross talk between and so could escape user's notice.

        # Generate the instructions by determining the configration, assigning the configuration, and the calling the setup methods.
        self.instr_generator.generate_instructions_with_config_and_repeat_combinations(sim_classes)

        # Generate the riescue_d testcase for the first pass
        instrs = resource.get_instr_instances()

        log.info(f"Generated {len(instrs)} instructions")

        self.test_generator.process_instrs(instrs, iteration=1)
        testfiles = self.test_generator.testfiles()

        # Run the First pass
        first_pass = None
        for testfile in testfiles:
            first_pass = self._rd_run_iss(Path(testfile), resource.first_pass_iss, resource, toolchain)

        if resource.disable_pass:
            log.warning("Second pass is disabled, skipping second pass")
            if first_pass is None:
                raise ValueError("Didn't run a final RiescueD instance")
            return first_pass.generated_files.elf  # FIXME: Does RiescueC bringup actually support multiple testfiles?

        # Parse the first pass log and generate the second pass testcase.
        self.test_generator.process_instrs(instrs, iteration=2)
        testfiles = self.test_generator.testfiles()
        testcases = self.test_generator.testcases()

        # run second pass
        last_rd = None
        if resource.compare_iss:
            # Comparator for invoking riescue-d framework
            self.comparator = Comparator(resource)
            for _, testcase in testcases.items():
                last_rd = self._rd_run_iss(Path(testcase.testname), ["whisper", "spike"], resource, toolchain)
                self.comparator.compare_logs(testcase)
        else:
            for testfile in testfiles:
                # FIXME: This is flimsy, if there really aren't multiple files then this should just return the file rather than re-assigning a temp
                last_rd = self._rd_run_iss(Path(testfile), resource.second_pass_iss, resource, toolchain)
                self._clean_up(testfile, output_format=resource.output_format)
        if last_rd is None:
            raise ValueError("Didn't run a final RiescueD instance")
        return last_rd.generated_files.elf

    # helper methods
    def _get_iss(self, iss_name: str, toolchain: Toolchain) -> Union[Spike, Whisper]:
        if iss_name == "whisper":
            iss = toolchain.whisper
        elif iss_name == "spike":
            iss = toolchain.spike
        else:
            raise ValueError(f"Invalid ISS: {iss_name}")
        if iss is None:
            raise ValueError(f"No ISS {iss_name} configured in toolchain")
        return iss

    def _rd_run_iss(self, file: Path, iss: Union[str, list[str]], resource: Resource, toolchain: Toolchain) -> RiescueD:
        "Shortcut for running RiescueD and ISS on the test"
        if isinstance(iss, str):
            iss = [iss]
        rd = RiescueD(file, run_dir=self.run_dir, seed=resource.seed, toolchain=toolchain)
        generator = rd.generate(resource.featmgr)
        rd.build(resource.featmgr, generator)
        for simulator in iss:
            if toolchain.whisper is not None:
                whisper_config_json_override = toolchain.whisper.whisper_config_json.resolve()
            else:
                whisper_config_json_override = None

            rd.simulate(resource.featmgr, iss=self._get_iss(simulator, toolchain), whisper_config_json_override=whisper_config_json_override)
        return rd

    def _clean_up(self, output_file: str, output_format: str):
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
