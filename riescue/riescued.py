# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import subprocess
import shutil
import sys
import random
import logging
from pathlib import Path

import riescue.lib.logger as RiescueLogger
from riescue.lib.json_argparse import JsonArgParser
from riescue.lib.rand import RandNum
from riescue.dtest_framework.parser import Parser
from riescue.dtest_framework.generator import Generator
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.configure import configure
from riescue.lib.cli_base import CliBase
from riescue.lib.toolchain import Compiler, Disassembler, Spike, Whisper


log = logging.getLogger(__name__)


class RunnerError(Exception):
    pass


class RiescueD(CliBase):
    package_path = Path(__file__).parent

    def __init__(self):
        pass

    @staticmethod
    def add_arguments(parser):
        "Adds group arguments to the parser"
        RiescueLogger.add_args(parser)
        Compiler.add_args(parser)
        Disassembler.add_args(parser)
        Spike.add_args(parser)
        Whisper.add_args(parser)

    @classmethod
    def run_cli(cls, args=None, **kwargs):
        """
        Use JsonArgParser to parse commandline arguments
        """

        parser = JsonArgParser.from_json(cls.package_path / "dtest_framework/cmdline.json")
        cls.add_arguments(parser)
        cl_args = parser.parse_args(args)

        if args is not None:
            print("riescued.py " + " ".join(args))
        else:
            print(" ".join(sys.argv))

        rd = cls(**kwargs)
        rd.run(cl_args)
        return rd

    def run(self, cl_args):
        # Set seed for the simulation, if commandline did not specify one use random one
        seed = 0
        if cl_args.seed is None:
            seed = random.randrange(2**32)
        else:
            seed = cl_args.seed
        self.rng = RandNum(seed)
        print(f"RiescueD: Using seed: {seed}")

        # Run directory management
        run_dir = Path(cl_args.run_dir).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)

        file = cl_args.testname
        filepath = Path(file).resolve()
        if not filepath.exists():
            filepath = (self.package_path / file).resolve()
        if not filepath.exists():
            filepath = (run_dir / file).resolve()
        if not filepath.exists():
            raise FileNotFoundError(f"Couldn't resolve path to file - tried {Path(file).resolve()} and {filepath}")
        testname = filepath.stem

        # Output Files
        test_assembly = run_dir / f"{testname}.S"
        test_elf = run_dir / testname
        test_dis = run_dir / f"{testname}.dis"
        test_logfile = run_dir / f"{testname}.testlog"
        RiescueLogger.from_args(args=cl_args, default_logger_file=test_logfile)

        log.info("Initializing pool")
        self.pool = Pool()
        self.pool.testname = testname

        # Copy test s file to current directory
        try:
            shutil.copy(filepath, run_dir)
        except shutil.SameFileError:
            pass

        parser = Parser(filepath, pool=self.pool)
        parser.parse()

        # Setup simulation configuration
        cpu_config = cl_args.cpuconfig
        if cl_args.cpuconfig:
            cpu_config = self.package_path / cl_args.cpuconfig
            if not cpu_config.exists():
                cpu_config = self.package_path.parent / cl_args.cpuconfig
        else:
            cpu_config = self.package_path / "dtest_framework/lib/config.json"
        featmgr = configure(rng=self.rng, pool=self.pool, json_config=cpu_config, cmdline_args=cl_args)

        print(f"test_config: env: {featmgr.env}")
        print(f"test_config: priv: {featmgr.priv_mode}")
        print(f"test_config: paging: {featmgr.paging_mode}")
        print(f"test_config: paging_g: {featmgr.paging_g_mode}")
        print(f"test_config: secure_mode: {featmgr.secure_mode}")
        log.info(f"test_config: env: {featmgr.env}")
        log.info(f"test_config: secure_mode: {featmgr.secure_mode}")
        log.info(f"test_config: priv: {featmgr.priv_mode}")
        log.info(f"test_config: paging: {featmgr.paging_mode}")
        log.info(f"test_config: paging_g: {featmgr.paging_g_mode}")

        # Call various generators
        test_gen = Generator(rng=self.rng, pool=self.pool, featmgr=featmgr, run_dir=run_dir)
        test_gen.generate(filepath, test_assembly)
        linker_script = test_gen.linker_script

        if cl_args.elaborate_only:
            print("Elaboration complete. Exiting...")
            log.info("Elaboration complete. Exiting...")
            return

        compiler = Compiler.from_args(cl_args)
        # fmt: off
        compiler.args += [
            "-I", str(run_dir),
            "-T", str(linker_script),
            "-o", str(test_elf),
            str(test_assembly),
        ]
        # fmt: on
        if cl_args.big_endian:
            compiler.args.append("-mbig-endian")

        disassembler = Disassembler.from_args(cl_args)
        disassembler.args.extend(["-D", str(test_elf), "-M", "numeric"])

        print("\nCompiling the test:")
        try:
            compiler.run(cwd=run_dir)
        except subprocess.CalledProcessError as e:
            print("Test x1b[6;30;41mFAILED\x1b[0m to compile on gcc\n")  # TODO: print_red shared function
            raise e

        # Generate Disassembly
        print("\nGenerating disassembly:")
        try:
            disassembler.run(output_file=test_dis, cwd=run_dir)
        except subprocess.CalledProcessError as e:
            print("Test x1b[6;30;41mFAILED\x1b[0m to disassemble\n")  # TODO: print_red shared function
            raise e

        # In wysiwyg mode, we use a different end-of-test mechanism where we look for x31=0xc001c0de to be written
        # This is not supported by whisper, so we are using --endpc to the end of the test in whisper
        # To do this, we need to find the pc of label "fail" in disassembly and send it to --endpc
        # Later we parse whisper log to find out what was the last value written to the x31 to indicate pass|fail
        failed_pc = None
        if cl_args.wysiwyg:
            # Read file <testname>.dis and find <failed>
            with open(test_dis, "r") as f:
                disasm_lines = f.readlines()
            for line in disasm_lines:
                if "<failed>:" in line:
                    # split line by spaces and get the first element
                    failed_pc = line.split()[0]
                    failed_pc = int(failed_pc, 16) + 0x10
                    print(f"Setting end-of-sim pc to: {failed_pc:016x}")

        # Run through simulator
        if cl_args.run_iss:
            iss_log = run_dir / f"{self.pool.testname}_{cl_args.iss}.log"

            # Spike ISS path and args
            if cl_args.iss == "spike":
                iss = Spike.from_args(cl_args)
                iss.args.append("--priv=msu")
                if not cl_args.force_alignment:
                    iss.args.append("--misaligned")

                if featmgr.mp_mode_on():
                    iss.args.append(f"-p{featmgr.num_cpus}")

                if cl_args.tohost_nonzero_terminate or cl_args.fe_tb:
                    iss.args.append("--tt-tohost-nonzero-terminate")
                if cl_args.big_endian:
                    iss.args.append("--big-endian")

                if cl_args.wysiwyg and failed_pc is not None:
                    iss.args += ["--end-pc", str(hex(failed_pc))]

            else:
                iss = Whisper.from_args(cl_args, default_whisper_config_json=featmgr.default_whisper_config_json)

                if featmgr.mp_mode_on():
                    iss.args += [
                        "--quitany",
                        "--harts",
                        str(featmgr.num_cpus),
                        "--deterministic",
                        "16",
                        "--seed",
                        str(seed),
                    ]
                if cl_args.wysiwyg and failed_pc is not None:
                    iss.args += ["--endpc", str(hex(failed_pc))]

            iss_output = iss.run(output_file=iss_log, elf_file=str(test_elf), cwd=run_dir, timeout=120)
            iss_stderr = iss_output.stderr or ""

            # In wysiwyg mode, we need to parse the log file to find out what was the last value written to the x31
            if cl_args.wysiwyg:
                correct_wysiwyg_exit = False
                with open(iss_log, "r") as f:
                    iss_lines = f.readlines()
                line = ""
                for line in iss_lines:
                    if "x31" in line and "c001c0de" in line:
                        correct_wysiwyg_exit = True
                        exitstatus = 0
                if not correct_wysiwyg_exit:
                    raise RunnerError(f"WYSIWYG mode failed to find the correct exit value - last line {line}")

            print("\nTest \x1b[0;30;42m PASSED \x1b[0m successfully on ISS\n")
