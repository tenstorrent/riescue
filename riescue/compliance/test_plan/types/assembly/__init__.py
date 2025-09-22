# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Types that generate assembly code

Current Hierarchy:

AssemblyFile
├── Header
├── DataSegment
│   ├── DataBlock (data allocations)
│   └── GlobalFunction (global routines)
│       ├── FunctionBlock (subroutine)
│       └── InstructionBlock (extra_blocks)
└── TextSegment
    └── TestBlock (test cases)
        ├── InstructionBlock
        └── TextBlock

"""

from .header import Header
from .file import AssemblyFile
from .segment import DataSegment, TextSegment
from .block import Block, InstructionBlock, FunctionBlock, TextBlock
from .case import TestCase
from .page import Page, DataPage
from .global_function import GlobalFunction

__all__ = [
    "Header",
    "AssemblyFile",
    "DataSegment",
    "TextSegment",
    "Block",
    "InstructionBlock",
    "FunctionBlock",
    "TextBlock",
    "TestCase",
    "Page",
    "DataPage",
    "GlobalFunction",
]
