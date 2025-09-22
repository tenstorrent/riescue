# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from .base import VectorInstrSetup
from .utilities import choose_randomly_but_not
from riescue.compliance.config import Resource


class OPMVV_VReg_Mask_Setup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        mask_enabled = False
        vm = lambda: None
        vm.name = "v0"
        tail = ""
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]
            mask_enabled = True
            tail = f", {vm.name}.t"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd = lambda: None
        vd.name = "v" + str(vd_choice)
        self.base_available_vregs.remove(vd_choice)
        instr._fields["vd"].name = vd.name  # Make the data the instruction retains match the selection

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2 = lambda: None
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        self.work_vreg = "v" + str(choose_randomly_but_not(self.resource_db, self.base_available_vregs, [vd_choice]))

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        if instr.name.startswith("vm") and not instr.name.startswith("vmv"):  # NOTE mask writing instructions always output to one vreg regardless of vtype
            self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, working_eew=8, working_emul=min(1, self.vlmul))
        else:
            self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class OPMVV_XRegDest_VReg_Mask_Setup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        vs2 = lambda: None
        vs2.name = None
        vm = lambda: None
        vm.name = "v0"

        mask_enabled = False
        tail = ""
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            tail = f", {vm.name}.t"
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        rd = self.get_operand(instr, "rd")  # scalar regs should be randomized automatically

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        self.write_pre(f"{instr.label} :")
        if instr.name == "vcpop.m":
            self.write_pre(f"\tvpopc.m {rd.name}, {vs2.name}{tail}")  # FIXME Compiler needs to be updated this is the old mnemonic
        else:
            self.write_pre(f"\t{instr.name} {rd.name}, {vs2.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class OPMVV_Mask_Setup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"
            self.get_operand(instr, "vd").randomize(prohibit_reuse=["v0"])
        else:
            self.get_operand(instr, "vd").randomize()

        vd = self.get_operand(instr, "vd")
        assert vd.name == instr._fields["vd"].name

        vm = self.get_operand(instr, "vm")

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        tail = ""  # Add nothing if not masking
        if mask_enabled:
            tail = f", {vm.name}.t"

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)
