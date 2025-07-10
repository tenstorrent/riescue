# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from abc import ABC, abstractmethod
from typing import Dict

import riescue.lib.enums as RV
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.featmanager import FeatMgr
from riescue.dtest_framework.lib.routines import Routines
from riescue.lib.rand import RandNum


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

    def __init__(self, rng: RandNum, pool: Pool, featmgr: FeatMgr):
        self.rng = rng
        self.pool = pool
        self.featmgr = featmgr
        self.hartid_reg = "s1"

        self.priv_mode = self.featmgr.priv_mode
        self.handler_priv_mode = "M" if self.priv_mode == RV.RiscvPrivileges.MACHINE else "S"

        self.mp_active = self.featmgr.mp == RV.RiscvMPEnablement.MP_ON
        self.mp_parallel = self.featmgr.mp_mode == RV.RiscvMPMode.MP_PARALLEL and self.mp_active
        self.mp_simultanous = self.featmgr.mp_mode == RV.RiscvMPMode.MP_SIMULTANEOUS and self.mp_active

        self._equates: Dict[str, str] = {}  # Dictionary of key value pairs to be generated with .equ key, value

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
    def switch_test_privilege(self, from_priv, to_priv, jump_label=None, pre_xret="", jump_register=None, switch_to_vs=False):
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

        if from_priv == RV.RiscvPrivileges.MACHINE:
            xepc_csr = "mepc"
            xstatus_csr = "mstatus"
            xret_instr = "mret"
        elif from_priv == RV.RiscvPrivileges.SUPER:
            xepc_csr = "sepc"
            xstatus_csr = "sstatus"
            xret_instr = "sret"
        else:
            raise ValueError(f"Privilege {from_priv} not yet supported for switching privilege")

        if from_priv == RV.RiscvPrivileges.MACHINE:
            if to_priv == RV.RiscvPrivileges.SUPER:
                xstatus_mpp_clear = "0x00001800"  # mstatus[12:11] = 01
                xstatus_mpp_set = "0x00000800"  # mstatus[12:11] = 01
            elif to_priv == RV.RiscvPrivileges.USER:
                xstatus_mpp_clear = "0x00001800"  # mstatus[12:11] = 11
                xstatus_mpp_set = "0x00000000"  # mstatus[12:11] = 11
            else:
                raise ValueError(f"Privilege {to_priv} not yet supported for switching privilege")
        elif from_priv == RV.RiscvPrivileges.SUPER:
            # We need to update sstatus.SPP to 0 if going to user mode
            # and 1 if staying in super mode
            if to_priv == RV.RiscvPrivileges.USER:
                xstatus_spp_clear = "0x00000100"
                xstatus_spp_set = "0x00000000"
            elif to_priv == RV.RiscvPrivileges.SUPER:
                xstatus_spp_clear = "0x00000000"
                xstatus_spp_set = "0x00000100"
        else:
            raise ValueError(f"Privilege {from_priv} not yet supported for switching privilege")

        # Setup mepc csr, so we jump to that label after MRET
        if jump_label is None:
            if jump_register is None:
                raise ValueError("jump_register must be provided if jump_label is None")
            code += f"""
                csrw {xepc_csr}, {jump_register}
            """
        else:
            code += f"""
                la t0, {jump_label}
                csrw {xepc_csr}, t0
            """

        # If we are going from machine mode, we need to update mstatus.mpp bits
        # Else update sstatus.spp bit to select User or Supervisor mode
        if from_priv == RV.RiscvPrivileges.MACHINE:
            code += """
                # Setup MEPC for the return label of MRET

                # MSTATUS.MPP bits control the privilege level we will switch to
                # | MPP[12:11] | Privilege  |
                # |     00     |    User    |
                # |     01     | Supervisor |
                # |     10     |  Reserved  |
                # |     11     |   Machine  |
            """
            code += f"""
                li x1, {xstatus_mpp_clear}
                csrrc x0, {xstatus_csr}, x1
                li x1, {xstatus_mpp_set}
                csrrs x0, {xstatus_csr}, x1
            """
        elif from_priv == RV.RiscvPrivileges.SUPER:
            code += f"""
                # Update SSTATUS.SPP
                li t0, {xstatus_spp_clear}
                csrrc x0, {xstatus_csr}, t0
                li t0, {xstatus_spp_set}
                csrrs x0, {xstatus_csr}, t0
            """
            # Write hstatus.spp=1 if we are switching to VS-mode
            if switch_to_vs:
                code += """
                    li x1, 0x00000080 # HSTATUS.SVP=1
                    csrrs x0, hstatus, x1
            """

        code += f"\n\t{pre_xret}\n\t{xret_instr}\n"
        return code

    # Note added a max number of times we can ignore an exception in case a randomly generated fault has created an infinite loop.
    def os_check_excp(self, return_label, xepc, xret):
        code = f"""
            # get hartid
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}

            # Check if check_exception is enabled
            li t3, check_excp
            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset
            add t3, t3, {self.hartid_reg} # Add offset for this harts check_excp element
            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset
            lb t0, 0(t3)
            beq t0, x0, {return_label}

            # Check for correct exception code
            li t3, check_excp_expected_cause
            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset
            add t3, t3, {self.hartid_reg} # Add offset for this harts check_excp_expected_cause element
            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset
            ld t0, 0(t3)
            sd x0, 0(t3)
            {"bne t1, t0, count_ignored_excp" if self.featmgr.skip_instruction_for_unexpected == True else "bne t1, t0, test_failed"}

            # TODO: Check for the correct pc value check_excp_expected_pc
            li t3, check_excp_expected_pc
            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset
            add t3, t3, {self.hartid_reg} # Add offset for this harts check_excp_expected_pc element
            ld t1, 0(t3)
            sd x0, 0(t3)
            li t3, check_excp_actual_pc
            add t3, t3, {self.hartid_reg} # Add offset for this harts check_excp_actual_pc element
            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset
            ld t0, 0(t3)
            sd x0, 0(t3)
            {"bne t1, t0, count_ignored_excp" if self.featmgr.skip_instruction_for_unexpected == True else "bne t1, t0, test_failed"}
            j {return_label}
        """

        # if not os_check_excp_called:
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
            li t0, excp_ignored_count
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

        code += f"""
        # Optionally, check the value of mtval/stval
            # get hartid
            {Routines.place_retrieve_hartid(dest_reg=self.hartid_reg, priv_mode=self.handler_priv_mode)}

            slli {self.hartid_reg}, {self.hartid_reg}, 3 # Multiply saved hartid by 8 to get offset
            li t3, check_excp_expected_tval
            add t3, t3,  {self.hartid_reg} # Add offset for this harts check_excp_expected_tval element
            ld t1, 0(t3)
            sd x0, 0(t3)
            {Routines.read_tval(dest_reg="t0", priv_mode=self.handler_priv_mode)}
            srli {self.hartid_reg}, {self.hartid_reg}, 3 # Restore saved hartid rather than offset
            bne t1, t0, test_failed
            j {return_label}"""

        return code
