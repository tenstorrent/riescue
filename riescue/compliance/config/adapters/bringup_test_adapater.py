# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from .base import BaseAdapter

from .. import BringupTest

if TYPE_CHECKING:
    from .. import ResourceBuilder


class BringupTestAdapter(BaseAdapter):
    """
    Adapter for :class:`BringupTest`.
    """

    def apply(self, builder: ResourceBuilder, src: Path) -> ResourceBuilder:
        bringup_test = BringupTest.from_json(self.find_config(src))
        resource = builder.resource
        resource.arch = bringup_test.arch
        # validate extensions
        for extension in bringup_test.include_extensions:
            if resource.check_extension(extension):
                resource.include_extensions.append(extension)
            else:
                raise ValueError(f"{extension} is not supported")
        resource.include_groups += bringup_test.include_groups
        resource.include_instrs += bringup_test.include_instrs
        resource.exclude_groups += bringup_test.exclude_groups
        resource.exclude_instrs += bringup_test.exclude_instrs

        # legacy behavior was to only use BringupTest.iss if --first_pass_iss=""
        # this isn't documented anywhere, so going to use iss here if passed in explicitly
        if bringup_test.iss:
            resource.first_pass_iss = bringup_test.iss
            resource.second_pass_iss = bringup_test.iss
        if bringup_test.first_pass_iss:
            resource.first_pass_iss = bringup_test.first_pass_iss
        if bringup_test.second_pass_iss:
            resource.second_pass_iss = bringup_test.second_pass_iss
        return builder
