# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.immediate import Immediate12
from riescue.compliance.config import Resource


class InstrSetup:
    def __init__(self, resource_db):
        self._pre_setup_instrs = []
        self._post_setup_instrs = []
        self.resource_db = resource_db
        self._rng = resource_db.rng
        self._asm_instr = ""
        self._pass_one_pre_appendix = ""

    def do_load_fp_regs(self):
        return self.resource_db.load_fp_regs

    def choose_randomly_but_not(self, choices: list, not_these: list):
        assert len(choices) > 0, "No choices to choose from"
        remaining_choices = [c for c in choices if c not in not_these]
        assert len(remaining_choices) > 0, f"Could not find a choice that was not in {not_these}"

        choice = self.resource_db.rng.random_entry_in(remaining_choices)

        return choice

    def no_overlap_method(self, vreg1_index: int, vreg2_index: int, emul: int) -> bool:
        v1_nearest_emul_aligned = vreg1_index - (vreg1_index % emul)
        v2_nearest_emul_aligned = vreg2_index - (vreg2_index % emul)
        return v1_nearest_emul_aligned != v2_nearest_emul_aligned

    @property
    def pre_setup_instrs(self):
        return self._pre_setup_instrs

    @property
    def post_setup_instrs(self):
        return self._post_setup_instrs

    @property
    def asm_instr(self):
        return self._asm_instr

    def pre_setup(self, instr):
        pass

    def j_pass_ok(self):
        return (self.resource_db.combine_compliance_tests == 0) and (not self.resource_db.wysiwyg)

    def post_setup(self, modified_arch_state, instr):
        # FIXME log relevant info if modified_arch_state is None or ""
        if modified_arch_state is None:
            print("problem with instruction: " + instr.label)

        self._post_setup_instrs = []
        mod_gprs = modified_arch_state[2].split(";")
        gprs = self.get_gprs(mod_gprs)

        result_reg = self.get_random_reg(instr.reg_manager)

        for gpr, value in gprs.items():
            self.write_post(f'\tli {result_reg},{"0x"+value}')
            if self.resource_db.wysiwyg:
                temp_reg = self.get_random_reg(instr.reg_manager)
                self.write_post(f"\tsub {temp_reg},{gpr},{result_reg}")
                self.write_post(f"\tadd x31,x31,{temp_reg}")
            else:
                self.write_post(f"\tbne {result_reg}, {gpr}, 1f")

        if self.j_pass_ok():
            self.write_post("\tli a0, passed_addr")
            self.write_post("\tld a1, 0(a0)")
            self.write_post("\tjalr ra, 0(a1)")
        else:
            self.write_post("\tj 2f")
        self.write_post("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_post("\tli a0, failed_addr")
            self.write_post("\tld a1, 0(a0)")
            self.write_post("\tjalr ra, 0(a1)")
        self.write_post("\t2:\n")

    # Tested with [add, sub, addi]
    def add_padding(self, instr, num_instrs, mnemonics):
        padding = []

        for rpt in range(num_instrs):
            mnemonic = self._rng.random_entry_in(mnemonics)
            imm12 = Immediate12("dont_care", resource_db=self.resource_db)
            imm12.randomize()
            imm12 = hex(imm12.value)
            asm = (
                "\t"
                + mnemonic
                + " "
                + self.get_random_reg(instr.reg_manager)
                + ", "
                + self.get_random_reg(instr.reg_manager)
                + ", "
                + (imm12 if mnemonic.endswith("i") else self.get_random_reg(instr.reg_manager))
            )
            padding.append(asm)

        return padding

    def get_gprs(self, mod_gprs):

        # Whisper has key-value pairs separated with "="
        try:
            gprs = dict(mod_gpr.split("=") for mod_gpr in mod_gprs)
        except ValueError:
            pass

        # Spike has key-value pairs separated with ":"
        try:
            gprs = dict(mod_gpr.split(":") for mod_gpr in mod_gprs)
        except ValueError:
            pass

        if len(gprs) == 0:
            raise ValueError("No Modified State Found")

        return gprs

    # def write_setup(self,cmd):
    #    if inspect.stack()[1].function == "pre_setup":
    #        self._pre_setup_instrs.append(cmd)
    #    elif inspect.stack()[1].function == "post_setup":
    #        self._post_setup_instrs.append(cmd)
    #    else:
    #        self._pre_setup_instrs.append(cmd)

    def write_pre(self, cmd):
        # assert any(entry.function == "pre_setup" for entry in inspect.stack()[:6])
        self._pre_setup_instrs.append(cmd)

    def write_post(self, cmd):
        # assert any(entry.function == "post_setup" for entry in inspect.stack()[:6])
        self._post_setup_instrs.append(cmd)

    def write_data(self, instr, cmd):
        instr._data_section.append(cmd)

    def get_random_reg(self, reg_manager, reg_type="Int"):
        if reg_type.lower() != "vec":
            result_regs = reg_manager.get_avail_regs(reg_type)
            result_reg = result_regs[0]
            reg_manager.update_avail_regs(reg_type, result_reg)
        else:
            # No need to remove the register from the available registers, just ensure it isn't reused where that's specifically a problem
            # this is the case for vector registers and probably fp too because they're not used for store base addresses and the like
            # at worst we should see a redundant value initialization.
            # We still must prohibit the use of the same register for the self checking sliding code.
            # This means prohibit picking the same reg for working vreg and vd, but otherwise other overlapping is fine where eew allows.
            result_regs = reg_manager.get_avail_regs(reg_type)
            result_reg = result_regs[0]
            # manually move the register to the end of the list
            reg_manager._avail_vec_regs = result_regs[1:] + [result_reg]

        return result_reg

    def get_lowest_index_reg(self, reg_manager, reg_type="Vector"):
        result_regs = reg_manager.get_avail_regs(reg_type)
        result_reg = result_regs[0]

        for reg in result_regs:
            if int(reg[1:]) < int(result_reg[1:]):
                result_reg = reg

        return result_reg

    def get_operand(self, instr, operand_field_name):
        return instr._fields.get(operand_field_name, None)

    def setup_memory(self, label, size, setup_phase):
        self._lin_addr = label + "_lin"
        self._phy_addr = label + "_phy"
        if setup_phase == "pre_setup":
            self._pre_setup_instrs.append(f";#random_addr(name={self._lin_addr}, type=linear, size={size}, and_mask=0xfffffffffffff000)")
            self._pre_setup_instrs.append(f";#random_addr(name={self._phy_addr}, type=physical, size={size}, and_mask=0xfffffffffffff000)")
            self._pre_setup_instrs.append(f";#page_mapping(lin_name={self._lin_addr}, phys_name={self._phy_addr}, v=1, r=1, w=1, a=1, d=1)")
        else:
            self._post_setup_instrs.append(f";#random_addr(name={self._lin_addr}, type=linear, size={size}, and_mask=0xfffffffffffff000)")
            self._post_setup_instrs.append(f";#random_addr(name={self._phy_addr}, type=physical, size={size}, and_mask=0xfffffffffffff000)")
            self._post_setup_instrs.append(f";#page_mapping(lin_name={self._lin_addr}, phys_name={self._phy_addr}, v=1, r=1, w=1, a=1, d=1)")

    def randomize_vstart(self, instr, evl: int):
        # Write a random value into vstart from 1 to evl -1
        vstart = self.resource_db.rng.random_entry_in(range(1, evl))
        vsgpr = self.get_random_reg(instr.reg_manager, "Int")
        self.write_pre(f"\tli {vsgpr},{vstart}")
        self.write_pre(f"\tcsrw vstart, {vsgpr} # Set first element (vstart) to {vstart}")

    def lookup_and_set_fixedpoint_rounding_mode(self, instr):
        if "fixed" not in instr.group:
            return
        rounding_mode_map = {"rnu": "0x0", "rne": "0x1", "rdn": "0x2", "rod": "0x3"}
        rounding_mode = rounding_mode_map[instr.config.vxrm]
        self.write_pre(f"\tcsrwi vxrm, {rounding_mode} # Set rounding mode to {instr.config.vxrm}")
        self.write_pre("\tcsrwi vxsat, 0x0 # Reset saturation bit")
