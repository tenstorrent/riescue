# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# flake8: noqa: F401
"""
Macros provided for the dtest framework.
"""

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.parser import ParsedCsrAccess
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class Macro:
    """
    Class used to create assembly macros.
    Macro arguments must start with a double underscore.

    .. code-block:: python

        macro = Macro("MACRO_NAME")
        macro.args = ["__arg1", "__arg2", "__arg3"]
        macro.code = '''
            li t0, \\__arg1
            li t1, \\__arg2
            li t2, \\__arg3
        '''
    """

    def __init__(self, name):
        self.name = name
        self.args = []
        self.code = ""

    def generate(self) -> str:
        """
        Generates the code for this macro.
        """
        for arg in self.args:
            if not arg.startswith("__"):
                raise ValueError(f"Macro argument {arg} must start with two underscores")
        macro_definition = ".macro " + self.name + " "
        macro_definition += ", ".join(self.args)

        macro_body = self.code

        macro_body += "\n.endm\n"

        return macro_definition + macro_body


class Macros(AssemblyGenerator):
    """Generates assembly macros for test framework.

    Provides macros for multiprocessing synchronization, exception handling,
    and system operations.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.paging_mode = self.featmgr.paging_mode
        self.mp_enabled = self.mp_active
        self.macros = list()

    def get_hart_context(self) -> str:
        """
        Generates code to get the hart context pointer into the tp register.

        :return: Assembly string to get the hart context pointer into the tp register
        """
        if not self.mp_enabled:
            get_tp = "li tp, hart_context"
        else:
            if self.test_priv == RV.RiscvPrivileges.MACHINE:
                get_tp = f"csrr tp, mscratch"
            else:
                # Syscall always returns the hart-local storage pointer in a0
                get_tp = """
                    li x31, 0xf0002001 # retrieve hard-local storage pointer in a0 register.
                    ecall
                    mv tp, a0
                """
        return get_tp

    def generate(self) -> str:
        code = ""
        self.gen_os_setup_check_excp()
        self.gen_os_skip_check_excp()
        self.gen_os_get_hartid()
        self.gen_mutex_acquire_amo()
        self.gen_mutex_release_amo()
        self.gen_mutex_acquire_lr_sc()
        self.gen_mutex_release_lr_sc()
        self.gen_semaphore_acquire()
        self.gen_semaphore_release()
        self.gen_critical_section_amo()
        self.gen_critical_section_lr_sc()
        self.gen_barrier_amo()
        self.gen_interrupts_macros()

        for macro in self.macros:
            code += macro.generate()

        return code

    def gen_os_setup_check_excp(self):
        """
        Store the expected exception parameters in the hart context.

        Clobbers a0, tp, t3
        """
        name = "OS_SETUP_CHECK_EXCP"
        macro = Macro(name=name)
        macro.args = [
            "__expected_cause",
            "__expected_pc",
            "__return_pc",
            "__expected_tval=0",
            "__expected_htval=0",
            "__skip_pc_check=0",
            "__far_expected_pc=0",
            "__far_return_pc=0",
            "__gva_check=0",
            "__expected_mode=0",
            "__re_execute=0",
        ]

        check_excp_expected_cause = self.variable_manager.get_variable("check_excp_expected_cause")
        check_excp_expected_pc = self.variable_manager.get_variable("check_excp_expected_pc")
        check_excp_expected_tval = self.variable_manager.get_variable("check_excp_expected_tval")
        check_excp_expected_htval = self.variable_manager.get_variable("check_excp_expected_htval")
        check_excp_return_pc = self.variable_manager.get_variable("check_excp_return_pc")
        check_excp_skip_pc_check = self.variable_manager.get_variable("check_excp_skip_pc_check")
        check_excp_gva_check = self.variable_manager.get_variable("check_excp_gva_check")
        check_excp_expected_mode = self.variable_manager.get_variable("check_excp_expected_mode")
        check_excp_re_execute = self.variable_manager.get_variable("check_excp_re_execute")

        macro.code = f"""
            {self.get_hart_context()}
            li t3, \\__expected_cause
            {check_excp_expected_cause.store(src_reg="t3")}

            # Expected PC (use la for labels; when __far_expected_pc=1 use li for equates like random_addr)
            .if \\__far_expected_pc
            li t3, \\__expected_pc
            .else
            la t3, \\__expected_pc
            .endif
            {check_excp_expected_pc.store(src_reg="t3")}

            # Expected TVAL
            li t3, \\__expected_tval
            {check_excp_expected_tval.store(src_reg="t3")}

            # Expected HTVAL
            li t3, \\__expected_htval
            {check_excp_expected_htval.store(src_reg="t3")}

            # Return pc (use la for labels; __far_return_pc=1 for equates)
            .if \\__far_return_pc
            li t3, \\__return_pc
            .else
            la t3, \\__return_pc
            .endif
            {check_excp_return_pc.store(src_reg="t3")}

            # Skip PC check
            li t3, \\__skip_pc_check
            {check_excp_skip_pc_check.store(src_reg="t3")}

            # GVA check flag
            li t3, \\__gva_check
            {check_excp_gva_check.store(src_reg="t3")}

            # Expected handler mode (0 = any)
            li t3, \\__expected_mode
            {check_excp_expected_mode.store(src_reg="t3")}

            # Re-execute flag: when set, the OS trap handler returns to the faulting PC
            # instead of overwriting xepc with __return_pc (sdtrig icount/mcontrol6 use cases).
            li t3, \\__re_execute
            {check_excp_re_execute.store(src_reg="t3")}

        """

        self.register_equate("CHECK_EXCP_MODE_MACHINE", "1")
        self.register_equate("CHECK_EXCP_MODE_HS", "2")
        self.register_equate("CHECK_EXCP_MODE_VS", "3")

        self.macros.append(macro)

    def gen_os_skip_check_excp(self):
        """
        Clobbers t3, tp, and a0
        """
        name = "OS_SKIP_CHECK_EXCP"
        macro = Macro(name=name)
        macro.args = ["__return_pc"]

        check_excp_return_pc = self.variable_manager.get_variable("check_excp_return_pc")
        check_excp = self.variable_manager.get_variable("check_excp")

        macro.args = ["__return_pc", "__far_addr=0"]
        macro.code = f"""
            {self.get_hart_context()}
            .if \\__far_addr
            li t3, \\__return_pc
            .else
            la t3, \\__return_pc
            .endif
            {check_excp_return_pc.store(src_reg="t3")}

            li t3, 0
            {check_excp.store(src_reg="t3")}

        """
        self.macros.append(macro)

    def gen_os_get_hartid(self):
        """
        Macro to retrieve mhartid from hart-local context.

        If test is in machine mode, can just use mhartid CSR
        If test is in supervisor mode, can just read sscratch register and load
        If test is in user mode, going to need to use ecall to get hart-local storage and load

        clobbers x31 (t6), tp, and a0, dest_reg
        """

        macro = Macro(name="GET_MHART_ID")
        macro.args = ["__dest_reg=s1"]

        code = []
        mhartid = self.variable_manager.get_variable("mhartid")
        if self.test_priv == RV.RiscvPrivileges.MACHINE:
            code = ["\n csrr \\__dest_reg, mhartid"]
        elif self.test_priv == RV.RiscvPrivileges.SUPER:
            code = [
                "csrr \\__dest_reg, sscratch",
                f"ld \\__dest_reg, {mhartid.offset}(\\__dest_reg)",
            ]
        else:
            code = [
                self.get_hart_context(),
                f"ld \\__dest_reg, {mhartid.offset}(tp)",
            ]
        macro.code = "\n" + "\n\t".join(code)
        self.macros.append(macro)

    def gen_barrier_amo(self):
        name = "OS_SYNC_HARTS"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__lock_addr_reg=a0",
            "__arrive_counter_addr_reg=a1",
            "__depart_counter_addr_reg=a2",
            "__flag_addr_reg=a3",
            "__swap_val_reg=t0",
            "__work_reg_1=t1",
            "__work_reg_2=t2",
            "__end_test_label=end_test_addr",
        ]
        macro.code = Routines.place_barrier(
            name="\\__test_label\\()",
            lock_addr_reg="\\__lock_addr_reg",
            arrive_counter_addr_reg="\\__arrive_counter_addr_reg",
            depart_counter_addr_reg="\\__depart_counter_addr_reg",
            flag_addr_reg="\\__flag_addr_reg",
            swap_val_reg="\\__swap_val_reg",
            work_reg_1="\\__work_reg_1",
            work_reg_2="\\__work_reg_2",
            num_cpus=self.featmgr.num_cpus,
            end_test_label="\\__end_test_label",
            max_tries=50000,
            use_zawrs=self.featmgr.is_feature_enabled("zawrs"),
        )
        self.macros.append(macro)

    def gen_mutex_acquire_amo(self):
        name = "MUTEX_ACQUIRE_AMO"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__lock_addr_reg=a0",
            "__swap_val_reg=t0",
            "__work_reg=t1",
            "__end_test_label=end_test_addr",
        ]
        macro.code = Routines.place_acquire_lock(
            name="\\__test_label\\()",
            lock_addr_reg="\\__lock_addr_reg",
            swap_val_reg="\\__swap_val_reg",
            work_reg="\\__work_reg",
            end_test_label="\\__end_test_label",
            use_zawrs=self.featmgr.is_feature_enabled("zawrs"),
        )
        self.macros.append(macro)

    def gen_mutex_release_amo(self):
        name = "MUTEX_RELEASE_AMO"
        macro = Macro(name=name)
        macro.args = ["__test_label:req", "__lock_addr_reg=a0"]
        macro.code = Routines.place_release_lock(name="\\__test_label\\()", lock_addr_reg="\\__lock_addr_reg")
        self.macros.append(macro)

    def gen_mutex_acquire_lr_sc(self):
        name = "MUTEX_ACQUIRE_LR_SC"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__lock_addr_reg=a0",
            "__expected_val_reg=a1",
            "__desired_val_reg=a2",
            "__return_val_reg=a3",
            "__work_reg=t0",
        ]
        macro.code = Routines.place_acquire_lock_lr_sc(
            name="\\__test_label\\()",
            lock_addr_reg="\\__lock_addr_reg",
            expected_val_reg="\\__expected_val_reg",
            desired_val_reg="\\__desired_val_reg",
            return_val_reg="\\__return_val_reg",
            work_reg="\\__work_reg",
            retry=True,
        )
        self.macros.append(macro)

    # Desired and expected values should be swapped for release
    def gen_mutex_release_lr_sc(self):
        name = "MUTEX_RELEASE_LR_SC"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__lock_addr_reg=a0",
            "__expected_val_reg=a1",
            "__desired_val_reg=a2",
            "__return_val_reg=a3",
            "__work_reg=t0",
        ]
        macro.code = Routines.place_release_lock_lr_sc(
            name="\\__test_label\\()",
            lock_addr_reg="\\__lock_addr_reg",
            expected_val_reg="\\__expected_val_reg",
            desired_val_reg="\\__desired_val_reg",
            return_val_reg="\\__return_val_reg",
            work_reg="\\__work_reg",
            retry=True,
        )
        self.macros.append(macro)

    def gen_critical_section_amo(self):
        name = "CRITICAL_SECTION_AMO"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__lock_addr_reg=a0",
            "__swap_val_reg=t0",
            "__work_reg=t1",
            "__critical_section_addr_reg=a1",
            "__end_test_label=end_test_addr",
        ]
        macro.code = Routines.place_acquire_lock(
            name="\\__test_label\\()",
            lock_addr_reg="\\__lock_addr_reg",
            swap_val_reg="\\__swap_val_reg",
            work_reg="\\__work_reg",
            end_test_label="\\__end_test_label",
            use_zawrs=self.featmgr.is_feature_enabled("zawrs"),
        )
        macro.code += "jalr ra, \\__critical_section_addr_reg"
        macro.code += Routines.place_release_lock(name="\\__test_label\\()", lock_addr_reg="\\__lock_addr_reg")
        self.macros.append(macro)

    def gen_critical_section_lr_sc(self):
        """
        This is unused by any tests and doesn't appear to work as intended
        """
        name = "CRITICAL_SECTION_LR_SC"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__lock_addr_reg=a0",
            "__expected_val_reg=a1",
            "__desired_val_reg=a2",
            "__return_val_reg=a3",
            "__work_reg=t0",
            "__critical_section_addr_reg=a4",
        ]
        macro.code = Routines.place_acquire_lock_lr_sc(
            name="\\__test_label\\()",
            lock_addr_reg="\\__lock_addr_reg",
            expected_val_reg="\\__expected_val_reg",
            desired_val_reg="\\__desired_val_reg",
            return_val_reg="\\__return_val_reg",
            work_reg="\\__work_reg",
            retry=True,
        )
        macro.code += "bnez \\__return_val_reg, \\__test_label\\()_exit\n"
        macro.code += "jalr ra, \\__critical_section_addr_reg\n"
        macro.code += Routines.place_release_lock_lr_sc(
            name="\\__test_label\\()",
            lock_addr_reg="\\__lock_addr_reg",
            expected_val_reg="\\__desired_val_reg",
            desired_val_reg="\\__expected_val_reg",
            return_val_reg="\\__return_val_reg",
            work_reg="\\__work_reg",
            retry=True,
        )
        macro.code += "\\__test_label\\()_exit:"
        self.macros.append(macro)

    def gen_semaphore_acquire(self):
        name = "SEMAPHORE_ACQUIRE_TICKET"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__semaphore_addr_reg=a0",
            "__lock_addr_reg=a1",
            "__swap_val_reg=t0",
            "__return_val_reg=a2",
            "__work_reg=t2",
            "__end_test_label=end_test_addr",
        ]
        macro.code = Routines.place_semaphore_acquire_ticket(
            name="\\__test_label\\()",
            semaphore_addr_reg="\\__semaphore_addr_reg",
            lock_addr_reg="\\__lock_addr_reg",
            swap_val_reg="\\__swap_val_reg",
            return_val_reg="\\__return_val_reg",
            work_reg="\\__work_reg",
            retry=False,
            end_test_label="\\__end_test_label",
            use_zawrs=self.featmgr.is_feature_enabled("zawrs"),
        )
        self.macros.append(macro)

    def gen_semaphore_release(self):
        name = "SEMAPHORE_RELEASE_TICKET"
        macro = Macro(name=name)
        macro.args = [
            "__test_label:req",
            "__semaphore_addr_reg=a0",
            "__lock_addr_reg=a1",
            "__swap_val_reg=t0",
            "__return_val_reg=a2",
            "__work_reg=t2",
        ]
        macro.code = Routines.place_semaphore_release_ticket(
            name="\\__test_label\\()",
            semaphore_addr_reg="\\__semaphore_addr_reg",
            lock_addr_reg="\\__lock_addr_reg",
            swap_val_reg="\\__swap_val_reg",
            return_val_reg="\\__return_val_reg",
            work_reg="\\__work_reg",
            use_zawrs=self.featmgr.is_feature_enabled("zawrs"),
        )
        self.macros.append(macro)

    def gen_interrupts_macros(self):
        self.macros.extend(self.interrupt_control_macros())

    def _csr_ecall_code(self, csr_name: str, set_bit: bool, imm_value: str) -> str:
        """Generate ecall-based CSR set/clear code and register the access in the pool."""
        operation = "set" if set_bit else "clear"

        if csr_name.startswith("m"):
            priv_mode = "machine"
            flag_name = "machine_csr_jump_table_flags"
            syscall = "0xf0001005"
        else:  # s-prefixed: ecall reaches HS-mode supervisor jump table
            priv_mode = "supervisor"
            flag_name = "super_csr_jump_table_flags"
            syscall = "0xf0001006"

        # Register in pool if not already present (so jump table includes this CSR)
        existing = self.pool.get_parsed_csr_accesses()
        if csr_name not in existing or operation not in existing[csr_name]:
            csr_id = self.pool.get_next_csr_id()
            label = f"csr_access_{csr_name}_{priv_mode}_key_{csr_id}_{operation}"
            csr_access = ParsedCsrAccess(csr_name=csr_name, priv_mode=priv_mode, read_write_set_clear=operation, label=label, csr_id=csr_id, hypervisor=False)
            self.pool.add_parsed_csr_access(csr_access)

        parsed = self.pool.get_parsed_csr_access(csr_name, operation)

        code = f"\nli t2, {imm_value}"
        code += f"\nli x31, {flag_name}"
        code += f"\nli t0, {parsed.csr_id}"
        code += "\nsd t0, 0(x31)"
        code += f"\nli x31, {syscall}"
        code += "\necall"
        return code

    def _make_csr_macro(self, name: str, csr_name: str, set_bit: bool, imm_value: str) -> Macro:
        """Create a macro that sets/clears a CSR bit, using direct access or ecall as needed.

        Determines direct access eligibility from the CSR name prefix:
        - m-prefixed CSRs: require M-mode
        - s-prefixed CSRs: require M or S-mode (bare metal only)
        In virtualized mode, all CSRs require ecall: m-prefixed go to the machine jump table,
        s-prefixed go to the supervisor (HS-mode) jump table.
        """
        is_virtualized = self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED

        if csr_name.startswith("m"):
            can_direct_access = self.test_priv == RV.RiscvPrivileges.MACHINE
        elif csr_name.startswith("s"):
            can_direct_access = self.test_priv in (RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER) and not is_virtualized
        else:
            raise ValueError(f"Unexpected CSR name prefix: {csr_name}")

        macro = Macro(name=name)
        if can_direct_access:
            csr_instr = "csrsi" if set_bit else "csrci"
            macro.code = f"\n{csr_instr} {csr_name}, {imm_value}"
        else:
            macro.code = self._csr_ecall_code(csr_name, set_bit, imm_value)
        return macro

    def interrupt_control_macros(self) -> list:
        return [
            self._make_csr_macro("DISABLE_MIE", "mstatus", False, "(1<<3)"),
            self._make_csr_macro("DISABLE_SIE", "sstatus", False, "(1<<1)"),
            self._make_csr_macro("ENABLE_MIE", "mstatus", True, "(1<<3)"),
            self._make_csr_macro("ENABLE_SIE", "sstatus", True, "(1<<1)"),
            self._make_csr_macro("SET_DIRECT_INTERRUPTS", "mtvec", False, "0x1"),
            self._make_csr_macro("SET_VECTORED_INTERRUPTS", "mtvec", True, "0x1"),
            self._make_csr_macro("SET_DIRECT_INTERRUPTS_S", "stvec", False, "0x1"),
            self._make_csr_macro("SET_VECTORED_INTERRUPTS_S", "stvec", True, "0x1"),
        ]
