# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path

import riescue.lib.enums as RV
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.runtime.trap_handler import InterruptHandler, TrapHandler


class InterruptHandlerTest(unittest.TestCase):
    """
    Test the InterruptHandler module.
    """

    def test_valid_privilege_modes(self):
        """
        Test valid privilege modes work correctly.
        """
        handler_m = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)
        self.assertEqual(handler_m.xip, "mip")
        self.assertEqual(handler_m.xret, "mret")

        handler_s = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.SUPER)
        self.assertEqual(handler_s.xip, "sip")
        self.assertEqual(handler_s.xret, "sret")

    def test_register_vector(self):
        """
        Test vector registration works correctly.
        """
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

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
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

        # Mark a vector as invalid
        handler.mark_invalid_vector(20)

        # Verify the vector was marked invalid
        isr = handler.vector_table[20]
        self.assertEqual(isr.label, "invalid_interrupt")

    def test_mark_invalid_vectors(self):
        """
        Test marking multiple vectors as invalid works correctly.
        """
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

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
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

        # Mark a vector as default
        handler.mark_vector_as_default(30)

        # Verify the vector was marked as default
        isr = handler.vector_table[30]
        self.assertEqual(isr.label, "clear_highest_priority_interrupt")

    def test_reserved_interrupts_marked_invalid(self):
        """
        Test that reserved interrupts are marked as invalid by default.
        """
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

        # Check reserved interrupt indices
        for reserved_idx in InterruptHandler.reserved_interrupt_indicies:
            isr = handler.vector_table[reserved_idx]
            self.assertEqual(isr.label, "invalid_interrupt")

    def test_generate_assembly(self):
        """
        Test that assembly generation produces expected output.
        """
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

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
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.SUPER)

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
        handler_64 = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE, xlen=RV.Xlen.XLEN64)
        self.assertEqual(handler_64.vector_count, 63)

        handler_32 = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE, xlen=RV.Xlen.XLEN32)
        self.assertEqual(handler_32.vector_count, 31)

    def test_default_interrupt_vectors(self):
        """
        Test that default interrupt vectors are correctly initialized.
        """
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

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
        handler = InterruptHandler(pool=Pool(), privilege_mode=RV.RiscvPrivileges.MACHINE)

        # Check vectors 16 and above (except reserved ones)
        for i in range(16, handler.vector_count):
            if i not in InterruptHandler.reserved_interrupt_indicies:
                isr = handler.vector_table[i]
                self.assertEqual(isr.label, "clear_highest_priority_interrupt")


class XtinstTablesTest(unittest.TestCase):
    """
    Test the ``mtinst``/``htinst`` spec tables and the bitmasks the trap handler derives from them
    and bakes into the generated check. All of these are classmethods, so no handler is built.
    """

    def test_cause_masks(self):
        """
        Every derived cause mask matches the privileged-spec table.
        """
        Option = TrapHandler.XtinstOption
        self.assertEqual(TrapHandler._xtinst_known_cause_mask(), 0x00F0BFFF)
        self.assertEqual(TrapHandler._xtinst_cause_mask(Option.ZERO), 0x00F0BFFF)
        self.assertEqual(TrapHandler._xtinst_cause_mask(Option.TRANSFORMED), 0x00A0A0F0)
        self.assertEqual(TrapHandler._xtinst_cause_mask(Option.CUSTOM), 0x00E0AFF9)
        self.assertEqual(TrapHandler._xtinst_cause_mask(Option.PSEUDO), 0x00B00000)

    def test_zero_always_permitted(self):
        """
        Zero is legal for every cause the table covers. The single case where the spec forbids it
        also requires a nonzero htval/mtval2, which is WARL and may always read zero.
        """
        for cause, options in TrapHandler.XTINST_CAUSE_OPTIONS.items():
            self.assertTrue(options & TrapHandler.XtinstOption.ZERO, f"cause {cause} does not permit zero")

    def test_pseudo_causes_are_guest_page_faults(self):
        """
        Only the three guest-page faults may carry a pseudoinstruction.
        """
        pseudo_causes = {cause for cause, options in TrapHandler.XTINST_CAUSE_OPTIONS.items() if options & TrapHandler.XtinstOption.PSEUDO}
        self.assertEqual(
            pseudo_causes,
            {
                RV.RiscvExcpCauses.INSTRUCTION_GUEST_PAGE_FAULT.value,
                RV.RiscvExcpCauses.LOAD_GUEST_PAGE_FAULT.value,
                RV.RiscvExcpCauses.STORE_GUEST_PAGE_FAULT.value,
            },
        )

    def test_transformed_causes_are_explicit_memory_accesses(self):
        """
        A transformed standard instruction is only permitted for faults on explicit load/store/AMO
        accesses, i.e. exactly the union of the load and store cause sets.
        """
        transformed = {cause for cause, options in TrapHandler.XTINST_CAUSE_OPTIONS.items() if options & TrapHandler.XtinstOption.TRANSFORMED}
        self.assertEqual(transformed, set(TrapHandler.XTINST_LOAD_CAUSES) | set(TrapHandler.XTINST_STORE_CAUSES))

    def test_direction_cause_masks(self):
        """
        Load-cause and store-cause masks are disjoint and match the spec cause codes.
        """
        load = TrapHandler._bitmask(TrapHandler.XTINST_LOAD_CAUSES)
        store = TrapHandler._bitmask(TrapHandler.XTINST_STORE_CAUSES)
        self.assertEqual(load, 0x00202030)
        self.assertEqual(store, 0x008080C0)
        self.assertEqual(load & store, 0)

    def test_opcode_masks(self):
        """
        Every derived major-opcode mask matches the spec's RVG base opcode map.
        """
        self.assertEqual(TrapHandler._xtinst_opcode_mask(TrapHandler.XTINST_OPCODE_CUSTOM), 0x40400404)
        self.assertEqual(TrapHandler._xtinst_opcode_mask(TrapHandler.XTINST_OPCODE_RESERVED), 0x84808080)
        self.assertEqual(TrapHandler._xtinst_opcode_mask(TrapHandler.XTINST_OPCODE_LOAD), 0x00000003)
        self.assertEqual(TrapHandler._xtinst_opcode_mask(TrapHandler.XTINST_OPCODE_STORE), 0x00000300)
        self.assertEqual(TrapHandler._xtinst_opcode_mask(TrapHandler.XTINST_OPCODE_AMO), 0x00000800)
        self.assertEqual(TrapHandler._xtinst_opcode_mask(TrapHandler.XTINST_OPCODE_HYPER), 0x10000000)
        self.assertEqual(TrapHandler._xtinst_opcode_mask(TrapHandler.XTINST_OPCODE_CMO), 0x00000008)

    def test_opcode_classes_are_disjoint(self):
        """
        No major opcode is in two classes, and every classified opcode has inst[1:0]=11.
        """
        classes = [
            TrapHandler.XTINST_OPCODE_CUSTOM,
            TrapHandler.XTINST_OPCODE_RESERVED,
            TrapHandler.XTINST_OPCODE_LOAD,
            TrapHandler.XTINST_OPCODE_STORE,
            TrapHandler.XTINST_OPCODE_AMO,
            TrapHandler.XTINST_OPCODE_HYPER,
            TrapHandler.XTINST_OPCODE_CMO,
        ]
        seen: set = set()
        for opcodes in classes:
            for opcode in opcodes:
                self.assertEqual(opcode & 0x3, 0x3, f"opcode 0x{opcode:02X} is not a 32-bit encoding")
                self.assertNotIn(opcode, seen, f"opcode 0x{opcode:02X} is classified twice")
                seen.add(opcode)

    def test_transform_keep_covers_every_transformable_opcode(self):
        """
        The transformation kinds the spec defines are exactly the keys of the keep-mask table --
        the four from the hypervisor extension plus the cache-block one from the CMO extension.
        """
        transformable = set(TrapHandler.XTINST_OPCODE_LOAD + TrapHandler.XTINST_OPCODE_STORE + TrapHandler.XTINST_OPCODE_AMO + TrapHandler.XTINST_OPCODE_HYPER + TrapHandler.XTINST_OPCODE_CMO)
        self.assertEqual(set(TrapHandler.XTINST_TRANSFORM_KEEP), transformable)

    def test_compare_and_zero_field_masks(self):
        """
        The per-kind masks match the spec transformations: a basic load keeps funct3/rd/opcode, a
        basic store keeps rs2/funct3/opcode, atomic and virtual-machine accesses keep every field,
        and a cache-block operation keeps opcode/funct3/operation.
        """
        expected = {
            0x03: (0x00007FFF, 0xFFF00000),
            0x07: (0x00007FFF, 0xFFF00000),
            0x23: (0x01F0707F, 0xFE000F80),
            0x27: (0x01F0707F, 0xFE000F80),
            0x2F: (0xFFF07FFF, 0x00000000),
            0x73: (0xFFF07FFF, 0x00000000),
            0x0F: (0xFFF0707F, 0x000F8F80),
        }
        for opcode, (compare, zero) in expected.items():
            self.assertEqual(TrapHandler._xtinst_compare_mask(opcode), compare, f"compare mask for opcode 0x{opcode:02X}")
            self.assertEqual(TrapHandler._xtinst_zero_field_mask(opcode), zero, f"zero-field mask for opcode 0x{opcode:02X}")
            # Never compared: bits 19:15 are Addr. Offset, or zero for a cache-block operation.
            self.assertEqual(compare & TrapHandler.XTINST_RS1_MASK, 0)
            # Together the two masks account for the whole encoding.
            self.assertEqual(compare | zero | TrapHandler.XTINST_RS1_MASK, 0xFFFFFFFF)

    def test_cache_block_transformation_zeroes_rs1(self):
        """
        The cache-block transformation is the one kind with no Addr. Offset: bits 19:15 must be
        zero rather than carrying a displacement, so they belong to its zero-field mask.
        """
        (cmo_opcode,) = TrapHandler.XTINST_OPCODE_CMO
        self.assertEqual(TrapHandler._xtinst_zero_field_mask(cmo_opcode) & TrapHandler.XTINST_RS1_MASK, TrapHandler.XTINST_RS1_MASK)
        for opcode in set(TrapHandler.XTINST_TRANSFORM_KEEP) - {cmo_opcode}:
            self.assertEqual(TrapHandler._xtinst_zero_field_mask(opcode) & TrapHandler.XTINST_RS1_MASK, 0, f"opcode 0x{opcode:02X}")

    def test_cache_block_transformation_matches_spec(self):
        """
        A cbo.flush transformed under the CMO spec's layout: opcode, funct3 and operation (bits
        31:20) kept, rd and rs1 zeroed.
        """
        (cmo_opcode,) = TrapHandler.XTINST_OPCODE_CMO
        trapping = 0x0028A00F  # cbo.flush 0(x17)
        self.assertEqual(trapping & TrapHandler.XTINST_TRANSFORM_KEEP[cmo_opcode], 0x0020200F)

    def test_pseudo_values_are_rv64(self):
        """
        Only the VSXLEN=64 pseudoinstruction pair is accepted, and both have bits[1:0]=00.
        """
        self.assertEqual(TrapHandler.XTINST_PSEUDO_VALUES, (0x3000, 0x3020))
        for value in TrapHandler.XTINST_PSEUDO_VALUES:
            self.assertEqual(value & 0x3, 0)

    def test_cause_table_agrees_with_the_masks(self):
        """
        The emitted per-cause byte table says the same thing as the bitmasks it replaced, entry by
        entry, and marks exactly the covered causes as known.
        """
        table = TrapHandler._xtinst_cause_table()
        Option = TrapHandler.XtinstOption
        self.assertEqual(len(table), max(TrapHandler.XTINST_CAUSE_OPTIONS) + 1)
        for cause, byte in enumerate(table):
            known = bool(byte & TrapHandler.XTINST_CAUSE_KNOWN)
            self.assertEqual(known, cause in TrapHandler.XTINST_CAUSE_OPTIONS, f"cause {cause} known bit")
            for bit, option in (
                (TrapHandler.XTINST_CAUSE_TRANSFORMED, Option.TRANSFORMED),
                (TrapHandler.XTINST_CAUSE_CUSTOM, Option.CUSTOM),
                (TrapHandler.XTINST_CAUSE_PSEUDO, Option.PSEUDO),
            ):
                expected = bool(TrapHandler.XTINST_CAUSE_OPTIONS.get(cause, 0) & option)
                self.assertEqual(bool(byte & bit), expected, f"cause {cause} option {option!r}")
            self.assertEqual(bool(byte & TrapHandler.XTINST_CAUSE_LOAD_DIR), cause in TrapHandler.XTINST_LOAD_CAUSES, f"cause {cause} load dir")
            self.assertEqual(bool(byte & TrapHandler.XTINST_CAUSE_STORE_DIR), cause in TrapHandler.XTINST_STORE_CAUSES, f"cause {cause} store dir")

    def test_opcode_table_agrees_with_the_masks(self):
        """
        The emitted per-opcode byte table classifies every major opcode the same way the
        custom/reserved/transformable masks do, and carries the right kind.
        """
        table = TrapHandler._xtinst_opcode_table()
        self.assertEqual(len(table), 32)
        for index, byte in enumerate(table):
            opcode = (index << 2) | 0x3
            self.assertEqual(bool(byte & TrapHandler.XTINST_OPCODE_RESERVED_BIT), opcode in TrapHandler.XTINST_OPCODE_RESERVED, f"opcode 0x{opcode:02X} reserved")
            self.assertEqual(bool(byte & TrapHandler.XTINST_OPCODE_CUSTOM_BIT), opcode in TrapHandler.XTINST_OPCODE_CUSTOM, f"opcode 0x{opcode:02X} custom")
            kind = byte >> TrapHandler.XTINST_KIND_SHIFT
            self.assertEqual(kind, TrapHandler.XTINST_OPCODE_KIND.get(opcode, TrapHandler.XTINST_KIND_NONE), f"opcode 0x{opcode:02X} kind")
            # A byte of zero has to mean "nothing special", so no class may use kind 0.
            self.assertEqual(kind == TrapHandler.XTINST_KIND_NONE, opcode not in TrapHandler.XTINST_TRANSFORM_KEEP, f"opcode 0x{opcode:02X}")

    def test_kind_masks_table_agrees_with_the_masks(self):
        """
        Each kind's (zero, compare) pair matches the per-opcode masks for every opcode of that
        kind -- opcodes sharing a kind must share their masks, or one table entry cannot serve both.
        """
        table = TrapHandler._xtinst_kind_masks_table()
        self.assertEqual(table[TrapHandler.XTINST_KIND_NONE], (0, 0))
        for opcode, kind in TrapHandler.XTINST_OPCODE_KIND.items():
            expected = (TrapHandler._xtinst_zero_field_mask(opcode), TrapHandler._xtinst_compare_mask(opcode))
            self.assertEqual(table[kind], expected, f"opcode 0x{opcode:02X} (kind {kind})")

    def test_kind_constants_index_the_masks_table(self):
        """
        Every kind constant is a valid index into the masks table, so adding a kind without
        extending the table is a generation-time failure rather than a silent bad lookup.
        """
        table = TrapHandler._xtinst_kind_masks_table()
        for opcode, kind in TrapHandler.XTINST_OPCODE_KIND.items():
            self.assertLess(kind, len(table), f"opcode 0x{opcode:02X} kind {kind} outside the table")
            self.assertNotEqual(kind, TrapHandler.XTINST_KIND_NONE, f"opcode 0x{opcode:02X} uses the reserved kind 0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
