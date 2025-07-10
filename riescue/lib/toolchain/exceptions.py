# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from enum import Enum, auto
from typing import Optional
from dataclasses import dataclass
from pathlib import Path


class ToolFailureType(Enum):
    OK = auto()
    ELF_FAILURE = auto()
    COMPILE_FAILURE = auto()
    BAD_CONFIG = auto()
    TOHOST_FAIL = auto()
    MAX_INSTRUCTION_LIMIT = auto()
    ILLEGAL_INSTRUCTIONS = auto()
    SEGFAULT = auto()
    NONZERO_EXIT = auto()  # Worst case, don't know what happened


@dataclass
class ToolchainError(Exception):
    """
    ToolchainError raised when a tool fails to run.
    Consumed by unit tests to check for expected failures.

    :param tool_name: name of the tool that failed to run
    :type tool_name: str
    :param cmd: command that failed to run
    :type cmd: list[str]
    :param kind: type of failure
    :type kind: ToolFailureType
    :param returncode: return code of the tool
    :type returncode: int
    :param fail_code: numeric 'tohost' value, otherwise 0
    :type fail_code: int
    :param log_path: path to the log file
    :type log_path: Path
    """

    tool_name: str
    cmd: list[str]
    kind: ToolFailureType
    returncode: int
    fail_code: int = 0
    log_path: Optional[Path] = None
    error_text: Optional[str] = None

    def __str__(self):
        "Set error message based on failure type"
        err = f"{self.tool_name} Failed: "
        err += f"\n\tran: {' '.join(str(c) for c in self.cmd)}\n"
        if self.kind == ToolFailureType.OK:
            err += "OK"
        elif self.kind == ToolFailureType.COMPILE_FAILURE:
            err += "Compile failure"
            err += f"\n{self.error_text}"
        elif self.kind == ToolFailureType.ELF_FAILURE:
            err += f"ELF failure: {self.error_text}"
        elif self.kind == ToolFailureType.BAD_CONFIG:
            err += "Bad configuration"
            err += f"\n{self.error_text}"
        elif self.kind == ToolFailureType.TOHOST_FAIL:
            err += f"write to tohost failure: {self.fail_code}"
            err += f"\n{self.error_text}"
        elif self.kind == ToolFailureType.NONZERO_EXIT:
            err += "Nonzero exit"
            err += f"\n{self.error_text}"
        elif self.kind == ToolFailureType.MAX_INSTRUCTION_LIMIT:
            err += "Max instruction limit"
            err += f"\n{self.error_text}"
        elif self.kind == ToolFailureType.ILLEGAL_INSTRUCTIONS:
            err += "Ran illegal instructions"
            err += f"\n{self.error_text}"
        elif self.kind == ToolFailureType.SEGFAULT:
            err += f"Segmentation fault - exit {self.returncode}"
            err += f"\n{self.error_text}"
        else:
            err += "Unknown failure"
        return err
