# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from .base import VectorInstrSetup
from riescue.compliance.lib.instr_setup.utils import FpLoadUtil


class VecImmSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])

        simm5 = self.get_operand(instr, "simm5")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        if instr.name.startswith("vmv"):
            # set vtype to vlmax, sew 64, max(1, current lmul)
            old_vsew = self.vsew
            old_vlmul = self.vlmul
            old_vl = self.vl
            self.vl = "vlmax"
            self.vsew = 64
            self.vlmul = max(1, old_vlmul)
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.vector_config(instr, payload_reg, False)

            self.write_pre(f"\tvmv.v.x {vd.name}, x0")

            # set vtype back to original flow
            self.vsew = old_vsew
            self.vlmul = old_vlmul
            self.vl = old_vl
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.vector_config(instr, payload_reg, False)
        self.finalize_vreg_initializations(instr)

        simm5.value = self._rng.randint(-16, 15)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {str(simm5.value)}")

    def post_setup(self, modified_arch_state, instr):
        if instr.name.startswith("vmv"):
            try:
                self.numerical_vl = int(self.vl)
            except Exception:
                # FIXME: except should be specific
                if self.vl == "vlmax" or self.vl == "zero":
                    self.numerical_vl = 32
                else:
                    self.numerical_vl = 9999
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, vtype_needs_changing=True if instr.name.startswith("vmv") else False)


class VecVRegXRegSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        mask_required = False
        tail = ""
        vm = lambda: None
        vm.name = "v0"
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_required = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"
            tail = f", {vm.name}.t"

        vd_prohibited = [self.work_vreg]
        vs2_prohibited = []
        if mask_required:
            vd_prohibited.append("v0")
            vs2_prohibited.append("v0")

        instr._fields["vd"].randomize(prohibit_reuse=vd_prohibited)
        vd = self.get_operand(instr, "vd")

        if "slide" in instr.name or "gather" in instr.name:
            vs2_prohibited.append(vd.name)

        instr._fields["vs2"].randomize(prohibit_reuse=vs2_prohibited)
        vs2 = self.get_operand(instr, "vs2")
        rs1 = self.get_operand(instr, "rs1")

        # If we have a vrgather, that instruction is allowed to access elements up through vlmax regardless of the vl setting.

        if "slide" in instr.name:
            # required initialization of bit64 logic
            old_vsew = self.vsew
            self.vsew = 64
            old_vlmul = self.vlmul
            self.vlmul = max(1, self.vlmul)
            vreg_initializer = self.get_vreg_initializer(instr, new=False, label_addendum="")

            element_mask = int("0x" + "".join(["ff" for _ in range(self.vsew // 8)]), 16)

            vl = self.vl

            max_index = 0
            if vl == "vlmax" or vl == "zero":
                max_index = int((self.vlen * max(1, self.vlmul)) // self.vsew)
            else:
                if instr.config.vset_instr == "vsetivli":
                    max_index = min(self.vl, 31)
                else:
                    max_index = self.vl

            self.vreg_elvals[vs2.name] = []
            self.vreg_elvals[vd.name] = []
            element_index = 0

            # vs2
            while element_index < max_index:
                data = self.generate_elements(self.vsew, self.vsew, element_mask)
                for elt in data:
                    data_string = str(hex(elt))
                    self.vreg_elvals[vs2.name].append(data_string)
                    element_index += 1

            element_index = 0

            # vd
            while element_index < max_index:
                data = self.generate_elements(self.vsew, self.vsew, element_mask)
                for elt in data:
                    data_string = str(hex(elt))
                    self.vreg_elvals[vd.name].append(data_string)
                    element_index += 1

            vreg_initializer.add_vreg(vs2.name, self.vreg_elvals[vs2.name], max_index, self.vsew, self.vlmul)
            vreg_initializer.add_vreg(vd.name, self.vreg_elvals[vd.name], max_index, self.vsew, self.vlmul)

            self.write_pre(vreg_initializer.get_mem_setups())
            self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
            self.write_data(instr, vreg_initializer.get_mem_inits())
            self.vsew = old_vsew
            self.vlmul = old_vlmul
        else:
            self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
            self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
            self.finalize_vreg_initializations(instr)

        if mask_required:
            self.load_v0_mask(instr, payload_reg)

        # FIXME the test framework needs an instruction to modify state to be testable. Slide instructions have the potential to not modify any state
        # depending on the rs1 or uimm arguments. This means the test framework limits the test scenarios for these instructions beyond what
        # the architecture calls for.
        if "vslide" in instr.name:
            if not isinstance(self.vl, str):
                rs1.value = min(self.vl - 1, rs1.value & 0b11)
            else:
                rs1.value = rs1.value & 0b11
        elif "vrgather" in instr.name:
            if not isinstance(self.vl, str):
                rs1.value = (rs1.value % self.vl) + self._rng.random_entry_in([0, 1])  # If rs1 is greater than vl, then the destination just gets filled with zeros, not terribly interesting.
            else:
                rs1.value = (rs1.value % 30) + self._rng.random_entry_in([0, 1])

        self.lookup_and_set_fixedpoint_rounding_mode(instr)
        self.write_pre(f"\tli {rs1.name}, {str(hex(rs1.value))}")
        self.write_pre(f"{instr.label} :")

        if instr.name in ["vmacc.vx", "vmadd.vx"]:  # These instructions have the rs1 and vs2 operands swapped
            self.write_pre(f"\t{instr.name} {vd.name}, {rs1.name}, {vs2.name}{tail}")
        else:
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        if "vslide" in instr.name:
            self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, working_eew=64, working_emul=1)
        elif "gather" in instr.name:
            self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, working_eew=64)
        else:
            self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecVRegXRegMaskExplicitSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        rs1 = self.get_operand(instr, "rs1")
        instr._fields["vm"].name = "v0"
        vm = self.get_operand(instr, "vm")

        if instr.name.startswith(("vadc", "vmadc", "vmerge", "vsbc", "vmsbc")):
            instr._fields["vd"].randomize(prohibit_reuse=["v0", self.work_vreg])
            instr._fields["vs2"].randomize(prohibit_reuse=["v0"])
        else:
            instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])
            instr._fields["vs2"].randomize()

        vs2 = self.get_operand(instr, "vs2")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.finalize_vreg_initializations(instr)
        if "vm" in instr._fields:
            self.load_v0_mask(instr, payload_reg)

        self.write_pre(f"\tli {rs1.name}, {str(hex(rs1.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}, {vm.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecVRegImmSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        mask_required = False
        vm = None
        if hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_required = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"

        vd = None
        vs2 = None

        if not mask_required:
            instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])
            instr._fields["vs2"].randomize()
        else:
            instr._fields["vd"].randomize(prohibit_reuse=["v0", self.work_vreg])
            instr._fields["vs2"].randomize(prohibit_reuse=["v0"])

        vm = self.get_operand(instr, "vm")
        vd = self.get_operand(instr, "vd")
        vs2 = self.get_operand(instr, "vs2")

        simm5 = self.get_operand(instr, "simm5")
        unsigned_6bit = False
        if simm5 is None:
            unsigned_6bit = True
            simm5 = self.get_operand(instr, "zimm6hi")

        if "vslide" in instr.name:
            # required initialization of bit64 logic
            old_vsew = self.vsew
            self.vsew = 64
            old_vlmul = self.vlmul
            self.vlmul = max(1, self.vlmul)
            vreg_initializer = self.get_vreg_initializer(instr, new=False, label_addendum="")

            element_mask = int("0x" + "".join(["ff" for _ in range(self.vsew // 8)]), 16)

            vl = self.vl

            max_index = 0
            if vl == "vlmax" or vl == "zero":
                max_index = int((self.vlen * max(1, self.vlmul)) // self.vsew)
            else:
                if instr.config.vset_instr == "vsetivli":
                    max_index = min(self.vl, 31)
                else:
                    max_index = self.vl

            self.vreg_elvals[vs2.name] = []
            self.vreg_elvals[vd.name] = []
            element_index = 0

            # vs2
            while element_index < max_index:
                data = self.generate_elements(self.vsew, self.vsew, element_mask)
                for elt in data:
                    data_string = str(hex(elt))
                    self.vreg_elvals[vs2.name].append(data_string)
                    element_index += 1

            element_index = 0

            # vd
            while element_index < max_index:
                data = self.generate_elements(self.vsew, self.vsew, element_mask)
                for elt in data:
                    data_string = str(hex(elt))
                    self.vreg_elvals[vd.name].append(data_string)
                    element_index += 1

            vreg_initializer.add_vreg(vs2.name, self.vreg_elvals[vs2.name], max_index, self.vsew, self.vlmul)
            vreg_initializer.add_vreg(vd.name, self.vreg_elvals[vd.name], max_index, self.vsew, self.vlmul)

            self.write_pre(vreg_initializer.get_mem_setups())
            self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
            self.write_data(instr, vreg_initializer.get_mem_inits())
            self.vsew = old_vsew
            self.vlmul = old_vlmul
        else:
            self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, require_one_element=True)
            self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, require_one_element=True)
            self.finalize_vreg_initializations(instr)

        if mask_required:
            self.load_v0_mask(instr, payload_reg)

        # FIXME the test framework needs an instruction to modify state to be testable. Slide instructions have the potential to not modify any state
        # depending on the rs1 or uimm arguments. This means the test framework limits the test scenarios for these instructions beyond what
        # the architecture calls for.
        if "vslide" in instr.name:
            if not isinstance(self.vl, str):
                simm5.value = self._rng.randint(0, min(self.vl - 1, 30))
            else:
                simm5.value = self._rng.randint(0, min(max(self.vlmul, 1) * self.vlen // self.vsew - 1, 30))
        elif "vrgather" in instr.name:
            simm5.value = self._rng.randint(0, 30)
        elif unsigned_6bit:
            simm5.value = self._rng.randint(0, 63)
        elif instr.name.startswith("vs") and instr.name not in ["vsadd.vi", "vsaddu.vi"]:
            simm5.value = self._rng.randint(0, 31)
        else:
            simm5.value = self._rng.randint(-16, 15)

        self.lookup_and_set_fixedpoint_rounding_mode(instr)
        self.write_pre(f"{instr.label} :")
        tail = ""
        if mask_required:
            tail = f", {vm.name}.t"
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {str(simm5.value)}{tail}")

    def post_setup(self, modified_arch_state, instr):
        if "vslide" in instr.name or "gather" in instr.name:
            self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, working_eew=64, working_emul=max(1, self.vlmul))
        else:
            self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecVRegImmMaskSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        simm5 = self.get_operand(instr, "simm5")

        if "vadc" in instr.name or "vmerge" in instr.name or "vsbc" in instr.name:
            instr._fields["vd"].randomize(prohibit_reuse=["v0", self.work_vreg])
            instr._fields["vs2"].randomize(prohibit_reuse=["v0"])
        else:
            instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])
            instr._fields["vs2"].randomize()
        vs2 = self.get_operand(instr, "vs2")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        self.load_v0_mask(instr, payload_reg)

        simm5.value = self._rng.randint(-16, 15)

        self.lookup_and_set_fixedpoint_rounding_mode(instr)
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {str(simm5.value)}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecVRegImmMaskExplicitSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        simm5 = self.get_operand(instr, "simm5")
        instr._fields["vm"].name = "v0"
        vm = self.get_operand(instr, "vm")

        if instr.name.startswith(("vadc", "vmadc", "vmerge")):
            instr._fields["vd"].randomize(prohibit_reuse=["v0", self.work_vreg])
            instr._fields["vs2"].randomize(prohibit_reuse=["v0"])
        else:
            instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])
            instr._fields["vs2"].randomize()

        vs2 = self.get_operand(instr, "vs2")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        if "vm" in instr._fields:
            self.load_v0_mask(instr, payload_reg)

        simm5.value = self._rng.randint(-16, 15)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {str(simm5.value)}, {vm.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecRegRegSetup(VectorInstrSetup):

    def pre_setup(self, instr):
        self._pre_setup_instrs = []
        rs1 = instr.srcs[0]
        rs2 = instr.srcs[1]
        rd = instr.dests[0]

        self.write_pre(f"\tli {rs1.name}, {str(hex(rs1.value))}")
        self.write_pre(f"\tli {rs2.name}, {str(hex(rs2.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {rd.name},{rs1.name},{rs2.name}")


class VecRegImmSetup(VectorInstrSetup):

    def pre_setup(self, instr):
        rs1 = instr.srcs[0]
        imm = instr.imms[0]
        rd = instr.dests[0]

        self.write_pre(f"\tli {rs1.name}, {str(hex(rs1.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {rd.name},{rs1.name},{str(hex(imm.value))}")


class VecImmImmSetup(VectorInstrSetup):

    def pre_setup(self, instr):
        imm1 = instr.imms[0]
        imm2 = instr.imms[1]
        rd = instr.dests[0]

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {rd.name}, {str(hex(imm2.value))}, {str(hex(imm1.value))}")
