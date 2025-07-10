# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
import subprocess
import logging
from typing import Optional
from pathlib import Path
import re

from riescue.lib.toolchain.tool import Tool
from riescue.lib.toolchain.exceptions import ToolchainError, ToolFailureType

log = logging.getLogger(__name__)


class Whisper(Tool):
    def __init__(self, whisper_path: Path, whisper_args: list, whisper_config_json: Path, whisper_max_instr: int, whisper_memory_size: str, whisper_dumpmem: str = None):
        whisper_config_json = self.check_filepath(whisper_config_json)
        args = whisper_args + [
            "--configfile",
            str(whisper_config_json),
            "--traceptw",
            "--memorysize",
            whisper_memory_size,
            f"--maxinst={whisper_max_instr}",
        ]
        self.dumpmem_arg = whisper_dumpmem
        super().__init__(path=whisper_path, env_name="WHISPER_PATH", tool_name="whisper", args=args)

    @staticmethod
    def add_args(parser: argparse.ArgumentParser):
        whisper_parser = parser.add_argument_group("Whisper", description="Arguments that affect Whisper ISS behavior. If not provided, will use SPIKE_PATH environment variable")
        # fmt: off
        whisper_parser.add_argument("--whisper_path", type=Path, default=Tool.package_path.parent / "whisper/whisper", help="Path to Whisper executable. If not provided, will use WHISPER_PATH environment variable")  # noqa: E501
        whisper_parser.add_argument("--whisper_args", nargs="*", default=[], help="Additional spike args to pass to the simulator")
        whisper_parser.add_argument("--whisper_config_json", type=Path, default=None, help="Relative path to the whisper config json file")  # noqa: E501
        whisper_parser.add_argument("--whisper_max_instr", type=int, default=2000000, help="Max instructions to simulate on whisper")
        whisper_parser.add_argument("--whisper_memory_size", type=str, default="0x10000000000000000", help="Size of whisper memory")
        whisper_parser.add_argument("--whisper_dumpmem", required=False, type=str, default=None, help="Dump memory command to pass to the simulator. Use @ to reference symbols in elf file")
        # fmt: on

    @classmethod
    def from_args(cls, args: argparse.Namespace, default_whisper_config_json: Path = Tool.package_path / "dtest_framework/lib/whisper_config.json"):
        if args.whisper_config_json is None:
            whisper_config_json = default_whisper_config_json
        else:
            whisper_config_json = args.whisper_config_json
        return cls(
            whisper_path=args.whisper_path,
            whisper_args=args.whisper_args,
            whisper_config_json=whisper_config_json,
            whisper_max_instr=args.whisper_max_instr,
            whisper_memory_size=args.whisper_memory_size,
            whisper_dumpmem=args.whisper_dumpmem,
        )

    def process_dumpmem_arg(self, elf_file: Path) -> str:
        """
        Replaces occurrences of @symbol in the input string with their values from nm output of the ELF file.
        Also performs basic arithmetic operations using eval.

        Args:
        elf_file (str or Path): Path to the ELF file.

        Returns:
        str: String with all @variables replaced with their nm values (hex strings).
        """
        # 1. Collect all @xxx variables in the string
        varnames = set(re.findall(r"@([A-Za-z0-9_]+)", self.dumpmem_arg))
        if not varnames:
            return self.dumpmem_arg

        # 2. Run nm and collect their values
        try:
            nm_out = subprocess.check_output(["nm", str(elf_file)], encoding="utf-8")
        except subprocess.CalledProcessError as e:
            print(f"Error running nm: {e}")
            return self.dumpmem_arg

        # Build symbol name -> value dict
        symvals = {}
        for line in nm_out.splitlines():
            line = line.strip()
            # Typical nm line: 0000000010020000 D breadcrumbs_phys
            m = re.match(r"^([0-9a-fA-F]+)\s+\w\s+(.+)$", line)
            if m:
                addr, sym = m.groups()
                symvals[sym] = hex(int(addr, 16))

        # 3. Replace @var in the string
        def repl(match):
            var = match.group(1)
            return symvals.get(var, var)

        result = re.sub(r"@([A-Za-z0-9_]+)", repl, self.dumpmem_arg)

        # process any arithmetic operations in the string
        terms = result.split(":")
        for idx, term in enumerate(terms):
            try:
                terms[idx] = hex(eval(term))
            except Exception as e:
                pass
        return ":".join(terms)

    def run(self, elf_file: Path, output_file, cwd=None, timeout=60) -> subprocess.CompletedProcess:
        """
        Whisper is a special run case since output file is an argument for command instead of pipe
        """
        if self.dumpmem_arg:
            self.args.extend(["--dumpmem", self.process_dumpmem_arg(elf_file)])

        self.args.extend([str(elf_file)])
        self.log_file = output_file
        self.args.extend(["--logfile", str(self.log_file)])
        # print(" ".join(self.args))
        return super().run(output_file=None, cwd=cwd, timeout=timeout)

    def _classify(self, process: subprocess.CompletedProcess, output_file: Optional[Path]):
        if process.returncode != 0:
            if "Failed to load ELF" in process.stderr:
                failed_to_load_message = process.stderr.split("Error:")[-1].strip()
                self._raise_toolchain_error(process, ToolFailureType.ELF_FAILURE, failed_to_load_message)
            elif "No program file specified" in process.stderr:
                self._raise_toolchain_error(process, ToolFailureType.ELF_FAILURE, "No program file specified", log_path=self.log_file)
            elif "consecutive illegal instructions" in process.stderr:
                error_line = "consecutive illegal instructions"
                for line in process.stderr.split("\n"):
                    if "consecutive illegal instructions" in line:
                        error_line = line
                        break
                self._raise_toolchain_error(process, ToolFailureType.ILLEGAL_INSTRUCTIONS, error_line, log_path=self.log_file)

            elif self.log_file.exists():
                with open(self.log_file, "r") as f:
                    log_lines = f.readlines()
                if log_lines == []:
                    self._raise_toolchain_error(process, ToolFailureType.BAD_CONFIG, "\t" + process.stderr.replace("\n", "\n\t"))
                else:
                    last_write = self._find_tohost_write(log_lines)
                    tohost_value = int(last_write.split()[7], 16)
                    self._raise_toolchain_error(process, ToolFailureType.TOHOST_FAIL, process.stderr, fail_code=tohost_value, log_path=self.log_file)
            else:
                log.error(f"Couldn't find a log file at {self.log_file}")
            self._raise_toolchain_error(process, ToolFailureType.NONZERO_EXIT, process.stderr)
        if "Reached instruction limit hart=0" in process.stderr:
            raise ToolchainError(tool_name=self.__class__.__name__, cmd=process.args, kind=ToolFailureType.MAX_INSTRUCTION_LIMIT, returncode=process.returncode, error_text=process.stderr)

    def _find_tohost_write(self, log_lines: list[str]):
        "Assumes the last line is a write to the tohost address, doesn't check what the tohost address is"
        i = -1
        last_line = log_lines[i]
        while not last_line.startswith("#"):
            i -= 1
            last_line = log_lines[i]
        return last_line.strip()
