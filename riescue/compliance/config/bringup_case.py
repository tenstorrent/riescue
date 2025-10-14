# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field
from pathlib import Path
import json

import riescue.lib.enums as RV

log = logging.getLogger(__name__)


@dataclass
class BringupTest:
    """
    Test configuration for ``BringupMode``. Used to validate user-input JSON files for a single test.

    Required fields:
    :param arch: Architecture
    :param include_extensions: List of extensions to include
    :param include_groups: List of groups to include
    :param include_instrs: List of instructions to include
    :param exclude_groups: List of groups to exclude
    :param exclude_instrs: List of instructions to exclude


    Optional fields:
    :param iss: ISS to use, ``spike`` or ``whisper``
    :param first_pass_iss: First pass ISS to use, ``spike`` or ``whisper``
    :param second_pass_iss: Second pass ISS to use, ``spike`` or ``whisper``
    """

    arch: RV.RiscvBaseArch = field(default=RV.RiscvBaseArch.ARCH_RV64I)
    include_extensions: list[str] = field(default_factory=list)
    include_groups: list[str] = field(default_factory=list)
    include_instrs: list[str] = field(default_factory=list)
    exclude_groups: list[str] = field(default_factory=list)
    exclude_instrs: list[str] = field(default_factory=list)
    iss: str = ""
    first_pass_iss: str = ""
    second_pass_iss: str = ""

    @classmethod
    def from_dict(cls, test: dict) -> "BringupTest":
        """
        Construct from a dictionary. Validates user dictionary for required fields
        """
        # validate dict
        valid_arch = [x.value for x in RV.RiscvBaseArch]
        for required_field in ["arch", "include_extensions", "include_groups", "include_instrs", "exclude_groups", "exclude_instrs"]:
            if required_field not in test:
                raise ValueError(f"{required_field} is a required field in bringup test")
            if required_field == "arch" and test[required_field] not in valid_arch:
                raise ValueError(f"{required_field} must be one of {valid_arch}")

        return cls(
            arch=RV.RiscvBaseArch(test["arch"]),
            include_extensions=test["include_extensions"],
            include_groups=test["include_groups"],
            include_instrs=test["include_instrs"],
            exclude_groups=test["exclude_groups"],
            exclude_instrs=test["exclude_instrs"],
            iss=test.get("iss", ""),
            first_pass_iss=test.get("first_pass_iss", ""),
            second_pass_iss=test.get("second_pass_iss", ""),
        )

    @classmethod
    def from_json(cls, json_file: Path) -> "BringupTest":
        with open(json_file, "r") as f:
            test = json.load(f)
        return cls.from_dict(test)
