# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
import subprocess
import logging
from typing import Optional
from pathlib import Path

from riescue.lib.toolchain.tool import Tool
from riescue.lib.toolchain.exceptions import ToolchainError, ToolFailureType

log = logging.getLogger(__name__)


class Sail(Tool):
    """
    Sail RISC-V golden model ISS backend (Sail 0.10+).

    Sail (riscv-sail) is used as a second-pass ISS — it runs the final compiled
    ELF and pass/fail is determined by the HTIF tohost write detected in Sail's
    trace log.

    Sail 0.10 changed the CLI significantly from older versions:
      - Configuration is now done via a JSON config file (--config /
        --config-override) rather than individual CLI flags like
        --enable-dirty-update or --mtval-has-illegal-inst-bits.
      - Trace output is written to a file via --trace-output <file>.
      - --trace-htif enables HTIF-specific trace lines, which is how we detect
        tohost writes for pass/fail determination.
      - --inst-limit replaces the old --instruction-limit flag.
      - The binary is now a unified 'sail_riscv_sim' supporting both RV32/RV64,
        with ISA configured via the JSON config file rather than a CLI ISA string.

    Sail is NOT used as a first-pass ISS (that role belongs to Spike), since
    Sail's log format is incompatible with riescue's first-pass log parser.

    Known limitations (tracked as GitHub issues):
      - Memory layout must be configured via Sail's JSON config file. Riescue
        does not yet auto-generate this from cpu_config mmap.
      - The default Sail config uses 256MB DRAM at 0x80000000. The riescue
        cpu_config must match this layout, or a custom --sail_config_override
        must be provided.
      - Sail tohost trace format may differ slightly between 0.10.x releases.
        The parser handles all known variants.
    """

    def __init__(
        self,
        sail_path: Optional[Path] = None,
        sail_args: Optional[list[str]] = None,
        sail_config: Optional[Path] = None,
        sail_config_override: Optional[Path] = None,
        sail_max_instr: int = 2000000,
    ):
        """
        :param sail_path: path to sail_riscv_sim binary. If None, uses
            SAIL_PATH env var or searches PATH for 'sail_riscv_sim'.
        :param sail_args: additional args forwarded directly to Sail.
        :param sail_config: path to Sail JSON config file (--config).
            If None, Sail uses its built-in default config (RV64GC, 256MB DRAM
            at 0x80000000). See sail_riscv_sim --print-default-config for the
            full schema, and share/sail-riscv/config/ for example configs.
        :param sail_config_override: path to a partial JSON config that
            overrides only specific fields (--config-override). Recommended
            for adjusting memory layout to match riescue's cpu_config without
            rewriting the full config.
        :param sail_max_instr: maximum instructions before Sail terminates.
            Passed via --inst-limit (renamed from --instruction-limit in 0.10).
        """
        if sail_args is None:
            sail_args = []

        self.sail_config = sail_config
        self.sail_config_override = sail_config_override
        self.sail_max_instr = sail_max_instr

        # Sail 0.10 base args:
        #   --inst-limit N  : stop after N instructions
        #   --trace-htif    : emit HTIF trace lines including tohost writes.
        #                     Required for _parse_tohost() to detect pass/fail.
        #                     Without this, tohost writes are silent.
        # Note: --config / --config-override / --trace-output are added in
        # run_iss() since they reference paths resolved at runtime.
        args = sail_args + [
            "--inst-limit", str(sail_max_instr),
            "--trace-htif",
        ]

        super().__init__(
            path=sail_path,
            env_name="SAIL_PATH",
            tool_name="sail_riscv_sim",
            args=args,
        )

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser):
        sail_parser = parser.add_argument_group(
            "Sail",
            description="Arguments that affect Sail RISC-V golden model ISS behavior (Sail 0.10+)",
        )
        # fmt: off
        sail_parser.add_argument(
            "--sail_path", type=Path, default=None,
            help="Path to Sail simulator executable (sail_riscv_sim). "
                 "Unified binary supporting both RV32 and RV64. "
                 "If not provided, uses SAIL_PATH environment variable or searches PATH.",
        )
        sail_parser.add_argument(
            "--sail_args", nargs="*", default=[],
            help="Additional args to pass directly to the Sail simulator",
        )
        sail_parser.add_argument(
            "--sail_config", type=Path, default=None,
            help="Path to Sail JSON config file (--config). Controls ISA, memory layout, "
                 "and platform settings. If not provided, Sail uses its built-in default "
                 "(RV64GC, 256MB DRAM at 0x80000000). "
                 "Run 'sail_riscv_sim --print-default-config' to see the full schema. "
                 "Example configs: share/sail-riscv/config/rv64d_v256_e64.json.",
        )
        sail_parser.add_argument(
            "--sail_config_override", type=Path, default=None,
            help="Path to a partial Sail JSON config (--config-override). "
                 "Overrides only specified fields, leaving the rest at defaults. "
                 "Recommended for adjusting memory layout to match riescue cpu_config "
                 "without rewriting the full config.",
        )
        sail_parser.add_argument(
            "--sail_max_instr", type=int, default=2000000,
            help="Max instructions to simulate on Sail (--inst-limit). Default: 2000000.",
        )
        # fmt: on

    @classmethod
    def from_clargs(cls, args: argparse.Namespace) -> "Sail":
        return cls(
            sail_path=args.sail_path,
            sail_args=args.sail_args if args.sail_args else [],
            sail_config=getattr(args, "sail_config", None),
            sail_config_override=getattr(args, "sail_config_override", None),
            sail_max_instr=args.sail_max_instr,
        )

    def run(self, output_file=None, cwd=None, timeout=90, args: Optional[list[str]] = None):
        raise NotImplementedError("Use run_iss() instead of run() for Sail.")

    def run_iss(
        self,
        elf_file: Path,
        output_file: Path,
        cwd=None,
        timeout=120,
        args: Optional[list[str]] = None,
    ) -> subprocess.CompletedProcess:
        """
        Run Sail 0.10 on the given ELF file.

        In Sail 0.10, trace output goes to --trace-output <file> rather than
        stdout. We pass output_file as the --trace-output destination so that
        _classify/_parse_tohost can find the HTIF tohost lines.

        :param elf_file: path to the ELF binary to simulate
        :param output_file: path to write Sail's trace log to (--trace-output)
        :param cwd: working directory
        :param timeout: simulation timeout in seconds. Default 120s (Sail is
            an interpreter and runs significantly slower than Spike).
        :param args: additional runtime args forwarded after standard flags
        """
        if args is None:
            args = []

        extra_args = list(args)

        # --config: full Sail JSON config (ISA, memory, platform).
        # Resolve to absolute path so Sail can find it regardless of cwd.
        if self.sail_config is not None:
            sail_config_abs = Path(self.sail_config).resolve()
            extra_args.extend(["--config", str(sail_config_abs)])
            log.debug(f"Sail using config: {sail_config_abs}")
        else:
            log.debug(
                "No --sail_config provided. Using Sail built-in default "
                "(RV64GC, 256MB DRAM at 0x80000000). If your ELF has segments "
                "outside this range, provide --sail_config_override."
            )

        # --config-override: partial JSON to change specific fields only.
        # Resolve to absolute path so Sail can find it regardless of cwd.
        if self.sail_config_override is not None:
            sail_config_override_abs = Path(self.sail_config_override).resolve()
            extra_args.extend(["--config-override", str(sail_config_override_abs)])
            log.debug(f"Sail using config override: {sail_config_override_abs}")

        # --trace-output: Sail 0.10 writes trace to a file, not stdout.
        # _parse_tohost() will read this file for HTIF tohost lines.
        # Resolve to absolute path for same reason.
        self.log_file = output_file
        extra_args.extend(["--trace-output", str(Path(output_file).resolve())])

        # ELF file is a positional argument at the end of the command.
        # Resolve to absolute path.
        extra_args.append(str(Path(elf_file).resolve()))

        # output_file=None because Sail writes trace to --trace-output itself.
        # Sail's stdout/stderr are used only for errors and warnings.
        return super().run(
            output_file=None,
            cwd=cwd,
            timeout=timeout,
            args=extra_args,
        )

    def _classify(self, process: subprocess.CompletedProcess, output_file: Optional[Path]):
        """
        Determine pass/fail from Sail's exit code and trace log.

        Sail 0.10 exit codes:
          0  — simulation ran to completion (NOT necessarily a test pass)
          1  — configuration or startup error
          other — unexpected failure

        Pass/fail is determined by parsing the --trace-htif output for a
        tohost write. tohost=1 means pass; any other nonzero value is a
        test failure code.

        Note: output_file is None here because Sail writes its trace via
        --trace-output (not stdout piping). The actual trace path is stored
        in self.log_file, set in run_iss() before calling super().run().
        """
        # Resolve the actual trace log path — Sail writes it itself via
        # --trace-output, so output_file from super().run() is always None.
        trace_log = output_file if output_file is not None else getattr(self, "log_file", None)
        stderr = process.stderr or ""

        if process.returncode != 0:
            if "No such file" in stderr or "failed to load" in stderr.lower():
                self._raise_toolchain_error(
                    process, ToolFailureType.ELF_FAILURE,
                    f"Sail failed to load ELF: {stderr.strip()}",
                )
            if "inst-limit" in stderr.lower() or "instruction limit" in stderr.lower():
                self._raise_toolchain_error(
                    process, ToolFailureType.MAX_INSTRUCTION_LIMIT, stderr.strip(),
                )
            if "error" in stderr.lower() and ("config" in stderr.lower() or "option" in stderr.lower()):
                self._raise_toolchain_error(
                    process, ToolFailureType.BAD_CONFIG,
                    f"Sail config/option error: {stderr.strip()}",
                )
            self._raise_toolchain_error(
                process, ToolFailureType.NONZERO_EXIT, stderr.strip(),
            )

        # Sail exited 0 — check trace file for tohost write
        if trace_log is not None and Path(trace_log).exists():
            tohost_value = self._parse_tohost(trace_log)
            if tohost_value is None:
                log.warning(
                    f"No tohost write found in Sail trace at {trace_log}. "
                    "Possible causes: instruction limit reached before EOT, "
                    "HTIF not mapped in Sail config, or test has no EOT code."
                )
                self._raise_toolchain_error(
                    process, ToolFailureType.MAX_INSTRUCTION_LIMIT,
                    "No tohost write found in Sail trace log",
                )
            if tohost_value != 1:
                self._raise_toolchain_error(
                    process, ToolFailureType.TOHOST_FAIL,
                    f"Sail tohost failure: tohost={hex(tohost_value)}",
                    fail_code=tohost_value,
                    log_path=trace_log,
                )
            # tohost == 1 → test passed, fall through normally
        else:
            log.warning(
                f"Sail trace log not found at {trace_log}. "
                "Cannot verify tohost pass/fail. "
                "Check that --trace-output path is writable and Sail ran successfully."
            )

    def _parse_tohost(self, log_file: Path) -> Optional[int]:
        """
        Parse Sail 0.10 trace log for the last HTIF tohost write.

        With --trace-htif enabled, Sail 0.10 emits lines like:
            [HTIF] tohost write: 0x1
            [HTIF] tohost: 0x1
            htif: tohost = 0x1

        We collect the last tohost value seen (the final EOT write).
        Returns the integer tohost value, or None if no tohost write found.
        """
        tohost_value = None
        try:
            with open(log_file, "r") as f:
                for line in f:
                    val = self._try_parse_tohost_line(line.strip())
                    if val is not None:
                        tohost_value = val  # keep updating — use last seen
        except OSError as e:
            log.error(f"Could not read Sail trace log {log_file}: {e}")
            return None
        return tohost_value

    def _try_parse_tohost_line(self, line: str) -> Optional[int]:
        """
        Attempt to extract a tohost value from a single trace log line.
        Handles all known Sail 0.10.x --trace-htif output formats.
        Returns the integer value if found, None otherwise.

        Known formats from Sail 0.10:
          "htif[0x0000000070000000] <- 0x00000001"   (write arrow format)
          "tohost: 0x1"
          "tohost = 0x1"
          "[HTIF] tohost write: 0x1"
        """
        line_lower = line.lower()

        # Sail 0.10 --trace-htif primary format:
        # "htif[0x<addr>] <- 0x<value>"
        # We match any htif write line regardless of address.
        if "htif[" in line_lower and "<-" in line:
            parts = line.split("<-")
            if len(parts) == 2:
                val_str = parts[1].strip()
                try:
                    if val_str.lower().startswith("0x"):
                        return int(val_str, 16)
                    elif val_str.isdigit():
                        return int(val_str, 10)
                except ValueError:
                    pass
            return None

        # Fallback formats from older Sail versions
        if "tohost" not in line_lower:
            return None

        # Try splitting on the last ':' or '=' to isolate the value
        for sep in (":", "="):
            if sep in line:
                val_str = line.rsplit(sep, 1)[-1].strip()
                try:
                    if val_str.lower().startswith("0x"):
                        return int(val_str, 16)
                    elif val_str.isdigit():
                        return int(val_str, 10)
                except ValueError:
                    continue
        return None
