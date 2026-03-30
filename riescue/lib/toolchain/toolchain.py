# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import argparse
from typing import Optional

from riescue.lib.toolchain.tool import Compiler, Disassembler, Objcopy, Spike
from riescue.lib.toolchain.whisper import Whisper
from riescue.lib.toolchain.sail import Sail

log = logging.getLogger(__name__)


class Toolchain:
    """
    Manages the toolchain for the project. Allows for easier construction of ``Tool`` objects for library instantiation. Also provides CLI arguments for toolchain components.

    E.g. users overriding just the compiler without overriding any other tools:

    .. code-block:: python

        toolchain = Toolchain(compiler=Compiler(compiler_path="/path/to/compiler"))


    :param compiler: ``Compiler`` object
    :param disassembler: ``Disassembler`` object
    :param spike: Optional ``Spike`` object
    :param whisper: Optional ``Whisper`` object
    :param sail: Optional ``Sail`` object. Sail is used as a second-pass ISS
        (golden model verification), analogous to Spike. It is NOT used as a
        first-pass ISS since Sail's log format differs from Whisper's commit
        log format that riescue uses for operand value extraction.
    """

    def __init__(
        self,
        compiler: Optional[Compiler] = None,
        disassembler: Optional[Disassembler] = None,
        spike: Optional[Spike] = None,
        whisper: Optional[Whisper] = None,
        sail: Optional[Sail] = None,
    ):
        self.compiler = compiler if compiler is not None else Compiler()
        self.disassembler = disassembler if disassembler is not None else Disassembler()

        self.whisper = whisper
        self.spike = spike
        self.sail = sail

        # ISS priority: sail > spike > whisper > None
        # Sail and Spike are second-pass ISS (golden model verification).
        # Whisper is the first-pass ISS (operand value extraction).
        # When sail or spike is selected as the primary simulator, Whisper
        # is still used internally for the first pass in bringup mode.
        if sail is not None:
            self.simulator = sail
            self.tool_name = "sail"
        elif spike is not None:
            self.simulator = spike
            self.tool_name = "spike"
        elif whisper is not None:
            self.simulator = whisper
            self.tool_name = "whisper"
        else:
            self.simulator = None
            self.tool_name = "None"

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser):
        # toolchain args
        toolchain_parser = parser.add_argument_group("Compiler", description="Arguments that affect compiling behavior")
        toolchain_parser.add_argument(
            "--iss", type=str, default="whisper",
            choices=["whisper", "spike", "sail"],
            help="Instruction set simulator to use",
        )

        # tool args
        Compiler.add_arguments(parser)
        Disassembler.add_arguments(parser)
        Spike.add_arguments(parser)
        Whisper.add_arguments(parser)
        Sail.add_arguments(parser)

    @classmethod
    def from_clargs(cls, args: argparse.Namespace, build_both: bool = False) -> "Toolchain":
        """
        Create toolchain from command line arguments.

        When build_both=True, all available ISS tools are instantiated.
        This is used by the bringup two-pass flow where Whisper is needed
        for the first pass regardless of which ISS is selected for verification.
        """
        if build_both:
            try:
                whisper = Whisper.from_clargs(args)
            except FileNotFoundError as e:
                log.warning(f"Whisper not found: {e}")
                whisper = None
            try:
                spike = Spike.from_clargs(args)
            except FileNotFoundError as e:
                log.warning(f"Spike not found: {e}")
                spike = None
            # Build all available ISS tools. Sail is only included if
            # --sail_path or SAIL_PATH is provided, to avoid a FileNotFoundError
            # when Sail is not installed.
            sail = cls._try_build_sail(args)
            return cls(
                compiler=Compiler.from_clargs(args),
                disassembler=Disassembler.from_clargs(args),
                whisper=whisper,
                spike=spike,
                sail=sail,
            )
        elif args.iss == "sail":
            sail = cls._try_build_sail(args)
            if sail is None:
                raise ValueError(
                    "--iss sail was specified but no Sail executable was found. "
                    "Provide --sail_path or set the SAIL_PATH environment variable."
                )
            return cls(
                compiler=Compiler.from_clargs(args),
                disassembler=Disassembler.from_clargs(args),
                whisper=Whisper.from_clargs(args),  # still needed for first pass
                sail=sail,
            )
        elif args.iss == "whisper":
            return cls(
                compiler=Compiler.from_clargs(args),
                disassembler=Disassembler.from_clargs(args),
                whisper=Whisper.from_clargs(args),
            )
        elif args.iss == "spike":
            return cls(
                compiler=Compiler.from_clargs(args),
                disassembler=Disassembler.from_clargs(args),
                spike=Spike.from_clargs(args),
            )
        else:
            return cls(
                compiler=Compiler.from_clargs(args),
                disassembler=Disassembler.from_clargs(args),
            )

    @staticmethod
    def _try_build_sail(args: argparse.Namespace) -> Optional[Sail]:
        """
        Attempt to build a Sail instance from CLI args.
        Returns None (with a warning) if Sail executable is not found,
        so that build_both=True does not hard-fail when Sail is absent.
        """
        import os
        import shutil
        from pathlib import Path

        sail_path = getattr(args, "sail_path", None)
        sail_env = os.environ.get("SAIL_PATH")

        if sail_path is None and sail_env is None:
            if shutil.which("sail_riscv_sim") is None:
                log.warning(
                    "Sail executable not found (no --sail_path, no SAIL_PATH env var, "
                    "and 'sail_riscv_sim' not in PATH). Sail will not be available."
                )
                return None

        try:
            return Sail.from_clargs(args)
        except FileNotFoundError as e:
            log.warning(f"Could not build Sail ISS: {e}. Sail will not be available.")
            return None


def apply_iss_fallback(cl_args: argparse.Namespace, toolchain: Toolchain) -> None:
    """
    Apply ISS availability fallback logic to cl_args in-place.

    Rules:
    - No --first_pass_iss flag + only whisper available  → whisper single-pass
    - No --first_pass_iss flag + only spike available    → spike single-pass
    - No --first_pass_iss flag + neither available       → error
    - No --first_pass_iss flag + both available          → existing two-pass (no change)
    - --first_pass_iss whisper + whisper available       → whisper single-pass
    - --first_pass_iss spike  + spike available          → spike single-pass
    - --first_pass_iss <iss>  + <iss> not available      → error
    """
    whisper_available = toolchain.whisper is not None
    spike_available = toolchain.spike is not None
    iss_flag = cl_args.first_pass_iss  # None if not passed by user

    if iss_flag is None:
        if not whisper_available and not spike_available:
            raise SystemExit("ERROR: No ISS found. Set WHISPER_PATH or SPIKE_PATH, or add whisper/spike to your PATH.")
        elif whisper_available and not spike_available:
            log.info("Spike not found — using whisper single-pass mode")
            cl_args.first_pass_iss = "whisper"
            cl_args.disable_pass = True
        elif spike_available and not whisper_available:
            log.info("Whisper not found — using spike single-pass mode")
            cl_args.first_pass_iss = "spike"
            cl_args.disable_pass = True
        # else: both available → existing two-pass behaviour, no changes
    else:
        if iss_flag == "whisper" and not whisper_available:
            raise SystemExit("ERROR: --first_pass_iss whisper specified but whisper not found. Set WHISPER_PATH or add whisper to PATH.")
        if iss_flag == "spike" and not spike_available:
            raise SystemExit("ERROR: --first_pass_iss spike specified but spike not found. Set SPIKE_PATH or add spike to PATH.")
        # Only disable the second pass when no explicit --second_pass_iss was
        # provided. If the user specified both --first_pass_iss and
        # --second_pass_iss (e.g. whisper + sail), the two-pass flow should
        # run normally and the second pass must not be skipped.
        if getattr(cl_args, "second_pass_iss", None) is None:
            cl_args.disable_pass = True
