# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

import riescue.compliance
from coretp import TestEnvCfg
from coretp.rv_enums import PrivilegeMode
from coretp.step import TestStep, CsrRead, CsrWrite, Arithmetic

from tests.cli_tests.riescuec.tp_mode.basic_test.basic_test_base import BasicTestBase


class CsrTest(BasicTestBase):
    """
    Basic CSR read/write test.

    Checks that CsrRead, CsrWrite work as standalone and dependent steps.
    For now just dealing with unprivileged CSRs
    TODO:
    - Add privileged CSRs

    Currently tests imports, TestScenario types, etc. Eventually will want to move coretp tests somewhere else.
    """

    def test_csr_read(self):
        """
        Test reading cycles CSR, an unprivileged CSR that's Read-only
        """

        read_time = CsrRead(csr_name="time")
        steps: list[TestStep] = [read_time]
        # text = self.generator_from_steps(steps, "rv64imafdc_zicsr")
        # self.assertNotIn("None", text)
        # self.assertIn("csr", text, "No CSR instructions found in generated test")
        self.run_test("test_csr_read", steps, "rv64imafdc_zicsr")

    def test_csr_read_machine(self):
        """
        Test reading cycles CSR, an machine-privileged CSR that's Read-only
        """
        op1 = Arithmetic(op="li", src1=100)
        op2 = Arithmetic(op="li", src1=100)
        op3 = Arithmetic(op="li", src1=100)
        read_mhartid = CsrRead(csr_name="mhartid")
        steps: list[TestStep] = [op1, op2, op3, read_mhartid]
        # text = self.generator_from_steps(steps, "rv64imafdc_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]))
        # self.assertNotIn("None", text)
        # self.assertIn("csr", text, "No CSR instructions found in generated test")
        self.run_test("test_csr_read_machine", steps, "rv64imafdc_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]))

    # def test_csr_read_user_supervisor(self):
    #     """
    #     Test reading M CSR, in User and Supervisor modes
    #     """
    #     op1 = Arithmetic(op="li", src1=100)
    #     op2 = Arithmetic(op="li", src1=100)
    #     op3 = Arithmetic(op="li", src1=100)
    #     read_mhartid = CsrRead(csr_name="mhartid")
    #     steps: list[TestStep] = [op1, op2, op3, read_mhartid]
    #     # text = self.generator_from_steps(steps, "rv64imafdc_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]))
    #     # self.assertNotIn("None", text)
    #     # self.assertIn("csr", text, "No CSR instructions found in generated test")
    #     self.run_test("test_csr_read_machine", steps, "rv64imafdc_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M | PrivilegeMode.S]))

    def test_csr_write(self) -> None:
        """
        Test writing to CSR, no dependencies
        """
        # Testing that setting both set_mask and clear_mask raises an error
        with self.assertRaises(ValueError):
            CsrWrite(csr_name="mcounteren", set_mask=1, clear_mask=1)

        # This should result in a csrrw and csrrwi. The value of the csrrwi should be 1

        write_csr = CsrWrite(csr_name="mcounteren")
        write_csr2 = CsrWrite(csr_name="mcounteren", value=1)

        steps: list[TestStep] = [write_csr, write_csr2]
        scenario_name = "csr_test"
        text = self.generator_from_steps(
            steps,
            "rv64imafdc_zicsr",
            env=TestEnvCfg(priv_modes=[PrivilegeMode.M]),
            test_scenario_name=scenario_name,
        )

        # Basic checks
        self.assertNotIn("None", text)
        self.assertIn("csr", text, "No CSR instructions found in generated test")
        self.assertIn("mcounteren", text, "No CSR instructions found in generated test")

        # Check generated instructions in test routine
        instrs = self.text_to_instr_list(text)
        test_routine = instrs[scenario_name]
        test_instr_names = [instr.name for instr in test_routine.instructions]

        self.assertIn("csrrw", test_instr_names, f"No csrrw instruction found in generated test - {test_instr_names}")
        self.assertIn("csrrwi", test_instr_names, f"No csrrwi instruction found in generated test - {test_instr_names}")
        csrrw_instrs = test_routine.matching_instr(name="csrrw")
        self.assertEqual(len(csrrw_instrs), 1, f"Expected 1 csrrw instruction, got {len(csrrw_instrs)}")
        csrrw = csrrw_instrs[0]
        self.assertEqual(csrrw.operands[1], "mcounteren")

        csrrwi_instrs = test_routine.matching_instr(name="csrrwi")
        self.assertEqual(len(csrrwi_instrs), 1, f"Expected 1 csrrwi instruction, got {len(csrrwi_instrs)}")
        csrrwi = csrrwi_instrs[0]
        self.assertEqual(csrrwi.operands[1], "mcounteren")
        self.assertEqual(csrrwi.operands[2], "0x1")  # Expect immediate value of 1, no register

        self.assert_registers_in_order(test_routine)
        self.assert_no_unused_li(test_routine)
        self.run_test("csr_test", steps, "rv64imafdc_zicsr", env=TestEnvCfg(priv_modes=[PrivilegeMode.M]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
