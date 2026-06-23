# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import getpass
from dataclasses import dataclass, field
from typing import Optional

from coretp import TestEnv

from .base import AssemblyBase


@dataclass
class Header(AssemblyBase):
    """
    Header for assembly file. Should be the first element in the assembly file.

    Usage:

    .. code-block:: python
        header = Header.from_env(env, "test_plan")
    """

    plan_name: str
    arch: str
    priv: str
    cpus: int
    paging_mode: str
    g_paging_mode: str
    category: str
    virtualized: str
    features: str = ""
    tags: str = ""
    # Optional extra ``;#…`` directives appended verbatim after the ``;#test.*``
    # header lines, at column 0 so the dtest_framework parser picks them up.
    # Used e.g. for top-of-test IMSIC ``;#page_mapping`` injection when the
    # ``--map_imsic_pages`` flag is set, or ACLINT ``;#page_mapping`` injection
    # when the ``--map_aclint_pages`` flag is set.
    extra_directives: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.author = getpass.getuser()

    @classmethod
    def from_env(cls, env: TestEnv, plan_name: str, extra_directives: Optional[list] = None):
        """
        Create a Header object from a TestEnv object.

        :param extra_directives: Optional list of additional ``;#…`` directive
            strings to append after the standard ``;#test.*`` header lines.
        """

        return cls(
            plan_name=plan_name,
            arch=f"rv{env.reg_width}",
            virtualized="virtualized" if env.virtualized else "bare_metal",
            priv=env.priv.long_name(),
            cpus=1,
            paging_mode=str(env.paging_mode),
            g_paging_mode=str(env.g_paging_mode),
            category="arch compliance",
            extra_directives=list(extra_directives) if extra_directives else [],
        )

    def emit(self) -> str:
        lines = [
            f";#test.name       {self.plan_name}",
            f";#test.author     {self.author}",
            f";#test.arch       {self.arch}",
            f";#test.priv       {self.priv}",
            f";#test.env        {self.virtualized}",
            f";#test.cpus       {self.cpus}",
            f";#test.paging     {self.paging_mode}",
            f";#test.paging_g   {self.g_paging_mode}",
            f";#test.category   {self.category}",
            f";#test.class      {self.plan_name}",
            f";#test.features   {self.features}",
            f";#test.tags       {self.tags}",
            f";#test.summary    Generated test case from TestPlan: {self.plan_name}",
        ]
        lines.extend(self.extra_directives)
        return "\n".join(lines)
