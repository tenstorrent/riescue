# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import copy

from .base import VecLoadStoreBase, VectorInstrSetup
from .utilities import choose_randomly_but_not, no_overlap


class VecVRegVRegSetup(VecLoadStoreBase):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def extract_eew(self, instr_name):
        assert "ei" in instr_name
        name = copy.deepcopy(instr_name)
        name = name[: name.find(".v")]
        name = name[::-1]
        name = name[: name.find("i")]
        name = name[::-1]
        index_bit_width = int(name)
        self.eew = index_bit_width

        return index_bit_width

    def pre_setup(self, instr):
        mask_enabled = False
        vm = None
        ph_use_list = []
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr.reg_manager.reserve_reg("v0", "Vector")
            vm = self.get_operand(instr, "vm")  # NOTE Don't randomize, it has to be v0
            vm.name = "v0"
            ph_use_list.append(vm.name)

        """
        #   The vrgather.vv form uses SEW/LMUL for both the data and indices. The vrgatherei16.vv form uses SEW/LMUL for the data in vs2 but EEW=16 and EMUL = (16/SEW)*LMUL for the indices in vs1.
        """
        payload_reg = ""
        vs2_loaded = False
        if "16" in instr.name:
            instr.reg_manager.reinit_vregs()

            self.extract_config(instr)
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.vector_config(instr, payload_reg, False)

            # TODO randomize the vs2 operand?
            self.work_vreg = self.setup_ls_vec_operand(instr, "vs2", payload_reg, prohibited_reuse=ph_use_list, return_operand=True)
            vs2_loaded = True
        else:
            payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)
            instr._fields["vs2"].name = self.work_vreg

        # Either way vs2 will be the same as work_vreg

        vs2 = self.get_operand(instr, "vs2")

        if mask_enabled:
            instr._fields["vs1"].randomize(prohibit_reuse=[vm.name])
        else:
            instr._fields["vs1"].randomize()
        vs1 = self.get_operand(instr, "vs1")

        vd_prohibs = [self.work_vreg]
        if "vrgather" in instr.name:
            vd_prohibs.append(vs2.name)
            vd_prohibs.append(vs1.name)
            instr._fields["vd"].randomize(prohibit_reuse=[vs2.name, vs1.name, self.work_vreg])

        if mask_enabled:
            vd_prohibs.append(vm.name)

        instr._fields["vd"].randomize(prohibit_reuse=vd_prohibs)
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg, new=True)
        if not vs2_loaded:
            self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, new=False)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, new=False)
        self.finalize_vreg_initializations(instr)

        tail = ""
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)
            tail = f", {vm.name}.t"

        self.lookup_and_set_fixedpoint_rounding_mode(instr)
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs1.name}, {vs2.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        if instr.name.startswith("vm"):  # NOTE mask writing instructions always output to one vreg regardless of vtype
            self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, working_eew=8, working_emul=min(1, self.vlmul))
        else:
            self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecVRegVRegMaskSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)
        mask_enabled = False
        vm = None
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr.reg_manager.reserve_reg("v0", "Vector")
            vm = self.get_operand(instr, "vm")
            vm.name = "v0"

        if "vadc" in instr.name or "vmerge" in instr.name or "vsbc" in instr.name:
            instr._fields["vd"].randomize(prohibit_reuse=["v0", self.work_vreg])
            instr._fields["vs1"].randomize(prohibit_reuse=["v0"])
            instr._fields["vs2"].randomize(prohibit_reuse=["v0"])
        else:
            instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])
            instr._fields["vs1"].randomize()
            instr._fields["vs2"].randomize()

        vs1 = self.get_operand(instr, "vs1")
        vs2 = self.get_operand(instr, "vs2")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        tail = ""
        if mask_enabled:
            tail = f", {vm.name}"

        self.lookup_and_set_fixedpoint_rounding_mode(instr)
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs1.name}, {vs2.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecVRegVRegMaskExplicitSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)

        self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]

        vm = lambda: None
        vm.name = "v0"
        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        vs1 = lambda: None
        vs1.name = "nothing"

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd.name = "v" + str(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        vs1_choice = self._rng.choice(self.base_available_vregs)
        vs1.name = "v" + str(vs1_choice)
        instr._fields["vs1"].name = vs1.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs, [vd_choice])
        self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        if "vm" in instr._fields:
            self.load_v0_mask(instr, payload_reg)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs1.name}, {vs2.name}, {vm.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecVRegMaskVRegMaskSetup(VectorInstrSetup):
    "Instructions wuth names that end with .mm"

    def __init__(self, resource_db):
        super().__init__(resource_db)
        self.vsew = 64
        self.vlmul = 1

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr, overload_vsew=64, overload_vlmul=1)

        instr._fields["vs1"].randomize()
        instr._fields["vs2"].randomize()
        instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])

        vs1 = self.get_operand(instr, "vs1")
        vs2 = self.get_operand(instr, "vs2")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)

        vreg_initializer = self.get_vreg_initializer(instr, new=False, label_addendum="")
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs1.name}, {vs2.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, working_eew=64, working_emul=1)  # note that all .mm cases only work on one register group


class VecWideningSetup(VectorInstrSetup):

    def __init__(self, resource_db, scalar_second, scalar_third, imm_third, mask_required, no_overlap=False):
        self.scalar_second = scalar_second
        self.scalar_third = scalar_third
        self.imm_third = imm_third
        self.mask_required = mask_required
        self.no_overlap = no_overlap
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)

        tail = ""
        vm = lambda: None
        vm.name = "v0"
        if self.mask_required or (hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask"):
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]
            tail = f", {vm.name}.t"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        # just to get something with an attribute name compatible with code that expects that.
        vd = lambda: None
        vd.name = "nothing"
        vs1 = lambda: None
        vs1.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        rs1 = lambda: None
        rs1.name = "nothing"
        third_operand = lambda: None
        third_operand.name = "nothing"
        third_operand_scalar = False
        second_operand = lambda: None
        second_operand.name = "nothing"
        second_operand_scalar = False

        if "rs1" in instr._fields:
            instr._fields["rs1"].randomize()
            rs1.value = self.get_operand(instr, "rs1").value
            rs1.name = instr._fields["rs1"].name
            self.write_pre(f"\tli {rs1.name}, {rs1.value}")

        if self.scalar_second:
            second_operand_scalar = True
            second_operand = self.get_operand(instr, "rs1")
        elif self.scalar_third:
            third_operand_scalar = True
            third_operand = self.get_operand(instr, "rs1")
        elif self.imm_third:
            third_operand.name = hex(self.get_operand(instr, "zimm5").value)

        has_scalar_operand = second_operand_scalar or third_operand_scalar

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        # In widening the destination has to be aligned with twice the vlmul
        useable_scaled_vlmul = max(1, 2 * self.vlmul)
        self.widened_available_regs = [reg for reg in self.base_available_vregs if reg % useable_scaled_vlmul == 0 and no_overlap(vs2_choice, reg, useable_scaled_vlmul)]
        assert len(self.widened_available_regs) > 0, f"No widened available registers, config: {self.give_config()}"

        vd_choice = self._rng.choice(self.widened_available_regs)
        for reg in range(vd_choice, vd_choice + useable_scaled_vlmul, 1):
            if reg in self.base_available_vregs:
                self.base_available_vregs.remove(reg)
        self.widened_available_regs.remove(vd_choice)
        vd.name = "v" + str(vd_choice)
        instr._fields["vd"].name = vd.name

        if not has_scalar_operand and not self.imm_third:
            if self.no_overlap:
                # vs1 cannot overlap with vs2
                int_lmul = int(max(1, self.vlmul))
                for reg in range(vs2_choice, vs2_choice + int_lmul, 1):
                    if reg in self.base_available_vregs:
                        self.base_available_vregs.remove(reg)
            vs1_choice = self._rng.choice(self.base_available_vregs)
            vs1.name = "v" + str(vs1_choice)
            instr._fields["vs1"].name = vs1.name
            second_operand = vs2
            third_operand = vs1
        else:
            if second_operand_scalar:
                third_operand = vs2
            else:
                second_operand = vs2

        work_reg_choice = 0
        if len(self.widened_available_regs) > 0:
            work_reg_choice = choose_randomly_but_not(self.resource_db, self.widened_available_regs, [vd_choice])
        self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name=self.work_vreg, fp=False)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=False)
        if not has_scalar_operand and not self.imm_third:
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg, work_vreg_name=self.work_vreg, fp=False)

        self.finalize_vreg_initializations(instr)

        if self.mask_required:
            self.load_v0_mask(instr, payload_reg)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {second_operand.name}, {third_operand.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecNarrowingSetup(VectorInstrSetup):

    def __init__(self, resource_db, variant):
        self._variant = variant
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)

        mask_enabled = False
        vm = lambda: None
        vm.name = "v0"
        tail = ""
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]
            tail = f", {vm.name}.t"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        emul = 2 * self.vlmul
        eew = 2 * self.vsew

        if emul not in self.resource_db.supported_lmuls or eew not in self.resource_db.supported_sews:
            raise ValueError(f"Unsupported EEW or EMUL value for narrowing op : eew {eew} emul : {emul}")

        useable_scaled_vlmul = max(1, emul)
        self.narrowed_available_regs = [reg for reg in self.base_available_vregs if reg % useable_scaled_vlmul == 0]

        self.write_pre("\tcsrrw x0,vxrm,x0")
        self.write_pre("\tcsrr x1,vxrm")

        vd = lambda: None
        vd.name = "nothing"
        vs1 = lambda: None
        vs1.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        rs1 = lambda: None
        rs1.name = "nothing"
        imm = None

        vd_choice = self._rng.choice(self.base_available_vregs)
        self.base_available_vregs.remove(vd_choice)
        # Make it impossible to overlab with different eew destination
        nearest_avail = vd_choice - (vd_choice % useable_scaled_vlmul)
        if nearest_avail in self.narrowed_available_regs:
            self.narrowed_available_regs.remove(nearest_avail)
        vd.name = "v" + str(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.narrowed_available_regs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name
        self.narrowed_available_regs.remove(vs2_choice)

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = self._rng.choice(self.base_available_vregs)
        self.work_vreg = "v" + str(work_reg_choice)

        self.old_config = self.give_config()
        temp_config = {"vsew": eew, "vlmul": emul, "num_vregs": self.num_vregs, "vlen": self.vlen, "vl": self.vl}
        self.replace_config(instr, temp_config, False, payload_reg)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=False)

        if self._variant == "wi":
            imm = self.get_operand(instr, "simm5")
            imm.value = self._rng.randint(0, 31)
        elif self._variant == "wx":
            if "rs1" in instr._fields:
                instr._fields["rs1"].randomize()
                rs1.value = self.get_operand(instr, "rs1").value
                rs1.name = instr._fields["rs1"].name
                self.write_pre(f"\tli {rs1.name}, {rs1.value}")
        elif self._variant == "wv":
            vs1_choice = self._rng.choice(self.narrowed_available_regs)
            self.narrowed_available_regs.remove(vs1_choice)
            vs1.name = "v" + str(vs1_choice)
            instr._fields["vs1"].name = vs1.name
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg, work_vreg_name=self.work_vreg, fp=False)
        else:
            raise ValueError(f"Unsupported Variant Type : {self._variant}")

        self.finalize_vreg_initializations(instr)
        dont_restore = False
        self.restore_config(instr, dont_restore, payload_reg)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        self.lookup_and_set_fixedpoint_rounding_mode(instr)
        self.write_pre(f"{instr.label} :")
        if self._variant == "wi":
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {str(imm.value)}{tail}")
        elif self._variant == "wx":
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}{tail}")
        elif self._variant == "wv":
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {vs1.name}{tail}")
        else:
            raise ValueError(f"Unsupported Variant Type : {self._variant}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecIntExtensionSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")

        # Because these instructions are widening, we need to make sure the source eew is eligible.
        fractional_divisor_to_vsewmin = {1: 8, 2: 16, 4: 32, 8: 64}
        # last character is fractional divisor
        fractional_divisor = int(instr.name[-1])
        self.vsew = max(self.vsew, fractional_divisor_to_vsewmin[fractional_divisor])
        fractional_divisor_to_vlmulmin = {1: 0.125, 2: 0.25, 4: 0.5, 8: 1}
        self.vlmul = max(self.vlmul, fractional_divisor_to_vlmulmin[fractional_divisor])

        self.vector_config(instr, payload_reg, False)

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

        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd.name = "v" + str(vd_choice)
        self.base_available_vregs.remove(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs, [vd_choice])  # self.base_available_vregs[0]
        self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg)
        self.finalize_vreg_initializations(instr)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)
