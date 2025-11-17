# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import shutil
import sys
import logging
import argparse
from pathlib import Path
from typing import Optional, Union
import os

import riescue.lib.logger as RiescueLogger
from riescue.lib.rand import RandNum, initial_random_seed
from riescue.dtest_framework.artifacts import GeneratedFiles
from riescue.dtest_framework.parser import Parser
from riescue.dtest_framework.config import FeatMgr, FeatMgrBuilder, Conf
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.generator import Generator
from riescue.dtest_framework.lib.discrete_test import DiscreteTest
from riescue.lib.cli_base import CliBase
from riescue.lib.toolchain import Toolchain, Spike, Whisper


log = logging.getLogger("riescue")  # special case because riescued can be a main module


class RunnerError(Exception):
    pass


class RiescueD(CliBase):
    """
    RiescueD is the main class for the RiescueD framework. Used to generate and compile tests.

    Constructor makes run_dir if it doesn't exist and picks a random seed if unset.


    :param testfile: RiescueD Assembly File (.rasm) ; to be suffixed with ``.rasm`` in the future.
    :param cpuconfig: CPU Config file with memory map and features
    :param run_dir: The directory to run the test in. Defaults to current directory
    :param seed: The seed to use for the random number generator. Defaults to a random number in range 0 to 2^32
    :param toolchain: The ``Toolchain`` to use for the test. Defaults to a default ``Toolchain`` object if not provided
    """

    package_path = Path(__file__).parent

    def __init__(
        self,
        testfile: Path,
        cpuconfig: Path = Path("dtest_framework/lib/config.json"),
        run_dir: Path = Path("."),
        seed: Optional[int] = None,
        toolchain: Optional[Toolchain] = None,
    ):
        self.run_dir = run_dir.resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.testfile = self._resolve_path(testfile)
        self.testname = self.testfile.stem
        self.cpuconfig = self._resolve_path(cpuconfig if cpuconfig is not None else Path("dtest_framework/lib/config.json"))
        self.generated_files = GeneratedFiles.from_testname(self.testname, self.run_dir)

        if seed is None:
            seed = initial_random_seed()
        self.rng = RandNum(seed)

        if toolchain is None:
            self.toolchain = Toolchain()
        else:
            self.toolchain = toolchain

        log.info(f"Initialized RiescueD with seed: {self.rng.get_seed()}")
        log.debug("Initializing pool")
        self.pool = Pool()
        self.pool.testname = self.testname
        parser = Parser(self.testfile, pool=self.pool)

        self.parsed_data = parser

        # This internally passes data to Pool, which makes Parser harder to test. It should be changed to return a ParsedData dataclass and passed to Pool constructor or a pool class method
        # Pool is only required for generator. With assumption that 1 RiescueD object corresponds to a single test:
        # 1. Parse test header here, create self.parsed_data: ParsedData
        # 2. Pass self.parsed_data to FeatMgrBuilder in configure()
        # 3. Pass self.parsed_data to Pool constructor / pool class method in generate()

        parser.parse()

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser):
        """
        Adds group arguments to the parser. Includes arguments for FeatMgr, RiescueLogger, Compiler, Disassembler, Spike, and Whisper.
        """
        parser.add_argument(
            "--testfile",
            "-t",
            type=Path,
            help="Testname with path to be compiled with RiESCUE-D",
        )
        parser.add_argument(
            "--testname",
            type=Path,
            help="Legacy switch to be deprecated, use --testfile instead.",
        )
        parser.add_argument(
            "--run_dir",
            "-rd",
            type=Path,
            default="./",
            help="Run directory where the test will be run",
        )
        parser.add_argument("--seed", type=int, help="Seed for the test")
        parser.add_argument(
            "--cpuconfig",
            type=Path,
            default=None,
            help="Path to cpu feature configuration. Defaults to dtest_framework/lib/config.json",
        )
        parser.add_argument(
            "--conf",
            type=Path,
            default=None,
            help="Path to conf.py file for additional config and hooks.",
        )

        run_args = parser.add_argument_group(
            "Run Control",
            "Arguments that modify the run flow - runnning simulators, compilers, excluding OS code",
        )
        run_args.add_argument(
            "--elaborate_only",
            action="store_true",
            default=None,
            help="Only elaborate the test but dont attempt to call external compiler or simulator",
        )
        run_args.add_argument(
            "--run_iss",
            action="store_true",
            default=None,
            help="Run ISS with the test. Default ISS is Whisper, but can be run with any other ISS using --iss <iss>",
        )

        FeatMgrBuilder.add_arguments(parser)
        RiescueLogger.add_arguments(parser)
        Toolchain.add_arguments(parser)

    @classmethod
    def run_cli(cls, args=None, **kwargs):
        """
        Main entry point for the RiescueD command line interface. Called when running ``python3 -m riescued``

        :param args: Command line arguments. Passed to argparse, if not provided, sys.argv is used.
        """

        parser = argparse.ArgumentParser()
        cls.add_arguments(parser)
        cl_args = parser.parse_args(args)

        if args is not None:
            print("riescued.py " + " ".join(args))
        else:
            print(" ".join(sys.argv))

        return cls.from_clargs(cl_args, **kwargs)

    @classmethod
    def from_clargs(cls, cl_args, **kwargs):
        """
        Create a RiescueD instance from command line arguments.
        """
        testname = cl_args.testname
        testfile = cl_args.testfile
        if testname and testfile:
            raise ValueError("Cannot use both --testname and --testfile; just use --testfile")
        if testname:
            print("Warning: --testname is deprecated, use --testfile instead")  # FIXME: move to logger
            testfile = testname
        if testfile is None:
            raise ValueError("Testfile is required when running from CLI")

        rd = cls(
            testfile=testfile,
            cpuconfig=cl_args.cpuconfig,
            run_dir=cl_args.run_dir,
            seed=cl_args.seed,
            toolchain=Toolchain.from_clargs(cl_args),
        )

        # load Conf from path if provided
        if cl_args.conf is not None:
            conf = Conf.load_conf_from_path(cl_args.conf)
        else:
            conf = None

        rd.run(
            cl_args,
            elaborate_only=cl_args.elaborate_only,
            run_iss=cl_args.run_iss,
            conf=conf,
        )
        return rd

    def run(
        self,
        cl_args: argparse.Namespace,
        elaborate_only: bool = False,
        run_iss: bool = False,
        conf: Optional[Conf] = None,
    ) -> GeneratedFiles:
        """
        Run RiescueD configuration, generation, and compilation. Simulate if requested.

        :param cl_args: Command line arguments
        :param elaborate_only: Generate assembly file, don't compile or simulate
        :param run_iss: Run ISS with the test. Default ISS is Whisper, but can be run with any other ISS using --iss <iss>
        :param conf: Optional ``Conf`` object to modify ``FeatMgr``. CLI-passed ``Conf`` takes priority over ``Conf`` passed into this method

        :return: Generated files; structure containing all generated files
        """

        # Ideally this is done in constructor
        test_logfile = self.run_dir / f"{self.testname}.testlog"
        RiescueLogger.from_clargs(args=cl_args, default_logger_file=test_logfile)

        featmgr = self.configure(args=cl_args, conf=conf)
        self.generate(featmgr)
        if elaborate_only:
            log.info("Elaboration complete. Exiting...")
            return self.generated_files

        self.build(featmgr)
        if run_iss:
            if self.toolchain.simulator is None:
                raise ValueError("No ISS selected. Provide ISS in toolchain configuration")
            self.simulate(
                featmgr,
                iss=self.toolchain.simulator,
                whisper_config_json_override=cl_args.whisper_config_json,
            )

        return self.generated_files

    def configure(self, args: Optional[argparse.Namespace] = None, conf: Optional[Conf] = None) -> FeatMgr:
        """
        Configure ``FeatMgr`` object for generation. ``FeatMgr`` can be modified before generation.

        :param args: optional ``argparse.Namespace`` object used to build ``FeatMgr``. If not provided, ``FeatMgr`` is built with test header and cpu config.
        :param conf: optional ``Conf`` object to modify ``FeatMgr``. CLI-passed ``Conf`` takes priority over ``Conf`` passed into this method

        :return: Constructed ``FeatMgr`` object
        """

        log.debug("Initializing FeatMgr")
        featmgr_builder = FeatMgrBuilder()
        featmgr_builder.conf = conf
        featmgr_builder.with_test_header(self.parsed_data.test_header)
        featmgr_builder.with_cpu_json(self.cpuconfig)
        if args is not None:
            featmgr_builder.with_args(args)
        return featmgr_builder.build(rng=self.rng)

    def generate(self, featmgr: FeatMgr) -> GeneratedFiles:
        """

        Generate the test code into an assembly file, linker script, and any additional files.

        Uses :class:`FeatMgr` object to generate the test code. Modifies
        :returns: The internal :attr:`generated_files` instance, a :class:`GeneratedFiles` object containing paths to generated files.
        """
        # Copy test s file to current directory
        try:
            shutil.copy(self.testfile, self.run_dir)
        except shutil.SameFileError:
            pass

        # review this part (is it necessary? Should this be done in FeatMgr or inside pool?)
        for test in self.pool.parsed_discrete_tests.keys():
            dtest = DiscreteTest(name=test, priv=featmgr.priv_mode)
            self.pool.add_discrete_test(dtest)

        log.info(f"test_config: env: {featmgr.env}")
        log.info(f"test_config: secure_mode: {featmgr.secure_mode}")
        log.info(f"test_config: priv: {featmgr.priv_mode}")
        log.info(f"test_config: paging: {featmgr.paging_mode}")
        log.info(f"test_config: paging_g: {featmgr.paging_g_mode}")

        # Call various generators
        test_gen = Generator(rng=self.rng, pool=self.pool, featmgr=featmgr, run_dir=self.run_dir)
        test_gen.generate(file_in=self.testfile, generated_files=self.generated_files)
        return self.generated_files

    def build(self, featmgr: FeatMgr, generator: Optional[Generator] = None) -> GeneratedFiles:
        """
        Compile and disassemble the test code.

        :param featmgr: ``FeatMgr`` object
        :param generator: [Deprecated] ``Generator`` object, currently ignored
        :returns: The internal :attr:`generated_files` instance, a :class:`GeneratedFiles` object containing paths to generated files.
        """

        if generator is not None:
            log.warning("generator parameter is deprecated and will be removed in a future version.")

        compiler = self.toolchain.compiler
        linker_script = self.generated_files.linker_script
        # fmt: off
        compiler_args = [
            "-I", str(self.run_dir),
            "-T", str(linker_script),
            "-o", str(self.generated_files.elf),
            str(self.generated_files.assembly),
        ]
        # fmt: on
        if featmgr.big_endian:
            compiler_args.append("-mbig-endian")

        if featmgr.cfiles is not None:
            for cfile in featmgr.cfiles:
                compiler_args.append(f"{str(self._resolve_path(cfile))}")

        compiler.run(cwd=self.run_dir, args=compiler_args)

        # Generate Disassembly
        disassembler = self.toolchain.disassembler
        disassembler_args = ["-D", str(self.generated_files.elf), "-M", "numeric"]
        disassembler.run(
            output_file=self.generated_files.dis,
            cwd=self.run_dir,
            args=disassembler_args,
        )

        return self.generated_files

    def simulate(
        self,
        featmgr: FeatMgr,
        iss: Union[Spike, Whisper],
        whisper_config_json_override: Optional[Path] = None,
    ) -> Path:
        """
        Run test code through ISS

        :param featmgr: ``FeatMgr`` object
        :param iss: ``Spike`` or ``Whisper`` object
        :param whisper_config_json_override: Optional path to whisper config json file to override default
        :return: Path to ISS log file
        """
        # In wysiwyg mode, we use a different end-of-test mechanism where we look for x31=0xc001c0de to be written
        # This is not supported by whisper, so we are using --endpc to the end of the test in whisper
        # To do this, we need to find the pc of label "fail" in disassembly and send it to --endpc
        # Later we parse whisper log to find out what was the last value written to the x31 to indicate pass|fail
        failed_pc = None
        if featmgr.wysiwyg:
            # Read file <testname>.dis and find <failed>
            with open(self.generated_files.dis, "r") as f:
                disasm_lines = f.readlines()
            for line in disasm_lines:
                if "<failed>:" in line:
                    print(f"WYSIWYG mode failed to find the <failed> label in disassembly, {line}")
                    # split line by spaces and get the first element
                    # FIXME: need to document this a bit better
                    # why is it +4 instructions from failed? Why is eot there and not just <end>: ?
                    failed_pc = line.split()[0]
                    failed_pc = int(failed_pc, 16) + 0x10
                    print(f"Setting end-of-sim pc to: {failed_pc:016x}")

        # Spike ISS path and args
        iss_args = []
        if isinstance(iss, Spike):

            iss_args.append("--priv=msu")
            iss_args.append(f"--pc=0x{featmgr.reset_pc:x}")
            iss_log = self.run_dir / f"{self.testname}_spike.log"
            if not featmgr.force_alignment:
                iss_args.append("--misaligned")

            if featmgr.mp_mode_on():
                iss_args.append(f"-p{featmgr.num_cpus}")

            if featmgr.tohost_nonzero_terminate or featmgr.fe_tb:
                iss_args.append("--tt-tohost-nonzero-terminate")
            if featmgr.big_endian:
                iss_args.append("--big-endian")

            if featmgr.wysiwyg and failed_pc is not None:
                iss_args += ["--end-pc", str(hex(failed_pc))]

        elif isinstance(iss, Whisper):
            iss_log = self.run_dir / f"{self.testname}_whisper.log"
            if whisper_config_json_override is not None:
                whisper_config_json = whisper_config_json_override
            elif featmgr.secure_mode:
                whisper_config_json = self.package_path / "dtest_framework/lib/whisper_secure_config.json"
            else:
                whisper_config_json = self.package_path / "dtest_framework/lib/whisper_config.json"
            iss.whisper_config_json = whisper_config_json

            if featmgr.mp_mode_on():
                iss_args += [
                    "--quitany",
                    "--harts",
                    str(featmgr.num_cpus),
                    "--deterministic",
                    "16",
                    "--seed",
                    str(self.rng.get_seed()),
                ]
            if featmgr.wysiwyg and failed_pc is not None:
                iss_args += ["--endpc", str(hex(failed_pc))]
        else:
            raise ValueError("No ISS selected. Provide ISS in toolchain configuration")

        iss.run_iss(
            output_file=iss_log,
            elf_file=self.generated_files.elf,
            cwd=self.run_dir,
            timeout=120,
            args=iss_args,
        )

        # In wysiwyg mode, we need to parse the log file to find out what was the last value written to the x31
        if featmgr.wysiwyg:
            correct_wysiwyg_exit = False
            with open(iss_log, "r") as f:
                iss_lines = f.readlines()
            line = ""
            for line in iss_lines:
                if "x31" in line and "c001c0de" in line:
                    correct_wysiwyg_exit = True
            if not correct_wysiwyg_exit:
                raise RunnerError(f"WYSIWYG mode failed to find the correct exit value - last line {line}")

        log.info("\tTest \x1b[0;30;42m PASSED \x1b[0m successfully on ISS\n")
        return iss_log

    def _resolve_path(self, file: Union[str, Path]) -> Path:
        """
        Helper function to resolve config/test file paths. Tries relative to cwd, then package/install dir, then run_dir.

          relative to install directory.
        """
        if isinstance(file, str):
            filepath = Path(file).resolve()
        else:
            filepath = file.resolve()
        if not filepath.exists():
            filepath = (self.package_path / file).resolve()
        if not filepath.exists():
            filepath = (self.run_dir / file).resolve()
        if not filepath.exists():
            # temp until cpuconfig paths are fixed, check relative to install directory as last resort
            filepath = (self.package_path.parent / file).resolve()
        if not filepath.exists():
            raise FileNotFoundError(f"Couldn't resolve path to file - tried {Path(file).resolve()} and relative to install directory {filepath}")
        return filepath


def main():
    RiescueD.run_cli()


if __name__ == "__main__":
    main()
