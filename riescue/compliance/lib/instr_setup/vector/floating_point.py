# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from enum import Enum

from .base import VectorInstrSetup
from .utilities import VecInstrVregInitializer, choose_randomly_but_not, no_overlap
from riescue.compliance.lib.instr_setup.utils import FpLoadUtil, generate_a_fp_value
from riescue.compliance.config import Resource


class VecFpConvertSetup(VectorInstrSetup):
    class DataTreatment(Enum):
        NARROWING = 1
        WIDENING = 2
        SINGLE_WIDTH = 3

    class TypeChange(Enum):
        F2I = 1
        I2F = 2
        F2F = 3

    OPERAND_TYPE_MAP = {TypeChange.F2I: {"vs2": "float", "vd": "int"}, TypeChange.I2F: {"vs2": "int", "vd": "float"}, TypeChange.F2F: {"vs2": "float", "vd": "float"}}

    NARROWING_MATCHERS = ["vfn"]
    WIDENING_MATCHERS = ["vfw"]

    F2I_MATCHERS = [".xu.f.", ".x.f."]
    I2F_MATCHERS = [".f.xu.", ".f.x."]
    F2F_MATCHERS = [".f.f."]

    def get_conversion_type(self, instr_name):
        data_treatment = None
        type_change = None
        if any(matcher in instr_name for matcher in VecFpConvertSetup.NARROWING_MATCHERS):
            data_treatment = VecFpConvertSetup.DataTreatment.NARROWING
        elif any(matcher in instr_name for matcher in VecFpConvertSetup.WIDENING_MATCHERS):
            data_treatment = VecFpConvertSetup.DataTreatment.WIDENING
        else:
            data_treatment = VecFpConvertSetup.DataTreatment.SINGLE_WIDTH

        if any(matcher in instr_name for matcher in VecFpConvertSetup.F2I_MATCHERS):
            type_change = VecFpConvertSetup.TypeChange.F2I
        elif any(matcher in instr_name for matcher in VecFpConvertSetup.I2F_MATCHERS):
            type_change = VecFpConvertSetup.TypeChange.I2F
        elif any(matcher in instr_name for matcher in VecFpConvertSetup.F2F_MATCHERS):
            type_change = VecFpConvertSetup.TypeChange.F2F
        else:
            type_change = VecFpConvertSetup.TypeChange.F2I

        float_type = "regular"
        if "bf16" in instr_name:
            float_type = "bf16"

        return data_treatment, type_change, float_type

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        self.extract_config(instr)
        data_treatment, type_change, float_type = self.get_conversion_type(instr.name)

        vsewmax = 64
        vsewmin = 8
        if data_treatment == VecFpConvertSetup.DataTreatment.NARROWING or data_treatment == VecFpConvertSetup.DataTreatment.WIDENING:
            vsewmax = 32

        if data_treatment == VecFpConvertSetup.DataTreatment.SINGLE_WIDTH and (type_change == VecFpConvertSetup.TypeChange.F2I or type_change == VecFpConvertSetup.TypeChange.I2F):
            vsewmin = 16

        if data_treatment == VecFpConvertSetup.DataTreatment.NARROWING and (type_change == VecFpConvertSetup.TypeChange.F2F or type_change == VecFpConvertSetup.TypeChange.I2F):
            vsewmin = 16

        if data_treatment == VecFpConvertSetup.DataTreatment.WIDENING and (type_change == VecFpConvertSetup.TypeChange.F2F or type_change == VecFpConvertSetup.TypeChange.F2I):
            vsewmin = 16

        self.vsew = min(self.vsew, vsewmax)
        self.vsew = max(self.vsew, vsewmin)

        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        self.vector_config(instr, payload_reg, False)

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr.reg_manager.reserve_reg("v0", "Vector")
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        # No real purpose to these actually, just to get something with an attribute name compatible with code that expects that.
        vm = lambda: None
        vm.name = "v0"
        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"

        vs2_fp = True if type_change in [VecFpConvertSetup.TypeChange.F2F, VecFpConvertSetup.TypeChange.F2I] else False

        if data_treatment == VecFpConvertSetup.DataTreatment.NARROWING:
            vd_choice = self._rng.choice(self.base_available_vregs)
            vd.name = "v" + str(vd_choice)
            self.base_available_vregs.remove(vd_choice)
            instr._fields["vd"].name = vd.name

            # In narrowing, source has to be aligned with twice the vlmul
            useable_scaled_vlmul = max(1, 2 * self.vlmul)
            self.narrowed_available_regs = [reg for reg in self.base_available_vregs if reg % useable_scaled_vlmul == 0 and no_overlap(vd_choice, reg, useable_scaled_vlmul)]
            vs2_choice = self._rng.choice(self.narrowed_available_regs)
            vs2.name = "v" + str(vs2_choice)
            instr._fields["vs2"].name = vs2.name

            work_reg_choice = self._rng.choice(self.narrowed_available_regs)
            self.work_vreg = "v" + str(work_reg_choice)

            self.old_config = self.give_config()
            temp_config = {"vsew": 2 * self.vsew, "vlmul": self.vlmul * 2, "num_vregs": self.num_vregs, "vlen": self.vlen, "vl": self.vl}
            self.replace_config(instr, temp_config, False, payload_reg)
            self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=vs2_fp)
            self.finalize_vreg_initializations(instr)
            dont_restore = False
            self.restore_config(instr, dont_restore, payload_reg)

        elif data_treatment == VecFpConvertSetup.DataTreatment.SINGLE_WIDTH:
            vd_choice = self._rng.choice(self.base_available_vregs)
            vd.name = "v" + str(vd_choice)
            instr._fields["vd"].name = vd.name

            vs2_choice = self._rng.choice(self.base_available_vregs)
            vs2.name = "v" + str(vs2_choice)
            instr._fields["vs2"].name = vs2.name

            work_reg_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs, [vd_choice])
            self.work_vreg = "v" + str(work_reg_choice)

            self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=vs2_fp)
            self.finalize_vreg_initializations(instr)
        elif data_treatment == VecFpConvertSetup.DataTreatment.WIDENING:
            vs2_choice = self._rng.choice(self.base_available_vregs)
            vs2.name = "v" + str(vs2_choice)
            self.base_available_vregs.remove(vs2_choice)
            instr._fields["vs2"].name = vs2.name

            # In widening the destination has to be aligned with twice the vlmul
            useable_scaled_vlmul = max(1, 2 * self.vlmul)
            self.widened_available_regs = [reg for reg in self.base_available_vregs if reg % useable_scaled_vlmul == 0 and no_overlap(vs2_choice, reg, useable_scaled_vlmul)]
            vd_choice = self._rng.choice(self.widened_available_regs)
            self.widened_available_regs.remove(vd_choice)
            vd.name = "v" + str(vd_choice)
            instr._fields["vd"].name = vd.name

            work_reg_choice = self.widened_available_regs[0]
            self.work_vreg = "v" + str(work_reg_choice)

            self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=vs2_fp)
            self.finalize_vreg_initializations(instr)
        else:
            assert False, "Unknown data treatment"

        tail = ""
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)
            tail = f", {vm.name}.t"

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecFPSplatSetup(VectorInstrSetup, FpLoadUtil):

    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)

        self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        vd = lambda: None
        vd.name = "nothing"
        rs1 = lambda: None
        print(instr._fields, type(instr._fields))
        rs1.name = instr._fields["rs1"].name
        self.load_fp_regs_nanbox_in_double(self.vsew // 8, [rs1.name], instr)

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd.name = "v" + str(vd_choice)
        self.base_available_vregs.remove(vd_choice)
        instr._fields["vd"].name = vd.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = self.base_available_vregs[0]
            self.base_available_vregs.remove(work_reg_choice)
        self.work_vreg = "v" + str(work_reg_choice)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {rs1.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecFPSingleWidthSetup(VectorInstrSetup, FpLoadUtil):

    def __init__(self, resource_db: Resource, scalar_second, scalar_third, v1_before_v2, initialize_vd, mask_required, fp, widening, no_overlap=False):
        self.scalar_second = scalar_second
        self.scalar_third = scalar_third
        self.v1_before_v2 = v1_before_v2
        self.initialize_vd = initialize_vd
        self.mask_required = mask_required
        self.fp = fp
        self.widening = widening
        self.no_overlap = no_overlap
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)

        tail = ""
        vm = lambda: None
        vm.name = "v0"
        if self.mask_required or (hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask"):
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % max(1, self.vlmul) == 0]
            if "vfmerge" not in instr.name:
                tail = f", {vm.name}.t"
            else:
                tail = f", {vm.name}"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % max(1, self.vlmul) == 0]

        # just to get something with an attribute name compatible with code that expects that.
        vd = lambda: None
        vd.name = "nothing"
        vs1 = lambda: None
        vs1.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
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
            rs1.name = instr._fields["rs1"].name
            if self.fp:
                self.load_fp_regs_nanbox_in_double(self.vsew // 8, [rs1.name], instr)
            else:
                self.write_pre(f"\tli {rs1.name}, 0x{self._rng.random_nbit(64):x}")

        if self.scalar_second:
            second_operand_scalar = True
            second_operand = self.get_operand(instr, "rs1")
        elif self.scalar_third:
            third_operand_scalar = True
            third_operand = self.get_operand(instr, "rs1")

        has_scalar_operand = second_operand_scalar or third_operand_scalar

        widened_available_regs = self.base_available_vregs  # [reg for reg in self.base_available_vregs if reg % int(max(1,(2 * self.vlmul))) == 0]
        vd_choice = self._rng.choice(self.base_available_vregs if not self.widening else widened_available_regs)
        if self.widening:
            self.base_available_vregs.remove(vd_choice)
        vd.name = "v" + str(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        if not has_scalar_operand:
            if self.no_overlap:
                # vs1 cannot overlap with vs2
                int_lmul = int(max(1, self.vlmul))
                for reg in range(vs2_choice, vs2_choice + int_lmul, 1):
                    if reg in self.base_available_vregs:
                        self.base_available_vregs.remove(reg)
            vs1_choice = self._rng.choice(self.base_available_vregs)
            vs1.name = "v" + str(vs1_choice)
            instr._fields["vs1"].name = vs1.name

            if self.v1_before_v2:
                second_operand = vs1
                third_operand = vs2
            else:
                second_operand = vs2
                third_operand = vs1
        else:
            if second_operand_scalar:
                third_operand = vs2
            else:
                second_operand = vs2

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs if not self.widening else widened_available_regs, [vd_choice])
        self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)
        if not has_scalar_operand:
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)
        if self.initialize_vd:
            if self.widening is True:
                # Need to have VD match at all times
                self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp, eew=self.vsew * 2)
            else:
                self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        if "red" in instr.name and self.widening is True:
            # set vtype to vlmax, sew 64, max(1, current lmul)
            old_vsew = self.vsew
            old_vlmul = self.vlmul
            self.vsew = 64
            self.vlmul = max(1, old_vlmul)
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.vector_config(instr, payload_reg, False)

            # vmv.v.x to 0 all source regs
            self.write_pre(f"\tvmv.v.x {vs1.name}, x0")
            self.write_pre(f"\tvmv.v.x {vs2.name}, x0")

            # vmv.v.x dep regs + following regs
            self.write_pre(f"\tvmv.v.x {vd.name}, x0")
            if old_vlmul > 1:
                vd_int = int(vd.name[1:])
                next_vd_int = vd_int + old_vlmul
                if next_vd_int < 32:  # no clude why this is happening
                    next_vd = "v" + str(next_vd_int)
                    self.write_pre(f"\tvmv.v.x {next_vd}, x0")

            # set vtype back to original flow
            self.vsew = old_vsew
            self.vlmul = old_vlmul
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.vector_config(instr, payload_reg, False)
        self.finalize_vreg_initializations(instr)

        if self.mask_required:
            self.load_v0_mask(instr, payload_reg)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")

        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {second_operand.name}, {third_operand.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        # NOTE: I know the class is called ...SingleWidth... but it's simpler to just shoehorn that into here.
        active_elts = min(self.numerical_vl, self.vlen * self.vlmul // self.vsew)
        if self.widening is True:
            self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts, working_eew=2 * self.vsew, working_emul=max(1, self.vlmul))
        else:
            self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts)


class VecFpRecSqrtSetup(VectorInstrSetup, FpLoadUtil):

    def __init__(self, resource_db, mask_required, fp):
        self.fp = fp
        self.mask_required = mask_required
        super().__init__(resource_db)

    def pre_setup(self, instr):

        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)
        self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]

        # just to get something with an attribute name compatible with code that expects that.
        vm = lambda: None
        vm.name = "v0"
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
        self.base_available_vregs.remove(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = self.base_available_vregs[0]
            self.base_available_vregs.remove(work_reg_choice)
        self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)
        self.finalize_vreg_initializations(instr)

        if self.mask_required:
            self.load_v0_mask(instr, payload_reg)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")
        self.write_pre(f"{instr.label} :")
        if not self.mask_required:
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}")
        else:
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {vm.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecFpCompareSetup(VectorInstrSetup, FpLoadUtil):

    def __init__(self, resource_db, mask_required, initialize_vd, fp):
        self.fp = fp
        self.mask_required = mask_required
        self.initialize_vd = initialize_vd
        super().__init__(resource_db)

    def pre_setup(self, instr):

        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)
        self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]

        # just to get something with an attribute name compatible with code that expects that.
        vm = lambda: None
        vm.name = "v0"
        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        vs1 = lambda: None
        vs1.name = "nothing"
        rs1 = lambda: None
        rs1.name = "nothing"
        set_vs1 = False

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd.name = "v" + str(vd_choice)
        self.base_available_vregs.remove(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.base_available_vregs)
        vs2.name = "v" + str(vs2_choice)
        self.base_available_vregs.remove(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        if instr.name.endswith(".vf"):
            rs1.name = instr._fields["rs1"].name
            if self.fp:
                self.load_fp_regs_nanbox_in_double(self.vsew // 8, [rs1.name], instr)
            else:
                self.write_pre(f"\tli {rs1.name}, 0x{self._rng.random_nbit(64):x}")
        else:
            vs1_choice = self._rng.choice(self.base_available_vregs)
            vs1.name = "v" + str(vs1_choice)
            self.base_available_vregs.remove(vs1_choice)
            instr._fields["vs1"].name = vs1.name
            set_vs1 = True

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = self.base_available_vregs[0]
            self.base_available_vregs.remove(work_reg_choice)
        self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)
        if set_vs1:
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        if self.initialize_vd:
            self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        self.finalize_vreg_initializations(instr)

        if self.mask_required:
            self.load_v0_mask(instr, payload_reg)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")
        self.write_pre(f"{instr.label} :")
        if not self.mask_required:
            if instr.name.endswith(".vf"):
                self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}")
            else:
                self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {vs1.name}")
        else:
            if instr.name.endswith(".vf"):
                self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}, {vm.name}")
            else:
                self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {vs1.name}, {vm.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecFpWideningAddSubSetup(VectorInstrSetup, FpLoadUtil):

    def __init__(self, resource_db, mask_required, initialize_vd, fp):
        self.fp = fp
        self.mask_required = mask_required
        self.initialize_vd = initialize_vd
        super().__init__(resource_db)

    def pre_setup(self, instr):

        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)
        self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        emul = 2 * self.vlmul
        eew = 2 * self.vsew

        if emul not in self.resource_db.supported_lmuls or eew not in self.resource_db.supported_sews:
            raise ValueError(f"Unsupported EEW or EMUL value for narrowing op : eew {eew} emul : {emul}, instr.label : {instr.label}")

        no_overlap = lambda reg, reg_group_size, new_reg: (reg + reg_group_size) <= new_reg or reg >= (new_reg + reg_group_size)
        useable_scaled_vlmul = max(1, emul)
        self.narrowed_available_regs = [reg for reg in self.base_available_vregs if reg % useable_scaled_vlmul == 0]

        # just to get something with an attribute name compatible with code that expects that.
        vm = lambda: None
        vm.name = "v0"
        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        vs1 = lambda: None
        vs1.name = "nothing"
        rs1 = lambda: None
        rs1.name = "nothing"
        set_vs1 = False

        vd_choice = self._rng.choice(self.narrowed_available_regs)
        vd.name = "v" + str(vd_choice)
        self.narrowed_available_regs.remove(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.narrowed_available_regs)
        self.narrowed_available_regs.remove(vs2_choice)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = self.narrowed_available_regs[0]
            self.narrowed_available_regs.remove(work_reg_choice)
            self.work_vreg = "v" + str(work_reg_choice)

        self.old_config = self.give_config()
        temp_config = {"vsew": eew, "vlmul": emul, "num_vregs": self.num_vregs, "vlen": self.vlen, "vl": self.vl}
        self.replace_config(instr, temp_config, False, payload_reg)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        if instr.name.endswith((".vf", ".wf")):
            rs1.name = instr._fields["rs1"].name
            if self.fp:
                self.load_fp_regs_nanbox_in_double(self.vsew // 8, [rs1.name], instr)
            else:
                self.write_pre(f"\tli {rs1.name}, 0x{self._rng.random_nbit(64):x}")
        else:
            vs1_choice = self._rng.choice(self.narrowed_available_regs)
            vs1.name = "v" + str(vs1_choice)
            self.narrowed_available_regs.remove(vs1_choice)
            instr._fields["vs1"].name = vs1.name
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        if self.initialize_vd:
            self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        self.finalize_vreg_initializations(instr)
        dont_restore = False
        self.restore_config(instr, dont_restore, payload_reg)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")
        self.write_pre(f"{instr.label} :")
        if instr.name.endswith((".vf", ".wf")):
            if instr.name.startswith(("vfwmacc", "vfwnmacc", "vfwmsac", "vfwnmsac")):
                self.write_pre(f"\t{instr.name} {vd.name}, {rs1.name}, {vs2.name}")
            else:
                self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}")
        else:
            if instr.name.startswith(("vfwmacc", "vfwnmacc", "vfwmsac", "vfwnmsac")):
                self.write_pre(f"\t{instr.name} {vd.name}, {vs1.name}, {vs2.name}")
            else:
                self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {vs1.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecFpSlideSetup(VectorInstrSetup, FpLoadUtil):

    def __init__(self, resource_db, mask_required, initialize_vd, fp):
        self.fp = fp
        self.mask_required = mask_required
        self.initialize_vd = initialize_vd
        super().__init__(resource_db)

    def pre_setup(self, instr):

        payload_reg, _ = self.vector_frontmatter_common(instr, set_vreg_choices=False)
        self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        # just to get something with an attribute name compatible with code that expects that.
        vm = lambda: None
        vm.name = "v0"
        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        rs1 = lambda: None
        rs1.name = "nothing"
        set_vs1 = False

        vd_choice = self._rng.choice(self.base_available_vregs)
        vd.name = "v" + str(vd_choice)
        self.base_available_vregs.remove(vd_choice)
        instr._fields["vd"].name = vd.name

        vs2_choice = self._rng.choice(self.base_available_vregs)
        self.base_available_vregs.remove(vs2_choice)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = self.base_available_vregs[0]
            self.base_available_vregs.remove(work_reg_choice)
            self.work_vreg = "v" + str(work_reg_choice)

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        rs1.name = instr._fields["rs1"].name
        if self.fp:
            self.load_fp_regs_nanbox_in_double(self.vsew // 8, [rs1.name], instr)
        else:
            self.write_pre(f"\tli {rs1.name}, 0x{self._rng.random_nbit(64):x}")
        if self.initialize_vd:
            self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name=self.work_vreg, fp=self.fp)

        self.finalize_vreg_initializations(instr)

        # Clear fflags
        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecFpCvtBF16Setup(VectorInstrSetup):
    def __init__(self, resource_db, is_widening):
        super().__init__(resource_db)
        self.is_widening = is_widening
        self.source_is_bf16 = is_widening
        self.vsew = 16
        self.eew = 32 if is_widening else 16
        self.source_ew = 16 if is_widening else 32
        self.fp_type = "bf16" if is_widening else ""

    def pre_setup(self, instr):
        self.extract_config(instr)
        self.dest_mul = self.vlmul if not self.is_widening else 2 * self.vlmul  # Only vsew of 16 allowed
        self.source_mul = self.vlmul if self.is_widening else 2 * self.vlmul
        self.emul = self.dest_mul
        reg_selection_mul = max(1, int(self.dest_mul), int(self.source_mul))
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % reg_selection_mul == 0]
            instr._fields["vm"].name = "v0"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % reg_selection_mul == 0]

        vs2 = lambda: None
        vs2.name = None
        vd = lambda: None
        vd.name = None
        vd_choice = self.resource_db.rng.random_entry_in(self.base_available_vregs)
        self.base_available_vregs.remove(vd_choice)
        vs2_choice = self.resource_db.rng.random_entry_in(self.base_available_vregs)
        self.base_available_vregs.remove(vs2_choice)
        vd.name = f"v{vd_choice}"
        vs2.name = f"v{vs2_choice}"
        self.work_vreg = vs2.name
        instr._fields["vs2"].name = vs2.name
        instr._fields["vd"].name = vd.name

        assert vd_choice % reg_selection_mul == 0
        assert vs2_choice % reg_selection_mul == 0

        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)
        old_vlmul = self.vlmul
        old_vsew = self.vsew
        self.vlmul = self.source_mul
        self.vsew = self.source_ew
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, temp_regs[0], work_vreg_name=self.work_vreg, fp=True, override_request=True, eew=self.source_ew, fp_type=self.fp_type)
        vreg_initializer.add_vreg(vs2.name, self.vreg_elvals[vs2.name], self.vl, self.source_ew, self.source_mul)
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())
        self.vlmul = old_vlmul
        self.vsew = old_vsew

        self.vector_config(instr, temp_regs[0], dont_generate=False)

        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        self.write_pre("\tcsrrw x0,fflags,x0")
        self.write_pre("\tcsrr x1,fflags")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, {instr._fields["vs2"].name}{tail}')

    def post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        if hasattr(self, "first_vsew"):
            assert self.vsew == self.first_vsew, f"VSEW changed from {self.first_vsew} to {self.vsew} during instruction setup"
        # if emul is an attribute of self, then we should use it
        # emul = self.emul if hasattr(self, "emul") else self.vlmul

        if modified_arch_state is None or "_mask_" in instr.label:
            #   #Should we expect nothing to happen because this instruction is masked out?
            #   #This does not apply when v0 is used for carry-in etc. rather than as a mask
            #   vl = instr.config.avl
            #   vset_instr = instr.config.vset_instr
            #   if vl == "vlmax":
            #     if vset_instr == "vsetivli":
            #       vl = 31
            #     else:
            #       vl = 256
            #   else:
            #     vl = int(vl)
            #   v0_elsize = 64
            #   if not "slide" in instr.name: # Because of the complication of slide instructions, we don't check the mask for them
            #       self.check_v0_mask_against_max_elements(self.vlmul, self.vlen, self.vsew, vl, instr.label, v0_elsize)

            # If this his instruction wasn't masked it was supposed to definitely do something.
            excludes = ["vmv", "vcompress.vm"]
            assert (
                ("_mask_" in instr.label or "_zero_" in instr.label) or "_zero_" in instr.label or any(exclude in instr.name for exclude in excludes)
            ), f"No updates. ERROR: {instr.name} CONFIG: {instr.config}"

            """Every operation was masked out, no architectural state change to match"""
            if not self.resource_db.wysiwyg:
                if self.j_pass_ok():
                    self.write_pre(";#test_passed()")
            else:
                self.write_pre("\tadd x31,x31,x0")
            return

        mod_vrs = modified_arch_state[2].split(";")

        mod_fflags = [entry for entry in modified_arch_state[3].split(";") if "fflags" in entry]

        vrs = dict()
        gprs = dict()
        for vr in mod_vrs:
            if ":" not in vr:
                continue
            (reg, value) = tuple(vr.split(":"))
            if reg[0] == "v":
                vrs[int(reg[1:])] = value
            else:
                gprs[reg] = value

        """
        #   Reg used to temporarily store expected update values and compare to the architectural state.
        """
        result_reg = self.get_random_reg(instr.reg_manager, "Int")

        """
        #   NOTE sliding register groups down pulls information into the lowest numerical register, therefore updates to the whole
        #   group have to be read from just one register
        """
        temp_vrs = None
        if len(vrs) > 0:
            dest_reg = int(self.get_operand(instr, "vd").name[1:])
            regs_we_should_have = [dest_reg]
            print(f"regs_we_should_have: {regs_we_should_have}")
            element_offsets_we_should_have = dict()
            element_offsets_we_do_have = dict()

            lmul_to_use_eowsh = self.dest_mul
            vsew_to_use_eowsh = self.eew

            effective_vl = min(self.numerical_vl, active_elts)

            """
            #   Regs we should have includes those first regs of register groups implied by the combination of Vd, effective lmul and NF value.
            """
            for reg in regs_we_should_have:
                element_offsets_we_should_have[reg] = list()

                first_offset = 0
                last_offset = min(effective_vl * vsew_to_use_eowsh, self.vlen * lmul_to_use_eowsh)
                element_offsets_we_should_have[reg] += [offset for offset in range(first_offset, last_offset, vsew_to_use_eowsh)]
                print(f"element_offsets_we_should_have: {element_offsets_we_should_have}")
            """
            #   The V0 mask register may have prevented updates to elements or entire registers from the set we expected.
            #   Here we determine for what we have actually received updates.
            """
            for reg, value in vrs.items():
                rem = (int(self.vlen * lmul_to_use_eowsh) // 4) - len(value)
                if rem != 0:
                    value = "".join("0" for digit in range((self.vlen // 4) - rem)) + value

                distance_from_first = reg % int(max(1, lmul_to_use_eowsh))
                first_reg = reg - distance_from_first
                first_offset = distance_from_first * self.vlen
                last_offset = first_offset + self.vlen

                if first_reg not in element_offsets_we_do_have:
                    element_offsets_we_do_have[first_reg] = dict()

                for offset in range(first_offset, last_offset, vsew_to_use_eowsh):
                    relative_offset = offset - first_offset
                    first_slice_index = (len(value) * 4 - relative_offset - vsew_to_use_eowsh) // 4
                    last_slice_index = (len(value) * 4 - relative_offset) // 4
                    assert (last_slice_index - first_slice_index) == (
                        vsew_to_use_eowsh // 4
                    ), f"Slice index calculation error. Last: {last_slice_index}, First: {first_slice_index}, VSEW: {vsew_to_use_eowsh}"

                    error_string = f"Slice index calculation error. Last: {last_slice_index}, First: {first_slice_index},"
                    error_string += f"VSEW: {vsew_to_use_eowsh}, relative_offset: {relative_offset}, len value {len(value)}, rem {rem}"
                    if vsew_to_use_eowsh == 8 and "vlm" in instr.name:
                        assert (last_slice_index - first_slice_index) == 2, error_string

                    element_offsets_we_do_have[first_reg][offset] = self.sign_extend(int(value[first_slice_index:last_slice_index], 16), vsew_to_use_eowsh, 64)

            comparison_reg = self.get_random_reg(instr.reg_manager, "Int")
            working_vec_reg = work_vreg_name

            if lmul_to_use_eowsh >= 1 and int(working_vec_reg[1:]) % int(lmul_to_use_eowsh) != 0:
                print("ERROR working vreg: " + working_vec_reg + " not aligned to vlmul: " + str(lmul_to_use_eowsh) + " INSTRUCTION: " + instr.name)
                assert False

            mask = "0x" + "ff" * (self.eew // 8)
            mask_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.write_pre(f"\tli {mask_reg}, {mask}")
            if self.eew != self.vsew:
                vlen_reg = self.get_random_reg(instr.reg_manager, "Int")
                vtype_code = self.make_vtype_code(instr.config.vma, instr.config.vta, self.dest_mul, self.eew)
                assert self.eew <= 32
                self.write_pre(f"\tli {comparison_reg}, {vtype_code}")
                self.write_pre(f"\tli {vlen_reg}, {effective_vl}")
                self.write_pre(f"\tvsetvl x5, {vlen_reg}, {comparison_reg}")

            for reg, offsets in element_offsets_we_should_have.items():
                if reg not in element_offsets_we_do_have:
                    continue

                _vreg = "v" + str(reg)
                assert _vreg != working_vec_reg, f"Vector register constraints violated. Active: {_vreg}, Working: {working_vec_reg}"

                if active_elts >= 1 or active_elts == -1:
                    elcount = 0
                    for element_offset in offsets:
                        """
                        #   Slide past the register group contents that were not updated.
                        """
                        if element_offset not in element_offsets_we_do_have[reg]:
                            active_vreg, support_vreg = (_vreg, working_vec_reg) if (element_offset // vsew_to_use_eowsh) % 2 == 0 else (working_vec_reg, _vreg)
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                            continue

                        result = element_offsets_we_do_have[reg][element_offset]
                        active_vreg, support_vreg = (_vreg, working_vec_reg) if (element_offset // vsew_to_use_eowsh) % 2 == 0 else (working_vec_reg, _vreg)

                        self.write_pre(f"\tli {result_reg}, {result}")
                        self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                        self.write_pre(f"\tand {comparison_reg}, {comparison_reg}, {mask_reg}")
                        self.write_pre(f"\tand {result_reg}, {result_reg}, {mask_reg}")
                        self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")
                        if element_offset < (int(self.vlen * lmul_to_use_eowsh) - vsew_to_use_eowsh):
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                        else:
                            break

                        elcount += 1
                        if elcount >= (effective_vl if active_elts == -1 else active_elts):
                            break
                else:
                    element_offset = offsets[0]
                    num_bytes = int(active_elts * self.vsew // 8)
                    fractional_mask = "0x" + "".join(["ff" for _ in range(int(active_elts * self.vsew // 8))])
                    if element_offset not in element_offsets_we_do_have[reg]:
                        assert False, "Can't handle fractional update with missing leading elements"
                    mask_reg = self.get_random_reg(instr.reg_manager, "Int")
                    result = element_offsets_we_do_have[reg][element_offset]
                    active_vreg, _ = (_vreg, working_vec_reg) if (element_offset // vsew_to_use_eowsh) % 2 == 0 else (working_vec_reg, _vreg)

                    self.write_pre(f"\tli {mask_reg},{fractional_mask}")
                    self.write_pre(f"\tli {result_reg},{result}")
                    self.write_pre(f"\tand {result_reg}, {result_reg}, {mask_reg}")
                    self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                    self.write_pre(f"\tand {comparison_reg}, {comparison_reg}, {mask_reg}")
                    self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")

            # for csr in mod_fflags:
            #     csr_value = csr.split(":")[1]
            #     self.write_pre(f'\tli {result_reg},{"0x"+csr_value}')
            #     self.write_pre(f'\tcsrr {comparison_reg}, fflags')
            #     self.write_pre(f'\tbne {result_reg}, {comparison_reg}, 1f')

        for xreg, value in gprs.items():
            self.write_pre(f'\tli {result_reg},{"0x"+value}')
            self.write_pre(f"\tbne {result_reg}, {xreg}, 1f")

        if self.j_pass_ok():
            self.write_pre(";#test_passed()")
        else:
            self.write_pre("\tj 2f")
        self.write_pre("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_pre(";#test_failed()")
        self.write_pre("\t2:")

    def post_setup(self, modified_arch_state, instr):
        active_elts = min(self.numerical_vl, int(self.vlen * self.dest_mul / self.eew))
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts)


class VecFpWmaBF16Setup(VectorInstrSetup, FpLoadUtil):
    def __init__(self, resource_db, scalar_source):
        super().__init__(resource_db)
        self.eew = 32
        self.source_fp_type = "bf16"
        self.is_widening = True
        self.scalar_source = scalar_source

    def pre_setup(self, instr):
        self.extract_config(instr)
        self.dest_mul = 2 * self.vlmul
        self.source_mul = self.vlmul
        self.emul = self.dest_mul
        reg_selection_mul = max(1, int(self.dest_mul), int(self.source_mul))
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % reg_selection_mul == 0]
            instr._fields["vm"].name = "v0"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % reg_selection_mul == 0]

        rs1 = lambda: None
        rs1.name = None
        vs1 = lambda: None
        vs1.name = None
        vs2 = lambda: None
        vs2.name = None
        vd = lambda: None
        vd.name = None
        vd_choice = self.resource_db.rng.random_entry_in(self.base_available_vregs)
        self.base_available_vregs.remove(vd_choice)
        vs2_choice = self.resource_db.rng.random_entry_in(self.base_available_vregs)
        self.base_available_vregs.remove(vs2_choice)
        vd.name = f"v{vd_choice}"
        vs2.name = f"v{vs2_choice}"
        self.work_vreg = vs2.name
        instr._fields["vs2"].name = vs2.name
        instr._fields["vd"].name = vd.name

        if self.scalar_source:
            rs1.name = self.get_random_reg(instr.reg_manager, "Float")
            instr._fields["rs1"].name = rs1.name
            self.load_fp_regs_nanbox_in_double(a_num_bytes=2, a_fp_reg_names=[rs1.name], a_instr=instr, a_values=[generate_a_fp_value(self.resource_db, 2, "bf")])
        else:
            vs1_choice = self.resource_db.rng.random_entry_in(self.base_available_vregs)
            self.base_available_vregs.remove(vs1_choice)
            vs1.name = f"v{vs1_choice}"
            instr._fields["vs1"].name = vs1.name
            assert vs1_choice % reg_selection_mul == 0

        assert vd_choice % reg_selection_mul == 0
        assert vs2_choice % reg_selection_mul == 0

        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, temp_regs[0], work_vreg_name=self.work_vreg, fp=True, override_request=True, eew=self.vsew, fp_type=self.source_fp_type)
        vreg_initializer.add_vreg(vs2.name, self.vreg_elvals[vs2.name], self.vl, self.vsew, self.source_mul)
        if not self.scalar_source:
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, temp_regs[1], work_vreg_name=self.work_vreg, fp=True, override_request=True, eew=self.vsew, fp_type=self.source_fp_type)
            vreg_initializer.add_vreg(vs1.name, self.vreg_elvals[vs1.name], self.vl, self.vsew, self.source_mul)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, temp_regs[2], work_vreg_name=self.work_vreg, override_request=True, eew=self.vsew)
        vreg_initializer.add_vreg(vd.name, self.vreg_elvals[vd.name], self.vl, self.eew, self.dest_mul)  # NOTE initializing vd here because a reproducibility issue was observed.
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        self.vector_config(instr, temp_regs[0], dont_generate=False)

        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if not self.scalar_source:
            self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, {instr._fields["vs1"].name}, {instr._fields["vs2"].name}{tail}')
        else:
            self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, {instr._fields["rs1"].name}, {instr._fields["vs2"].name}{tail}')

    def post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        if hasattr(self, "first_vsew"):
            assert self.vsew == self.first_vsew, f"VSEW changed from {self.first_vsew} to {self.vsew} during instruction setup"

        if modified_arch_state is None or "_mask_" in instr.label:
            # If this his instruction wasn't masked it was supposed to definitely do something.
            excludes = ["vmv", "vcompress.vm"]
            assert ("_mask_" in instr.label or "_zero_" in instr.label) or any(exclude in instr.name for exclude in excludes), f"No updates. ERROR: {instr.name} CONFIG: {instr.config}"

            """Every operation was masked out, no architectural state change to match"""
            if not self.resource_db.wysiwyg:
                if self.j_pass_ok():
                    self.write_pre(";#test_passed()")
            else:
                self.write_pre("\tadd x31,x31,x0")
            return

        mod_vrs = modified_arch_state[2].split(";")

        mod_fflags = [entry for entry in modified_arch_state[3].split(";") if "fflags" in entry]

        vrs = dict()
        gprs = dict()
        for vr in mod_vrs:
            if ":" not in vr:
                continue
            (reg, value) = tuple(vr.split(":"))
            if reg[0] == "v":
                vrs[int(reg[1:])] = value
            else:
                gprs[reg] = value

        """
        #   Reg used to temporarily store expected update values and compare to the architectural state.
        """
        result_reg = self.get_random_reg(instr.reg_manager, "Int")

        """
        #   NOTE sliding register groups down pulls information into the lowest numerical register, therefore updates to the whole
        #   group have to be read from just one register
        """
        temp_vrs = None
        if len(vrs) > 0:
            dest_reg = int(self.get_operand(instr, "vd").name[1:])
            regs_we_should_have = [dest_reg]
            print(f"regs_we_should_have: {regs_we_should_have}")
            element_offsets_we_should_have = dict()
            element_offsets_we_do_have = dict()

            lmul_to_use_eowsh = self.dest_mul
            vsew_to_use_eowsh = self.eew

            effective_vl = min(self.numerical_vl, active_elts)

            """
            #   Regs we should have includes those first regs of register groups implied by the combination of Vd, effective lmul and NF value.
            """
            for reg in regs_we_should_have:
                element_offsets_we_should_have[reg] = list()

                first_offset = 0
                last_offset = min(effective_vl * vsew_to_use_eowsh, self.vlen * lmul_to_use_eowsh)
                element_offsets_we_should_have[reg] += [offset for offset in range(first_offset, last_offset, vsew_to_use_eowsh)]
                print(f"element_offsets_we_should_have: {element_offsets_we_should_have}")
            """
            #   The V0 mask register may have prevented updates to elements or entire registers from the set we expected.
            #   Here we determine for what we have actually received updates.
            """
            for reg, value in vrs.items():
                rem = (int(self.vlen * lmul_to_use_eowsh) // 4) - len(value)
                if rem != 0:
                    value = "".join("0" for digit in range((self.vlen // 4) - rem)) + value

                distance_from_first = reg % int(max(1, lmul_to_use_eowsh))
                first_reg = reg - distance_from_first
                first_offset = distance_from_first * self.vlen
                last_offset = first_offset + self.vlen

                if first_reg not in element_offsets_we_do_have:
                    element_offsets_we_do_have[first_reg] = dict()

                for offset in range(first_offset, last_offset, vsew_to_use_eowsh):
                    relative_offset = offset - first_offset
                    first_slice_index = (len(value) * 4 - relative_offset - vsew_to_use_eowsh) // 4
                    last_slice_index = (len(value) * 4 - relative_offset) // 4
                    assert (last_slice_index - first_slice_index) == (
                        vsew_to_use_eowsh // 4
                    ), f"Slice index calculation error. Last: {last_slice_index}, First: {first_slice_index}, VSEW: {vsew_to_use_eowsh}"

                    error_string = f"Slice index calculation error. Last: {last_slice_index}, First: {first_slice_index},"
                    error_string += f"VSEW: {vsew_to_use_eowsh}, relative_offset: {relative_offset}, len value {len(value)}, rem {rem}"
                    if vsew_to_use_eowsh == 8 and "vlm" in instr.name:
                        assert (last_slice_index - first_slice_index) == 2, error_string

                    element_offsets_we_do_have[first_reg][offset] = self.sign_extend(int(value[first_slice_index:last_slice_index], 16), vsew_to_use_eowsh, 64)

            comparison_reg = self.get_random_reg(instr.reg_manager, "Int")
            working_vec_reg = work_vreg_name

            if lmul_to_use_eowsh >= 1 and int(working_vec_reg[1:]) % int(lmul_to_use_eowsh) != 0:
                print("ERROR working vreg: " + working_vec_reg + " not aligned to vlmul: " + str(lmul_to_use_eowsh) + " INSTRUCTION: " + instr.name)
                assert False

            mask = "0x" + "ff" * (self.eew // 8)
            mask_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.write_pre(f"\tli {mask_reg}, {mask}")
            if self.eew != self.vsew:
                vlen_reg = self.get_random_reg(instr.reg_manager, "Int")
                vtype_code = self.make_vtype_code(instr.config.vma, instr.config.vta, self.dest_mul, self.eew)
                assert self.eew <= 32
                self.write_pre(f"\tli {comparison_reg}, {vtype_code}")
                self.write_pre(f"\tli {vlen_reg}, {effective_vl}")
                self.write_pre(f"\tvsetvl x5, {vlen_reg}, {comparison_reg}")

            for reg, offsets in element_offsets_we_should_have.items():
                if reg not in element_offsets_we_do_have:
                    continue

                _vreg = "v" + str(reg)
                assert _vreg != working_vec_reg, f"Vector register constraints violated. Active: {_vreg}, Working: {working_vec_reg}"

                if active_elts >= 1 or active_elts == -1:
                    elcount = 0
                    for element_offset in offsets:
                        """
                        #   Slide past the register group contents that were not updated.
                        """
                        if element_offset not in element_offsets_we_do_have[reg]:
                            active_vreg, support_vreg = (_vreg, working_vec_reg) if (element_offset // vsew_to_use_eowsh) % 2 == 0 else (working_vec_reg, _vreg)
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                            continue

                        result = element_offsets_we_do_have[reg][element_offset]
                        active_vreg, support_vreg = (_vreg, working_vec_reg) if (element_offset // vsew_to_use_eowsh) % 2 == 0 else (working_vec_reg, _vreg)

                        self.write_pre(f"\tli {result_reg}, {result}")
                        self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                        self.write_pre(f"\tand {comparison_reg}, {comparison_reg}, {mask_reg}")
                        self.write_pre(f"\tand {result_reg}, {result_reg}, {mask_reg}")
                        self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")
                        if element_offset < (int(self.vlen * lmul_to_use_eowsh) - vsew_to_use_eowsh):
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                        else:
                            break

                        elcount += 1
                        if elcount >= (effective_vl if active_elts == -1 else active_elts):
                            break
                else:
                    element_offset = offsets[0]
                    num_bytes = int(active_elts * self.vsew // 8)
                    fractional_mask = "0x" + "".join(["ff" for _ in range(int(active_elts * self.vsew // 8))])
                    if element_offset not in element_offsets_we_do_have[reg]:
                        assert False, "Can't handle fractional update with missing leading elements"
                    mask_reg = self.get_random_reg(instr.reg_manager, "Int")
                    result = element_offsets_we_do_have[reg][element_offset]
                    active_vreg, _ = (_vreg, working_vec_reg) if (element_offset // vsew_to_use_eowsh) % 2 == 0 else (working_vec_reg, _vreg)

                    self.write_pre(f"\tli {mask_reg},{fractional_mask}")
                    self.write_pre(f"\tli {result_reg},{result}")
                    self.write_pre(f"\tand {result_reg}, {result_reg}, {mask_reg}")
                    self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                    self.write_pre(f"\tand {comparison_reg}, {comparison_reg}, {mask_reg}")
                    self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")

        for xreg, value in gprs.items():
            self.write_pre(f'\tli {result_reg},{"0x"+value}')
            self.write_pre(f"\tbne {result_reg}, {xreg}, 1f")

        if self.j_pass_ok():
            self.write_pre(";#test_passed()")
        else:
            self.write_pre("\tj 2f")
        self.write_pre("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_pre(";#test_failed()")
        self.write_pre("\t2:")

    def post_setup(self, modified_arch_state, instr):
        active_elts = min(self.numerical_vl, int(self.vlen * self.dest_mul / self.eew))
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts)
