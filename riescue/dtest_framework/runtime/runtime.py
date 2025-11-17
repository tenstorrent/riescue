# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from typing import Any, Generator, Callable

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator, RuntimeContext
from riescue.dtest_framework.runtime.loader import Loader
from riescue.dtest_framework.runtime.opsys import OpSys
from riescue.dtest_framework.runtime.test_scheduler import TestScheduler
from riescue.dtest_framework.runtime.syscalls import SysCalls
from riescue.dtest_framework.runtime.trap_handler import TrapHandler
from riescue.dtest_framework.runtime.hypervisor import Hypervisor
from riescue.dtest_framework.runtime.macros import Macros
from riescue.dtest_framework.runtime.eot import Eot
from riescue.dtest_framework.runtime.variable import VariableManager
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

        self.variable_manager = VariableManager(
            data_section_name="hart_context",
            xlen=RV.Xlen.XLEN64,
            hart_count=self.featmgr.num_cpus,
            amo_enabled=self.featmgr.feature.is_enabled("a"),
        )

        self.variable_manager.register_hart_variable("check_excp", 0x1, size=1)
        self.variable_manager.register_hart_variable("check_excp_expected_pc", -1)
        self.variable_manager.register_hart_variable("check_excp_actual_pc", -1)
        self.variable_manager.register_hart_variable("check_excp_return_pc", -1)
        self.variable_manager.register_hart_variable("check_excp_skip_pc_check", 0)
        self.variable_manager.register_hart_variable("check_excp_expected_tval", -1)
        self.variable_manager.register_hart_variable("check_excp_expected_cause", 0xFF)
        self.variable_manager.register_hart_variable("check_excp_actual_cause", 0xFF)

        self.variable_manager.register_shared_variable("excp_ignored_count", 0x0)
        self.variable_manager.register_shared_variable("machine_flags", 0x0)
        self.variable_manager.register_shared_variable("user_flags", 0x0)
        self.variable_manager.register_shared_variable("super_flags", 0x0)
        self.variable_manager.register_shared_variable("machine_area", 0x0)
        self.variable_manager.register_shared_variable("user_area", 0x0)
        self.variable_manager.register_shared_variable("super_area", 0x0)

        self.test_priv = self.featmgr.priv_mode  #: Privilege mode of the test
        self.handler_priv: RV.RiscvPrivileges  #: Privilege mode of scheduler.
        if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.SUPER:
            self.handler_priv = RV.RiscvPrivileges.SUPER
        else:
            self.handler_priv = RV.RiscvPrivileges.MACHINE

        mp_active = self.featmgr.mp == RV.RiscvMPEnablement.MP_ON
        mp_parallel = self.featmgr.mp_mode == RV.RiscvMPMode.MP_PARALLEL and mp_active
        mp_simultaneous = self.featmgr.mp_mode == RV.RiscvMPMode.MP_SIMULTANEOUS and mp_active

        # ctx is a helper data class to reduce repeated arguments
        ctx = RuntimeContext(
            rng=self.rng,
            pool=self.pool,
            featmgr=self.featmgr,
            variable_manager=self.variable_manager,
            test_priv=self.test_priv,
            handler_priv=self.handler_priv,
            mp_active=mp_active,
            mp_parallel=mp_parallel,
            mp_simultaneous=mp_simultaneous,
        )

        # Save registered Runtime modules here
        self._modules: dict[str, AssemblyGenerator] = dict()  # name -> module_instance

        self._modules["macros"] = Macros(ctx=ctx)
        self._modules["loader"] = Loader(ctx=ctx)

        # WYSIWYG mode only needs macros, loader, and equates
        if self.featmgr.wysiwyg:
            return

        # only need trap handling if not in linux mode.
        # linux mode only needs macros, loader, os, and scheduler
        if not self.featmgr.linux_mode:
            self._modules["syscalls"] = SysCalls(ctx=ctx)
            generate_trap_handler = True
            if not self.featmgr.linux_mode:
                self._modules["trap_handler"] = TrapHandler(ctx=ctx)
                generate_trap_handler = False  # Trap handler inserted by TrapHandler
            if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED and not self.featmgr.wysiwyg:
                self._modules["hypervisor"] = Hypervisor(generate_trap_handler=generate_trap_handler, ctx=ctx)

        self._modules["os"] = OpSys(ctx=ctx)
        self._modules["eot"] = Eot(ctx=ctx)
        self._modules["scheduler"] = TestScheduler(ctx=ctx)

    def test_passed(self) -> list[str]:
        """
        Generate code for test passed.
        Used by AssemblyWriter to replace ;#test_passed() with the correct code.
        """
        if self.test_priv != self.handler_priv:
            return [
                "li x31, 0xf0000001  # Test Passed; Schedule test",
                "ecall",
            ]
        return [
            "li t0, os_passed_addr",
            "ld t1, 0(t0)",
            "jr t1",
        ]

    def test_failed(self) -> list[str]:
        """
        Generate code for test failed.
        Used by AssemblyWriter to replace ;#test_failed() with the correct code.
        """
        if self.test_priv != self.handler_priv:
            return [
                "li x31, 0xf0000002  # Test Failed; End test with fail",
                "ecall",
            ]
        return [
            "li t0, os_failed_addr",
            "ld t1, 0(t0)",
            "jr t1",
        ]

    def generate(self) -> Generator[tuple[str, Generator[str, Any, None]], Any, None]:
        """
        Generate code for each of the modules. Yields name of module and a generator for each module's code.
        """

        for module_name, module in self._modules.items():
            yield module_name, self._format_code(s for s in module.generate().split("\n"))

    def generate_equates(self) -> str:
        retstr = ""
        for mod in self._modules.values():
            equates = mod.generate_equates()
            if equates:
                retstr += f"\n# {mod.__class__.__name__} equates\n"
                retstr += equates + "\n"
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
