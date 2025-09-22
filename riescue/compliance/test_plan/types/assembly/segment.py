# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Union
from enum import Enum

from .base import AssemblyBase
from .block import Block
from .page import DataPage
from .case import TestCase
from .global_function import GlobalFunction


class Segment(AssemblyBase):
    """
    Container for a block of assembly code. Extended classes define ``emit()``.

    :param name: Name of segment
    :param blocks: List of :class:`Block` objects
    """

    section_header: str = ""

    def __init_subclass__(cls, **kwargs) -> None:
        """Affects class creation, ensures section_header is defined and is a string"""
        super().__init_subclass__(**kwargs)
        if "section_header" not in cls.__dict__:
            raise ValueError(f"Segment {cls.__name__} must define a section_header attribute")
        if not isinstance(cls.section_header, str):
            raise TypeError(f"{cls.__name__} must define a 'section_header' attribute of type str (got {type(cls.section_header)})")

    def __init__(self, blocks: Optional[list[Block]] = None):
        self.blocks = blocks or []

    def emit(self) -> str:
        return self.section_header + "\n" + "\n".join(block.emit() for block in self.blocks)


class DataSegment(Segment):
    """
    Container for Data Segment where test data, remote code pages / global functions, global data lives
    """

    section_header: str = ".section .data"
    blocks: list[Union[DataPage, GlobalFunction]]


class TextSegment(Segment):
    """
    Container for Text Segment where test code lives
    """

    section_header: str = '.section .code, "ax"'
    blocks: list[TestCase]
