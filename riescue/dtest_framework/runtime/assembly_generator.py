# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from abc import ABC, abstractmethod
from typing import Optional, NamedTuple, Union

import riescue.lib.enums as RV
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.variable import VariableManager
from riescue.lib.rand import RandNum
from riescue.lib.csr_manager.csr_manager_interface import CsrManagerInterface


class RuntimeContext(NamedTuple):
    """
    Shared runtime dependencies.
    No real functionality here, just a container to pass common arguments to all AssemblyGenerator subclasses.
    ``NamedTuple`` is used to make the arguments immutable and easier to pass around.
    """

    rng: RandNum
    pool: Pool
    featmgr: FeatMgr
    variable_manager: VariableManager
    test_priv: RV.RiscvPrivileges
    mp_active: bool
    mp_parallel: bool
    mp_simultaneous: bool


class AssemblyGenerator(ABC):
    """
    Base class for assembly code generation classes. Extended classes must implement the `generate` method.

    :param rng: Random number generator
    :param pool: Test pool
    :param featmgr: Feature manager

    E.g. to extend this class, should implement the following:

    .. code-block:: python

        from riescue.dtest_framework.runtime.system import System

        class MyClass(System):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)

            def generate(self) -> str:
                return "nop"

    """

    IGNORED_EXCP_MAX_COUNT = 5000

    def __init__(self, ctx: RuntimeContext):
        self.rng = ctx.rng
        self.pool = ctx.pool
        self.featmgr = ctx.featmgr
        self.variable_manager = ctx.variable_manager
        self.test_priv = ctx.test_priv
        self.mp_active = ctx.mp_active
        self.mp_parallel = ctx.mp_parallel
        self.mp_simultaneous = ctx.mp_simultaneous

        self.hartid_reg = "s1"
        self.csr_manager: Optional[CsrManagerInterface] = None
        self.xlen: RV.Xlen = RV.Xlen.XLEN64

        self.scratch_reg: str  #: Trap handler scratch register - used to store hart-local storage pointer (tp)
        # Assume machine mode
        self.scratch_reg = "mscratch"
        self.tvec = "mtvec"

        self._equates: dict[str, str] = {}  # Dictionary of key value pairs to be generated with .equ key, value

        self.excp_ignored_count = self.variable_manager.get_variable("excp_ignored_count")

        # common routine labels
        self.scheduler_init_label = "scheduler__init"
        self.scheduler_dispatch_label = "scheduler__dispatch"
        self.scheduler_finished_label = "scheduler__finished"
        self.scheduler_panic_label = "scheduler__panic"

    @abstractmethod
    def generate(self) -> str:
        """
        Generate assembly code

        :return: Assembly code string
        """
        pass

    def generate_equates(self) -> str:
        """
        Generate assembly equates definitions.

        :return: Assembly equates definitions strings
        """
        return "\n".join([f".equ {key}, {value}" for key, value in self._equates.items()])

    def register_equate(self, key: str, value: str):
        """
        Register an equate. API if equates get more complex later, e.g.
            - Equates are generated in the order they are registered and silently overwritten
            - Need to lock equates to not be re-written
        """
        self._equates[key] = value

    # common utilities
    def switch_test_privilege(
        self,
        from_priv: RV.RiscvPrivileges,
        to_priv: RV.RiscvPrivileges,
        jump_label: Optional[str] = None,
        pre_xret: str = "",
        jump_register: Optional[str] = None,
        switch_to_vs: bool = False,
    ) -> str:
        """
        Switch test to given to_priv from a given from_priv and then jump to jump_label using
        xret instruction

        Setting jump_register instead of jump_label will allow the caller to jump to an address loaded into a register
        instead of a label. This is useful for when the caller wants to jump to a test.

        :param from_priv: Privilege level to switch from
        :param to_priv: Privilege level to switch to
        :param jump_label: Label to jump to after switching privilege
        :param jump_register: Register to jump to after switching privilege if jump_label is not provided
        :param pre_xret: Optional assembly instruction to execute before xret, e.g. barrier code
        :param switch_to_vs: Whether to switch to VS-mode
        """

        code = ""
        xepc_csr = ""
        xstatus_csr = ""
        xret_instr = ""
        post_xpp_code = ""

        code = ""
        if pre_xret:
            code += pre_xret + "\n"
        code = f"# Switch from {from_priv.name.lower()} to {to_priv.name.lower()} mode\n"

        # | xPP[12:11] | Privilege  |
        # |     00     |    User    |
        # |     01     | Supervisor |
        # |     10     |  Reserved  |
        # |     11     |   Machine  |

        # switch from machine mode
        if from_priv == RV.RiscvPrivileges.MACHINE:
            # Set MPP and run an mret
            xepc_csr = "mepc"
            xstatus_csr = "mstatus"
            xret_instr = "mret"

            # set xPP correctly
            if to_priv == RV.RiscvPrivileges.SUPER:
                # Set MPP to 01
                xpp_clear_mask = 0b11 << 11
                xpp_set_mask = 0b01 << 11

            elif to_priv == RV.RiscvPrivileges.USER:
                # Set MPP to 00
                xpp_clear_mask = 0b11 << 11
                xpp_set_mask = 0

            elif to_priv == RV.RiscvPrivileges.MACHINE:
                raise ValueError("Switching from Machine to Machine is not supported.")

        # switch from supervisor mode
        elif from_priv == RV.RiscvPrivileges.SUPER:
            xepc_csr = "sepc"
            xstatus_csr = "sstatus"
            xret_instr = "sret"
            if to_priv == RV.RiscvPrivileges.USER:
                # clear bit 8
                xpp_clear_mask = 1 << 8
                xpp_set_mask = 0
            elif to_priv == RV.RiscvPrivileges.SUPER:
                # not sure if this is needed, but legacy code had this
                xpp_clear_mask = 0
                xpp_set_mask = 1 << 8
            else:
                raise ValueError(f"Switching from Supervisor to {to_priv} is not supported.")
        else:
            raise ValueError(f"Privilege {from_priv} not yet supported for switching privilege")

        # Setup mepc csr, so we jump to that label after MRET
        if jump_label is None:
            if jump_register is None:
                raise ValueError("jump_register must be provided if jump_label is None")
            code += f"csrw {xepc_csr}, {jump_register}\n"
        else:
            code += f"la t0, {jump_label}\n"
            code += f"csrw {xepc_csr}, t0\n"

        if xpp_clear_mask:
            code += f""" # clear xPP
                li t0, 0x{xpp_clear_mask:x}
                csrrc x0, {xstatus_csr}, t0
            """
        if xpp_set_mask:
            code += f""" # set xPP
                li t0, 0x{xpp_set_mask:x}
                csrrs x0, {xstatus_csr}, t0
            """

        # Write hstatus.spp=1 if we are switching to VS-mode
        if switch_to_vs:
            code += """
                li x1, 0x00000080 # HSTATUS.SVP=1
                csrrs x0, hstatus, x1
            """

        code += xret_instr + "\n"
        return code

    def csr_read_randomization(self):
        """
        This function is responsible for generating random CSR reads based on the current OS mode.
        Some testbenches may only have a CSR value comparison check triggered on a CSR read and this
        randomization helps with triggering those checks.

        This assumes the function is running in the ``handler_priv`` mode when choosing the correct maximum privilege.
        It also uses the ``featmgr.supported_priv_modes`` to include all lower privileged CSRs that can be accessed.

        These CSR reads can be disabled with commandline switch ``--no_random_csr_reads``

        When ``--fs_randomization`` or ``--vs_randomization`` is set, on each scheduler
        entry the FS or VS field is set to the next value from a precomputed table
        (round-robin over ``--fs_randomization_values`` / ``--vs_randomization_values``).
        Deterministic and reproducible. Only when F/D or V extension is supported.
        Values 0-3: Off, Initial, Clean, Dirty.
        """

        # delay initializing until it's needed
        if self.featmgr.no_random_csr_reads:
            return ""
        elif self.csr_manager is None:
            self.csr_manager = CsrManagerInterface(self.rng)

        # Pick status CSR from current OS privilege and env (shared by FS and VS)
        def _status_csr() -> str:
            if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
                return "mstatus"
            if self.featmgr.priv_mode == RV.RiscvPrivileges.SUPER:
                return "vsstatus" if self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED else "sstatus"
            return "sstatus"

        fs_vs_instrs = ""
        fp_supported = self.featmgr.is_feature_supported("f") or self.featmgr.is_feature_supported("d")
        v_supported = self.featmgr.is_feature_supported("v")
        # variable_manager is present when called from Scheduler (round-robin indices registered there)
        vm = getattr(self, "variable_manager", None)

        # FS: round-robin over fs_randomization_values on each dispatch (when enabled)
        if self.featmgr.fs_randomization > 0 and fp_supported and self.featmgr.fs_randomization_values and vm is not None:
            status_csr = _status_csr()
            fs_var = vm.get_variable("fs_rr_index")
            fs_size = len(self.featmgr.fs_randomization_values)
            fs_vs_instrs += f"""# FS round-robin (--fs_randomization)
        la t3, fs_rr_table
        {fs_var.load(dest_reg="t4")}
        slli t5, t4, 2
        add t5, t3, t5
        lw t5, 0(t5)
        slli t5, t5, 13
        li t3, 0x6000
        csrrc x0, {status_csr}, t3
        csrrs x0, {status_csr}, t5
        addi t4, t4, 1
        li t3, {fs_size}
        blt t4, t3, 1f
        mv t4, zero
1:      {fs_var.store(src_reg="t4")}
"""
        # VS: round-robin over vs_randomization_values on each dispatch (when enabled)
        if self.featmgr.vs_randomization > 0 and v_supported and self.featmgr.vs_randomization_values and vm is not None:
            status_csr = _status_csr()
            vs_var = vm.get_variable("vs_rr_index")
            vs_size = len(self.featmgr.vs_randomization_values)
            fs_vs_instrs += f"""# VS round-robin (--vs_randomization)
        la t3, vs_rr_table
        {vs_var.load(dest_reg="t4")}
        slli t5, t4, 2
        add t5, t3, t5
        lw t5, 0(t5)
        slli t5, t5, 9
        li t3, 0x600
        csrrc x0, {status_csr}, t3
        csrrs x0, {status_csr}, t5
        addi t4, t4, 1
        li t3, {vs_size}
        blt t4, t3, 2f
        mv t4, zero
2:      {vs_var.store(src_reg="t4")}
"""
        # Find the current privilege mode of OS
        # Machine mode is default
        instrs = fs_vs_instrs
        csr_list = []

        available_privileges: set[RV.RiscvPrivileges] = set(self.featmgr.supported_priv_modes)  # all supported privileges by platform
        # remove CSRs that aren't accessible by the current privilege
        forbidden_by_privilege: dict[RV.RiscvPrivileges, set[RV.RiscvPrivileges]] = {
            RV.RiscvPrivileges.MACHINE: set(),
            RV.RiscvPrivileges.SUPER: {RV.RiscvPrivileges.MACHINE},
            RV.RiscvPrivileges.USER: {RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER},
        }
        available_privileges -= forbidden_by_privilege.get(RV.RiscvPrivileges.MACHINE, set())

        for privilege in available_privileges:
            if privilege == RV.RiscvPrivileges.MACHINE and self.featmgr.random_machine_csr_list:
                csr_list += self.featmgr.random_machine_csr_list.split(",")
            elif privilege == RV.RiscvPrivileges.SUPER and self.featmgr.random_supervisor_csr_list:
                csr_list += self.featmgr.random_supervisor_csr_list.split(",")
            elif privilege == RV.RiscvPrivileges.USER and self.featmgr.random_user_csr_list:
                csr_list += self.featmgr.random_user_csr_list.split(",")

        priv_mode_to_str: dict[RV.RiscvPrivileges, str] = {
            RV.RiscvPrivileges.MACHINE: "Machine",
            RV.RiscvPrivileges.SUPER: "Supervisor",
            RV.RiscvPrivileges.USER: "User",
        }
        # Get up to max_random_csr_reads random CSR to read
        available_privileges.discard(RV.RiscvPrivileges.USER)  # no user CSRs supported in CsrManager
        available_privilege_list = sorted(available_privileges, key=lambda x: x.value)  # without sorting the set will be randomly ordered based on PYTHONHASHSEED. Sorting for deterministic behavior.
        for _ in range(self.rng.randint(3, self.featmgr.max_random_csr_reads)):
            random_priv_mode = self.rng.choice(available_privilege_list)
            priv_mode_str = priv_mode_to_str[random_priv_mode]
            csr_config = self.csr_manager.get_random_csr(match={"Accessibility": priv_mode_str, "ISS_Support": "Yes"})
            csr_name = list(csr_config.keys())[0]

            instrs += f"csrr t0, {csr_name}\n"

        for csr in csr_list:
            instrs += f"csrr t0, {csr}\n"

        return instrs

    def kernel_panic(self, name: str) -> str:
        """
        Code for runtime to end test early if an unexpected error occurs in runtime code.
        Named after kernel panic convention used in UNIX to catch fatal internal errors.

        Useful for catching errors before trap handler is setup, or during trap handling.

        Assumes that panic is in the ``handler_priv`` mode. Calls ``eot__end_test`` to end the test.
        Setting gp to 0 to fail test.

        :param name: Name of the kernel panic function
        :param cause: Cause of the kernel panic. Can be an equate or a value. If int is passed, it must be odd for the test to end. Even values will raise a ValueError.
        """

        return f"""
{name}:
    li gp, 0
    j eot__end_test
        """

    def save_gprs(self, scratch_reg: str) -> str:
        """Save all GPRs to hart-local gpr_save_area. Assumes tp already points to hart context."""
        gpr = self.variable_manager.get_variable("gpr_save_area")
        lines = ["# Save GPRs"]

        # Save x1 first (we'll use it as temp)
        lines.append(gpr.store("x1", index=1))

        # Get original tp from scratch, save to index 4
        lines.append(f"csrr x1, {scratch_reg}")
        lines.append(gpr.store("x1", index=4))

        # Save x2, x3
        lines.append(gpr.store("x2", index=2))
        lines.append(gpr.store("x3", index=3))

        # Save x5-x31
        for i in range(5, 32):
            lines.append(gpr.store(f"x{i}", index=i))

        return "\n\t".join(lines)

    def restore_gprs(self, scratch_reg: str) -> str:
        """Restore all GPRs from hart-local gpr_save_area. Must be called right before xret or exit."""
        gpr = self.variable_manager.get_variable("gpr_save_area")
        lines = ["# Restore GPRs"]

        # Restore x2, x3
        lines.append(gpr.load("x2", index=2))
        lines.append(gpr.load("x3", index=3))

        # Restore x5-x31
        for i in range(5, 32):
            lines.append(gpr.load(f"x{i}", index=i))

        # Restore tp via scratch, then restore x1
        lines.append(gpr.load("x1", index=4))  # x1 = original tp
        lines.append(f"csrw {scratch_reg}, x1")  # scratch = original tp
        lines.append(gpr.load("x1", index=1))  # x1 = original ra
        lines.append(f"csrrw tp, {scratch_reg}, tp")  # restore tp

        return "\n\t".join(lines)
