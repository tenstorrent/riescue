# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import copy

from .base import VectorInstrSetup
from .utilities import choose_randomly_but_not
from riescue.compliance.lib.instr_setup.floating_point import FpRegRegSetup
from riescue.compliance.lib.instr_setup.utils import FpLoadUtil


class VecXRegSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])

        rs1 = self.get_operand(instr, "rs1")
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

        self.write_pre(f"\tli {rs1.name}, {str(hex(rs1.value))}")
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {rs1.name}")

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


class VecFPRegSetup(VectorInstrSetup, FpLoadUtil):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])

        rs1 = self.get_operand(instr, "rs1")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.finalize_vreg_initializations(instr)
        self.load_fp_regs_nanbox_in_double(self.vsew // 8, [rs1.name], instr)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0 # Clear fflags")
        self.write_pre("\tcsrr x1,fflags")
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {rs1.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts=1)


class VecXRegDestSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        instr._fields["vs2"].randomize(prohibit_reuse=[self.work_vreg])

        rd = self.get_operand(instr, "rd")
        vs2 = self.get_operand(instr, "vs2")

        temp_config = copy.deepcopy(self.give_config())
        temp_config["vl"] = "vlmax"
        self.replace_config(instr, temp_config, False, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.finalize_vreg_initializations(instr)
        self.restore_config(instr, False, payload_reg)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {rd.name}, {vs2.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecFPRegDestSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def load_fp_regs_nanbox_in_double(self, a_num_bytes, a_fp_reg_names, a_instr, a_values=None, a_postfix=None):
        return FpLoadUtil.load_fp_regs_nanbox_in_double(self, a_num_bytes, a_fp_reg_names, a_instr, a_values, a_postfix)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)
        self.num_bytes = self.vsew // 8

        instr._fields["vs2"].randomize(prohibit_reuse=[self.work_vreg])

        rd = self.get_operand(instr, "rd")
        vs2 = self.get_operand(instr, "vs2")

        temp_config = copy.deepcopy(self.give_config())
        temp_config["vl"] = "vlmax"
        self.replace_config(instr, temp_config, False, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, fp=True)
        self.finalize_vreg_initializations(instr)
        self.restore_config(instr, False, payload_reg)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0 # Clear fflags")
        self.write_pre("\tcsrr x1,fflags")
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {rd.name}, {vs2.name}")

    def post_setup(self, modified_arch_state, instr):
        FpRegRegSetup.post_setup(self, modified_arch_state, instr)


class VecVRegSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        # VMV V V
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        instr._fields["vs1"].randomize()
        instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg])

        vd = self.get_operand(instr, "vd")
        vs1 = self.get_operand(instr, "vs1")

        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg)
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

            # vmv.v.x to 0 all source regs
            self.write_pre(f"\tvmv.v.x {vs1.name}, x0")
            self.write_pre(f"\tvmv.v.x {vd.name}, x0")

            # set vtype back to original flow
            self.vsew = old_vsew
            self.vlmul = old_vlmul
            self.vl = old_vl
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.vector_config(instr, payload_reg, False)
        self.finalize_vreg_initializations(instr)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs1.name}")

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


class VecWholeRegMoveSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)

        # find the number that preceds the r. in the instruction name
        num_regs = instr.name[: instr.name.find("r.v")]
        num_regs = int(num_regs[-1])
        alignment = max(num_regs, self.vlmul)

        self.base_available_vregs = [reg for reg in range(0, 32) if reg % alignment == 0]

        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd.name = "v" + str(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs, [vd_choice])
        self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, ignore_alignment=True)
        self.finalize_vreg_initializations(instr)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}")

    def post_setup(self, modified_arch_state, instr):
        self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VtypeIndepVecWholeRegMoveSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        # payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)
        self.extract_config(instr)

        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        self.work_vreg = None

        # find the number that preceds the r. in the instruction name
        num_regs = instr.name[: instr.name.find("r.v")]
        num_regs = int(num_regs[-1])
        alignment = max(num_regs, self.vlmul)

        self.base_available_vregs = [reg for reg in range(0, 32) if reg % alignment == 0]

        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd.name = "v" + str(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs, [vd_choice])
        self.work_vreg = "v" + str(work_reg_choice)

        # find index in name of r.v
        index = instr.name.find("r.v")
        nr = int(instr.name[index - 1])
        acting_emul = nr

        temp_config = copy.deepcopy(self.give_config())
        temp_config["vlmul"] = acting_emul
        self.effective_vector_length = int(acting_emul * self.vlen // self.vsew)
        temp_config["vl"] = "vlmax"

        self.replace_config(instr, temp_config, False, payload_reg)
        # self.vector_config(instr, payload_reg, False)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, ignore_alignment=True)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name=self.work_vreg, ignore_alignment=True)
        self.finalize_vreg_initializations(instr)

        # restore config to the old one
        self.restore_config(instr, False, payload_reg)

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}")

    def post_setup(self, modified_arch_state, instr):
        self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts=self.effective_vector_length)
