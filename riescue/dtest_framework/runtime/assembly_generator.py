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
    handler_priv: RV.RiscvPrivileges
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
        self.handler_priv = ctx.handler_priv
        self.mp_active = ctx.mp_active
        self.mp_parallel = ctx.mp_parallel
        self.mp_simultaneous = ctx.mp_simultaneous

        self.hartid_reg = "s1"
        self.csr_manager: Optional[CsrManagerInterface] = None
        self.xlen: RV.Xlen = RV.Xlen.XLEN64

        self.scratch_reg: str  #: Trap handler scratch register - used to store hart-local storage pointer (tp)
        if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.SUPER:
            self.scratch_reg = "sscratch"
        else:
            self.scratch_reg = "mscratch"

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

    def os_check_excp(self, return_label: str, xepc: str, xret: str) -> str:
        """
        Generates code to check for expected exceptions.
        Exceptions can be set to expected using the OS_SETUP_CHECK_EXCP macro.

        .. note::

            Assumes that expected exception cause is loaded into t1

        If skip_instruction_for_unexpected is set, skips the instruction check for unexpected exceptions.

        :param return_label: Label to return to after checking exceptions
        :param xepc: CSR to read the exception PC from
        :param xret: Instruction to return from the exception handler
        :return: Assembly code string
        """
        # Hart-local variables
        check_excp = self.variable_manager.get_variable("check_excp")
        check_excp_expected_cause = self.variable_manager.get_variable("check_excp_expected_cause")
        check_excp_skip_pc_check = self.variable_manager.get_variable("check_excp_skip_pc_check")
        check_excp_expected_pc = self.variable_manager.get_variable("check_excp_expected_pc")
        check_excp_actual_pc = self.variable_manager.get_variable("check_excp_actual_pc")
        # label to jump to if invalid exception is encountered
        if self.featmgr.skip_instruction_for_unexpected:
            unexpected_exception = "count_ignored_excp"
        else:
            unexpected_exception = "test_failed"

        code = f"""
            # Check if check_exception is enabled
            {check_excp.load(dest_reg="t0")}
            bne t0, x0, do_check_excp

            # restore check_excp, return to return_label
            addi t0, t0, 1
            {check_excp.store(src_reg="t0")}
            j {return_label}

        do_check_excp:
            # Check for correct exception code
            {check_excp_expected_cause.load_and_clear(dest_reg="t0"):<35}  # check_excp_expected_cause
            bne t1, t0, {unexpected_exception}

            # if skip_pc_check is set, skip the pc check
            {check_excp_skip_pc_check.load_and_clear(dest_reg="t0"):<35}  # check_excp_skip_pc_check
            bne t0, x0, skip_pc_check

            # compare expected and actual PC values
            {check_excp_expected_pc.load_and_clear(dest_reg="t1"):<35}  # check_excp_expected_pc
            {check_excp_actual_pc.load_and_clear(dest_reg="t0"):<35}  # check_excp_actual_pc
            bne t1, t0, {unexpected_exception}

        skip_pc_check:
            j {return_label}
        """

        if self.featmgr.skip_instruction_for_unexpected:
            # generates code for skipping trap, incrementing ignored exception count, and ending test if max count is reached
            # otherwise, skips trapped instruction and continues to test
            code += f"""
            count_ignored_excp:
                # Get PC exception {xepc}
                csrr t0, {xepc}
                lwu t1, 0(t0)
                # Check lower 2 bits to see if it equals 3
                andi t1, t1, 0x3
                li t2, 3
                # If bottom two bits are 0b11, we need to add 4 to the PC
                beq t1, t2, pc_plus_four

            pc_plus_two:
                # Otherwise, add 2 to the PC (compressed instruction)
                addi t0, t0, 2
                j jump_over_pc
            pc_plus_four:
                addi t0, t0, 4
            jump_over_pc:
                # Load to {xepc}
                csrw {xepc}, t0
                {self.excp_ignored_count.load_immediate("t0")}
                li t1, 1
                amoadd.w t1, t1, (t0)
                li t0, {self.IGNORED_EXCP_MAX_COUNT}
                bge t1, t0, soft_end_test
                # Jump to new PC
                {xret}


            soft_end_test:
                # Have to os_end_test_addr because we're at an elevated privilege level.
                addi gp, zero, 0x1
                li t0, os_end_test_addr
                ld t1, 0(t0)
                jr t1
            """

        # FIXME: This code is currently unreachable. Should it be included above?
        check_excp_expected_tval = self.variable_manager.get_variable("check_excp_expected_tval")
        if self.featmgr.deleg_excp_to == RV.RiscvPrivileges.MACHINE:
            tval = "mtval"
        else:
            tval = "stval"
        code += f"""
        # Optionally, check the value of mtval/stval
            csrr t1, {tval}
            {check_excp_expected_tval.load_and_clear(dest_reg="t0"):<35}  # check_excp_expected_tval
            bne t1, t0, test_failed
            j {return_label}"""

        return code

    def csr_read_randomization(self):
        """
        This function is responsible for generating random CSR reads based on the current OS mode.
        Some testbenches may only have a CSR value comparison check triggered on a CSR read and this
        randomization helps with triggering those checks.

        This assumes the function is running in the ``handler_priv`` mode when choosing the correct maximum privilege.
        It also uses the ``featmgr.supported_priv_modes`` to include all lower privileged CSRs that can be accessed.

        These CSR reads can be disabled with commandline switch ``--no_random_csr_reads``
        """

        # delay initializing until it's needed
        if self.featmgr.no_random_csr_reads:
            return ""
        elif self.csr_manager is None:
            self.csr_manager = CsrManagerInterface(self.rng)

        # Find the current privilege mode of OS
        # Machine mode is default
        instrs = ""
        csr_list = []

        available_privileges: set[RV.RiscvPrivileges] = set(self.featmgr.supported_priv_modes)  # all supported privileges by platform
        # remove CSRs that aren't accessible by the current privilege
        forbidden_by_privilege: dict[RV.RiscvPrivileges, set[RV.RiscvPrivileges]] = {
            RV.RiscvPrivileges.MACHINE: set(),
            RV.RiscvPrivileges.SUPER: {RV.RiscvPrivileges.MACHINE},
            RV.RiscvPrivileges.USER: {RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER},
        }
        available_privileges -= forbidden_by_privilege.get(self.handler_priv, set())

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
        available_privilege_list = list(available_privileges)
        for _ in range(self.rng.randint(3, self.featmgr.max_random_csr_reads)):
            random_priv_mode = self.rng.choice(available_privilege_list)
            priv_mode_str = priv_mode_to_str[random_priv_mode]
            csr_config = self.csr_manager.get_random_csr(match={"Accessibility": priv_mode_str, "ISS_Support": "Yes"})
            csr_name = list(csr_config.keys())[0]

            instrs += f"csrr t0, {csr_name}\n"

        for csr in csr_list:
            instrs += f"csrr t0, {csr}\n"

        return instrs

    def kernel_panic(self, name: str, cause: Union[str, int] = 3) -> str:
        """
        Code for runtime to end test early if an unexpected error occurs in runtime code.
        Named after kernel panic convention used in UNIX to catch fatal internal errors.

        Useful for catching errors before trap handler is setup, or during trap handling.

        :param name: Name of the kernel panic function
        :param cause: Cause of the kernel panic. Can be an equate or a value. If int is passed, it must be odd for the test to end. Even values will raise a ValueError.
        """
        if self.xlen == RV.Xlen.XLEN64:
            variable_size = "dword"
            store_instruction = "sd"
            load_instruction = "ld"
        elif self.xlen == RV.Xlen.XLEN32:
            variable_size = "word"
            store_instruction = "sw"
            load_instruction = "lw"
        else:
            raise ValueError(f"Unsupported xlen: {self.xlen}")

        tohost_ptr = f"{name}_tohost_ptr"

        if isinstance(cause, int) and cause % 2 == 0:
            raise ValueError(f"Cause must be odd for the test to end ({cause=})")

        return f"""
# pointer to tohost, so {name} can end test immediately.
{tohost_ptr}:
    .{variable_size} tohost

{name}:
    la t0, {tohost_ptr}
    {load_instruction} t0, 0(t0)
    li t1, {cause}
    {store_instruction} t1, 0(t0)
    wfi
    j {name}
        """
