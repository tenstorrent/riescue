# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false

from types import MappingProxyType
from typing import Any, Dict, Optional

import riescue.lib.enums as RV
from riescue.lib.csr_manager.csr_manager_interface import CsrManagerInterface
from riescue.dtest_framework.lib.dtest_instruction_helper import DtestInstructionHelper
from riescue.dtest_framework.parser import ParsedCsrAccess
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class OpSys(AssemblyGenerator):
    """
    Generates "OS" or Riescue Runtime code for the test. This includes

    - Test Pass/Fail jump table
    - CSR jump tables
    - Runtime Variable generation
    - End of Test routines
    """

    # static pointers to OS functions or routines
    POINTERS = MappingProxyType(
        {
            # passed, failed, and end_test are legacy pointers to a jump table entry.
            # Tests should use ;#test_passed() and ;#test_failed() instead
            # These will stay to support legacy tests that jump to these addresses directly.
            "passed_addr": "passed",
            "failed_addr": "failed",
            "end_test_addr": "end_test",
            # Pointers to test passed and failed routines.
            # ;#test_passed() and #;test_failed() li and jalr to these addresses
            # syscall table also jumps to these addressses to support tests running in user mode
            "os_passed_addr": "test_passed",
            "os_failed_addr": "test_failed",
            "os_end_test_addr": "os_end_test",
        }
    )

    EOT_WAIT_FOR_OTHERS_TIMEOUT = 500000
    MAX_OS_BARRIER_TRIES = 50000

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if self.featmgr.linux_mode and self.mp_active:
            raise ValueError("Linux mode and MP mode are not supported together")

        # Build OS Data variables
        self.machine_csr_jump_table_flags = self.variable_manager.register_shared_variable("machine_csr_jump_table_flags", 0x0)
        self.super_csr_jump_table_flags = self.variable_manager.register_shared_variable("super_csr_jump_table_flags", 0x0)
        self.pte_access_flags = self.variable_manager.register_shared_variable("pte_access_flags", 0x0, element_count=3)

        if self.mp_active:
            self.variable_manager.register_shared_variable("barrier_arrive_counter", 0x0)
            self.variable_manager.register_shared_variable("barrier_flag", 0x0)
            self.variable_manager.register_shared_variable("barrier_lock", 0x0)
            self.variable_manager.register_shared_variable("hartid_counter", 0x0)
            self.variable_manager.register_shared_variable("num_harts", self.featmgr.num_cpus)
            self.variable_manager.register_shared_variable("barrier_depart_counter", self.featmgr.num_cpus)

        # Runtime Pointers
        self.runtime_pointers: dict[str, str] = dict(OpSys.POINTERS)
        if self.featmgr.excp_hooks:
            self.runtime_pointers["excp_handler_pre_addr"] = "excp_handler_pre"
            self.runtime_pointers["excp_handler_post_addr"] = "excp_handler_post"

    def generate(self) -> str:
        code = ""
        code += self.generate_os()

        return code

    def generate_os(self) -> str:
        code = ""
        code += self.test_passed_failed_labels()

        if self.featmgr.selfcheck:
            selfcheck_code = f"""
                {self.save_gprs(self.scratch_reg)}
                jal selfcheck__save_or_check
            """
        else:
            selfcheck_code = ""

        # For M-mode paging, MPRV=1 is active in test code (data accesses use S-mode translation).
        # When returning to the runtime/scheduler, we must clear MPRV so that the runtime can
        # access data using bare physical addresses (e.g. hart context via mscratch PA).
        # MPRV is re-enabled in scheduler's execute_test() and trap_handler's trap_exit().
        clear_mprv = ""
        if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE and self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
            clear_mprv = """
                li t0, (1 << 17)    # Clear MPRV
                csrrc x0, mstatus, t0
            """

        # Generate RVCP pass call for enter_scheduler (syscall path)
        # Only print for discrete tests (skip test_setup and test_cleanup)
        enter_scheduler_rvcp = ""
        if self.featmgr.print_rvcp_passed:
            enter_scheduler_rvcp = """
            li a0, 0  # 0 = PASS
            call rvcp_print_test_result
            """

        code += f"""
        .section .runtime, "ax"

        enter_scheduler:
            {clear_mprv}
            # Check if t0 has a pass or fail condition
            li t1, 0xbaadc0de
            beq t0, t1, test_failed
            {enter_scheduler_rvcp}
            j {self.scheduler_dispatch_label}

        """

        # Generate RVCP pass call for test_passed
        rvcp_pass_call = ""
        if self.featmgr.print_rvcp_passed:
            rvcp_pass_call = """
                li a0, 0  # 0 = PASS
                call rvcp_print_test_result
            """

        if self.featmgr.fe_tb:
            code += f"""
            # With FE testbench, we can't jump to failed even in the bad path since they can take that
            # path and end the test prematurely. So, make failed==passed label
            test_failed:
            test_passed:
            {clear_mprv}
            {self.variable_manager.enter_hart_context(scratch=self.scratch_reg)}
            {rvcp_pass_call}
            {selfcheck_code}
            j {self.scheduler_dispatch_label}
            os_end_test:
                {clear_mprv}
                {self.variable_manager.enter_hart_context(scratch=self.scratch_reg)}
                li gp, 0x1
                j eot__end_test
            """
        else:
            code += f"""
            test_passed:
                {clear_mprv}
                {self.variable_manager.enter_hart_context(scratch=self.scratch_reg)}
                {rvcp_pass_call}
                {selfcheck_code}
                j {self.scheduler_dispatch_label}

            test_failed:
                {clear_mprv}
                {self.variable_manager.enter_hart_context(scratch=self.scratch_reg)}
                li gp, 0x3
                j eot__end_test

            os_end_test:
                {clear_mprv}
                {self.variable_manager.enter_hart_context(scratch=self.scratch_reg)}
                li gp, 0x1
                j eot__end_test
            """

        # Add RVCP print routine if enabled
        code += self._rvcp_print_routine()

        # FIXME: This is linux mode only, and linux mode is mp mode only. Does this need to be in OpSys? Or should it be in LinuxModeScheduler?
        code += "os_rng_orig:\n"
        code += Routines.place_rng_unsafe_reg(
            seed_addr_reg="a1",
            modulus_reg="a2",
            seed_offset_scale_reg="a3",
            target_offset_scale_reg="a4",
            num_ignore_reg="a5",
            handler_priv_mode=RV.RiscvPrivileges.MACHINE,
            mhartid_offset=self.variable_manager.get_variable("mhartid").offset,
        )
        code += "\tret\n"

        # Append all the functions
        code += self.fn_rand()
        # Append the os_data
        code += self.append_os_data()
        # Append the to_from_host addresses
        code += self.generate_csr_rw_jump_table()
        return code

    def _rvcp_print_routine(self) -> str:
        """Generate RVCP print routine that prints pass/fail messages via RVMODEL_IO_WRITE_STR macro.

        This routine is called from enter_scheduler (pass) and eot (fail) to print
        RVCP-formatted messages that identify which discrete test passed or failed.

        Format: "RVCP: Test File {testname} {discrete_test_name} PASSED/FAILED\\n"

        Entry: a0 = 0 for PASS, 1 for FAIL
        Uses only t-registers (no stack): t0-t5
        """
        if not self.featmgr.rvcp_print_enabled():
            return ""

        current_test_index = self.variable_manager.get_variable("current_test_index")
        return f"""
.section .runtime, "ax"
.balign 4
rvcp_print_test_result:
    # Entry: a0 = 0 for PASS, 1 for FAIL
    # Format: "RVCP: Test File {{testname}} {{discrete_test}} PASSED/FAILED\\n"
    # Uses t-registers: t0-t5, a0 preserved as pass/fail flag via t3
    mv t3, a0  # t3 = pass/fail flag (0=PASS, 1=FAIL)

    # Load current test index from hart-local variable
    {current_test_index.load(dest_reg='t0')}

    # Check for special indices - should not be called for setup/cleanup
    li t1, 0xFFFFFFFE
    beq t0, t1, .Lrvcp_skip_print  # test_setup - skip
    li t1, 0xFFFFFFFF
    beq t0, t1, .Lrvcp_skip_print  # test_cleanup - skip
    bltz t0, .Lrvcp_skip_print     # negative = invalid - skip

    # Get test name from descriptor table
    # t0 = index, descriptor table has pointers to name strings
    la t1, dtest_descriptor_table
    slli t2, t0, 3  # index * 8
    add t1, t1, t2
    ld t4, 0(t1)    # t4 = pointer to discrete test name string

    # Print "RVCP: Test File " prefix
    li t5, rvcp_prefix_str_pa
    RVMODEL_IO_WRITE_STR(t0, t1, t2, t5)

    # Print testname (test file name)
    li t5, rvcp_testname_str_pa
    RVMODEL_IO_WRITE_STR(t0, t1, t2, t5)

    # Print " " separator between testname and discrete test name
    li t5, rvcp_space_str_pa
    RVMODEL_IO_WRITE_STR(t0, t1, t2, t5)

    # Print discrete test name (dynamic pointer from t4)
    mv t5, t4
    RVMODEL_IO_WRITE_STR(t0, t1, t2, t5)

    # Print " " separator
    li t5, rvcp_space_str_pa
    RVMODEL_IO_WRITE_STR(t0, t1, t2, t5)

    # Print PASSED or FAILED
    bnez t3, .Lrvcp_print_fail_str
    li t5, rvcp_passed_str_pa
    j .Lrvcp_print_status_done
.Lrvcp_print_fail_str:
    li t5, rvcp_failed_str_pa
.Lrvcp_print_status_done:
    RVMODEL_IO_WRITE_STR(t0, t1, t2, t5)

    # Print newline
    li t5, rvcp_newline_str_pa
    RVMODEL_IO_WRITE_STR(t0, t1, t2, t5)

.Lrvcp_skip_print:
    ret
"""

    def test_passed_failed_labels(self) -> str:
        """Generate passed/failed/end_test labels for test code.

        User mode uses ECALL while other modes load addresses from .runtime section.
        """
        # Add end of the test passed and failed routines
        # USER mode always needs to do ecall to exit from the discrete_test and muist be placed in user accessible pages
        if self.test_priv != RV.RiscvPrivileges.MACHINE:
            # TODO: Move this from .code to  .section .runtime_user, "ax"
            # When this change happens, existing tests which jump to passed/failed labels will fail to link since this jump will
            # be beyond the 2GB limit addressable by auipc instructions. Those jumps have to be replaced by a directive which adds
            # "li a0, passed_addr; ld a1, 0(a0); jalr ra, 0(a1);"
            return """
                .section .code, "ax"
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
            # TODO: Move this from .code to  .section .runtime
            # When this change happens, existing tests which jump to passed/failed labels will fail to link since this jump will
            # be beyond the 2GB limit addressable by auipc instructions. Those jumps have to be replaced by a directive which adds
            # "li a0, passed_addr; ld a1, 0(a0); jalr ra, 0(a1);"
            # M-mode with paging uses MPRV=1 (data accesses go through S-mode translation),
            # so use VA equates. Without paging, use PA equates (bare addressing).
            suffix = "" if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE else "_pa"
            return f"""
                .section .code, "ax"
                passed:
                    li t0, os_passed_addr{suffix}
                    ld t1, 0(t0)
                    jr t1

                failed:
                    li t0, os_failed_addr{suffix}
                    ld t1, 0(t0)
                    jr t1

                end_test:
                    li t0, os_end_test_addr{suffix}
                    ld t1, 0(t0)
                    jr t1
            """

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

    def append_os_data(self):
        # This function creates a jump table to ensure we can jump from .code to .runtime regions even if they span > 2GB
        # We use defines in the equates file to provide an immediate value to be used as an address to the required fn ptr
        # caller can do
        #   li t0, passed_addr # where passed_addr is in the equates file pointing to the memory location of the fn ptr
        #   ld t1, 0(t0)
        #   jalr t1

        if self.xlen == RV.Xlen.XLEN64:
            var_type = "dword"
        else:
            var_type = "word"

        os_data_section = """
        .section .os_data, "aw"
        """

        os_data_section += "\n# OS Pointers\n"
        for var in self.runtime_pointers:
            os_data_section += f"{var}_ptr:\n"
            os_data_section += f"    .{var_type} {self.runtime_pointers[var]}\n"

        intr_ptrs = self.pool.get_interrupt_handler_pointers()
        if intr_ptrs:
            os_data_section += "\n# Interrupt handler pointers (per-segment custom handlers)\n"
            for ptr_equate, ptr_label in intr_ptrs.items():
                os_data_section += f"{ptr_equate}_mem:\n"
                os_data_section += f"    .{var_type} {ptr_label}\n"

        # RVCP strings (for HTIF console output) - before variable manager
        if self.featmgr.rvcp_print_enabled():
            os_data_section += f"""
# RVCP message strings
# Format: "RVCP: Test File {{testname}} {{discrete_test}} PASSED/FAILED\\n"
rvcp_prefix_str_data: .asciz "RVCP: Test File "
rvcp_testname_str_data: .asciz "{self.pool.testname}"
rvcp_passed_str_data: .asciz "PASSED"
rvcp_failed_str_data: .asciz "FAILED"
rvcp_space_str_data: .asciz " "
rvcp_newline_str_data: .asciz "\\n"
.balign 8
"""

        os_data_section += self.variable_manager.allocate()
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
        offset_adder = 0
        if self.xlen == RV.Xlen.XLEN64:
            os_variable_size = 8
        else:
            os_variable_size = 4

        def build_text(variable_name: str, offset: int) -> str:
            va_equ = f".equ {variable_name},"
            pa_equ = f".equ {variable_name}_pa,"
            return f"{va_equ:<40} os_data + {offset}\n{pa_equ:<40} os_data_pa + {offset}\n"

        equates += "\n"
        equates += "\n# OS Pointers\n"
        for variable_name in self.runtime_pointers:
            equates += build_text(variable_name, offset_adder)
            offset_adder += os_variable_size

        intr_ptrs = self.pool.get_interrupt_handler_pointers()
        if intr_ptrs:
            equates += "\n# Interrupt handler pointers (per-segment custom handlers)\n"
            for ptr_equate in intr_ptrs:
                equates += build_text(ptr_equate, offset_adder)
                offset_adder += os_variable_size

        # RVCP string equates (for M-mode access) - before variable manager
        if self.featmgr.rvcp_print_enabled():
            rvcp_prefix = "RVCP: Test File "
            rvcp_passed = "PASSED"
            rvcp_failed = "FAILED"
            rvcp_space = " "
            rvcp_newline = "\n"
            equates += "\n# RVCP string equates\n"
            equates += build_text("rvcp_prefix_str", offset_adder)
            offset_adder += len(rvcp_prefix) + 1  # string + null
            equates += build_text("rvcp_testname_str", offset_adder)
            offset_adder += len(self.pool.testname) + 1  # testname + null
            equates += build_text("rvcp_passed_str", offset_adder)
            offset_adder += len(rvcp_passed) + 1  # string + null
            equates += build_text("rvcp_failed_str", offset_adder)
            offset_adder += len(rvcp_failed) + 1  # string + null
            equates += build_text("rvcp_space_str", offset_adder)
            offset_adder += len(rvcp_space) + 1  # string + null
            equates += build_text("rvcp_newline_str", offset_adder)
            offset_adder += len(rvcp_newline) + 1  # string + null
            # Align to 8 bytes (matches .balign 8 in os_data)
            offset_adder = (offset_adder + 7) & ~7

        equates += self.variable_manager.equates(offset=offset_adder)

        # Generate per-hart PA equates for hart context
        equates += self.variable_manager.hart_context_pa_equates()

        if not self.mp_active:
            # need to include variable equates for non-mp mode, that way users can read/write hart variables
            equates += self.variable_manager.single_hart_variable_equates()

        return equates

    def _get_csr_manager(self) -> CsrManagerInterface:
        if self.csr_manager is None:
            self.csr_manager = CsrManagerInterface(self.rng)
        return self.csr_manager

    def _resolve_csr_config(self, csr_spec: str) -> Optional[Dict[str, Any]]:
        csr_mgr = self._get_csr_manager()
        if csr_spec.startswith("0x") or csr_spec.startswith("0X") or (csr_spec.isdigit() and not csr_spec.startswith("0")):
            addr = int(csr_spec, 0) & 0xFFF
            return csr_mgr.lookup_csr_by_address(addr)
        return csr_mgr.lookup_csr_by_name(str(csr_spec))

    def _get_csr_name_for_asm(self, csr_spec: str, csr_config: Optional[Dict[str, Any]]) -> str:
        """Return CSR name for assembly; use lowercase (assembler expects tselect, tdata1, etc.)."""
        name: str = next(iter(csr_config.keys())) if csr_config else csr_spec
        return name.lower()

    def generate_csr_rw_jump_table(self) -> str:
        machine_code = ""
        super_code = ""
        end_machine_label = "end_machine_label"
        end_super_label = "end_super_label"

        # Build Machine and Super Jump Tables
        parsed_csr_accesses = self.pool.get_parsed_csr_accesses()

        def _get_entries(entries: dict[str, ParsedCsrAccess], key: str) -> list[ParsedCsrAccess]:
            """Get both base and _force_machine entries for a key."""
            return list(filter(None, [entries.get(key), entries.get(f"{key}_force_machine")]))

        for csr in parsed_csr_accesses:
            # Collect all field-qualified write_subfield_*/read_subfield_* entries
            write_subfield_csrs = [v for k, v in parsed_csr_accesses[csr].items() if k.startswith("write_subfield")]
            read_subfield_csrs = [v for k, v in parsed_csr_accesses[csr].items() if k.startswith("read_subfield")]
            csr_config = self._resolve_csr_config(csr)
            csr_name = self._get_csr_name_for_asm(csr, csr_config)

            for write_csr in _get_entries(parsed_csr_accesses[csr], "write"):
                if write_csr.priv_mode == "machine" or write_csr.force_machine_rw:
                    machine_code += "\n"
                    machine_code += f"\t# Machine Write: {csr_name}, label: {write_csr.label}\n"
                    machine_code += f"{write_csr.label}:\n"
                    machine_code += f"\tcsrw {csr_name}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif write_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Write: {csr_name}, label: {write_csr.label}\n"
                    super_code += f"{write_csr.label}:\n"
                    super_code += f"\tcsrw {csr_name}, t2\n"
                    super_code += f"\tj {end_super_label}\n"

            for read_csr in _get_entries(parsed_csr_accesses[csr], "read"):
                if read_csr.priv_mode == "machine" or read_csr.force_machine_rw:
                    machine_code += "\n"
                    machine_code += f"\t# Machine Read: {csr_name}, label: {read_csr.label}\n"
                    machine_code += f"{read_csr.label}:\n"
                    machine_code += f"\tcsrr t2, {csr_name}\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif read_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Read: {csr_name}, label: {read_csr.label}\n"
                    super_code += f"{read_csr.label}:\n"
                    super_code += f"\tcsrr t2, {csr_name}\n"
                    super_code += f"\tj {end_super_label}\n"

            for set_csr in _get_entries(parsed_csr_accesses[csr], "set"):
                if set_csr.priv_mode == "machine" or set_csr.force_machine_rw:
                    machine_code += "\n"
                    machine_code += f"\t# Machine Set: {csr_name}, label: {set_csr.label}\n"
                    machine_code += f"{set_csr.label}:\n"
                    machine_code += f"\tcsrs {csr_name}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif set_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Set: {csr_name}, label: {set_csr.label}\n"
                    super_code += f"{set_csr.label}:\n"
                    super_code += f"\tcsrs {csr_name}, t2\n"
                    super_code += f"\tj {end_super_label}\n"
            for clear_csr in _get_entries(parsed_csr_accesses[csr], "clear"):
                if clear_csr.priv_mode == "machine" or clear_csr.force_machine_rw:
                    machine_code += "\n"
                    machine_code += f"\t# Machine Clear: {csr_name}, label: {clear_csr.label}\n"
                    machine_code += f"{clear_csr.label}:\n"
                    machine_code += f"\tcsrc {csr_name}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif clear_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Clear: {csr_name}, label: {clear_csr.label}\n"
                    super_code += f"{clear_csr.label}:\n"
                    super_code += f"\tcsrc {csr_name}, t2\n"
                    super_code += f"\tj {end_super_label}\n"

            for set_bit_csr in _get_entries(parsed_csr_accesses[csr], "set_bit"):
                if set_bit_csr.priv_mode == "machine" or set_bit_csr.force_machine_rw:
                    machine_code += "\n"
                    machine_code += f"\t# Machine SetBit: {csr_name}, label: {set_bit_csr.label}\n"
                    machine_code += f"{set_bit_csr.label}:\n"
                    machine_code += f"\tcsrs {csr_name}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif set_bit_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor SetBit: {csr_name}, label: {set_bit_csr.label}\n"
                    super_code += f"{set_bit_csr.label}:\n"
                    super_code += f"\tcsrs {csr_name}, t2\n"
                    super_code += f"\tj {end_super_label}\n"
            for clear_bit_csr in _get_entries(parsed_csr_accesses[csr], "clear_bit"):
                if clear_bit_csr.priv_mode == "machine" or clear_bit_csr.force_machine_rw:
                    machine_code += "\n"
                    machine_code += f"\t# Machine ClearBit: {csr_name}, label: {clear_bit_csr.label}\n"
                    machine_code += f"{clear_bit_csr.label}:\n"
                    machine_code += f"\tcsrc {csr_name}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif clear_bit_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor ClearBit: {csr_name}, label: {clear_bit_csr.label}\n"
                    super_code += f"{clear_bit_csr.label}:\n"
                    super_code += f"\tcsrc {csr_name}, t2\n"
                    super_code += f"\tj {end_super_label}\n"

            for write_subfield_csr in write_subfield_csrs:
                instr_helper = DtestInstructionHelper(exclude=["t2"])
                assert write_subfield_csr.field is not None
                subfield = {write_subfield_csr.field: ""}
                try:
                    if csr_config:
                        asm = self._get_csr_manager().csr_access(
                            instr_helper,
                            "write_subfield",
                            csr_config,
                            rs="t2",
                            rd="t2",
                            subfield=subfield,
                            value_in_reg=True,
                        )
                        if write_subfield_csr.priv_mode == "machine" or write_subfield_csr.force_machine_rw:
                            machine_code += "\n"
                            machine_code += f"\t# Machine WriteSubfield: {csr_name}.{write_subfield_csr.field}, label: {write_subfield_csr.label}\n"
                            machine_code += f"{write_subfield_csr.label}:\n"
                            machine_code += "\t" + asm.replace("\n", "\n\t").strip() + "\n"
                            machine_code += f"\tj {end_machine_label}\n"
                        elif write_subfield_csr.priv_mode == "supervisor":
                            super_code += "\n"
                            super_code += f"\t# Supervisor WriteSubfield: {csr_name}.{write_subfield_csr.field}, label: {write_subfield_csr.label}\n"
                            super_code += f"{write_subfield_csr.label}:\n"
                            super_code += "\t" + asm.replace("\n", "\n\t").strip() + "\n"
                            super_code += f"\tj {end_super_label}\n"
                    else:
                        raise ValueError("No csr_config for write_subfield")
                except Exception as e:
                    raise ValueError(f"write_subfield for {csr_name}.{write_subfield_csr.field} requires csr_config: {e}") from e

            for read_subfield_csr in read_subfield_csrs:
                instr_helper = DtestInstructionHelper()
                assert read_subfield_csr.field is not None
                subfield = {read_subfield_csr.field: "0"}
                try:
                    if csr_config:
                        asm = self._get_csr_manager().csr_access(
                            instr_helper,
                            "read_subfield",
                            csr_config,
                            rd="t2",
                            subfield=subfield,
                        )
                        if read_subfield_csr.priv_mode == "machine" or read_subfield_csr.force_machine_rw:
                            machine_code += "\n"
                            machine_code += f"\t# Machine ReadSubfield: {csr_name}.{read_subfield_csr.field}, label: {read_subfield_csr.label}\n"
                            machine_code += f"{read_subfield_csr.label}:\n"
                            machine_code += "\t" + asm.replace("\n", "\n\t").strip() + "\n"
                            machine_code += f"\tj {end_machine_label}\n"
                        elif read_subfield_csr.priv_mode == "supervisor":
                            super_code += "\n"
                            super_code += f"\t# Supervisor ReadSubfield: {csr_name}.{read_subfield_csr.field}, label: {read_subfield_csr.label}\n"
                            super_code += f"{read_subfield_csr.label}:\n"
                            super_code += "\t" + asm.replace("\n", "\n\t").strip() + "\n"
                            super_code += f"\tj {end_super_label}\n"
                    else:
                        raise ValueError("No csr_config for read_subfield")
                except Exception:
                    if read_subfield_csr.priv_mode == "machine" or read_subfield_csr.force_machine_rw:
                        machine_code += "\n"
                        machine_code += f"\t# Machine ReadSubfield (fallback): {csr_name}, label: {read_subfield_csr.label}\n"
                        machine_code += f"{read_subfield_csr.label}:\n"
                        machine_code += f"\tcsrr t2, {csr_name}\n"
                        machine_code += f"\tj {end_machine_label}\n"
                    elif read_subfield_csr.priv_mode == "supervisor":
                        super_code += "\n"
                        super_code += f"\t# Supervisor ReadSubfield (fallback): {csr_name}, label: {read_subfield_csr.label}\n"
                        super_code += f"{read_subfield_csr.label}:\n"
                        super_code += f"\tcsrr t2, {csr_name}\n"
                        super_code += f"\tj {end_super_label}\n"

        # CSR Machine Jump Table 1
        code = f"""
        .section .csr_machine_0, "ax"
        {self.machine_csr_jump_table_flags.load("x31")}

"""
        for csr in parsed_csr_accesses:
            write_subfield_csrs = [v for k, v in parsed_csr_accesses[csr].items() if k.startswith("write_subfield")]
            read_subfield_csrs = [v for k, v in parsed_csr_accesses[csr].items() if k.startswith("read_subfield")]
            for op in ["write", "read", "set", "clear", "set_bit", "clear_bit"]:
                for entry in _get_entries(parsed_csr_accesses[csr], op):
                    if entry.priv_mode == "machine" or entry.force_machine_rw:
                        code += f"\tli t0, {entry.csr_id}\n"
                        code += f"\tbeq x31, t0, {entry.label}\n"
            for write_subfield_csr in write_subfield_csrs:
                if write_subfield_csr.priv_mode == "machine" or write_subfield_csr.force_machine_rw:
                    code += f"\tli t0, {write_subfield_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {write_subfield_csr.label}\n"
            for read_subfield_csr in read_subfield_csrs:
                if read_subfield_csr.priv_mode == "machine" or read_subfield_csr.force_machine_rw:
                    code += f"\tli t0, {read_subfield_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {read_subfield_csr.label}\n"
        code += f"\tj {end_machine_label}\n"
        code += machine_code

        # CSR Super Jump Table 1
        code += f"""
        {end_machine_label}:
        li x31, 0xf0001004
        ecall
        """

        code += f"""
        .section .csr_super_0, "ax"
        {self.super_csr_jump_table_flags.load("x31", bare=False)}

"""
        for csr in parsed_csr_accesses:
            write_subfield_csrs = [v for k, v in parsed_csr_accesses[csr].items() if k.startswith("write_subfield")]
            read_subfield_csrs = [v for k, v in parsed_csr_accesses[csr].items() if k.startswith("read_subfield")]
            for op in ["read", "write", "set", "clear", "set_bit", "clear_bit"]:
                for entry in _get_entries(parsed_csr_accesses[csr], op):
                    if entry.priv_mode == "supervisor" and not entry.force_machine_rw:
                        code += f"\tli t0, {entry.csr_id}\n"
                        code += f"\tbeq x31, t0, {entry.label}\n"
            for write_subfield_csr in write_subfield_csrs:
                if write_subfield_csr.priv_mode == "supervisor" and not write_subfield_csr.force_machine_rw:
                    code += f"\tli t0, {write_subfield_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {write_subfield_csr.label}\n"
            for read_subfield_csr in read_subfield_csrs:
                if read_subfield_csr.priv_mode == "supervisor" and not read_subfield_csr.force_machine_rw:
                    code += f"\tli t0, {read_subfield_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {read_subfield_csr.label}\n"
        code += f"\tj {end_super_label}\n"
        code += super_code

        code += f"""
        {end_super_label}:
        # return to testmode
        li x31, 0xf0001004
        ecall
        """

        return code
