# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from typing import Any, Generator, Callable

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator
from riescue.dtest_framework.runtime.loader import Loader
from riescue.dtest_framework.runtime.opsys import OpSys
from riescue.dtest_framework.runtime.test_scheduler import TestScheduler
from riescue.dtest_framework.runtime.syscalls import SysCalls
from riescue.dtest_framework.runtime.trap_handler import TrapHandler
from riescue.dtest_framework.runtime.hypervisor import Hypervisor
from riescue.dtest_framework.runtime.macros import Macros
from riescue.dtest_framework.config import FeatMgr


Formatter = Callable[[str], str]


class Runtime:
    """
    Generates Test Runtime Environment code.
    Runtime code consists of Loader, TestScheduler, OpSys, Macros, and Equates.

    Most code is generated for .text sections, with some exceptions for user jump tables.

    Yields name of module and a generator for each module's code. Name can be used by consumers as file name, or comment for inline code.

    :param rng: Random number generator
    :param pool: Test pool
    :param featmgr: Feature manager
    """

    def __init__(self, rng: RandNum, pool: Pool, featmgr: FeatMgr):
        self.rng = rng
        self.pool = pool
        self.featmgr = featmgr

        self._modules: dict[str, AssemblyGenerator] = dict()

        # Save registered Runtime modules here
        self._modules: dict[str, AssemblyGenerator] = dict()  # name -> module_instance
        self._modules["macros"] = Macros(mp_enablement=self.featmgr.mp, rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        self._modules["loader"] = Loader(rng=self.rng, pool=self.pool, featmgr=self.featmgr)

        # WYSIWYG mode only needs macros, loader, and equates
        if self.featmgr.wysiwyg:
            return

        # only need trap handling if not in linux mode.
        # linux mode only needs macros, loader, os, and scheduler
        if not self.featmgr.linux_mode:
            self._modules["syscalls"] = SysCalls(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
            generate_trap_handler = True
            if not self.featmgr.linux_mode:
                self._modules["trap_handler"] = TrapHandler(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
                generate_trap_handler = False  # Trap handler inserted by TrapHandler
            if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and not self.featmgr.wysiwyg:
                self._modules["hypervisor"] = Hypervisor(generate_trap_handler=generate_trap_handler, rng=self.rng, pool=self.pool, featmgr=self.featmgr)

        self._modules["os"] = OpSys(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        self._modules["scheduler"] = TestScheduler(rng=self.rng, pool=self.pool, featmgr=self.featmgr)

    def generate(self) -> Generator[tuple[str, Generator[str, Any, None]], Any, None]:
        """
        Generate code for each of the modules. Yields name of module and a generator for each module's code.
        """

        for module_name, module in self._modules.items():
            yield module_name, self._format_code(s for s in module.generate().split("\n"))

    def generate_equates(self) -> str:
        retstr = ""
        for mod in self._modules.values():
            retstr += mod.generate_equates()
        return retstr

    def _format_code(self, code_generator: Generator[str, Any, None]) -> Generator[str, Any, None]:
        """
        Basic formatting for emitted code. Tries to reduce tabs, empty new lines, etc
        """

        prev_lines_blank = 0
        for line in code_generator:
            parsed_line = line.strip()

            if parsed_line == "":
                prev_lines_blank += 1
                # yield ""
                if prev_lines_blank <= 2:
                    yield ""
            elif any([parsed_line.startswith(x) for x in [".align", ".section", ".macro", ".endm", "#", ";"]]) or parsed_line.endswith(":"):
                prev_lines_blank = 0
                yield f"{parsed_line}"
            else:
                prev_lines_blank = 0
                yield f"\t{parsed_line}"
