# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.trap_handler import InterruptHandler


class InterruptHandlerTest(unittest.TestCase):
    """
    Test the InterruptHandler module.
    """

    def test_invalid_privilege_mode(self):
        """
        Test that an invalid privilege mode causes an error.
        """
        with self.assertRaises(ValueError):
            InterruptHandler(privilege_mode="X")

    def test_valid_privilege_modes(self):
        """
        Test valid privilege modes work correctly.
        """
        handler_m = InterruptHandler(privilege_mode="M")
        self.assertEqual(handler_m.xip, "mip")
        self.assertEqual(handler_m.xret, "mret")

        handler_s = InterruptHandler(privilege_mode="S")
        self.assertEqual(handler_s.xip, "sip")
        self.assertEqual(handler_s.xret, "sret")

    def test_register_vector(self):
        """
        Test vector registration works correctly.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Register a custom handler
        handler.register_vector(16, "custom_handler", indirect=False)

        # Verify the vector was registered
        isr = handler.vector_table[16]
        self.assertEqual(isr.label, "custom_handler")
        self.assertFalse(isr.indirect)

        # Register an indirect handler
        handler.register_vector(17, "indirect_handler", indirect=True)
        isr = handler.vector_table[17]
        self.assertEqual(isr.label, "indirect_handler")
        self.assertTrue(isr.indirect)

    def test_mark_invalid_vector(self):
        """
        Test marking vectors as invalid works correctly.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Mark a vector as invalid
        handler.mark_invalid_vector(20)

        # Verify the vector was marked invalid
        isr = handler.vector_table[20]
        self.assertEqual(isr.label, "invalid_interrupt")

    def test_mark_invalid_vectors(self):
        """
        Test marking multiple vectors as invalid works correctly.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Mark multiple vectors as invalid
        handler.mark_invalid_vectors([25, 26, 27])

        # Verify all vectors were marked invalid
        for v in [25, 26, 27]:
            isr = handler.vector_table[v]
            self.assertEqual(isr.label, "invalid_interrupt")

    def test_mark_vector_as_default(self):
        """
        Test marking a vector as default works correctly.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Mark a vector as default
        handler.mark_vector_as_default(30)

        # Verify the vector was marked as default
        isr = handler.vector_table[30]
        self.assertEqual(isr.label, "clear_highest_priority_interrupt")

    def test_reserved_interrupts_marked_invalid(self):
        """
        Test that reserved interrupts are marked as invalid by default.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Check reserved interrupt indices
        for reserved_idx in InterruptHandler.reserved_interrupt_indicies:
            isr = handler.vector_table[reserved_idx]
            self.assertEqual(isr.label, "invalid_interrupt")

    def test_generate_assembly(self):
        """
        Test that assembly generation produces expected output.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Generate assembly code
        asm_code = handler.generate()

        # Check for expected components
        self.assertIn("interrupt_vector_table:", asm_code)
        self.assertIn("trap_entry:", asm_code)
        self.assertIn("invalid_interrupt:", asm_code)
        self.assertIn("clear_highest_priority_interrupt:", asm_code)
        self.assertIn("check_expected_interrupt", asm_code)
        self.assertIn("clear_interrupt_bit", asm_code)

        # Check for machine mode specific elements
        self.assertIn("mip", asm_code)
        self.assertIn("mret", asm_code)

    def test_supervisor_mode_csrs(self):
        """
        Test that supervisor mode uses correct CSRs.
        """
        handler = InterruptHandler(privilege_mode="S")

        # Generate assembly code
        asm_code = handler.generate()

        # Check for supervisor mode specific elements
        self.assertIn("sip", asm_code)
        self.assertIn("sret", asm_code)

        # Should not contain machine mode CSRs
        self.assertNotIn("mip", asm_code)
        self.assertNotIn("mret", asm_code)

    def test_vector_bounds_checking(self):
        """
        Test vector bounds are respected based on XLEN.
        """
        handler_64 = InterruptHandler(privilege_mode="M", xlen=64)
        self.assertEqual(handler_64.vector_count, 63)

        handler_32 = InterruptHandler(privilege_mode="M", xlen=32)
        self.assertEqual(handler_32.vector_count, 31)

    def test_default_interrupt_vectors(self):
        """
        Test that default interrupt vectors are correctly initialized.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Check that standard RISC-V interrupts have default handlers
        for interrupt_enum in RV.RiscvInterruptCause:
            isr = handler.vector_table[interrupt_enum.value]
            expected_label = f"_CLEAR_{interrupt_enum.name}"
            self.assertEqual(isr.label, expected_label)
            self.assertFalse(isr.indirect)

    def test_platform_custom_interrupts_default(self):
        """
        Test that platform-custom interrupts (16+) have default handler.
        """
        handler = InterruptHandler(privilege_mode="M")

        # Check vectors 16 and above (except reserved ones)
        for i in range(16, handler.vector_count):
            if i not in InterruptHandler.reserved_interrupt_indicies:
                isr = handler.vector_table[i]
                self.assertEqual(isr.label, "clear_highest_priority_interrupt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
