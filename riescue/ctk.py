# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import argparse
from pathlib import Path
from typing import Optional, Generator, Union, Any
from dataclasses import dataclass, field, replace

import riescue.lib.logger as RiescueLogger
import riescue.lib.enums as RV
from riescue.dtest_framework.config import FeatMgrBuilder, Candidate
from riescue.compliance import BringupMode, TpMode
from riescue.compliance.base import BaseMode
from riescue.compliance.config import ResourceBuilder, TpBuilder, Resource, TpCfg
from riescue.compliance.config.resource_builder import ResourceConfig
from riescue.lib.toolchain import Compiler, Disassembler, Spike, Whisper
from riescue.lib.rand import initial_random_seed
from riescue.lib.cli_base import CliBase
from riescue.lib.toolchain import Toolchain
from riescue.lib.instr_info.instr_lookup_json import InstrInfoJson

log = logging.getLogger("riescue")  # special case because ctk can be a main module

ComplianceConfigs = Union[ResourceBuilder, TpBuilder]


# remove this?
@dataclass
class CtkCfg:
    """
    Configuration for the Ctk
    """

    run_dir: Path = Path("test_kit")
    isa: str = "rv64imf"
    flat_directory_structure: bool = False
    test_count: int = 20

    resource_builder: ResourceBuilder = field(default_factory=ResourceBuilder)
    tp_builder: TpBuilder = field(default_factory=TpBuilder)

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser):
        ctk_args = parser.add_argument_group("Ctk", "Arguments for the Compliance Test Kit")
        ctk_args.add_argument(
            "--flat",
            action="store_true",
            default=False,
            help="Flat directory structure",
        )
        ctk_args.add_argument("--test_count", type=int, default=20, help="Number of tests to generate")

    def with_args(self, args: argparse.Namespace):
        """
        Configure the CtkCfg from command line arguments.

        Passes through arguments to both builders.
        """
        # CTK specific arguments
        # self.isa = args.isa #FIXME
        self.flat_directory_structure = args.flat
        self.flat_directory_structure = True
        self.test_count = args.test_count
        self.isa = args.isa

        # pass through args to both builders
        self.resource_builder.with_args(args)
        self.tp_builder.with_args(args)
        return self


class Ctk(CliBase):
    """
    Compliance Test Kit. Generates a directory of RiescueC tests given an ISA and CPU configuration.

    :param seed: Seed for the test. Used as starting seed for test kit
    :param run_dir: Run directory where the test will be run
    :param toolchain: Configured ``Toolchain`` object. If none provided, a default ``Toolchain`` object will be used (assumes whisper and spike are available in environment)
    """

    def __init__(self, seed: int, run_dir: Path, toolchain: Toolchain):
        self.seed = seed
        self.run_dir = run_dir
        self.toolchain = toolchain

        if not self.run_dir.exists():
            self.run_dir.mkdir(parents=True)
        else:
            # check that directory is empty.
            if len(list(self.run_dir.iterdir())) > 0:
                raise ValueError(f"Run directory {self.run_dir} is not empty. Please remove before running, \n rm -rf {self.run_dir}")

        # logger init
        RiescueLogger.init_logger(log_path=self.run_dir / "ctk.log", level="INFO", tee_to_stderr=True)
        log.info(f"Initializing Ctk with seed: {seed} and run dir: {run_dir}")

        self.package_path = Path(__file__).parent

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser):
        parser.add_argument(
            "--run_dir",
            "-rd",
            type=Path,
            default=Path("test_kit"),
            help="Run directory where the test will be run",
        )
        parser.add_argument("--seed", type=int, help="Seed for the test")

        CtkCfg.add_arguments(parser)
        BringupMode.add_arguments(parser)
        # skipping TpMode.add_arguments(parser) since only args are isa and test_plan_name
        TpMode.add_arguments(parser)
        FeatMgrBuilder.add_arguments(parser)
        RiescueLogger.add_arguments(parser)
        Whisper.add_arguments(parser)
        Spike.add_arguments(parser)
        Compiler.add_arguments(parser)
        Disassembler.add_arguments(parser)

    @classmethod
    def run_cli(cls, args=None, **kwargs):
        "Initialize from command line arguments and runs correct run_<mode> method"

        # legacy print, not sure if needed:
        if args is not None:
            print(f"Args to process: {args}")

        # Switch to this later
        parser = argparse.ArgumentParser(
            prog="riescuec",
            description="RiESCUE-C compliance test generation",
            formatter_class=argparse.RawTextHelpFormatter,
        )
        cls.add_arguments(parser)
        cl_args = parser.parse_args(args)
        RiescueLogger.from_clargs(args=cl_args, default_logger_file=Path("ctk.testlog"))

        seed = cl_args.seed
        if seed is None:
            seed = initial_random_seed()
        ctk = cls(
            seed=seed,
            run_dir=cl_args.run_dir,
            toolchain=Toolchain.from_clargs(cl_args, build_both=True),
        )

        ctk.run(cl_args)
        print(f"Test kit generated in {ctk.run_dir}")
        return ctk

    def run(self, cl_args: Optional[argparse.Namespace] = None) -> Path:
        "Generate test kit, return the path to the test kit"
        cfg = self.configure(cl_args)
        return self.generate(cfg)

    def configure(self, cl_args: Optional[argparse.Namespace] = None):
        """
        Responsible for generating all Configuration objects for the test kit.

        Later this should return a Configuration object that power-users can create and pass to generate().
        """

        # Use arguments to generate the base configuration
        # generate will need to generate resource, TP configurations
        # This base cfg will need to be like a FeatMgrBuilder? Or ResourceBuilder?
        # Since building shouldn't modify inputs can reuse between resource and tp

        # CtkCfg should be enough to generate the entire test kit
        # So it needs to have both builders in it I guess.

        cfg = CtkCfg(run_dir=self.run_dir)
        if cl_args is not None:
            cfg.with_args(cl_args)
        return cfg

    def generate(self, configuration: CtkCfg) -> Path:
        """
        Generate the test kit with given configuration.

        """
        # This should not be harcoded here.
        # FIXME: add allowed privilege and paging modes to FeatMgr, CpuConfig, etc. Used to restrict csr configurations.
        priv_modes = [
            RV.RiscvPrivileges.MACHINE,
            RV.RiscvPrivileges.SUPER,
            RV.RiscvPrivileges.USER,
        ]
        paging_modes = [
            RV.RiscvPagingModes.DISABLE,
            RV.RiscvPagingModes.SV39,
            RV.RiscvPagingModes.SV48,
            RV.RiscvPagingModes.SV57,
        ]
        extensions = self.isa_to_valid_bringup_extensions(configuration.isa)

        # FIXME: need to also generate tp configurations based on isa
        # generate resource configurations
        bringup_configs: list[ResourceConfig] = configuration.resource_builder.products(
            seed=self.seed,
            priv_modes=priv_modes,
            paging_modes=paging_modes,
            extensions=extensions,
            max_tests=configuration.test_count,
            run_dir=configuration.run_dir,
        )
        # tp_configs = []

        # change run directory if not using a flat one
        if not configuration.flat_directory_structure:
            for cfg in bringup_configs:
                print(f"Changing run directory from {configuration.run_dir} to {configuration.run_dir / cfg.extension}")
                cfg.resource.run_dir = configuration.run_dir / cfg.extension

        # gather all runner and config pairs
        runners: list[tuple[BaseMode, Union[Resource, TpCfg], Any]] = []
        for config in bringup_configs:
            if config.extension == "v_ext":
                # special case, v_ext doesn't work out of the box
                # FIXME: Why doesn't v_ext work out of the box?
                config.resource.include_extensions = []
                config.resource.include_groups = [
                    "vector_load_fault_only_first",
                    "vector_int_arithmetic",
                    "vector_store_strided_segmented",
                    "vector_load_unit_stride",
                    "vector_load_unit_stride_segmented",
                    "vector_opmvv_macc",
                    "rvv_fp_widening_add_sub",
                    "rv_v_fp_move",
                    "vector_load_strided_segmented",
                    "vector_opivv",
                    "rvv_fp_compare",
                    "rvv_fp_slides",
                    "vector_store_strided",
                    "vector_opivv_2_data_processing",
                    "rvv_vec_integer_merge",
                    "rvv_vec_int_extension",
                    "rvv_fp_widening_mul",
                    "vector_opivi_2_data_processing",
                    "vector_opmvv_3_data_processing",
                    "vector_opivi",
                    "rvv_whole_vec_reg_move",
                    "vector_load_indexed_unordered",
                    "vector_opmvx_1_data_processing",
                    "vector_opmvv_4_data_processing",
                    "rv_v_fp_signinj",
                    "vector_store_indexed_ordered",
                    "vector_opivx_2_data_processing",
                    "vector_opmvv_vid",
                    "rv_v_fp_fma_s",
                    "rvv_fp_sqrt",
                    "rv_v_fp_minmax",
                    "rvv_fp_rec_sqrt_est",
                    "vector_store_indexed_unordered",
                    "vector_opmvv_2_data_processing",
                    "rvv_vec_single_width_shift",
                    "vector_opmvv_3_a_data_processing",
                    "rv_v_fp_merge",
                    "rv_v_fp_class",
                    "vector_opmvx_macc",
                    "vector_load_indexed_ordered",
                    "rvv_fp_widening_mac",
                    "rvv_fp_rec_est",
                    "vector_load_strided",
                    "rv_v_fp_addsub_s",
                ]
                config.resource.first_pass_iss = "whisper"
            runners.append((BringupMode(run_dir=config.resource.run_dir), config.resource, config))
        # for config in tp_configs:

        # generate all tests
        all_tests = []
        for runner, cfg, metadata in runners:
            try:

                test = runner.generate(cfg, toolchain=self.toolchain)
                all_tests.append(test)
            except Exception as e:
                log.error(f"Error generating test for {runner.__class__.__name__}: {e}.Reproduce with \n\t{metadata.repro}")
                raise e

        # cleanup
        all_test_set = set(t.resolve() for t in all_tests)

        def recursive_walk(path: Path):
            for p in path.iterdir():
                if p.is_dir():
                    yield from recursive_walk(p)
                else:
                    yield p

        for test in recursive_walk(self.run_dir):
            if test.resolve() not in all_test_set:
                test.unlink()

        return self.run_dir

    def isa_to_valid_bringup_extensions(self, isa: str) -> list[str]:
        """
        Filter the test kit to only include valid bringup extensions from isa.

        In the future, bringup needs a better way to handle extensions rather than the adhoc rv_xxx_ext formatting.
        Some consistent enums or types would make this easier, and make it easier for other programs to ask RiescueC what it supports
        """
        # isa
        if isa.startswith("rv64"):
            isa = isa.replace("rv64", "")
        else:
            raise ValueError("Currently only rv64 is supported. ISA should start with 'rv64'")
        if not isa:
            raise ValueError("ISA string is empty")
        isa_parts = isa.split("_")
        # assuming first part is single letter extensions
        extensions = list(isa_parts[0]) + isa_parts[1:]
        log.info(f"Extensions: {extensions}")

        # this library needs some cleanup, the formatting of extensions is not consistent.
        # this means we have to check "rv_"+extension, extension+"_ext", and have edge cases for crosses
        instr_info = InstrInfoJson()
        valid_extensions = []
        for extension in extensions:
            rv_extension = "rv_" + extension
            ext_extensions = extension + "_ext"
            if rv_extension in instr_info.translation_from_riescue_to_riscv_extensions:
                log.info(f"Including extension: {rv_extension}")
                valid_extensions.append(rv_extension)
            elif ext_extensions in instr_info.translation_from_riescue_to_riscv_extensions:
                log.info(f"Including extension: {ext_extensions}")
                valid_extensions.append(ext_extensions)

            # edge cases that aren't straight forward
            elif extension == "zcb":
                valid_extensions.extend(["rv32c", "rv64c"])
            elif extension == "zfa":
                if "d" in extensions:
                    valid_extensions.append("rv_d_zfa")
                if "q" in extensions:
                    valid_extensions.append("rv_q_zfa")
                if "f" in extensions:
                    valid_extensions.append("rv_f_zfa")
                if "zfa" in extensions:
                    valid_extensions.append("rv_zfh_zfa")
            elif extension == "zbc":
                valid_extensions.append("rv32zbc")
            else:
                log.warning(f"Extension {extension} not supported in bringup mode")

        if "c" in extensions and "f" in extensions:
            valid_extensions.append("rv32cf")
        if "c" in extensions and "d" in extensions:
            valid_extensions.append("rvcd")
        if "d" in extensions and "zfh" in extensions:
            valid_extensions.append("rv32d-zfh")
        return valid_extensions

    def recursive_clean(self, path: Path):
        """
        Recursively clean the path
        """
        for p in path.iterdir():
            if p.is_dir():
                self.recursive_clean(p)
                p.rmdir()
            else:
                p.unlink()


def main():
    Ctk.run_cli()


if __name__ == "__main__":
    main()
