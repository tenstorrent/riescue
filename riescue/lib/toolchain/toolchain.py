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

        # RiescueD only expects a single ISS run
        # Legacy wrappers rely on lazy ISS instantiation. Whisper/Spike fail without valid paths.
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

        Compiler.add_args(parser)
        Disassembler.add_args(parser)
        Spike.add_args(parser)
        Whisper.add_args(parser)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Toolchain":
        """
        Create toolchain from command line arguments.
        """
        if not args.run_iss:
            return cls(
                compiler=Compiler.from_args(args),
                disassembler=Disassembler.from_args(args),
            )
        if args.iss == "whisper":
            return cls(
                compiler=Compiler.from_args(args),
                disassembler=Disassembler.from_args(args),
                whisper=Whisper.from_args(args),
            )
        elif args.iss == "spike":
            return cls(
                compiler=Compiler.from_args(args),
                disassembler=Disassembler.from_args(args),
                spike=Spike.from_args(args),
            )
        else:
            raise ValueError(f"Invalid instruction set simulator but --run_iss is set: {args.iss}")
