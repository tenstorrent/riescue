# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Utility functions and classes for vector instruction setup.

Contains helper functions and the vector register initializer class.
"""

from riescue.compliance.config import Resource
from riescue.compliance.lib.instr_setup.utils import Page, Offset


def choose_randomly_but_not(resource_db: Resource, choices: list, not_these: list):
    """Choose a random element from choices that is not in not_these."""
    assert len(choices) > 0, "No choices to choose from"
    remaining_choices = [c for c in choices if c not in not_these]
    assert len(remaining_choices) > 0, f"Could not find a choice that was not in {not_these}"

    choice = resource_db.rng.random_entry_in(remaining_choices)
    return choice


def no_overlap(vreg1_index: int, vreg2_index: int, emul: int) -> bool:
    """Check if two vector registers overlap given the effective LMUL."""
    v1_nearest_emul_aligned = vreg1_index - (vreg1_index % emul)
    v2_nearest_emul_aligned = vreg2_index - (vreg2_index % emul)
    return v1_nearest_emul_aligned != v2_nearest_emul_aligned


class VecInstrVregInitializer:
    def __init__(self, label, temp_regs, vl):
        self.pages = []
        self.vregs_to_pages_and_offsets = dict()
        self.vregs_to_initial_values = dict()
        self.vregs_to_configs = dict()
        self.label = label
        self.vl = vl
        self.temp_regs = temp_regs
        assert len(temp_regs) > 1, "Need at least 2 temp regs for this to work"
        for reg in self.temp_regs:
            assert reg is not None, "Temp regs cannot be None"

    def compute_vreg_group_size(self, vreg, vlen=256):
        vl, eew, lmul = self.vregs_to_configs[vreg]
        if not isinstance(vl, str):
            return int(eew * vl)
        elif vl == "vlmax" or vl == "zero":
            return int(lmul * vlen)
        else:
            assert False, f"Invalid vl value {vl}"

    def request_another_page(self, page_size):
        page = Page(self.label, page_size, len(self.pages))
        self.pages.append(page)

    def check_if_vreg_would_fit(self, page, vreg):
        offsets = page.offsets
        if len(offsets) == 0:
            remaining_space = page.size
        else:
            remaining_space = page.size - (offsets[-1].offset + offsets[-1].size)
        return remaining_space >= self.compute_vreg_group_size(vreg)

    def add_vreg_to_page(self, page, vreg):
        last_offset_end = 0 if len(page.offsets) == 0 else int(page.offsets[-1].offset + page.offsets[-1].size)
        page.add_offsets([Offset(last_offset_end, self.compute_vreg_group_size(vreg), self.vregs_to_initial_values[vreg])])
        self.vregs_to_pages_and_offsets[vreg] = (page.lin_addr, page.offsets[-1])

    def test_assign_vreg_to_page(self, vreg):
        for page in self.pages:
            if self.check_if_vreg_would_fit(page, vreg):
                self.add_vreg_to_page(page, vreg)
                return True
        # print(f"vreg to configs: {self.vregs_to_configs[vreg]}")
        # assert False, f"Vreg group size {self.compute_vreg_group_size(vreg)} is larger than page size {self.pages[-1].size}!"
        self.request_another_page(0x1000)
        last_page = self.pages[-1]
        assert self.check_if_vreg_would_fit(last_page, vreg), f"Vreg group size {self.compute_vreg_group_size} is larger than page size {last_page.size}!"
        self.add_vreg_to_page(last_page, vreg)
        return True

    def add_vreg(self, vreg, initial_value, vl, eew, lmul):
        if len(self.pages) == 0:
            self.request_another_page(0x1000)

        if vreg not in self.vregs_to_initial_values:
            self.vregs_to_initial_values[vreg] = initial_value
            self.vregs_to_configs[vreg] = (vl, eew, lmul)
            self.test_assign_vreg_to_page(vreg)
            return True
        return False

    def get_mem_setups(self) -> str:
        return "".join([page.get_setup() for page in self.pages])

    def get_mem_inits(self) -> str:
        return "".join([page.get_inits() for page in self.pages])

    def choose_vload(self, eew) -> str:
        return "vle" + ("8" if eew == 8 else "16" if eew == 16 else "32" if eew == 32 else "64") + ".v"

    def choose_whole_register_vload(self, lmul, eew) -> str:
        normalized_lmul = str(int(max(1, lmul)))
        stringified_eew = "8" if eew == 8 else "16" if eew == 16 else "32" if eew == 32 else "64"
        return f"vl{normalized_lmul}re{stringified_eew}.v"

    def choose_seg_vload(self, eew) -> str:
        return "vlse" + ("8" if eew == 8 else "16" if eew == 16 else "32" if eew == 32 else "64") + ".v"

    """
        This method is written with the assumption that instruction setup context is managing the configuration of lmul and vsew.
    """

    def orig_get_vloads(self, active_config, instr, setup) -> str:
        vloads = []
        for vreg in self.vregs_to_pages_and_offsets:
            page, offset = self.vregs_to_pages_and_offsets[vreg]
            vl, eew, lmul = self.vregs_to_configs[vreg]
            if active_config:
                tail_setting = instr.config.vta
                mask_setting = instr.config.vma
                vtype_code = setup.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=setup.vlmul, vsew=setup.vsew)
                vloads.append(f"\tli {self.temp_regs[0]}, {hex(vtype_code)}")
                vloads.append(f"\tvsetvl x5, x0, {self.temp_regs[0]}")  # NOTE this always sets max vl
            vloads.append(f"\tli {self.temp_regs[0]}, {page}")
            vloads.append(f"\tli {self.temp_regs[1]}, {offset.offset}")
            vloads.append(f"\tadd {self.temp_regs[0]}, {self.temp_regs[0]}, {self.temp_regs[1]}")
            vloads.append(f"\t{self.choose_vload(eew)} {vreg}, ({self.temp_regs[0]})")
        return "\n".join(vloads)

    def get_vloads(self, active_config, instr, setup, force_integer_lmul=False, override_eew=None, whole_register_load=False) -> str:
        vloads = []
        for vreg in self.vregs_to_pages_and_offsets:
            page, offset = self.vregs_to_pages_and_offsets[vreg]
            vl, eew, lmul = self.vregs_to_configs[vreg]
            if force_integer_lmul:
                lmul = max(1, lmul)
            if override_eew:
                eew = override_eew
            if active_config:
                tail_setting = instr.config.vta
                mask_setting = instr.config.vma
                if isinstance(vl, str):
                    vl = setup.numerical_vl
                vtype_code = setup.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=lmul, vsew=eew)
                vloads.append(f"\t# Vtype is: vlmul = {lmul}, vsew = {eew}")
                vloads.append(f"\tli {self.temp_regs[0]}, {hex(vtype_code)}")
                if vl != -1:
                    vloads.append(f"\tli {self.temp_regs[1]}, {vl}")
                    vloads.append(f"\tvsetvl x5, {self.temp_regs[1]}, {self.temp_regs[0]}")  # NOTE this always sets max vl
                else:
                    vloads.append(f"\tvsetvl x5, x0, {self.temp_regs[0]}")  # NOTE this always sets max vl
            vloads.append(f"\tli {self.temp_regs[0]}, {page}")
            vloads.append(f"\tli {self.temp_regs[1]}, {offset.offset}")
            vloads.append(f"\tadd {self.temp_regs[0]}, {self.temp_regs[0]}, {self.temp_regs[1]}")
            if whole_register_load:
                vloads.append(f"\t{self.choose_whole_register_vload(lmul, eew)} {vreg}, ({self.temp_regs[0]})")
            else:
                vloads.append(f"\t{self.choose_vload(eew)} {vreg}, ({self.temp_regs[0]})")
        return "\n".join(vloads)

    def get_seg_vloads(self, active_config, instr, setup, mask_to_max_vl=False) -> str:
        vloads = []
        for vreg in self.vregs_to_pages_and_offsets:
            page, offset = self.vregs_to_pages_and_offsets[vreg]
            vl, eew, lmul = self.vregs_to_configs[vreg]
            if active_config:
                tail_setting = instr.config.vta
                mask_setting = instr.config.vma
                if isinstance(vl, str):
                    vl = setup.numerical_vl
                vtype_code = setup.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=lmul, vsew=eew)
                vloads.append(f"\t# Vtype is: vlmul = {lmul}, vsew = {eew}")
                vloads.append(f"\tli {self.temp_regs[0]}, {hex(vtype_code)}")
                if mask_to_max_vl:
                    vl = -1  # force set max vl in circumstances where it's needed
                if vl != -1:
                    vloads.append(f"\tli {self.temp_regs[1]}, {vl}")
                    vloads.append(f"\tvsetvl x5, {self.temp_regs[1]}, {self.temp_regs[0]}")  # NOTE this always sets max vl
                else:
                    vloads.append(f"\tvsetvl x5, x0, {self.temp_regs[0]}")  # NOTE this always sets max vl
            vloads.append(f"\tli {self.temp_regs[0]}, {page}")
            vloads.append(f"\tli {self.temp_regs[1]}, {offset.offset}")
            vloads.append(f"\tadd {self.temp_regs[0]}, {self.temp_regs[0]}, {self.temp_regs[1]}")
            vloads.append(f"\tli {self.temp_regs[2]}, {eew//8}")
            vloads.append(f"\t{self.choose_seg_vload(eew)} {vreg}, ({self.temp_regs[0]}), {self.temp_regs[2]}")
        return "\n".join(vloads)
