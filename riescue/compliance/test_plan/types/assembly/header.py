# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import getpass
from dataclasses import dataclass

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
    category: str
    hypervisor: str
    features: str = ""
    tags: str = ""

    def __post_init__(self):
        self.author = getpass.getuser()

    @classmethod
    def from_env(cls, env: TestEnv, plan_name: str):
        """
        Create a Header object from a TestEnv object.
        """

        return cls(
            plan_name=plan_name,
            arch=f"rv{env.reg_width}",
            hypervisor="virtualized" if env.hypervisor else "bare_metal",
            priv=env.priv.long_name(),
            cpus=1,
            paging_mode=str(env.paging_mode),
            category="arch compliance",
        )

    def emit(self) -> str:
        return "\n".join(
            [
                f";#test.name       {self.plan_name}",
                f";#test.author     {self.author}",
                f";#test.arch       {self.arch}",
                f";#test.priv       {self.priv}",
                f";#test.env        {self.hypervisor}",
                f";#test.cpus       {self.cpus}",
                f";#test.paging     {self.paging_mode}",
                f";#test.category   {self.category}",
                f";#test.class      {self.plan_name}",
                f";#test.features   {self.features}",
                f";#test.tags       {self.tags}",
                f";#test.summary    Generated test case from TestPlan: {self.plan_name}",
            ]
        )
