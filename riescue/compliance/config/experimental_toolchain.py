# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import argparse
import os
from pathlib import Path
from riescue.lib.toolchain import Toolchain, Whisper, Spike, Compiler, Disassembler


def experimental_toolchain_from_args(args: argparse.Namespace) -> Toolchain:
    """
    Create experimental toolchain from command line arguments.
    No state, just logic to use a different toolchain (clang) if using some vector / fp extensions.

    This might be better as a toolchain extension or in a riescuec-specific toolchain
    """
    whisper = Whisper.from_clargs(args)
    spike = Spike.from_clargs(args)

    experimental_enabled = any(
        [
            args.rv_zvknhb_experimental,
            args.rv_zvkg_experimental,
            args.rv_zvbc_experimental,
            args.rv_zvfbfwma_experimental,
            args.rv_zvfbfmin_experimental,
            args.rv_zfbfmin_experimental,
            args.rv_zvbb_experimental,
        ]
    )

    # FIXME: this should probably be part of the toolchain package, as experimental_tool.py
    if experimental_enabled:
        # legacy experimental features support
        compiler_march = ["rv64imafdcv_zfh_zba_zbb_zbc_zbs"]
        disassembler_opts = []
        if args.rv_zfbfmin_experimental:
            compiler_march.append("_zfbfmin0p6")
            disassembler_opts.append("zfbfmin")
        if args.rv_zvfbfmin_experimental:
            compiler_march.append("_zvfbfmin0p6")
            disassembler_opts.append("zvfbfmin")
        if args.rv_zvbb_experimental:
            compiler_march.append("_zvbb1")
            disassembler_opts.append("zvbb")
        if args.rv_zvfbfwma_experimental:
            compiler_march.append("_zvfbfwma0p6")
            disassembler_opts.append("zvfbfwma")
        if args.rv_zvbc_experimental:
            compiler_march.append("_zvbc1")
            disassembler_opts.append("zvbc")
        if args.rv_zvkg_experimental:
            compiler_march.append("_zvkg1")
            disassembler_opts.append("zvkg")
        if args.rv_zvknhb_experimental:
            compiler_march.append("_zvknhb1")
            disassembler_opts.append("zvknhb")
        compiler_march.extend(["_zifencei_zicsr"])

        compiler_path = args.compiler_path or args.experimental_compiler or os.getenv("EXPERIMENTAL_COMPILER")
        if not compiler_path:
            raise ValueError("Experimental compiler path is not set. Explicitly set --compiler_path or --experimental_compiler, or define EXPERIMENTAL_COMPILER environment variable.")
        compiler_opts = args.compiler_opts + ["-menable-experimental-extensions"]
        compiler = Compiler(
            compiler_path=Path(compiler_path),
            compiler_opts=compiler_opts,
            compiler_march="".join(compiler_march),
            test_equates=args.test_equates,
        )

        diassembler_path = args.disassembler_path or args.experimental_objdump or os.getenv("EXPERIMENTAL_OBJDUMP")
        if not diassembler_path:
            raise ValueError("Experimental objdump path is not set. Explicitly set --disassembler_path or --experimental_objdump, or define EXPERIMENTAL_OBJDUMP environment variable.")
        disassembler = Disassembler(
            disassembler_path=Path(diassembler_path),
            disassembler_opts=disassembler_opts,
        )
    else:
        compiler = Compiler.from_clargs(args)
        disassembler = Disassembler.from_clargs(args)
    return Toolchain(compiler=compiler, disassembler=disassembler, spike=spike, whisper=whisper)
