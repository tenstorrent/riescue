# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field
from typing import Optional

from coretp.isa import Instruction, Register


logger = logging.getLogger(__name__)


@dataclass
class BasicBlock:
    """
    list of instructions, with no jumps or branches inside. Used to logically group instructions
    into chunks that can be allocated independently.

    """

    instructions: list[Instruction]
    label: Optional[str] = None
    clobbered_regs: list[Register] = field(default_factory=list)
    live_in: set[str] = field(default_factory=set)
    live_out: set[str] = field(default_factory=set)


@dataclass
class Cfg:
    """
    Control flow graph of a function.
    """

    blocks: list[BasicBlock]
