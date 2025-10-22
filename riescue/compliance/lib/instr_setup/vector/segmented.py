# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import copy

from .components import VecStoreComponent, VecLoadComponent, VecUnitStrideComponent, VecStridedComponent, VecIndexedComponent, SegmentedComponent
from .load_store import VecStoreUnitStrideSetup, VecLoadUnitStrideSetup, VecLoadStoreBase
from .utilities import VecInstrVregInitializer, choose_randomly_but_not, no_overlap

from riescue.compliance.config import Resource


class VecStoreUnitStrideSegmentedSetup(SegmentedComponent, VecStoreUnitStrideSetup):
    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)

        # set the base available vregs depending on if we are masking or not
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32)]
            instr._fields["vm"].name = "v0"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32)]

        # get nf and emul
        self.nf_number = self.extract_nf(instr.name)
        self.eew = self.extract_eew(instr.name)
        self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)

        # adjust vtype to allow this instruction to be generated validly
        vlmul_index = 0
        vlmuls = [4, 2, 1, 0.5, 0.25, 0.125]
        while self.nf_number * self.emul > 8 and vlmul_index < len(vlmuls):
            self.vlmul = vlmuls[vlmul_index]
            self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)
            vlmul_index += 1
        assert self.nf_number * self.emul <= 8, f"nf * emul is {self.nf_number * self.emul} which is greater than 8"

        nf_biased_group = self.nf_number
        admissible_vlmuls = [1, 2, 4, 8]
        if nf_biased_group not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if nf_biased_group < lmul:
                    nf_biased_group = lmul
                    break

        combo_emul = int(max(1, self.nf_number * self.emul, self.nf_number, nf_biased_group))

        # determine register groups we can use for the data source based on vtype, element size and nf argument
        num_reg_groups = self.nf_number
        admissible_vlmuls = [1, 2, 4, 8]
        if num_reg_groups not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if num_reg_groups < lmul:
                    num_reg_groups = lmul
                    break

        self.nf_compliant_regs = [reg for reg in self.base_available_vregs if reg % combo_emul == 0 and (31 - reg > num_reg_groups)]
        # get the register group for the data source from nf_compliant_regs
        data_vreg = self.resource_db.rng.random_entry_in(self.nf_compliant_regs)
        instr._fields["vs3"].name = "v" + str(data_vreg)

        # determine the normal register groups starting with data_vrewg that need to be initialized

        emul_min = max(1, self.emul)
        normal_regs = [int(data_vreg + (i * emul_min)) for i in range(num_reg_groups)]

        # remove duplicates if any from normal_regs
        normal_regs = list(set(normal_regs))

        # assert that these regs are all within 0 to 31

        assert all([reg < 32 for reg in normal_regs]), "Register index is greater than 31"

        # generate a value for each of those groups and add them to the vreg_initializer
        assert len(normal_regs) > 0, "normal_regs is empty"

        # generate a value for each of those groups and add them to the vreg_initializer
        old_vlmul = self.vlmul
        self.vlmul = emul_min
        for reg in normal_regs:
            self.load_one_vreg_group_from_vreg_file(instr, "v" + str(reg), payload_reg, work_vreg_name="v0", fp=False)
            vreg_initializer.add_vreg("v" + str(reg), self.vreg_elvals["v" + str(reg)], self.vl, self.vsew, self.vlmul)

        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        self.vlmul = old_vlmul

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        min_size_in_bits = int(self.emul * self.vlen * self.nf_number)
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, int(self.nf_number * self.emul * self.vlen) // int(self.eew))
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vs3"].name}, ({mem_addr}){tail}')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)


class VecLoadUnitStrideSegmentedSetup(SegmentedComponent, VecLoadUnitStrideSetup):
    def pre_setup(self, instr):
        self.extract_config(instr)

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"

        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = True
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        self.work_vreg = self.setup_nf_ls_vec_operand(instr, "vd", payload_reg, was_vset_called=(not dont_generate), prohibited_reuse=["v0"] if mask_enabled else [])
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        min_size_in_bits = int(self.emul * self.vlen * self.nf_number)
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        rs1 = instr._fields["rs1"]
        mem_addr = "(" + rs1.name + ")"
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        self.write_pre(f"\tli {rs1.name},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, {mem_addr}{tail}')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)

    def post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        if hasattr(self, "first_vsew"):
            assert self.vsew == self.first_vsew, f"VSEW changed from {self.first_vsew} to {self.vsew} during instruction setup"

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
            regs_we_should_have = [reg for reg in range(dest_reg, dest_reg + self.nf_number * int(max(1, self.emul)), int(max(1, self.emul)))]

            element_offsets_we_should_have = dict()
            element_offsets_we_do_have = dict()

            lmul_to_use_eowsh = self.emul
            vsew_to_use_eowsh = self.eew
            effective_vl = min(self.numerical_vl, int(self.vlen * lmul_to_use_eowsh / self.eew))

            """
            #   Regs we should have includes those first regs of register groups implied by the combination of Vd, effective lmul and NF value.
            """
            for reg in regs_we_should_have:
                element_offsets_we_should_have[reg] = list()

                first_offset = 0
                last_offset = int(vsew_to_use_eowsh * effective_vl)
                element_offsets_we_should_have[reg] += [offset for offset in range(first_offset, last_offset, vsew_to_use_eowsh)]
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

                    element_offsets_we_do_have[first_reg][offset] = self.sign_extend(int(value[first_slice_index:last_slice_index], 16), vsew_to_use_eowsh, 64)

            comparison_reg = self.get_random_reg(instr.reg_manager, "Int")
            vtype_reg = self.get_random_reg(instr.reg_manager, "Int")
            working_vec_reg = work_vreg_name

            tail_setting = instr.config.vta
            mask_setting = instr.config.vma
            vtype_code = self.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=self.emul, vsew=self.eew)
            self.write_pre(f"\tli {vtype_reg}, {hex(vtype_code)}")
            self.write_pre(f"\tli {comparison_reg}, {self.numerical_vl}")
            self.write_pre(f"# Checking vtype: {vtype_code}, vl: {self.numerical_vl}, vlmul: {self.emul}, vsew: {self.eew}")
            self.write_pre(f"\tvsetvl x5, {comparison_reg}, {vtype_reg}")
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

                        self.write_pre(f"\tli {result_reg},{result}")
                        self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                        self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")
                        if element_offset < (int(self.vlen * lmul_to_use_eowsh) - vsew_to_use_eowsh):
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                        else:
                            break

                        elcount += 1
                        if elcount >= effective_vl if active_elts == -1 else active_elts:
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

            for csr in mod_fflags:
                csr_value = csr.split(":")[1]
                self.write_pre(f'\tli {result_reg},{"0x"+csr_value}')
                self.write_pre(f"\tcsrr {comparison_reg}, fflags")
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
        # Per the spec these whole reg instructions have an evl of NFIELDS * VLEN / EEW regardless of vtype
        active_elts = -1  # Determine the effective vector length in the next method
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts)


class VecLoadUnitStrideSegmentedFaultFirst(VecLoadUnitStrideSegmentedSetup):
    alias = True


class VecStoreStridedSegmentedSetup(SegmentedComponent, VecStoreComponent, VecUnitStrideComponent, VecLoadStoreBase, VecStridedComponent):
    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)

        # set the base available vregs depending on if we are masking or not
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32)]
            instr._fields["vm"].name = "v0"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32)]

        # get nf and emul
        self.nf_number = self.extract_nf(instr.name)
        self.eew = self.extract_eew(instr.name)
        self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)

        # adjust vtype to allow this instruction to be generated validly
        vlmul_index = 0
        vlmuls = [4, 2, 1, 0.5, 0.25, 0.125]
        while self.nf_number * self.emul > 8 and vlmul_index < len(vlmuls):
            self.vlmul = vlmuls[vlmul_index]
            self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)
            vlmul_index += 1
        assert self.nf_number * self.emul <= 8, f"nf * emul is {self.nf_number * self.emul} which is greater than 8"
        combo_emul = max(1, self.nf_number * self.emul)

        admissible_vlmuls = [1, 2, 4, 8]
        if combo_emul not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if combo_emul < lmul:
                    combo_emul = lmul
                    break

        # we effectively round nf to the nearest power of 2
        num_reg_groups = self.nf_number
        if num_reg_groups not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if num_reg_groups < lmul:
                    num_reg_groups = lmul
                    break

        # determine register groups we can use for the data source based on vtype, element size and nf argument
        self.nf_compliant_regs = [reg for reg in self.base_available_vregs if reg % combo_emul == 0 and (31 - reg > num_reg_groups)]

        # get the register group for the data source from nf_compliant_regs
        data_vreg = self.resource_db.rng.random_entry_in(self.nf_compliant_regs)
        instr._fields["vs3"].name = "v" + str(data_vreg)

        emul_min = max(1, self.emul)
        normal_regs = [int(data_vreg + (i * emul_min)) for i in range(num_reg_groups)]

        # remove duplicates if any from normal_regs
        normal_regs = list(set(normal_regs))

        # assert that these regs are all within 0 to 31
        assert all([reg < 32 for reg in normal_regs]), f"Register index is greater than 31 {normal_regs}, emul_min is {emul_min}"

        # generate a value for each of those groups and add them to the vreg_initializer
        assert len(normal_regs) > 0, "normal_regs is empty"

        old_vlmul = self.vlmul
        old_vsew = self.vsew
        self.vlmul = max(1, self.emul)
        self.vsew = self.eew

        for reg in normal_regs:
            self.load_one_vreg_group_from_vreg_file(instr, "v" + str(reg), payload_reg, work_vreg_name="v0", fp=False, override_request=True)
            vreg_initializer.add_vreg("v" + str(reg), self.vreg_elvals["v" + str(reg)], self.vl, self.vsew, self.vlmul)

        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        self.vlmul = old_vlmul
        self.vsew = old_vsew
        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate)
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)
        # The goal for now is to stay within a 4k page
        rs2 = instr._fields["rs2"]
        byte_stride_mask = 0xF
        rs2_val_constrained = rs2.value & byte_stride_mask
        self.write_pre(f"\tli {rs2.name}, {str(hex(rs2_val_constrained))}")

        min_size_in_bytes = self.compute_memory_footprint_bytes(self.emul * self.nf_number, self.vlen, self.eew, rs2_val_constrained)
        assert 0x1000 > min_size_in_bytes
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, int(self.emul * self.vlen * self.nf_number) // int(self.eew))
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vs3"].name}, ({mem_addr}), {rs2.name}{tail}')

        offsets = [rs2_val_constrained * element for element in range(0, int(self.emul * self.vlen * self.nf_number) // int(self.eew), 1)]
        sizes = [self.eew for offset in offsets]
        self.initialize_memory(instr, offsets, sizes)


class VecLoadStridedSegmentedSetup(SegmentedComponent, VecLoadComponent, VecUnitStrideComponent, VecLoadStoreBase, VecStridedComponent):
    def pre_setup(self, instr):
        instr.reg_manager.reinit_vregs()

        self.extract_config(instr)
        self.nf_number = self.extract_nf(instr.name)
        self.eew = self.extract_eew(instr.name)
        self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)

        while self.nf_number * self.emul > 8 and self.vlmul > 0.125:
            self.vlmul = self.vlmul / 2
            self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)
        assert self.nf_number * self.emul <= 8, f"nf * emul is {self.nf_number * self.emul} which is greater than 8"

        nearest_power_of_2 = 1
        while nearest_power_of_2 < self.nf_number * self.emul:
            nearest_power_of_2 *= 2
        assert nearest_power_of_2 <= 8

        max_po2 = max(self.vlmul, nearest_power_of_2, self.nf_number)

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % max_po2 == 0 and 32 - reg > max_po2]
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % max_po2 == 0 and 32 - reg > max_po2]

        payload_reg = self.get_random_reg(instr.reg_manager, "Int")

        prohibit = [-1] + ([0] if mask_enabled else [])
        instr._fields["vd"].name = "v" + str(choose_randomly_but_not(self.resource_db, self.base_available_vregs, prohibit))
        self.work_vreg = "v" + str(choose_randomly_but_not(self.resource_db, self.base_available_vregs, prohibit + [int(instr._fields["vd"].name[1:])]))

        # Initialize the vd reg groups to eliminate outside interference.
        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)
        self.vreg_elvals = dict()
        vd_index = int(instr._fields["vd"].name[1:])
        safe_emul = max(1, int(self.emul))
        for reg_index in range(vd_index, int(vd_index + self.nf_number * safe_emul), safe_emul):
            if reg_index >= 32:
                print(f"\tindex: {reg_index}")
            self.vreg_elvals["v" + str(reg_index)] = [hex(0) for i in range(int(safe_emul * self.vlen // self.vsew))]
            use_vl = int(safe_emul * self.vlen // self.vsew)
            vreg_initializer.add_vreg("v" + str(reg_index), self.vreg_elvals["v" + str(reg_index)], use_vl, self.vsew, safe_emul)
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)
        else:
            dont_generate = False
            self.write_pre(f"\t# Vtype vl {self.numerical_vl}, vsew {self.vsew}, vlmul {self.vlmul}")
            self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        # The goal for now is to stay within a 4k page
        rs2 = instr._fields["rs2"]
        byte_stride_mask = 0xF
        rs2_val_constrained = rs2.value & byte_stride_mask
        self.write_pre(f"\tli {rs2.name}, {str(hex(rs2_val_constrained))}")

        min_size_in_bytes = self.compute_memory_footprint_bytes(self.emul * self.nf_number, self.vlen, self.eew, rs2_val_constrained)
        assert 0x1000 > min_size_in_bytes
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, int(self.emul * self.vlen * self.nf_number) // int(self.eew))
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, ({mem_addr}), {rs2.name}{tail}')

        offsets = [rs2_val_constrained * element for element in range(0, int(self.emul * self.vlen * self.nf_number) // int(self.eew), 1)]
        sizes = [self.eew for offset in offsets]
        self.initialize_memory(instr, offsets, sizes)

    def post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        if hasattr(self, "first_vsew"):
            assert self.vsew == self.first_vsew, f"VSEW changed from {self.first_vsew} to {self.vsew} during instruction setup"

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
            assert ("_mask_" in instr.label or "_zero_" in instr.label or "_nonzero_" in instr.label) or any(
                exclude in instr.name for exclude in excludes
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
            regs_we_should_have = [reg for reg in range(dest_reg, dest_reg + self.nf_number * int(max(1, self.emul)), int(max(1, self.emul)))]

            element_offsets_we_should_have = dict()
            element_offsets_we_do_have = dict()

            lmul_to_use_eowsh = self.emul
            vsew_to_use_eowsh = self.eew
            effective_vl = min(self.numerical_vl, int(self.vlen * lmul_to_use_eowsh / self.eew))

            """
            #   Regs we should have includes those first regs of register groups implied by the combination of Vd, effective lmul and NF value.
            """
            for reg in regs_we_should_have:
                element_offsets_we_should_have[reg] = list()

                first_offset = 0
                last_offset = int(vsew_to_use_eowsh * effective_vl)
                element_offsets_we_should_have[reg] += [offset for offset in range(first_offset, last_offset, vsew_to_use_eowsh)]
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

                    element_offsets_we_do_have[first_reg][offset] = self.sign_extend(int(value[first_slice_index:last_slice_index], 16), vsew_to_use_eowsh, 64)

            comparison_reg = self.get_random_reg(instr.reg_manager, "Int")
            vtype_reg = self.get_random_reg(instr.reg_manager, "Int")
            working_vec_reg = work_vreg_name

            tail_setting = instr.config.vta
            mask_setting = instr.config.vma
            vtype_code = self.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=self.emul, vsew=self.eew)
            self.write_pre(f"\tli {vtype_reg}, {hex(vtype_code)}")
            self.write_pre(f"\tli {comparison_reg}, {self.numerical_vl}")
            self.write_pre(f"# Checking vtype: {vtype_code}, vl: {self.numerical_vl}, vlmul: {self.emul}, vsew: {self.eew}")
            self.write_pre(f"\tvsetvl x5, {comparison_reg}, {vtype_reg}")
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

                        self.write_pre(f"\tli {result_reg},{result}")
                        self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                        self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")
                        if element_offset < (int(self.vlen * lmul_to_use_eowsh) - vsew_to_use_eowsh):
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                        else:
                            break

                        elcount += 1
                        if elcount >= effective_vl if active_elts == -1 else active_elts:
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

            for csr in mod_fflags:
                csr_value = csr.split(":")[1]
                self.write_pre(f'\tli {result_reg},{"0x"+csr_value}')
                self.write_pre(f"\tcsrr {comparison_reg}, fflags")
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
        # Per the spec these whole reg instructions have an evl of NFIELDS * VLEN / EEW regardless of vtype
        active_elts = -1  # Determine the effective vector length in the next method
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts)


class VecLoadIndexedUnorderedSegmentedSetup(SegmentedComponent, VecLoadStoreBase, VecIndexedComponent):
    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)

        # set the base available vregs depending on if we are masking or not
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32)]
            instr._fields["vm"].name = "v0"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32)]

        # get nf and emul
        self.nf_number = self.extract_nf(instr.name)

        # adjust vtype to allow this instruction to be generated validly
        vlmul_index = 0
        vlmuls = [4, 2, 1, 0.5, 0.25, 0.125]
        while self.nf_number * self.vlmul > 8 and vlmul_index < len(vlmuls):
            self.vlmul = vlmuls[vlmul_index]
            vlmul_index += 1
        assert self.nf_number * self.vlmul <= 8, f"nf * vlmul is {self.nf_number * self.vlmul} which is greater than 8"
        combo_emul = max(1, self.nf_number * self.vlmul, self.nf_number)

        admissible_vlmuls = [1, 2, 4, 8]
        if combo_emul not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if combo_emul < lmul:
                    combo_emul = lmul
                    break

        # we effectively round nf to the nearest power of 2
        num_reg_groups = self.nf_number
        if num_reg_groups not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if num_reg_groups < lmul:
                    num_reg_groups = lmul
                    break

        # determine register groups we can use for the data source based on vtype, element size and nf argument
        self.nf_compliant_regs = [reg for reg in self.base_available_vregs if reg % combo_emul == 0 and (31 - reg > num_reg_groups)]

        # get the register group for the data source from nf_compliant_regs
        data_vreg = self.resource_db.rng.random_entry_in(self.nf_compliant_regs)
        instr._fields["vd"].name = "v" + str(data_vreg)
        self.work_vreg = "v" + str(choose_randomly_but_not(self.resource_db, self.nf_compliant_regs, [data_vreg]))

        emul_min = max(1, self.vlmul)
        normal_regs = [int(data_vreg + (i * emul_min)) for i in range(num_reg_groups)]

        # remove duplicates if any from normal_regs
        normal_regs = list(set(normal_regs))

        # assert that these regs are all within 0 to 31
        assert all([reg < 32 for reg in normal_regs]), f"Register index is greater than 31 {normal_regs}, emul_min is {emul_min}"

        # generate a value for each of those groups and add them to the vreg_initializer
        assert len(normal_regs) > 0, "normal_regs is empty"

        # Now we need the index related eew and emul. The vlmul may have allready been adjusted to be OK with the NF
        self.eew = self.extract_eew(instr.name)
        self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)

        old_vlmul = self.vlmul
        old_vsew = self.vsew
        self.vlmul = self.emul
        self.vsew = self.eew

        # determine min and max indices in normal_regs
        min_index = min(normal_regs)
        max_index = max(normal_regs)
        avoid_list = [i for i in range(min_index, max_index + 1)]
        okay_list = [i for i in self.base_available_vregs if i % (max(old_vlmul, self.vlmul)) == 0 and i not in avoid_list and no_overlap(i, min_index, self.vlmul)]
        index_reg_index = self.resource_db.rng.random_entry_in(okay_list)
        instr._fields["vs2"].name = "v" + str(index_reg_index)

        # Load the index register
        element_mask = 0xFF  # TODO make page crossing and whole page available
        if self.eew == 8:
            element_mask = 0xFF
        num_elts = int(self.emul * self.vlen // self.eew)
        self.vreg_elvals["v" + str(index_reg_index)] = [hex(self.resource_db.rng.randint(0, element_mask)) for i in range(num_elts)]
        vreg_initializer.add_vreg("v" + str(index_reg_index), self.vreg_elvals["v" + str(index_reg_index)], self.vl, self.eew, self.emul)
        self.vlmul = old_vlmul
        self.vsew = old_vsew
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_seg_vloads(active_config=True, instr=instr, setup=self, mask_to_max_vl=True))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        # Recreate the initializer so we can easily load with a different vtype
        vreg_initializer = VecInstrVregInitializer(instr.label + "_vd", temp_regs, -1)
        # Initialize vd and the multiple reg groups that represents
        for reg_index in range(data_vreg, int(data_vreg + self.nf_number * max(1, int(self.vlmul))), max(1, int(self.vlmul))):
            self.vreg_elvals["v" + str(reg_index)] = [hex(0) for i in range(int(self.vlmul * self.vlen // self.vsew))]
            vreg_initializer.add_vreg("v" + str(reg_index), self.vreg_elvals["v" + str(reg_index)], self.vl, self.vsew, self.vlmul)
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate)
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        vd = lambda: None
        vd.name = instr._fields["vd"].name
        vs2 = lambda: None
        vs2.name = instr._fields["vs2"].name
        # FIXME this block of code is a candidate to be shared with the store indexed counterpart to this class
        page_size = 0x1000
        offsets = [int(offset, 16) for offset in self.vreg_elvals["v" + str(index_reg_index)]]

        min_size_in_bytes = self.compute_memory_footprint_bytes(self.vsew, offsets)
        assert page_size > min_size_in_bytes
        self.setup_memory(instr.label, str(page_size), "pre_setup")

        # FIXME this block of code differs with the store indexed counterpart only in that vs3 is replaced with vs2, so this might be a candidate for putting into
        # the parent class or otherwise abstracting it.
        offset_bias = 0x7FF
        mem_addr = self.get_operand(instr, "rs1").name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, num_elts)
        self.write_pre(f"\tli {mem_addr}, {self._lin_addr}")
        self.write_pre(f"\tli {payload_reg}, {str(hex(offset_bias))}")
        self.write_pre(f"\tadd {mem_addr}, {mem_addr}, {payload_reg}")  # We add the offset bias so that we can handle negative offsets within the page we set up.
        self.write_pre(f"{instr.label}: {instr.name} {vd.name}, ({mem_addr}), {vs2.name}{tail}")

        segmented_offsets = []
        for offset in offsets:
            segmented_offsets += [offset + i * self.vsew // 8 for i in range(self.nf_number)]

        sizes = [self.vsew for offset in segmented_offsets]

        self.initialize_memory(instr, sorted(segmented_offsets), sizes, offset_bias)

    def post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        if hasattr(self, "first_vsew"):
            assert self.vsew == self.first_vsew, f"VSEW changed from {self.first_vsew} to {self.vsew} during instruction setup"

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
            assert ("_mask_" in instr.label or "_zero_" in instr.label or "_nonzero_" in instr.label) or any(
                exclude in instr.name for exclude in excludes
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
            regs_we_should_have = [dest_reg + i * max(1, int(self.vlmul)) for i in range(self.nf_number)]

            element_offsets_we_should_have = dict()
            element_offsets_we_do_have = dict()

            lmul_to_use_eowsh = self.vlmul
            vsew_to_use_eowsh = self.vsew
            effective_vl = int(self.vlen * lmul_to_use_eowsh / self.vsew)
            effective_vl = min(effective_vl, self.numerical_vl)

            """
            #   Regs we should have includes those first regs of register groups implied by the combination of Vd, effective lmul and NF value.
            """
            for reg in regs_we_should_have:
                element_offsets_we_should_have[reg] = list()

                first_offset = 0
                last_offset = int(vsew_to_use_eowsh * effective_vl)
                element_offsets_we_should_have[reg] += [offset for offset in range(first_offset, last_offset, vsew_to_use_eowsh)]

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

                    element_offsets_we_do_have[first_reg][offset] = self.sign_extend(int(value[first_slice_index:last_slice_index], 16), vsew_to_use_eowsh, 64)

            comparison_reg = self.get_random_reg(instr.reg_manager, "Int")
            working_vec_reg = work_vreg_name

            if lmul_to_use_eowsh >= 1 and int(working_vec_reg[1:]) % int(lmul_to_use_eowsh) != 0:
                print("ERROR working vreg: " + working_vec_reg + " not aligned to vlmul: " + str(lmul_to_use_eowsh) + " INSTRUCTION: " + instr.name)
                assert False

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

                        self.write_pre(f"\tli {result_reg},{result}")
                        self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                        self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")
                        if element_offset < (int(self.vlen * lmul_to_use_eowsh) - vsew_to_use_eowsh):
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                        else:
                            break

                        elcount += 1
                        if elcount >= effective_vl if active_elts == -1 else active_elts:
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

            for csr in mod_fflags:
                csr_value = csr.split(":")[1]
                self.write_pre(f'\tli {result_reg},{"0x"+csr_value}')
                self.write_pre(f"\tcsrr {comparison_reg}, fflags")
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
        # We don't set the config to emul and eew for the reason that the data in the indexed instructions is arranged according to vsew and vlmul
        # What we call in this setup class eew and emul pertain to the index vector only, which itself is not the subject of the post_setup
        # checking code.
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecStoreIndexedUnorderedSegmentedSetup(VecStoreComponent, SegmentedComponent, VecLoadStoreBase, VecIndexedComponent):
    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)

        # set the base available vregs depending on if we are masking or not
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            self.base_available_vregs = [reg for reg in range(1, 32)]
            instr._fields["vm"].name = "v0"
        else:
            self.base_available_vregs = [reg for reg in range(0, 32)]

        # get nf and emul
        self.nf_number = self.extract_nf(instr.name)

        # adjust vtype to allow this instruction to be generated validly
        vlmul_index = 0
        vlmuls = [4, 2, 1, 0.5, 0.25, 0.125]
        while self.nf_number * self.vlmul > 8 and vlmul_index < len(vlmuls):
            self.vlmul = vlmuls[vlmul_index]
            vlmul_index += 1
        assert self.nf_number * self.vlmul <= 8, f"nf * vlmul is {self.nf_number * self.vlmul} which is greater than 8"
        combo_emul = max(1, self.nf_number * self.vlmul)

        admissible_vlmuls = [1, 2, 4, 8]
        if combo_emul not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if combo_emul < lmul:
                    combo_emul = lmul
                    break

        # we effectively round nf to the nearest power of 2
        num_reg_groups = self.nf_number
        if num_reg_groups not in admissible_vlmuls:
            for lmul in admissible_vlmuls:
                if num_reg_groups < lmul:
                    num_reg_groups = lmul
                    break

        # determine register groups we can use for the data source based on vtype, element size and nf argument
        self.nf_compliant_regs = [reg for reg in self.base_available_vregs if reg % combo_emul == 0 and (31 - reg > num_reg_groups)]

        # get the register group for the data source from nf_compliant_regs
        data_vreg = self.resource_db.rng.random_entry_in(self.nf_compliant_regs)
        instr._fields["vs3"].name = "v" + str(data_vreg)

        emul_min = max(1, self.vlmul)
        normal_regs = [int(data_vreg + (i * emul_min)) for i in range(num_reg_groups)]

        # remove duplicates if any from normal_regs
        normal_regs = list(set(normal_regs))

        # assert that these regs are all within 0 to 31
        assert all([reg < 32 for reg in normal_regs]), f"Register index is greater than 31 {normal_regs}, emul_min is {emul_min}"

        # generate a value for each of those groups and add them to the vreg_initializer
        assert len(normal_regs) > 0, "normal_regs is empty"

        # Now we need the index related eew and emul. The vlmul may have allready been adjusted to be OK with the NF
        self.eew = self.extract_eew(instr.name)
        self.emul = self.compute_emul(self.eew, self.vsew, self.vlmul)

        old_vlmul = self.vlmul
        old_vsew = self.vsew
        self.vlmul = max(old_vlmul, self.emul)
        self.vsew = self.eew

        # determine min and max indices in normal_regs
        min_index = min(normal_regs)
        max_index = max(normal_regs)
        avoid_list = [i for i in range(min_index, max_index + 1)]

        okay_list = [i for i in self.base_available_vregs if i % self.vlmul == 0 and i not in avoid_list and no_overlap(i, min_index, self.vlmul)]

        index_reg_index = self.resource_db.rng.random_entry_in(okay_list)
        instr._fields["vs2"].name = "v" + str(index_reg_index)

        # Load the inddex register
        element_mask = 0x1F  # TODO make page crossing and whole page available
        self.load_one_vreg_group_from_vreg_file(instr, "v" + str(index_reg_index), payload_reg, work_vreg_name="v0", element_mask=element_mask, fp=False, override_request=True)
        vreg_initializer.add_vreg("v" + str(index_reg_index), self.vreg_elvals["v" + str(index_reg_index)], self.vl, self.vsew, self.vlmul)

        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_seg_vloads(active_config=True, instr=instr, setup=self, mask_to_max_vl=True))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        self.vlmul = old_vlmul
        self.vsew = old_vsew

        vreg_initializer = VecInstrVregInitializer(instr.label + "_data", temp_regs, -1)
        # For indexed addressing mode we use the vtype for the data register
        for reg in normal_regs:
            self.load_one_vreg_group_from_vreg_file(instr, "v" + str(reg), payload_reg, work_vreg_name="v0", fp=False, override_request=True)
            vreg_initializer.add_vreg("v" + str(reg), self.vreg_elvals["v" + str(reg)], self.vl, self.vsew, self.vlmul)

        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate)
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        vs3 = lambda: None
        vs3.name = instr._fields["vs3"].name
        vs2 = lambda: None
        vs2.name = instr._fields["vs2"].name
        # FIXME this block of code is a candidate to be shared with the store indexed counterpart to this class
        page_size = 0x1000
        offsets = self.extract_offsets(instr.name, self.vreg_elvals, self.emul, self.vlen, self.eew, vs2.name)
        min_size_in_bytes = self.compute_memory_footprint_bytes(self.vsew, offsets)
        assert page_size > min_size_in_bytes
        self.setup_memory(instr.label, str(page_size), "pre_setup")

        # FIXME this block of code differs with the store indexed counterpart only in that vs3 is replaced with vs2, so this might be a candidate for putting into
        # the parent class or otherwise abstracting it.
        offset_bias = 0x7FF
        mem_addr = self.get_operand(instr, "rs1").name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            evl = int(self.emul * self.vlen // self.eew)
            self.randomize_vstart(instr, evl)
        self.write_pre(f"\tli {mem_addr}, {self._lin_addr}")
        self.write_pre(f"\tli {payload_reg}, {str(hex(offset_bias))}")
        self.write_pre(f"\tadd {mem_addr}, {mem_addr}, {payload_reg}")  # We add the offset bias so that we can handle negative offsets within the page we set up.
        self.write_pre(f"{instr.label}: {instr.name} {vs3.name}, ({mem_addr}), {vs2.name}{tail}")

        sizes = [self.vsew for offset in offsets]
        self.initialize_memory(instr, sorted(offsets), sizes, offset_bias)


class VecLoadWholeRegSetup(SegmentedComponent, VecLoadUnitStrideSetup):
    @classmethod
    def extract_nf(cls, instr_name: str) -> int:
        name = copy.deepcopy(instr_name)
        name = name[name.find("vl") + 2 :]

        if name[0].isnumeric():
            nf = int(name[0])
        else:
            nf = 1

        return nf

    def extract_eew_unit_stride(self, instruction_name):
        name = copy.deepcopy(instruction_name)
        name = name[: name.find(".v")]

        name = name[::-1]
        name = name[: name.find("e")]
        name = name[::-1]

        try:
            eew = int(name)
            self.eew = eew
        except Exception:
            # FIXME: except should be specific
            eew = 8
            self.eew = eew
        finally:
            return eew

    def pre_setup(self, instr):
        self.extract_config(instr)
        self.nf_number = self.extract_nf(instr.name)
        safe_mul = max(self.nf_number, self.vlmul, 1)
        self.eew = self.extract_eew_unit_stride(instr.name)

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % safe_mul == 0]
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % safe_mul == 0]

        payload_reg = self.get_random_reg(instr.reg_manager, "Int")

        instr._fields["vd"].name = "v" + str(choose_randomly_but_not(self.resource_db, self.base_available_vregs, [-1]))
        self.work_vreg = "v" + str(choose_randomly_but_not(self.resource_db, self.base_available_vregs, [int(instr._fields["vd"].name[1:])]))
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        min_size_in_bits = int(self.nf_number * self.vlen)
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        rs1 = instr._fields["rs1"]
        mem_addr = "(" + rs1.name + ")"
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        self.write_pre(f"\tli {rs1.name},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, {mem_addr}{tail}')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)

    def post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        if hasattr(self, "first_vsew"):
            assert self.vsew == self.first_vsew, f"VSEW changed from {self.first_vsew} to {self.vsew} during instruction setup"

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

            element_offsets_we_should_have = dict()
            element_offsets_we_do_have = dict()

            lmul_to_use_eowsh = self.nf_number
            vsew_to_use_eowsh = self.vsew
            effective_vl = int(self.vlen * self.nf_number / self.vsew)

            """
            #   Regs we should have includes those first regs of register groups implied by the combination of Vd, effective lmul and NF value.
            """
            for reg in regs_we_should_have:
                element_offsets_we_should_have[reg] = list()

                first_offset = 0
                last_offset = int(vsew_to_use_eowsh * effective_vl)
                element_offsets_we_should_have[reg] += [offset for offset in range(first_offset, last_offset, vsew_to_use_eowsh)]
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

                    element_offsets_we_do_have[first_reg][offset] = self.sign_extend(int(value[first_slice_index:last_slice_index], 16), vsew_to_use_eowsh, 64)

            comparison_reg = self.get_random_reg(instr.reg_manager, "Int")
            working_vec_reg = work_vreg_name

            if lmul_to_use_eowsh >= 1 and int(working_vec_reg[1:]) % int(lmul_to_use_eowsh) != 0:
                print("ERROR working vreg: " + working_vec_reg + " not aligned to vlmul: " + str(lmul_to_use_eowsh) + " INSTRUCTION: " + instr.name)
                assert False

            tail_setting = instr.config.vta
            mask_setting = instr.config.vma
            vtype_code = self.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=self.nf_number, vsew=self.vsew)
            self.write_pre(f"\tli {comparison_reg}, {hex(vtype_code)}")
            self.write_pre(f"\tvsetvl x5, x0, {comparison_reg}")
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

                        self.write_pre(f"\tli {result_reg},{result}")
                        self.write_pre(f"\tvmv.x.s {comparison_reg}, {active_vreg}")
                        self.write_pre(f"\tbne {result_reg}, {comparison_reg}, 1f")
                        if element_offset < (int(self.vlen * lmul_to_use_eowsh) - vsew_to_use_eowsh):
                            self.write_pre(f"\tvslide1down.vx {support_vreg}, {active_vreg}, x0")
                        else:
                            break

                        elcount += 1
                        if elcount >= effective_vl if active_elts == -1 else active_elts:
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

            for csr in mod_fflags:
                csr_value = csr.split(":")[1]
                self.write_pre(f'\tli {result_reg},{"0x"+csr_value}')
                self.write_pre(f"\tcsrr {comparison_reg}, fflags")
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
        # Per the spec these whole reg instructions have an evl of NFIELDS * VLEN / EEW regardless of vtype
        active_elts = -1  # Determine the effective vector length in the next method
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts)


class VecStoreWholeRegSetup(VecStoreComponent, VecLoadStoreBase):
    @classmethod
    def extract_nf(cls, instr_name: str) -> int:
        name = copy.deepcopy(instr_name)
        name = name[name.find("vs") + 2 :]

        if name[0].isnumeric():
            nf = int(name[0])
        else:
            nf = 1

        return nf

    def pre_setup(self, instr):
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]
        vreg_initializer = VecInstrVregInitializer(instr.label, temp_regs, -1)

        self.extract_config(instr)
        self.nf_number = self.extract_nf(instr.name)
        safe_mul = max(self.nf_number, self.vlmul, 1)
        self.eew = self.extract_eew_unit_stride(instr.name)

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"
            self.base_available_vregs = [reg for reg in range(1, 32) if reg % safe_mul == 0]
        else:
            self.base_available_vregs = [reg for reg in range(0, 32) if reg % safe_mul == 0]

        data_vreg = self.resource_db.rng.random_entry_in(self.base_available_vregs)
        instr._fields["vs3"].name = "v" + str(data_vreg)
        self.work_vreg = "v" + str(data_vreg)

        # generate a value for each of those groups and add them to the vreg_initializer
        old_vlmul = self.vlmul
        self.vlmul = self.nf_number
        evl = int(self.vlen * self.nf_number / self.eew)
        self.load_one_vreg_group_from_vreg_file(instr, "v" + str(data_vreg), payload_reg, work_vreg_name="v0", fp=False)  # , override_request=True)
        vreg_initializer.add_vreg("v" + str(data_vreg), self.vreg_elvals["v" + str(data_vreg)], evl, self.eew, self.nf_number)

        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

        self.vlmul = old_vlmul

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        min_size_in_bits = int(self.vlen * self.nf_number)
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, evl)
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vs3"].name}, ({mem_addr}){tail}')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)

    def extract_eew_unit_stride(self, instruction_name):
        name = copy.deepcopy(instruction_name)
        name = name[: name.find(".v")]

        name = name[::-1]
        name = name[: name.find("e")]
        name = name[::-1]

        try:
            eew = int(name)
            self.eew = eew
        except Exception:
            # FIXME: except should be specific
            eew = 8
            self.eew = eew
        finally:
            return eew
