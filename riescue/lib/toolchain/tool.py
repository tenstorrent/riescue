# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import os
import argparse
import subprocess
import shutil
import logging
from abc import ABC, abstractmethod
from typing import Optional
from pathlib import Path

from riescue.lib.toolchain.exceptions import ToolchainError, ToolFailureType

log = logging.getLogger(__name__)


class Tool(ABC):
    """
    Class for running executable tools.
    Attempts to use provided path, otherwise uses environment variable, otherwise searches for path.

    :param args: arguments to run with executable
    :param path: path to executable
    :param env_name: name of executable (e.g. "RV_GCC")
    :param tool_name: executable name to search for in PATH (e.g. "riscv64-unknown-elf-gcc")
    """

    package_path = Path(__file__).parents[2].resolve()

    def __init__(self, path, env_name, tool_name, args=None):
        self.args = args if args else []
        self.executable = self.find_executable(path, env_name, tool_name).resolve()
        log.info(f"Built {self.__class__.__name__} with executable: {self.executable}")

    def find_executable(self, tool_path: Path, env_name: str, tool_name: str) -> Path:
        """
        Searches for executable. Checks for Path tool_path if provided
        if no tool_path, checks for env_name environment variable
        if no env_name, checks for tool_name in PATH
        """
        log.info("Searching for executable")
        if tool_path is not None and tool_path.exists():
            return tool_path
        if env_name:
            env_path = os.environ.get(env_name)
            if env_path:
                env_tool_path = Path(env_path)
                if env_tool_path.exists():
                    return env_tool_path
        which_executable = shutil.which(tool_name)
        if which_executable is None:
            raise FileNotFoundError(f"Could not find {tool_name} in PATH. Add it to your PATH or set {env_name} environment variable")
        else:
            return Path(which_executable)

    def check_filepath(self, filepath: Path) -> Path:
        """
        To support repo and python package installs, data files should be relative to install directory `riescue/`
        Returns path if path is absolute, otherwise checks file relative to cwd, then to riescue library.
        Raises FileNotFoundError if data file isn't found.
        """
        if filepath.is_absolute():
            if not filepath.exists():
                raise FileNotFoundError(f"Couldn't find file at absolute path {filepath}")
            return filepath
        if filepath.exists():
            return filepath
        riescue_relative = self.package_path / filepath
        if riescue_relative.exists():
            return riescue_relative
        raise FileNotFoundError(f"Couldn't find filepath {riescue_relative} Tried relative to current directory and library [{riescue_relative}]")

    def run(self, output_file=None, cwd=None, timeout=90) -> subprocess.CompletedProcess:
        """
        Run the executable with args, returns CompletedProcess.
        Use output_file to pipe all stdout to a file. stderr piped to terminal

        :param output_file: file to pipe output to
        :param cwd: working directory to run the command in
        :param timeout: timeout for the command

        :raises ToolchainError if tool failed to run
        """
        cmd = [self.executable] + self.args
        run_str = f"Running {' '.join(str(c) for c in cmd)}"
        log.info(run_str)

        if output_file is not None:
            if not isinstance(output_file, Path):
                raise TypeError(f"Expected output_file to be a Path, got {type(output_file)}. Cannot pipe to output file")
            log.info(f"Piping output to {str(output_file)}")
            with output_file.open("w") as f:
                process = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, text=True, cwd=cwd, timeout=timeout)
        else:
            process = subprocess.run(cmd, text=True, cwd=cwd, timeout=timeout, stderr=subprocess.PIPE)

        if process.returncode == 139:
            self._raise_toolchain_error(process, kind=ToolFailureType.SEGFAULT, error_text=process.stderr)

        self._classify(process, output_file)
        if process.stderr is not None:
            for line in process.stderr.split("\n"):
                if line:
                    log.warning(line)
        return process

    def _raise_toolchain_error(self, process: subprocess.CompletedProcess, kind: ToolFailureType, error_text: Optional[str] = None, **kwargs):
        """
        Helper method to raise ToolchainError with standardized parameters. Use instead of raising directly.
        """
        raise ToolchainError(tool_name=self.__class__.__name__, cmd=process.args, kind=kind, returncode=process.returncode, error_text=error_text, **kwargs)

    @abstractmethod
    def _classify(self, process: subprocess.CompletedProcess, output_file: Optional[Path]):
        """
        Check the return code of the subprocess run, raises meaningful ToolchainError if tool failed to run.

        :param process: subprocess.CompletedProcess
        :param output_file: optional output file tool piped to
        :raises ToolchainError if tool failed to run
        """
        pass


class Compiler(Tool):
    def __init__(self, compiler_path, compiler_opts, compiler_march, test_equates, abi: Optional[str] = None, feat_mgr=None):
        # If feat_mgr is provided and compiler_march is None, generate march from features
        if feat_mgr is not None and compiler_march is None:
            compiler_march = feat_mgr.get_compiler_march_string()
        elif compiler_march is None:
            # Fallback to default if no feat_mgr provided
            compiler_march = "rv64imafdcvh_svinval_zfh_zba_zbb_zbc_zbs_zifencei_zicsr_zvkned_zicbom_zicbop_zicboz_zawrs_zihintpause_zvbb1_zicond_zvkg_zvkn_zvbc_zfa"

        args = [
            "-static",
            "-mcmodel=medany",
            "-fvisibility=hidden",
            "-nostdlib",
            "-nostartfiles",
            f"-march={compiler_march}",
        ] + compiler_opts

        if abi is not None:
            args.append(f"-mabi={abi}")
        for define in test_equates or []:
            if "=" not in define:
                raise ValueError(f"Invalid define: {define}. Expected format is <name>=<value>")
            args.append(f"-D{define}")
        super().__init__(path=compiler_path, env_name="RV_GCC", tool_name="riscv64-unknown-elf-gcc", args=args)

    @staticmethod
    def add_args(parser: argparse.ArgumentParser):
        compiler_parser = parser.add_argument_group("Compiler", description="Arguments that affect compiling behavior")
        # fmt: off
        compiler_parser.add_argument("--compiler_path", type=Path, default=None, help="Path to compiler executable. If not provided, will use RV_GCC environment variable")
        compiler_parser.add_argument("--compiler_opts", nargs="*", default=[], help="Additional args to pass to the compiler")
        compiler_parser.add_argument("--compiler_march", type=str, default=None, help="march to pass to compiler. If not provided and config.json is available, will be generated from enabled features")   # noqa: E501
        compiler_parser.add_argument("--compiler_mabi", type=str, default=None, help="ABI to use for compiler. Leave unset to use compiler default")   # noqa: E501
        compiler_parser.add_argument("--test_equates", "-teq", action="append", help="Variables to have compiler define with -Dkey=value. e.g \n\t--test_equates EQUATE1=0x1 \nCan use multiple times, e.g. \n\t-teq A=1 -teq B=2.")  # noqa: E501
        compiler_parser.add_argument("--config_json", type=Path, default=None, help="Path to config.json file for feature management")  # noqa: E501
        # fmt: on

    @classmethod
    def from_args(cls, args: argparse.Namespace):
        feat_mgr = None

        # If config_json is provided, create FeatMgr instance
        if hasattr(args, "config_json") and args.config_json is not None:
            try:
                # Import here to avoid circular imports
                from riescue.dtest_framework.featmanager import FeatMgr
                from riescue.lib.rand import RandNum

                # Create a minimal FeatMgr instance just for feature management
                rng = RandNum()
                feat_mgr = FeatMgr(rng=rng, pool=None, config_path=args.config_json, test_config=None, cmdline=args)  # Not needed for march generation  # Not needed for march generation
            except ImportError as e:
                log.warning(f"Could not import FeatMgr: {e}")
                feat_mgr = None

        return cls(compiler_path=args.compiler_path, compiler_opts=args.compiler_opts, compiler_march=args.compiler_march, test_equates=args.test_equates, feat_mgr=feat_mgr)

    def _classify(self, process: subprocess.CompletedProcess, output_file: Optional[Path]):
        if process.returncode != 0:
            output_text = None
            if output_file is not None:
                with open(output_file, "r") as f:
                    output_text = f.read()
            else:
                output_text = process.stderr
            if output_text is None:
                self._raise_toolchain_error(process, ToolFailureType.COMPILE_FAILURE, "No output from compiler")
            else:
                self._raise_toolchain_error(process, ToolFailureType.COMPILE_FAILURE, output_text)


class Disassembler(Tool):
    def __init__(self, disassembler_path, disassembler_opts):
        super().__init__(path=disassembler_path, env_name="RV_OBJDUMP", tool_name="riscv64-unknown-elf-objdump")

    @staticmethod
    def add_args(parser: argparse.ArgumentParser):
        disassembler_parser = parser.add_argument_group("Disassembler", description="Arguments for disassembler")
        # fmt: off
        disassembler_parser.add_argument("--disassembler_path", type=Path, default=None, help="Path to disassembler executable. If not provided, will use RV_OBJDUMP environment variable")
        disassembler_parser.add_argument("--disassembler_opts", nargs="*", default=[], help="Additional args to pass to the disassembler")
        # fmt: on

    @classmethod
    def from_args(cls, args: argparse.Namespace):
        if args.disassembler_opts:
            # FIXME: not passing disassembler_opts to disassembler. Legacy flows are using `--dissasembler_opts=zvbb`
            # Before removing need to fix legacy flows calling with --disassembler_opts
            log.warning(f"--disassembler_opts={', --disassembler_opts='.join(args.disassembler_opts)} are ignored for disassembler")
        return cls(disassembler_path=args.disassembler_path, disassembler_opts=args.disassembler_opts)

    def _classify(self, process: subprocess.CompletedProcess, output_file: Optional[Path]):
        if process.returncode != 0:
            if process.stderr is None:
                if output_file is None:
                    stdout = "No output file was produced"
                else:
                    with open(output_file, "r") as f:
                        stdout = f.read().strip()
                self._raise_toolchain_error(process, ToolFailureType.COMPILE_FAILURE, stdout)
            else:
                self._raise_toolchain_error(process, ToolFailureType.COMPILE_FAILURE, process.stderr)


class Objcopy(Tool):
    def __init__(self, objcopy_path, objcopy_opts):
        super().__init__(path=objcopy_path, env_name="RV_OBJCOPY", tool_name="riscv64-unknown-elf-objcopy", args=objcopy_opts)

    @staticmethod
    def add_args(parser: argparse.ArgumentParser):
        objcopy_parser = parser.add_argument_group("Objcopy", description="Arguments for objcopy")
        # fmt: off
        objcopy_parser.add_argument("--objcopy_path", type=Path, default=None, help="Path to objcopy executable. If not provided, will use RV_OBJCOPY environment variable")
        objcopy_parser.add_argument("--objcopy_opts", nargs="*", default=[], help="Additional args to pass to the objcopy")
        # fmt: on

    @classmethod
    def from_args(cls, args: argparse.Namespace):
        return cls(objcopy_path=args.objcopy_path, objcopy_opts=args.objcopy_opts)

    def _classify(self, process: subprocess.CompletedProcess, output_file: Optional[Path]):
        if process.returncode != 0:
            log.error(f"Objcopy failed - stderr: {process.stderr}, stdout: {process.stdout}, returncode: {process.returncode}")
            self._raise_toolchain_error(process, ToolFailureType.COMPILE_FAILURE, process.stderr)


class Spike(Tool):
    def __init__(self, spike_path, spike_args, spike_isa, third_party_spike, spike_max_instr):
        args = spike_args + ["-l", "--log-commits"]
        if third_party_spike:
            if spike_isa is None:
                spike_isa = "RV64IMAFDCVH_zba_zbb_zbc_zfh_zbs_zfbfmin_zvfh_zvbb_zvbc_zvfbfmin_zvfbfwma_zvkg_zvkned_zvl256b_zve64d_svpbmt"
            tool_name = "spike"
        else:
            if spike_isa is None:
                spike_isa = "RV64IMAFDCVH_zba_zbb_zbc_zfh_zbs_zfbfmin_zvfh_zvbb_zvbc_zvfbfmin_zvfbfwma_zvkg_zvkned_zvknhb_svpbmt_sstc_zicntr"
            tool_name = "tt_spike"
            args.append("--varch=vlen:256,elen:64")
            args.append(f"--max-instrs={spike_max_instr}")
        args += [f"--isa={spike_isa}"]
        super().__init__(path=spike_path, env_name="SPIKE_PATH", tool_name=tool_name, args=args)

    @staticmethod
    def add_args(parser: argparse.ArgumentParser):
        spike_parser = parser.add_argument_group("Spike", description="Arguments that affect Spike ISS behavior")
        spike_parser.add_argument("--spike_path", type=Path, default=None, help="Path to spike executable")
        spike_parser.add_argument("--spike_args", nargs="*", default=[], help="Additional spike args to pass to the simulator")
        spike_parser.add_argument("--spike_isa", type=str, default=None, help="ISA string to pass to ISS --isa")
        spike_parser.add_argument("--third_party_spike", action="store_true", help="Use public version of spike. Default is internal spike")
        spike_parser.add_argument("--spike_max_instr", type=int, default=2000000, help="Max instructions to simulate on spike")

    @classmethod
    def from_args(cls, args: argparse.Namespace):
        return cls(spike_path=args.spike_path, spike_args=args.spike_args, spike_isa=args.spike_isa, third_party_spike=args.third_party_spike, spike_max_instr=args.spike_max_instr)

    def _classify(self, process: subprocess.CompletedProcess, output_file: Optional[Path]):
        if process.returncode != 0:
            self._raise_toolchain_error(process, ToolFailureType.NONZERO_EXIT)

    def run(self, elf_file: Path, output_file, cwd=None, timeout=60) -> subprocess.CompletedProcess:
        self.args.extend([str(elf_file)])
        return super().run(output_file=output_file, cwd=cwd, timeout=timeout)
