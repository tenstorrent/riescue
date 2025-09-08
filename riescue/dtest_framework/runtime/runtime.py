# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from typing import Any, Dict, Generator

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


class Runtime:
    """
    Class used to generate runtime code for a test based on environment and features. Runtime code consists of Loader, TestScheduler, OpSys, Macros, and Equates.
    Generates and optionally writes code to .inc files.

    :param rng: Random number generator
    :param pool: Test pool
    :param run_dir: Path to the directory where the generated code will be written
    :param featmgr: Feature manager
    """

    def __init__(self, rng: RandNum, pool: Pool, run_dir: Path, featmgr: FeatMgr):
        self.rng = rng
        self.pool = pool
        self.run_dir = run_dir
        self.featmgr = featmgr

        # Save registered Runtime modules here
        self.modules: Dict[str, AssemblyGenerator] = dict()  # name -> module_instant
        self.modules["loader"] = Loader(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        self.modules["os"] = OpSys(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        self.modules["scheduler"] = TestScheduler(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        self.modules["syscalls"] = SysCalls(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        generate_trap_handler = True
        if not self.featmgr.linux_mode:
            self.modules["trap_handler"] = TrapHandler(rng=self.rng, pool=self.pool, featmgr=self.featmgr)
            generate_trap_handler = False  # Trap handler inserted by TrapHandler
        if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED:
            self.modules["hypervisor"] = Hypervisor(generate_trap_handler=generate_trap_handler, rng=self.rng, pool=self.pool, featmgr=self.featmgr)
        self.modules["macros"] = Macros(mp_enablement=self.featmgr.mp, rng=self.rng, pool=self.pool, featmgr=self.featmgr)

    def generate(self, testname: str):
        """
        Generate code for each of the System modules and write to .inc files using format `{testname}_{module_name}.inc`

        :param testname: Name of the test file without the .s extension
        """
        # Go through each Runtime module and generate code
        for module_name in self.modules.keys():
            module = self.modules[module_name]
            include_file = self.run_dir / f"{testname}_{module_name}.inc"
            with open(include_file, "w") as f:
                for x in formatted_line_generator(module.generate().split("\n")):
                    f.write(x)

    def module_generator(self) -> Generator[tuple[str, str], Any, None]:
        """
        Generator function that yields tuple of (module_name, code) for each module name
        """
        for module_name in self.modules.keys():
            module = self.modules[module_name]
            yield module_name, "".join(formatted_line_generator(module.generate().split("\n")))

    def generate_equates(self) -> str:
        retstr = ""
        for mod in self.modules.values():
            retstr += mod.generate_equates()
        return retstr


# Strips white space from lines, formats to single indent
# global function so generator can use as well
def formatted_line_generator(lines: list[str]) -> Generator[str, Any, None]:
    """
    Generator function that yields formatted lines from a list of lines, stripping whitespace and formatting to single indent

    :param lines: List of lines to format
    """
    for line in lines:
        parsed_line = line.strip()

        if parsed_line == "":
            yield "\n"
        elif "\n" in parsed_line:
            yield from formatted_line_generator(parsed_line.split("\n"))
        elif any([parsed_line.startswith(x) for x in [".align", ".section", ".macro", ".endm", "#", ";"]]) or parsed_line.endswith(":"):
            yield f"{parsed_line}\n"
        else:
            yield f"\t{parsed_line}\n"
