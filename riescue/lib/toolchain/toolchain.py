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
            return cls(
                compiler=Compiler.from_clargs(args),
                disassembler=Disassembler.from_clargs(args),
                whisper=Whisper.from_clargs(args),
                spike=Spike.from_clargs(args),
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
