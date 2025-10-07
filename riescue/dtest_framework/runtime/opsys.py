# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
OpSys takes care of all the operating system related code including Scheduler
"""

from types import MappingProxyType
import riescue.lib.enums as RV
from riescue.lib.csr_manager.csr_manager_interface import CsrManagerInterface
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class OpSys(AssemblyGenerator):
    """
    Generates OS code for test. Includes end test jump table, end of test routines, and utility routines.

    Produces scheduler, end-test routines, barrier synchronization, and
    utility functions for discrete test execution and coordination.
    """

    PER_HART_OS_VARIABLES = MappingProxyType(
        {
            "check_excp": "0x1",
            "check_excp_expected_pc": "-1",
            "check_excp_actual_pc": "-1",
            "check_excp_return_pc": "-1",
            "check_excp_skip_pc_check": "0",
            "check_excp_expected_tval": "-1",
            "check_excp_expected_cause": "0xff",
            "check_excp_actual_cause": "0xff",
            "os_save_ecall_fn_epc": "-1",
        }
    )

    SHARED_OS_VARIABLES = MappingProxyType(
        {
            "passed_addr": "passed",
            "failed_addr": "failed",
            "machine_flags": "0x0",
            "user_flags": "0x0",
            "super_flags": "0x0",
            "machine_area": "0x0",
            "user_area": "0x0",
            "super_area": "0x0",
            "os_passed_addr": "test_passed",
            "os_failed_addr": "test_failed",
            "os_end_test_addr": "os_end_test",
            "end_test_addr": "end_test",
            "num_harts_ended": "0x0",
            "num_hard_fails": "0x0",
            "excp_ignored_count": "0x0",
            "machine_csr_jump_table_flags": "0x0",
            "super_csr_jump_table_flags": "0x0",
        }
    )

    MP_SHARED_OS_VARIABLES = MappingProxyType(
        {
            "num_harts": None,
            "barrier_arrive_counter": "0x0",
            "barrier_depart_counter": None,
            "barrier_flag": "0x0",
            "barrier_lock": "0x0",
            "hartid_counter": "0x0",
        }
    )

    OS_VARIABLE_SIZE = 8
    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        assert not (self.featmgr.linux_mode and self.mp_active), "Linux mode and not MP mode are not supported together currently"

        # Build OS Data variables
        self.per_hart_variables = dict(OpSys.PER_HART_OS_VARIABLES)
        self.shared_variables = dict(OpSys.SHARED_OS_VARIABLES)

        if self.featmgr.user_interrupt_table:
            self.shared_variables["user_interrupt_table_addr"] = "USER_INTERRUPT_TABLE"
        if self.featmgr.excp_hooks:
            self.shared_variables["excp_handler_pre_addr"] = "excp_handler_pre"
            self.shared_variables["excp_handler_post_addr"] = "excp_handler_post"
        if self.featmgr.vmm_hooks:
            self.shared_variables["vmm_handler_pre_addr"] = "vmm_handler_pre"
            self.shared_variables["vmm_handler_post_addr"] = "vmm_handler_post"
        if self.mp_active:
            self.shared_variables.update(OpSys.MP_SHARED_OS_VARIABLES)
            self.shared_variables["num_harts"] = str(self.featmgr.num_cpus)
            self.shared_variables["barrier_depart_counter"] = str(self.featmgr.num_cpus)

    def generate(self) -> str:
        code = ""

        # If WYSIWYG mode, then we only need to add to_from_host addresses
        if self.featmgr.wysiwyg:
            code += self.append_end_test()
            code += self.append_os_data()
            code += self.append_to_from_host()
            return code

        code += self.generate_os()

        return code

    def generate_os(self):
        code = ""
        code += self.test_passed_failed_labels()

        code += """
        .section .text

        enter_scheduler:
            # Check if t0 has a pass or fail condition
            li t1, 0xbaadc0de
            beq t0, t1, test_failed

        """
        if self.featmgr.fe_tb:
            code += """
            # With FE testbench, we can't jump to failed even in the bad path since they can take that
            # path and end the test prematurely. So, make failed==passed label
            test_failed:
            """
        code += """
        test_passed:
            j schedule_tests

    """
        if not self.featmgr.fe_tb:
            code += f"""
        test_failed:
            li a0, num_hard_fails
            li t0, 1
            amoadd.w x0, t0, (a0)
            li gp, 0x{self.featmgr.eot_fail_value:x}
            j os_end_test

        """
        code += "os_rng_orig:\n"
        code += Routines.place_rng_unsafe_reg(
            seed_addr_reg="a1", modulus_reg="a2", seed_offset_scale_reg="a3", target_offset_scale_reg="a4", num_ignore_reg="a5", handler_priv_mode=self.handler_priv_mode
        )
        code += "\tret\n"

        code += self.append_end_test()
        # Append all the functions
        code += self.fn_rand()
        # Append the os_data
        code += self.append_os_data()
        # Append the to_from_host addresses
        code += self.append_to_from_host()
        # Append the csr_rw_jump_table
        code += self.generate_csr_rw_jump_table()
        return code

    def test_passed_failed_labels(self) -> str:
        """Generate passed/failed/end_test labels for test code.

        User mode uses ECALL while other modes load addresses from .text section.
        """

        # Add end of the test passed and failed routines
        # USER mode always needs to do ecall to exit from the discrete_test and muist be placed in user accessible pages
        if self.featmgr.priv_mode == RV.RiscvPrivileges.USER:
            # TODO: Move this from .code to  .section .text_user, "ax"
            # When this change happens, existing tests which jump to passed/failed labels will fail to link since this jump will
            # be beyond the 2GB limit addressable by auipc instructions. Those jumps have to be replaced by a directive which adds
            # "li a0, passed_addr; ld a1, 0(a0); jalr ra, 0(a1);"
            return """
                .section .code
                passed:
                    li x31, 0xf0000001  # Schedule test
                    ecall

                failed:
                    li x31, 0xf0000002  # End test with fail
                    ecall

                end_test:
                    li x31, 0xf0000003 # End test with a pass, in case test is overlong but this was somewhat expected
                    ecall
            """
        else:
            # TODO: Move this from .code to  .section .text
            # When this change happens, existing tests which jump to passed/failed labels will fail to link since this jump will
            # be beyond the 2GB limit addressable by auipc instructions. Those jumps have to be replaced by a directive which adds
            # "li a0, passed_addr; ld a1, 0(a0); jalr ra, 0(a1);"
            return """
                .section .code
                passed:
                    li t0, os_passed_addr
                    ld t1, 0(t0)
                    jr t1

                failed:
                    li t0, os_failed_addr
                    ld t1, 0(t0)
                    jr t1

                end_test:
                    li t0, os_end_test_addr
                    ld t1, 0(t0)
                    jr t1
            """

    def append_end_test(self):
        code = f"""
        .align 6; .global tohost_mutex; tohost_mutex: .dword 0; # Used to protect access to tohost

        tohost_addr_mem:
            .dword tohost

        os_end_test:
        os_write_tohost:

        # each hart increments num_harts_ended so that the first one waits till all harts have finished before writing to tohost
        mark_done:
            li t0, 1
            li t3, num_harts_ended
            amoadd.w x0, t0, (t3)

        # MP Specific, check if gp[31] == 1
        # If so, then we detected a core bailed early. We should not write to tohost
        li t0, 0x80000000
        beq t0, gp, wait_for_dismissal

        # Try to obtain tohost_mutex
        la a0, tohost_mutex
        j tohost_try_lock

        wait_for_dismissal:
            wfi
            j wait_for_dismissal

        tohost_try_lock:
            li t0, 1                    # Initialize swap value.
            ld           t1, (a0)       # Check if lock is held.
            bnez         t1,  wait_for_dismissal        # fail if held.
            amoswap.d.aq t1, t0, (a0)   # Attempt to acquire lock.
            bnez         t1,  wait_for_dismissal        # fail if held

            # obtained lock, no need to release this one since we are ending the simulation.
            li t2, {self.featmgr.num_cpus}
            li t1, num_hard_fails
            li t4, {OpSys.EOT_WAIT_FOR_OTHERS_TIMEOUT} # Timeout for eot waiting

        wait_for_others:
            bltz t4, mark_fail # This is a timeout, other harts didn't finish
            addi t4, t4, -1
            lw t0, (t1)
            bnez t0, mark_fail # Write immediately if there was a hard fail
            lw t0, (t3)
            bne t0, t2, wait_for_others

        j load_tohost_addr

        mark_fail:
            li gp, 0x{self.featmgr.eot_fail_value:x}

        load_tohost_addr:
            ld t0, tohost_addr_mem


        write_to_tohost:
            fence iorw, iorw
            {"sd gp, 0(t0)" if self.featmgr.big_endian else "sw gp, 0(t0)"}
        """

        # If in linux mode then add following
        if self.featmgr.linux_mode:
            code += """
            _exit:
                li a7, 93  # __NR_exit
                li a0, 0   # exit code
                ecall

                j wait_for_dismissal
            """

        if not self.featmgr.fe_tb and not self.featmgr.linux_mode:
            code += """
        _exit:
           j wait_for_dismissal

        """

        # FE_TB needs to have extra instructions after the end of this segment
        if self.featmgr.fe_tb:
            code += """
            ## FE_TB needs to have extra instructions after the end of this segment
            .nop(0x100)
            """

        return code

    # TODO make this able to be used more than once and be dependent on test seed.
    def fn_rand(self):
        code = """
        # Pseudorandom number generator between 0 and 10 using LCG algorithm
        # Seed value
        li a0, 42       # Set initial seed value (can be any value)

        # LCG parameters
        li a1, 1664525  # Multiplier
        li a2, 1013904223  # Increment
        li a3, 2^32     # Modulus (2^32 for a 32-bit pseudorandom number)

        # Generate pseudorandom number
        mul a0, a0, a1   # a0 = a0 * multiplier
        add a0, a0, a2  # a0 = a0 + increment
        rem a0, a0, a3   # a0 = a0 % modulus (remainder)

        # Calculate pseudorandom number between 0 and 10
        li a1, 11        # Maximum value (10 + 1)
        rem a0, a0, a1   # a0 = a0 % maximum value

        ret

        # The pseudorandom number between 0 and 10 will be stored in a0

        """
        return code

    def append_to_from_host(self):
        code = """
        # HTIF is defeined at 0x7000_0000, can be used as a character device so should be read/writeable.
        .section .io_htif, "aw"
        .align 6; .global tohost; tohost: .dword 0;
        .align 6; .global fromhost; fromhost: .dword 0;

        """
        return code

    def append_os_data(self):
        # This function creates a jump table to ensure we can jump from .code to .text regions even if they span > 2GB
        # We use defines in the equates file to provide an immediate value to be used as an address to the required fn ptr
        # caller can do
        #   li t0, passed_addr # where passed_addr is in the equates file pointing to the memory location of the fn ptr
        #   ld t1, 0(t0)
        #   jalr t1

        os_data_section = """
        .section .os_data, "aw"
        # OS data
        """

        for var in self.per_hart_variables:
            os_data_section += f"{var}_mem:\n"
            os_data_section += "\n".join([f"    .dword {self.per_hart_variables[var]}" for _ in range(self.featmgr.num_cpus)]) + "\n"

        for var in self.shared_variables:
            os_data_section += f"{var}_mem:\n"
            os_data_section += f"    .dword {self.shared_variables[var]}\n"

        return os_data_section

    # Use a precalculated and scaled offset stored in an num_harts elements long array to access data from an array with a more exotic layout.
    def place_retrieve_memory_indexed_address(
        self,
        index_array_name: str,
        target_array_name: str,
        index_element_size: int,
        hartid_reg: str,
        work_reg_1: str,
        dest_reg: str,
    ) -> str:
        return f"""
            la {work_reg_1}, {index_array_name}
            {Routines.place_offset_address_by_scaled_hartid(address_reg=work_reg_1, dest_reg=work_reg_1, hartid_reg=hartid_reg, work_reg=dest_reg, scale=index_element_size)}
            ld {dest_reg}, 0({work_reg_1}) # Load the index
            la {work_reg_1}, {target_array_name}
            add {dest_reg}, {work_reg_1}, {dest_reg} # Offset to the correct element
        """

    def generate_equates(self) -> str:
        equates = ""
        # Some OS data hack
        equates += """
            # Test OS data hack:")
            # These symbols contain the addresses of OS variables that can't be LAd directly"
        """
        offset_adder = 0

        def build_text(variable_name, offset):
            return f".equ {variable_name:35}, os_data + {offset}\n"

        for variable_name in self.per_hart_variables:
            equates += build_text(variable_name, offset_adder)
            offset_adder += self.featmgr.num_cpus * OpSys.OS_VARIABLE_SIZE

        for variable_name in self.shared_variables:
            equates += build_text(variable_name, offset_adder)
            offset_adder += OpSys.OS_VARIABLE_SIZE

        equates += "\n"
        return equates

    def generate_csr_rw_jump_table(self) -> str:
        machine_code = ""
        super_code = ""
        end_machine_label = "end_machine_label"
        end_super_label = "end_super_label"

        # Build Machine and Super Jump Tables
        parsed_csr_accesses = self.pool.get_parsed_csr_accesses()
        for csr in parsed_csr_accesses:
            write_csr = parsed_csr_accesses[csr].get("write", None)
            read_csr = parsed_csr_accesses[csr].get("read", None)
            if write_csr:
                if write_csr.priv_mode == "machine":
                    machine_code += "\n"
                    machine_code += f"\t# Machine Write: {csr}, label: {write_csr.label}\n"
                    machine_code += f"{write_csr.label}:\n"
                    machine_code += f"\tcsrw {csr}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif write_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Write: {csr}, label: {write_csr.label}\n"
                    super_code += f"{write_csr.label}:\n"
                    super_code += f"\tcsrw {csr}, t2\n"
                    super_code += f"\tj {end_super_label}\n"

            if read_csr:
                if read_csr.priv_mode == "machine":
                    machine_code += "\n"
                    machine_code += f"\t# Machine Read: {csr}, label: {read_csr.label}\n"
                    machine_code += f"{read_csr.label}:\n"
                    machine_code += f"\tcsrr t2, {csr}\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif read_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Read: {csr}, label: {read_csr.label}\n"
                    super_code += f"{read_csr.label}:\n"
                    super_code += f"\tcsrr t2, {csr}\n"
                    super_code += f"\tj {end_super_label}\n"

        # CSR Machine Jump Table 1
        code = """
        .section .csr_machine_0, "ax"
        li x31, machine_csr_jump_table_flags
        ld x31, 0(x31)

"""
        for csr in parsed_csr_accesses:
            write_csr = parsed_csr_accesses[csr].get("write", None)
            read_csr = parsed_csr_accesses[csr].get("read", None)
            if write_csr:
                if write_csr.priv_mode == "machine":
                    code += f"\tli t0, {write_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {write_csr.label}\n"
            if read_csr:
                if read_csr.priv_mode == "machine":
                    code += f"\tli t0, {read_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {read_csr.label}\n"
        code += f"\tj {end_machine_label}\n"
        code += machine_code

        # CSR Super Jump Table 1
        code += f"""
        {end_machine_label}:
        li x31, 0xf0001004
        ecall
        """

        code += """
        .section .csr_super_0, "ax"
        li x31, super_csr_jump_table_flags
        ld x31, 0(x31)

"""
        for csr in parsed_csr_accesses:
            read_csr = parsed_csr_accesses[csr].get("read", None)
            write_csr = parsed_csr_accesses[csr].get("write", None)
            if read_csr:
                if read_csr.priv_mode == "supervisor":

                    code += f"\tli t0, {read_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {read_csr.label}\n"
            if write_csr:
                if write_csr.priv_mode == "supervisor":
                    code += f"\tli t0, {write_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {write_csr.label}\n"
        code += f"\tj {end_super_label}\n"
        code += super_code

        code += f"""
        {end_super_label}:
        # return to testmode
        li x31, 0xf0001004
        ecall
        """

        return code
