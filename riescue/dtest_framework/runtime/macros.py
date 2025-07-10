# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# flake8: noqa: F401
"""
Macros provided for the dtest framework.
"""

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
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

    def __init__(self, mp_enablement=RV.RiscvMPEnablement.MP_OFF, **kwargs):
        super().__init__(**kwargs)

        self.priv_mode = self.featmgr.priv_mode
        self.paging_mode = self.featmgr.paging_mode
        self.mp_enabled = mp_enablement != RV.RiscvMPEnablement.MP_OFF

        self.macros = list()

    def calculate_hartid_offset(self, gpr):
        routine_string = ""
        if not self.mp_enabled:
            return "mv " + gpr + ", zero\n"
        else:
            routine_string += f"""
            GET_MHART_ID t0
            mv {gpr}, t0
            li t0, 0x8
            mul {gpr}, {gpr}, t0
            """
        return routine_string

    def add_hartid_offset(self, address_gpr, offset_gpr):
        return f"add {address_gpr}, {address_gpr}, {offset_gpr}\n"

    def generate(self) -> str:
        code = ""
        self.gen_os_setup_check_excp()
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
        name = "OS_SETUP_CHECK_EXCP"
        macro = Macro(name=name)
        macro.args = ["__expected_cause", "__expected_pc", "__return_pc", "__expected_tval=0"]

        macro.code = f"""
            {self.calculate_hartid_offset('t2') if self.mp_enabled == True else ''}
            # Setup exception check
            li x1, check_excp_expected_cause
            {self.add_hartid_offset('x1', 't2') if self.mp_enabled == True else ''}
            li t3, \\__expected_cause
            sw t3, 0(x1)

            # Expected PC
            li x1, check_excp_expected_pc
            {self.add_hartid_offset('x1', 't2') if self.mp_enabled == True else ''}
            la t3, \\__expected_pc
            sd t3, 0(x1)

            # Expected TVAL
            li x1, check_excp_expected_tval
            {self.add_hartid_offset('x1', 't2') if self.mp_enabled == True else ''}
            li t3, \\__expected_tval
            sd t3, 0(x1)

            # Return pc
            li x1, check_excp_return_pc
            {self.add_hartid_offset('x1', 't2') if self.mp_enabled == True else ''}
            la t3, \\__return_pc
            sd t3, 0(x1)

        """

        self.macros.append(macro)

    """
        These is a convenience macro, since in actuality every exception call will refill s1 with the hartid.
        The code 0xf0002001 is the ecall code for getting the hartid and nothing else.
    """

    def gen_os_get_hartid(self):
        name = "GET_MHART_ID"
        macro = Macro(name=name)
        macro.args = ["__dest_reg=s1"]
        macro.code = """
            li x31, 0xf0002001 # Call to enter exception handler code, get hartid for free, and skip to next pc.
            ecall
            mv \\__dest_reg, s1
        """
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
            disable_wfi_wait=True,  # RVTOOLS-4204
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
            disable_wfi_wait=True,  # RVTOOLS-4204
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
            disable_wfi_wait=True,  # RVTOOLS-4204
        )
        macro.code += "jalr ra, \\__critical_section_addr_reg"
        macro.code += Routines.place_release_lock(name="\\__test_label\\()", lock_addr_reg="\\__lock_addr_reg")
        self.macros.append(macro)

    def gen_critical_section_lr_sc(self):
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
        macro.code += "bnez \\__return_val_reg, \\__test_label\\()_exit"
        macro.code += "jalr ra, \\__critical_section_addr_reg"
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
            disable_wfi_wait=True,  # RVTOOLS-4204
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
            disable_wfi_wait=True,  # RVTOOLS-4204
        )
        self.macros.append(macro)

    def gen_interrupts_macros(self):
        self.macros.extend(self.interrupt_control_macros())

    def interrupt_control_macros(self) -> list:
        disable_m = Macro(name="DISABLE_MIE")
        disable_m.code = "\ncsrci mstatus, (1<<3)"  # clear bit 3 of mstatus

        enable_m = Macro(name="ENABLE_MIE")
        enable_m.code = "\ncsrsi mstatus, (1<<3)"  # set bit 3 of mstatus

        disable_s = Macro(name="DISABLE_SIE")
        disable_s.code = "\ncsrci sstatus, (1<<1)"  # clear bit 1 of sstatus

        enable_s = Macro(name="ENABLE_SIE")
        enable_s.code = "\ncsrsi sstatus, (1<<1)"  # set bit 3 of sstatus

        set_direct_m_interrupts = Macro("SET_DIRECT_INTERRUPTS")
        set_direct_m_interrupts.code += "\ncsrci mtvec, 0x1"

        set_vec_m_interrupts = Macro("SET_VECTORED_INTERRUPTS")
        set_vec_m_interrupts.code += "\ncsrsi mtvec, 0x1"

        set_direct_s_interrupts = Macro("SET_DIRECT_INTERRUPTS_S")
        set_direct_s_interrupts.code += "\ncsrci stvec, 0x1"

        set_vec_s_interrupts = Macro("SET_VECTORED_INTERRUPTS_S")
        set_vec_s_interrupts.code += "\ncsrsi stvec, 0x1"

        return [
            disable_m,
            disable_s,
            enable_m,
            enable_s,
            set_direct_m_interrupts,
            set_vec_m_interrupts,
            set_direct_s_interrupts,
            set_vec_s_interrupts,
        ]
