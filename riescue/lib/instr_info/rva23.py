# SPDX-FileCopyrightText: (c) 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""
Class used to gather information about the RVA32 Profile.
Using JSON since it's easier to parse in python. Currently just a library to include all the extensions/expansions required by RVA23
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

Used to gather all extensions included by supervisor/user/mandatory/optional.

"""

import os
import sys
import json
from pathlib import Path


class RVA23_Extensions:
    def __init__(
        self,
        supervisor=True,
        user=True,
        mandatory=True,
        optional=True,
        rva32_json=Path(__file__).parent / "rva23.json",
    ):
        if not user and not supervisor:
            raise ValueError("Either user or supervisor must be True.")
        if user and not supervisor:
            raise ValueError("Must include user if supervisor is included.")
        self.supervisor = supervisor
        self.user = user
        self.mandatory = mandatory
        self.optional = optional
        self.rva32_json = rva32_json

        self.extensions = self._gather_extensions()

    def __iter__(self):
        for e in self.extensions:
            yield e

    def _gather_extensions(self) -> list:
        with open(self.rva32_json, "r") as f:
            rva = json.load(f)

        modes = []
        if self.supervisor:
            modes.append(rva["RVA23S64"])
        if self.user:
            modes.append(rva["RVA23U64"])

        included = []
        for m in modes:
            if self.optional:
                included.append(m["optional"])
            if self.mandatory:
                included.append(m["mandatory"])

        return [ext["ext"] for i in included for ext in i]
