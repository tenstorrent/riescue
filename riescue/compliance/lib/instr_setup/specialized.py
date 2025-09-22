# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
misc extensions
"""

from riescue.compliance.lib.instr_setup.vector.base import VectorInstrSetup
from riescue.compliance.lib.vqdot_translate import translate_vqdot_string_to_word
from riescue.compliance.lib.instr_setup.vector.utilities import VecInstrVregInitializer, choose_randomly_but_not
from riescue.compliance.config import Resource


class VecZvbcSetup(VectorInstrSetup):
    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)

        vd = lambda: None
        vd.name = "nothing"
        vs1 = lambda: None
        vs1.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        rs1 = lambda: None
        rs1.name = "nothing"
        zimm5 = None

        tail = ""
        masking = False
        prohibit_list = []
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            masking = True
            instr._fields["vm"].name = "v0"
            # reserve v0
            instr._reg_manager.reserve_reg("v0", "Vector")
            tail = f', {instr._fields["vm"].name}.t'
            prohibit_list.append("v0")

        if instr.name.endswith(".vx"):
            if "rs1" in instr._fields:
                instr._fields["rs1"].randomize()
                rs1.value = self.get_operand(instr, "rs1").value
                rs1.name = instr._fields["rs1"].name
                self.write_pre(f"\tli {rs1.name}, {rs1.value}")
        if "vs1" in instr._fields:
            instr._fields["vs1"].randomize(prohibit_reuse=prohibit_list)
            vs1 = self.get_operand(instr, "vs1")
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, payload_reg)

        instr._fields["vd"].randomize(prohibit_reuse=[self.work_vreg] + prohibit_list)
        instr._fields["vs2"].randomize(prohibit_reuse=prohibit_list)

        vs2 = self.get_operand(instr, "vs2")
        vd = self.get_operand(instr, "vd")

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg)
        self.finalize_vreg_initializations(instr)

        if masking:
            self.load_v0_mask(instr, payload_reg)

        self.lookup_and_set_fixedpoint_rounding_mode(instr)
        self.write_pre(f"{instr.label} :")
        if instr.name.endswith(".vx"):
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}{tail}")
        elif "vs1" in instr._fields:
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {vs1.name}{tail}")
        elif "zimm5" in instr._fields:
            instr._fields["zimm5"].randomize()
            zimm5 = self.get_operand(instr, "zimm5")
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {zimm5.value}")
        else:
            self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}")

    def post_setup(self, modified_arch_state, instr):
        self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VqdotSetup(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)

    def pre_setup(self, instr):
        payload_reg, self.work_vreg = self.vector_frontmatter_common(instr)
        reg1, reg2 = (self.get_random_reg(instr.reg_manager, "Int"), self.get_random_reg(instr.reg_manager, "Int"))
        vreg_initializer = VecInstrVregInitializer(instr.label, [payload_reg, reg1, reg2], -1)

        assert self.vsew == 32, f"VSEW must be 32 for {instr.name} instruction"
        assert (self.vlmul * self.vlen) >= self.vsew, "Fractional values of LMUL that result in LMUL*VLEN < SEW are not supported and cause an illegal instruction exception."

        mask_enabled = False
        reg_selection_mul = max(1, int(self.vlmul))
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

        assert vd_choice % reg_selection_mul == 0
        assert vs2_choice % reg_selection_mul == 0

        vd.name = f"v{vd_choice}"
        vs2.name = f"v{vs2_choice}"
        self.work_vreg = vs2.name
        instr._fields["vs2"].name = vs2.name
        instr._fields["vd"].name = vd.name

        self.scalar_source = instr.name.endswith(".vx")
        if self.scalar_source:
            rs1.name = self.get_random_reg(instr.reg_manager, "Int")
            instr._fields["rs1"].name = rs1.name
            random_word = self._rng.random_nbit(32)
            self.write_pre(f"\tli {rs1.name}, {hex(random_word)}")
        else:
            vs1_choice = self.resource_db.rng.random_entry_in(self.base_available_vregs)
            self.base_available_vregs.remove(vs1_choice)
            vs1.name = f"v{vs1_choice}"
            instr._fields["vs1"].name = vs1.name

        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, override_request=True)
        vreg_initializer.add_vreg(vs2.name, self.vreg_elvals[vs2.name], self.vl, self.vsew, self.vlmul)
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, reg1, work_vreg_name=self.work_vreg, override_request=True)
        vreg_initializer.add_vreg(vd.name, self.vreg_elvals[vd.name], self.vl, self.vsew, self.vlmul)
        if not self.scalar_source:
            self.load_one_vreg_group_from_vreg_file(instr, vs1.name, reg2, work_vreg_name=self.work_vreg, override_request=True)
            vreg_initializer.add_vreg(vs1.name, self.vreg_elvals[vs1.name], self.vl, self.vsew, self.vlmul)
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        tail = ""
        if mask_enabled:
            self.load_v0_mask(instr, None)
            tail = ", v0.t"
        self.write_pre(f"{instr.label} :")
        if self.scalar_source:
            self.write_pre("\t" + translate_vqdot_string_to_word(f"\t{instr.name} {vd.name}, {vs2.name}, {rs1.name}{tail}"))
        else:
            self.write_pre("\t" + translate_vqdot_string_to_word(f"\t{instr.name} {vd.name}, {vs2.name}, {vs1.name}{tail}"))

    def post_setup(self, modified_arch_state, instr):
        self.turbo_post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecGatherSetup(VectorInstrSetup):

    def __init__(self, resource_db: Resource, last_op_scalar, last_op_imm, last_op_vector, ei16_mode):
        self.last_op_scalar = last_op_scalar
        self.last_op_imm = last_op_imm
        self.last_op_vector = last_op_vector
        self.ei16_mode = ei16_mode
        super().__init__(resource_db)

    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % self.vlmul == 0]
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % self.vlmul == 0]

        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)

        vm = lambda: None
        vm.name = "v0"
        vd = lambda: None
        vd.name = "nothing"
        vs2 = lambda: None
        vs2.name = "nothing"
        third_operand = lambda: None
        third_operand.name = "nothing"

        vs2_choice = self._rng.choice(self.base_available_vregs)
        self.base_available_vregs.remove(vs2_choice)
        vs2.name = "v" + str(vs2_choice)
        instr._fields["vs2"].name = vs2.name

        if self.last_op_scalar:
            third_operand.name = instr._fields["rs1"].name
            maxval = 128  # FIXME need actual constraints
            self.write_pre(f"\tli {third_operand.name}, 0x{self._rng.randint(0,maxval):x}")
        elif self.last_op_imm:
            third_operand.name = instr._fields["simm5"].value
        elif self.last_op_vector:
            vs1_choice = None
            # EEW=16 and EMUL = (16/SEW)*LMUL
            EEW = self.vsew
            EMUL = self.vlmul
            if self.ei16_mode:
                EEW = 16
                EMUL = (16 / self.vsew) * self.vlmul
                # backtrack and calculate emul
                int_emul = int(max(1, 16 * self.vlmul / self.vsew))
                check_valid_vs1_regs = {}
                for reg in self.base_available_vregs:
                    if reg % int_emul == 0:
                        check_valid_vs1_regs[reg] = []
                        for overlap_reg in range(reg, reg + int_emul):
                            check_valid_vs1_regs[reg].append(overlap_reg)
                int_vs2 = int(vs2.name[1:])
                range_vs2_regs = []
                int_vs2_emul = max(1, self.vlmul)
                for reg in range(int_vs2, int_vs2 + int_vs2_emul):
                    range_vs2_regs.append(reg)

                # if any of vs2 work regs overlap any of vs1 regs, prohibit them
                # break early for performance reasons
                valid_vs1_regs = list(check_valid_vs1_regs.keys())
                for reg in check_valid_vs1_regs:
                    for check_reg in range_vs2_regs:
                        if check_reg in check_valid_vs1_regs[reg]:
                            valid_vs1_regs.remove(reg)
                            break

                vs1_choice = self._rng.choice(valid_vs1_regs)
                for reg in range(vs1_choice, vs1_choice + int_emul):
                    if reg in self.base_available_vregs:
                        self.base_available_vregs.remove(reg)
            else:
                vs1_choice = self._rng.choice(self.base_available_vregs)
                # self.base_available_vregs.remove(vs1_choice)

            third_operand.name = "v" + str(vs1_choice)
            instr._fields["vs1"].name = third_operand.name

            self.old_config = self.give_config()
            temp_config = {"vsew": EEW, "vlmul": EMUL, "num_vregs": self.num_vregs, "vlen": self.vlen, "vl": self.vl}
            self.replace_config(instr, temp_config, True, payload_reg)

            self.load_one_vreg_group_from_vreg_file(
                instr,
                third_operand.name,
                payload_reg,
                work_vreg_name="not_used",
                fp=False,
                max_elval=128,
                override_request=True,
                require_one_element=True,
                ignore_alignment=True if self.ei16_mode else False,
            )
            vreg_initializer.add_vreg(third_operand.name, self.vreg_elvals[third_operand.name], self.vl, EEW, EMUL)

            dont_restore = True
            self.restore_config(instr, dont_restore, payload_reg)

        # Since a gather, preventing vd from being same as any other vector register
        vd_choice = None
        vs2i = int((vs2.name[1:]))
        if self.last_op_vector:
            toi = int((third_operand.name[1:]))
            vd_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs, [toi, vs2i])
        else:
            vd_choice = choose_randomly_but_not(self.resource_db, self.base_available_vregs, [vs2i])
        vd.name = "v" + str(vd_choice)
        self.base_available_vregs.remove(vd_choice)
        instr._fields["vd"].name = vd.name

        # Initialize Vd to eliminate a source of discrepancy
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, work_vreg_name="not_used", fp=False, max_elval=128, override_request=True, require_one_element=True)
        vreg_initializer.add_vreg(vd.name, self.vreg_elvals[vd.name], self.vl, self.vsew, self.vlmul)

        work_reg_choice = 0
        if len(self.base_available_vregs) > 0:
            work_reg_choice = self._rng.choice(self.base_available_vregs)
        self.work_vreg = "v" + str(work_reg_choice)

        # VS2 needs to be initialized all the way through vlmax
        old_vl = self.vl
        old_vset = self.vset_instruction
        self.vl = "vlmax"
        self.load_one_vreg_group_from_vreg_file(instr, vs2.name, payload_reg, work_vreg_name=self.work_vreg, fp=False, override_request=True, require_one_element=True)
        vreg_initializer.add_vreg(vs2.name, self.vreg_elvals[vs2.name], self.vl, self.vsew, self.vlmul)
        self.vl = old_vl
        self.vset_instruction = old_vset

        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self, force_integer_lmul=True, override_eew=64, whole_register_load=True))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        tail = ""
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)
            tail = f", {vm.name}.t"

        self.vector_config(instr, payload_reg, False)
        self.write_pre(f"{instr.label} :")
        self.write_pre(f"\t{instr.name} {vd.name}, {vs2.name}, {third_operand.name}{tail}")

    def post_setup(self, modified_arch_state, instr):
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)
