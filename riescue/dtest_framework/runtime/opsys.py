# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from types import MappingProxyType
from typing import Any

import riescue.lib.enums as RV
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
        self.machine_leaf_pte_jump_table_flags = self.variable_manager.register_shared_variable("machine_leaf_pte_jump_table_flags", 0x0)

        if self.mp_active:
            self.variable_manager.register_shared_variable("barrier_arrive_counter", 0x0)
            self.variable_manager.register_shared_variable("barrier_flag", 0x0)
            self.variable_manager.register_shared_variable("barrier_lock", 0x0)
            self.variable_manager.register_shared_variable("hartid_counter", 0x0)
            self.variable_manager.register_shared_variable("num_harts", self.featmgr.num_cpus)
            self.variable_manager.register_shared_variable("barrier_depart_counter", self.featmgr.num_cpus)

        # Runtime Pointers
        self.runtime_pointers: dict[str, str] = dict(OpSys.POINTERS)
        if self.featmgr.user_interrupt_table:
            self.runtime_pointers["user_interrupt_table_addr"] = "USER_INTERRUPT_TABLE"
        if self.featmgr.excp_hooks:
            self.runtime_pointers["excp_handler_pre_addr"] = "excp_handler_pre"
            self.runtime_pointers["excp_handler_post_addr"] = "excp_handler_post"
        if self.featmgr.vmm_hooks:
            self.runtime_pointers["vmm_handler_pre_addr"] = "vmm_handler_pre"
            self.runtime_pointers["vmm_handler_post_addr"] = "vmm_handler_post"

    def generate(self) -> str:
        code = ""
        code += self.generate_os()

        return code

    def generate_os(self) -> str:
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
            code += f"""
            # With FE testbench, we can't jump to failed even in the bad path since they can take that
            # path and end the test prematurely. So, make failed==passed label
            test_failed:
            test_passed:
            j {self.scheduler_dispatch_label}
            os_end_test:
                li gp, 0x1
                j eot__end_test
            """
        else:
            code += f"""
            test_passed:
                j {self.scheduler_dispatch_label}

            test_failed:
                li gp, 0x3
                j eot__end_test

            os_end_test:
                li gp, 0x1
                j eot__end_test
            """

        # FIXME: This is linux mode only, and linux mode is mp mode only. Does this need to be in OpSys? Or should it be in LinuxModeScheduler?
        code += "os_rng_orig:\n"
        code += Routines.place_rng_unsafe_reg(
            seed_addr_reg="a1",
            modulus_reg="a2",
            seed_offset_scale_reg="a3",
            target_offset_scale_reg="a4",
            num_ignore_reg="a5",
            handler_priv_mode=self.handler_priv,
            mhartid_offset=self.variable_manager.get_variable("mhartid").offset,
        )
        code += "\tret\n"

        # Append all the functions
        code += self.fn_rand()
        # Append the os_data
        code += self.append_os_data()
        # Append the to_from_host addresses
        code += self.generate_csr_rw_jump_table()
        # Append the leaf_pte_jump_table
        code += self.generate_leaf_pte_jump_table()
        return code

    def test_passed_failed_labels(self) -> str:
        """Generate passed/failed/end_test labels for test code.

        User mode uses ECALL while other modes load addresses from .text section.
        """
        # Add end of the test passed and failed routines
        # USER mode always needs to do ecall to exit from the discrete_test and muist be placed in user accessible pages
        if self.test_priv != self.handler_priv:
            # TODO: Move this from .code to  .section .text_user, "ax"
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
            # TODO: Move this from .code to  .section .text
            # When this change happens, existing tests which jump to passed/failed labels will fail to link since this jump will
            # be beyond the 2GB limit addressable by auipc instructions. Those jumps have to be replaced by a directive which adds
            # "li a0, passed_addr; ld a1, 0(a0); jalr ra, 0(a1);"
            return """
                .section .code, "ax"
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
        # This function creates a jump table to ensure we can jump from .code to .text regions even if they span > 2GB
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
            equ = f".equ {variable_name},"
            return f"{equ:<40} os_data + {offset}\n"

        equates += "\n"
        equates += "\n# OS Pointers\n"
        for variable_name in self.runtime_pointers:
            equates += build_text(variable_name, offset_adder)
            offset_adder += os_variable_size

        equates += self.variable_manager.equates(offset=offset_adder)
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
            set_csr = parsed_csr_accesses[csr].get("set", None)
            clear_csr = parsed_csr_accesses[csr].get("clear", None)
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

            if set_csr:
                if set_csr.priv_mode == "machine":
                    machine_code += "\n"
                    machine_code += f"\t# Machine Set: {csr}, label: {set_csr.label}\n"
                    machine_code += f"{set_csr.label}:\n"
                    machine_code += f"\tcsrs {csr}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                elif set_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Set: {csr}, label: {set_csr.label}\n"
                    super_code += f"{set_csr.label}:\n"
                    super_code += f"\tcsrs {csr}, t2\n"
                    super_code += f"\tj {end_super_label}\n"
            if clear_csr:
                if clear_csr.priv_mode == "machine":
                    machine_code += "\n"
                    machine_code += f"\t# Machine Clear: {csr}, label: {clear_csr.label}\n"
                    machine_code += f"{clear_csr.label}:\n"
                    machine_code += f"\tcsrc {csr}, t2\n"
                    machine_code += f"\tj {end_machine_label}\n"
                if clear_csr.priv_mode == "supervisor":
                    super_code += "\n"
                    super_code += f"\t# Supervisor Clear: {csr}, label: {clear_csr.label}\n"
                    super_code += f"{clear_csr.label}:\n"
                    super_code += f"\tcsrc {csr}, t2\n"
                    super_code += f"\tj {end_super_label}\n"

        # CSR Machine Jump Table 1
        code = f"""
        .section .csr_machine_0, "ax"
        {self.machine_csr_jump_table_flags.load("x31")}

"""
        for csr in parsed_csr_accesses:
            write_csr = parsed_csr_accesses[csr].get("write", None)
            read_csr = parsed_csr_accesses[csr].get("read", None)
            set_csr = parsed_csr_accesses[csr].get("set", None)
            clear_csr = parsed_csr_accesses[csr].get("clear", None)
            if write_csr:
                if write_csr.priv_mode == "machine":
                    code += f"\tli t0, {write_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {write_csr.label}\n"
            if read_csr:
                if read_csr.priv_mode == "machine":
                    code += f"\tli t0, {read_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {read_csr.label}\n"
            if set_csr:
                if set_csr.priv_mode == "machine":
                    code += f"\tli t0, {set_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {set_csr.label}\n"
            if clear_csr:
                if clear_csr.priv_mode == "machine":
                    code += f"\tli t0, {clear_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {clear_csr.label}\n"
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
        {self.super_csr_jump_table_flags.load("x31")}

"""
        for csr in parsed_csr_accesses:
            read_csr = parsed_csr_accesses[csr].get("read", None)
            write_csr = parsed_csr_accesses[csr].get("write", None)
            set_csr = parsed_csr_accesses[csr].get("set", None)
            clear_csr = parsed_csr_accesses[csr].get("clear", None)
            if read_csr:
                if read_csr.priv_mode == "supervisor":

                    code += f"\tli t0, {read_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {read_csr.label}\n"
            if write_csr:
                if write_csr.priv_mode == "supervisor":
                    code += f"\tli t0, {write_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {write_csr.label}\n"
            if set_csr:
                if set_csr.priv_mode == "supervisor":
                    code += f"\tli t0, {set_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {set_csr.label}\n"
            if clear_csr:
                if clear_csr.priv_mode == "supervisor":
                    code += f"\tli t0, {clear_csr.csr_id}\n"
                    code += f"\tbeq x31, t0, {clear_csr.label}\n"
        code += f"\tj {end_super_label}\n"
        code += super_code

        code += f"""
        {end_super_label}:
        # return to testmode
        li x31, 0xf0001004
        ecall
        """

        return code

    def generate_leaf_pte_jump_table(self) -> str:
        """Generate jump table for reading leaf PTEs based on virtual address and paging mode."""
        machine_code = ""
        end_machine_label = "end_machine_leaf_pte_label"

        # Build Machine Jump Table for Leaf PTE reads
        parsed_leaf_ptes = self.pool.get_parsed_leaf_ptes()

        for (lin_name, paging_mode), leaf_pte in parsed_leaf_ptes.items():
            paging_mode = paging_mode.lower()
            label = leaf_pte.label

            # Generate page table walk code for this specific paging mode
            machine_code += "\n"
            machine_code += f"\t# Read Leaf PTE: {lin_name} in {paging_mode}, label: {label}\n"
            machine_code += f"{label}:\n"

            # Load virtual address from lin_name label into x31
            machine_code += f"\tli x31, {lin_name}\n"

            # Read satp to get root page table base
            machine_code += "\tcsrr t2, satp\n"
            machine_code += "\tslli t2, t2, 20\n"  # Shift left to clear MODE (bits 63:60) and ASID (bits 59:44)
            machine_code += "\tsrli t2, t2, 20\n"  # Shift right to get PPN in bits 43:0
            machine_code += "\tslli t2, t2, 12\n"  # Shift left by 12 to convert PPN to physical address

            # Determine number of levels based on paging mode
            if paging_mode == "sv39":
                levels = [(38, 30), (29, 21), (20, 12)]  # 3 levels
            elif paging_mode == "sv48":
                levels = [(47, 39), (38, 30), (29, 21), (20, 12)]  # 4 levels
            elif paging_mode == "sv57":
                levels = [(56, 48), (47, 39), (38, 30), (29, 21), (20, 12)]  # 5 levels
            else:
                # Default to sv39
                levels = [(38, 30), (29, 21), (20, 12)]

            # Walk through each level
            for level_idx, (vpn_hi, vpn_lo) in enumerate(levels):
                vpn_bits = vpn_hi - vpn_lo + 1
                vpn_mask = (1 << vpn_bits) - 1

                machine_code += f"\n\t# Level {level_idx}: VPN[{vpn_hi}:{vpn_lo}]\n"

                # Extract VPN for this level
                machine_code += "\tmv t1, x31\n"  # Copy virtual address
                machine_code += f"\tsrli t1, t1, {vpn_lo}\n"  # Shift to extract VPN
                machine_code += f"\tandi t1, t1, {hex(vpn_mask)}\n"  # Mask to get VPN bits
                machine_code += "\tslli t1, t1, 3\n"  # Multiply by 8 (PTE size)
                machine_code += "\tadd t1, t2, t1\n"  # t0 = PTE address
                machine_code += "\tld t2, 0(t1)\n"  # Load PTE into t2

                # Check if this is a leaf PTE (if not last level)
                if level_idx < len(levels) - 1:
                    # Check R/W/X bits (bits [3:1])
                    machine_code += "\tandi t1, t2, 0xE\n"  # Check bits [3:1]
                    machine_code += f"\tbnez t1, {label}_done\n"  # If any set, it's a leaf

                    # Not a leaf, extract next level base address
                    machine_code += "\tsrli t1, t2, 10\n"  # Extract PPN from PTE
                    machine_code += "\tslli t2, t1, 12\n"  # Convert PPN to address
                else:
                    # Last level, must be a leaf
                    machine_code += f"\tj {label}_done\n"

            machine_code += f"\n{label}_done:\n"
            machine_code += "\t# t2 now contains the leaf PTE value\n"
            machine_code += f"\tj {end_machine_label}\n"

        # Generate the jump table section
        code = f"""
.section .leaf_pte_machine_0, "ax"
{self.machine_leaf_pte_jump_table_flags.load("x31")}

"""
        # Generate comparison checks for each parsed leaf PTE
        for (lin_name, paging_mode), leaf_pte in parsed_leaf_ptes.items():
            code += f"\tli t1, {leaf_pte.pte_id}\n"
            code += f"\tbeq x31, t1, {leaf_pte.label}\n"

        code += f"\tj {end_machine_label}\n"
        code += machine_code

        # End label that returns to test mode
        code += f"""
{end_machine_label}:
\t# Return to testmode
\tli x31, 0xf0001004
\tecall
"""

        return code
