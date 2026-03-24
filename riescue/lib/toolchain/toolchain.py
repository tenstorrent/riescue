# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
import argparse
from typing import Optional

from riescue.lib.toolchain.tool import Compiler, Disassembler, Objcopy, Spike
from riescue.lib.toolchain.whisper import Whisper

log = logging.getLogger(__name__)


class Toolchain:
    """
    Manages the toolchain for the project. Allows for easier construction of ``Tool`` objects for library instantiation. Also provides CLI arguments for toolchain components.

    E.g. users overridding just the compiler without overriding any other tools:

    .. code-block:: python

        toolchain = Toolchain(compiler=Compiler(compiler_path="/path/to/compiler"))


    :param compiler: ``Compiler`` object
    :param disassembler: ``Disassembler`` object
    :param simulator: Optional ``Spike`` or ``Whisper`` object. If none provided, defaults to ``Whisper``
    """

    def __init__(
        self,
        compiler: Optional[Compiler] = None,
        disassembler: Optional[Disassembler] = None,
        spike: Optional[Spike] = None,
        whisper: Optional[Whisper] = None,
    ):
        self.compiler = compiler if compiler is not None else Compiler()
        self.disassembler = disassembler if disassembler is not None else Disassembler()

        self.whisper = whisper
        self.spike = spike

        if spike is not None:
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
        toolchain_parser.add_argument("--iss", type=str, default="whisper", choices=["whisper", "spike"], help="Instruction set simulator to use")
        # tool args

        Compiler.add_arguments(parser)
        Disassembler.add_arguments(parser)
        Spike.add_arguments(parser)
        Whisper.add_arguments(parser)

    @classmethod
    def from_clargs(cls, args: argparse.Namespace, build_both: bool = False) -> "Toolchain":
        """
        Create toolchain from command line arguments.
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
            return cls(
                compiler=Compiler.from_clargs(args),
                disassembler=Disassembler.from_clargs(args),
                whisper=whisper,
                spike=spike,
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
        cl_args.disable_pass = True
