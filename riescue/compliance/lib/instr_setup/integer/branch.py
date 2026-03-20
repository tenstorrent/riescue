# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.base import InstrSetup
from riescue.compliance.lib.riscv_registers import sign_aware_comparison_op


class JumpSetup(InstrSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        jump_backwards = self._rng.choice([True, False])

        if jump_backwards:
            self.write_pre(f"\tj {instr.label}")

            self.write_pre(f"jump_{instr.label}_passed :")
            if self.resource_db.combine_compliance_tests or self.resource_db.wysiwyg:
                self.write_pre("\tnop")

            self._pre_setup_instrs += self.add_padding(instr, self.resource_db.pad_size, ["add", "sub", "addi"])

            self.write_pre(f"\tj jump_{instr.label}_post")

            self.write_pre(f"{instr.label} :")
            if instr.name == "jal":
                self.write_pre(f"\t{instr.name} x1,jump_{instr.label}_passed")
            elif instr.name == "jalr":
                addr_reg = self.get_random_reg(instr.reg_manager)
                dest_reg = instr.dests[0].name
                self.write_pre(f"\tla {addr_reg},jump_{instr.label}_passed")
                self.write_pre(f"\t{instr.name} {dest_reg},0({addr_reg})")

            if self.resource_db.wysiwyg:
                self.write_pre("\tli x31, 0xbaadc0de")
            else:
                self.write_pre(";#test_failed()")

            self.write_pre(f"jump_{instr.label}_post :")
            self.write_pre("\tnop")

        else:
            if instr.name == "jal":
                self.write_pre(f"{instr.label} : {instr.name} x1,jump_{instr.label}_passed")
            elif instr.name == "jalr":
                addr_reg = self.get_random_reg(instr.reg_manager)
                dest_reg = instr.dests[0].name
                self.write_pre(f"la {addr_reg},jump_{instr.label}_passed")
                self.write_pre(f"{instr.label} : {instr.name} {dest_reg},0({addr_reg})")
            self._pre_setup_instrs += self.add_padding(instr, self.resource_db.pad_size, ["add", "sub", "addi"])

            if self.resource_db.wysiwyg:
                self.write_pre("\tli x31, 0xbaadc0de")
            else:
                self.write_pre(";#test_failed()")

            self.write_pre(f"jump_{instr.label}_passed :")
            if self.resource_db.combine_compliance_tests or self.resource_db.wysiwyg:
                self.write_pre("\tnop")

    def post_setup(self, modified_arch_state, instr):
        if self.j_pass_ok():
            self.write_post(";#test_passed()")


class BranchSetup(InstrSetup):

    def __init__(self, xlen, resource_db):
        self.xlen = xlen
        super().__init__(resource_db)

    def resolve_branch(self, instr):
        rs1 = instr.srcs[0]
        rs2 = instr.srcs[1]
        branch_outcome = instr.config.branch_outcome

        unsigned = "u" in instr.name
        s0_val = instr.srcs[0].value
        s1_val = instr.srcs[1].value
        s0_eq_s1 = s0_val == s1_val
        s0_plus1 = (s0_val + 1) % (1 << self.xlen)
        s1_plus1 = (s1_val + 1) % (1 << self.xlen)

        def swap_rs1_rs2():
            swap = rs1.value
            rs1.value = rs2.value
            rs2.value = swap

        def make_rs1_lt_rs2():
            if s0_eq_s1:
                rs2.value = s1_plus1
                # Now they are not equal if they were

            rs2_lt_rs1 = sign_aware_comparison_op(rs2.value, rs1.value, unsigned=unsigned)
            if rs2_lt_rs1:
                swap_rs1_rs2()

        def make_rs1_ge_rs2():
            if s0_eq_s1:
                return

            rs2_lt_rs1 = sign_aware_comparison_op(rs2.value, rs1.value, unsigned=unsigned)
            if not rs2_lt_rs1:
                swap_rs1_rs2()

        if instr.name == "beq":
            if branch_outcome == "taken":
                rs2.value = rs1.value
            else:
                if s0_eq_s1:
                    rs2.value = abs(~rs1.value) % (1 << self.xlen)
        elif instr.name == "bne":
            if branch_outcome == "taken":
                if s0_eq_s1:
                    rs2.value = abs(~rs1.value) % (1 << self.xlen)
            else:
                if not s0_eq_s1:
                    rs2.value = rs1.value
        elif instr.name == "blt":
            if branch_outcome == "taken":
                make_rs1_lt_rs2()
            else:
                make_rs1_ge_rs2()
        elif instr.name == "bge":
            if branch_outcome == "taken":
                make_rs1_ge_rs2()
            else:
                make_rs1_lt_rs2()
        elif instr.name == "bltu":
            if branch_outcome == "taken":
                make_rs1_lt_rs2()
            else:
                make_rs1_ge_rs2()
        elif instr.name == "bgeu":
            if branch_outcome == "taken":
                make_rs1_ge_rs2()
            else:
                make_rs1_lt_rs2()

        return rs1, rs2

    def pre_setup(self, instr):
        rs1, rs2 = self.resolve_branch(instr)
        self.write_pre(f"\tli {rs1.name},{hex(rs1.value)}")
        self.write_pre(f"\tli {rs2.name},{hex(rs2.value)}")

        self.conclusion_label = f"jump_{instr.label}_cct_passed"

        pad_size = self._rng.randint(0, 256)

        branch_backwards = self._rng.choice([True, False])

        if branch_backwards:
            self.write_pre(f"\tj {instr.label}")

            if instr.config.branch_outcome == "taken":
                self.write_pre(f"jump_{instr.label}_passed :")
            else:
                self.write_pre(f"jump_{instr.label}_failed :")
            self.write_pre("\tnop")

            self._pre_setup_instrs += self.add_padding(instr, pad_size, ["add", "sub", "addi"])

            if instr.config.branch_outcome == "taken":
                self.write_pre(f"\tj jump_{instr.label}_post")

            self.write_pre(f"{instr.label} :")
            if instr.config.branch_outcome == "taken":
                self.write_pre(f"\t{instr.name} {rs1.name},{rs2.name},jump_{instr.label}_passed")
            else:
                self.write_pre(f"\t{instr.name} {rs1.name},{rs2.name},jump_{instr.label}_failed")

            if instr.config.branch_outcome == "taken":
                if self.resource_db.wysiwyg:
                    self.write_pre("\tli x31, 0xbaadc0de")
                else:
                    self.write_pre(";#test_failed()")

                self.write_pre(f"jump_{instr.label}_post :")
                self.write_pre("\tnop")
            else:
                if self.j_pass_ok():
                    self.write_pre(";#test_passed()")
                elif self.resource_db.combine_compliance_tests:
                    self.write_pre(f"\tj {self.conclusion_label}")

        else:
            if instr.config.branch_outcome == "taken":
                self.write_pre(f"{instr.label} : {instr.name} {rs1.name},{rs2.name},jump_{instr.label}_passed")
            else:
                self.write_pre(f"{instr.label} : {instr.name} {rs1.name},{rs2.name},jump_{instr.label}_failed")

            self._pre_setup_instrs += self.add_padding(instr, pad_size, ["add", "sub", "addi"])

            if instr.config.branch_outcome == "taken":
                if self.resource_db.wysiwyg:
                    self.write_pre("\tli x31, 0xbaadc0de")
                else:
                    self.write_pre(";#test_failed()")
            else:
                if self.j_pass_ok():
                    self.write_pre(";#test_passed()")
                elif self.resource_db.combine_compliance_tests:
                    self.write_pre(f"\tj {self.conclusion_label}")

            if instr.config.branch_outcome == "taken":
                self.write_pre(f"jump_{instr.label}_passed :")
            else:
                self.write_pre(f"jump_{instr.label}_failed :")
            self.write_pre("\tnop")

        if self.resource_db.combine_compliance_tests:
            self.pass_one_pre_appendix += f"{self.conclusion_label} :\n"
            self.pass_one_pre_appendix += "\tnop\n"

    def post_setup(self, modified_arch_state, instr):
        if not self.resource_db.wysiwyg:
            if instr.config.branch_outcome == "taken":
                if self.j_pass_ok():
                    self.write_post(";#test_passed()")
            else:
                self.write_post(";#test_failed()")
        if self.resource_db.combine_compliance_tests:
            self.write_post(f"{self.conclusion_label}:")
            self.write_post("\tnop")
