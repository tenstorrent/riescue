# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from .base import VecLoadStoreBase
from .utilities import choose_randomly_but_not, no_overlap
from .components import VecStoreComponent, VecLoadComponent, VecUnitStrideComponent, VecStridedComponent, VecIndexedComponent


class VecStoreUnitStrideSetup(VecStoreComponent, VecLoadStoreBase, VecUnitStrideComponent):
    def pre_setup(self, instr):
        instr.reg_manager.reinit_vregs()

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"

        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = True
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        self.work_vreg = self.setup_ls_vec_operand(instr, "vs3", payload_reg, was_vset_called=(not dont_generate), maintain_reserved=False, prohibited_reuse=["v0"])
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        min_size_in_bits = self.emul * self.vlen
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            # Write a random value into vstart from 1 to evl -1
            evl = int(self.emul * self.vlen // self.eew)
            self.randomize_vstart(instr, evl)
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vs3"].name}, ({mem_addr}){tail}')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)


class VecLoadUnitStrideSetup(VecLoadComponent, VecUnitStrideComponent, VecLoadStoreBase):
    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        vd_reuse_prohibited = ["wvr"]
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            vd_reuse_prohibited.append("v0")
            instr._fields["vm"].name = "v0"

        self.work_vreg = self.setup_ls_vec_operand(instr, "vd", payload_reg, was_vset_called=(not dont_generate), prohibited_reuse=vd_reuse_prohibited, maintain_reserved=False)
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        min_size_in_bits = self.emul * self.vlen
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        rs1 = instr._fields["rs1"]
        mem_addr = "(" + rs1.name + ")"
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            # Write a random value into vstart from 1 to evl -1
            evl = int(self.emul * self.vlen // self.eew)
            self.randomize_vstart(instr, evl)
        self.write_pre(f"\tli {rs1.name},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, {mem_addr}{tail}')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)


class VecLoadMaskSetup(VecLoadUnitStrideSetup):
    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = True
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        emul_override = 1
        eew_override = 8
        vd_prohibited = ["wvr"]
        self.work_vreg = self.setup_ls_vec_operand(instr, "vd", payload_reg, emul_override, eew_override, was_vset_called=(not dont_generate), prohibited_reuse=vd_prohibited, maintain_reserved=False)

        min_size_in_bits = self.emul * self.vlen
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        rs1 = instr._fields["rs1"]
        mem_addr = "(" + rs1.name + ")"

        self.write_pre(f"\tli {rs1.name},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, {mem_addr}')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)


class VecStoreMaskSetup(VecStoreUnitStrideSetup):
    def pre_setup(self, instr):
        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = True
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        emul_override = 1
        eew_override = 8
        self.work_vreg = self.setup_ls_vec_operand(instr, "vs3", payload_reg, emul_override, eew_override, was_vset_called=(not dont_generate))

        min_size_in_bits = self.emul * self.vlen
        assert 0x1000 > (min_size_in_bits // 8)
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        if self.vstart == "nonzero":
            self.randomize_vstart(instr, int(emul_override * self.vlen) // int(eew_override))
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vs3"].name}, ({mem_addr})')

        offsets = [0]
        sizes = [min_size_in_bits]
        self.initialize_memory(instr, offsets, sizes)


class VecStoreStridedSetup(VecStoreComponent, VecUnitStrideComponent, VecLoadStoreBase, VecStridedComponent):
    def pre_setup(self, instr):
        instr.reg_manager.reinit_vregs()
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"

        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = True
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        self.work_vreg = self.setup_ls_vec_operand(instr, "vs3", payload_reg, was_vset_called=(not dont_generate))
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        # The goal for now is to stay within a 4k page
        rs2 = instr._fields["rs2"]
        byte_stride_mask = 0xF
        rs2_val_constrained = rs2.value & byte_stride_mask
        self.write_pre(f"\tli {rs2.name}, {str(hex(rs2_val_constrained))}")

        min_size_in_bytes = self.compute_memory_footprint_bytes(self.emul, self.vlen, self.eew, rs2_val_constrained)
        assert 0x1000 > min_size_in_bytes
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, int(self.emul * self.vlen) // int(self.eew))
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vs3"].name}, ({mem_addr}), {rs2.name}{tail}')

        offsets = [rs2_val_constrained * element for element in range(0, int(self.emul * self.vlen) // int(self.eew), 1)]
        sizes = [self.eew for offset in offsets]
        self.initialize_memory(instr, offsets, sizes)


class VecLoadStridedSetup(VecLoadComponent, VecUnitStrideComponent, VecLoadStoreBase, VecStridedComponent):
    def pre_setup(self, instr):
        instr.reg_manager.reinit_vregs()
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"

        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = False
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)
        self.first_vsew = self.vsew
        assert len(self.old_config) > 0

        same = str(self.vsew) in instr.name
        vd_prohibited = ["wvr"]
        if mask_enabled:
            vd_prohibited.append("v0")
        self.work_vreg = self.setup_ls_vec_operand(instr, "vd", payload_reg, was_vset_called=False, prohibited_reuse=vd_prohibited)

        assert self.old_config["vsew"] != self.eew or same
        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        assert self.vsew == self.first_vsew

        self.extract_config(instr)
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        # The goal for now is to stay within a 4k page
        rs2 = instr._fields["rs2"]
        byte_stride_mask = 0xF
        rs2_val_constrained = rs2.value & byte_stride_mask
        self.write_pre(f"\tli {rs2.name}, {str(hex(rs2_val_constrained))}")

        min_size_in_bytes = self.compute_memory_footprint_bytes(self.emul, self.vlen, self.eew, rs2_val_constrained)
        assert 0x1000 > min_size_in_bytes
        self.setup_memory(instr.label, "0x1000", "pre_setup")

        mem_addr = instr._fields["rs1"].name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, int(self.emul * self.vlen) // int(self.eew))
        self.write_pre(f"\tli {mem_addr},{self._lin_addr}")
        self.write_pre(f'{instr.label}: {instr.name} {instr._fields["vd"].name}, ({mem_addr}), {rs2.name}{tail}')

        offsets = [rs2_val_constrained * element for element in range(0, int(self.emul * self.vlen) // int(self.eew), 1)]
        sizes = [self.eew for offset in offsets]
        self.initialize_memory(instr, offsets, sizes)


class VecStoreIndexedUnorderedSetup(VecIndexedComponent, VecStoreComponent, VecLoadStoreBase):
    def pre_setup(self, instr):
        assert "lux" not in instr.label, f"instr.label is {instr.label}"
        instr.reg_manager.reinit_vregs()

        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            instr.reg_manager.reserve_reg("v0", "Vector")
            mask_enabled = True
            instr._fields["vm"].name = "v0"

        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = True
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        # We had to modify the register initialization routine because constraints here are necessary to not go outside memory bounds.
        byte_index_mask = 0x3FF
        self.work_vreg = self.setup_ls_index_vec_operand(byte_index_mask, instr, "vs2", payload_reg, was_vset_called=(not dont_generate))
        vs2 = instr._fields["vs2"]

        self.restore_config(instr, False, payload_reg)

        # The store is of sew data so we use the normal register initialization functions.
        vs3 = self.get_operand(instr, "vs3")
        vs3.randomize(prohibit_reuse=[vs2.name, "v0"])
        self.load_one_vreg_group_from_vreg_file(instr, vs3.name, payload_reg, new=True)
        self.finalize_vreg_initializations(instr)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

        page_size = 0x1000
        offsets = self.extract_offsets(instr.name, self.vreg_elvals, self.emul, self.vlen, self.eew, vs2.name)
        min_size_in_bytes = self.compute_memory_footprint_bytes(self.vsew, offsets)
        assert page_size > min_size_in_bytes
        self.setup_memory(instr.label, str(page_size), "pre_setup")

        offset_bias = 0x7FF
        mem_addr = self.get_operand(instr, "rs1").name
        tail = ""
        if mask_enabled:
            tail = f', {instr._fields["vm"].name}.t'

        if self.vstart == "nonzero":
            self.randomize_vstart(instr, int(self.emul * self.vlen) // int(self.eew))
        self.write_pre(f"\tli {mem_addr}, {self._lin_addr}")
        self.write_pre(f"\tli {payload_reg}, {str(hex(offset_bias))}")
        self.write_pre(f"\tadd {mem_addr}, {mem_addr}, {payload_reg}")  # We add the offset bias so that we can handle negative offsets within the page we set up.
        self.write_pre(f"{instr.label}: {instr.name} {vs3.name}, ({mem_addr}), {vs2.name}{tail}")

        sizes = [self.vsew for offset in offsets]
        self.initialize_memory(instr, sorted(offsets), sizes, offset_bias)


class VecLoadIndexedUnorderedSetup(VecIndexedComponent, VecLoadStoreBase):
    def pre_setup(self, instr):
        instr.reg_manager.reinit_vregs()
        mask_enabled = False
        if "vm" in instr._fields and hasattr(instr.config, "masking") and getattr(instr.config, "masking") == "mask":
            mask_enabled = True
            # reserve v0
            instr.reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"

        self.extract_config(instr)
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        dont_generate = True
        self.vector_config(instr, payload_reg, dont_generate=dont_generate)

        # We had to modify the register initialization routine because constraints here are necessary to not go outside memory bounds.
        byte_index_mask = 0x3FF
        self.work_vreg = self.setup_ls_index_vec_operand(byte_index_mask, instr, "vs2", payload_reg, was_vset_called=(not dont_generate))
        vs2 = instr._fields["vs2"]
        self.restore_config(instr, False, payload_reg)

        # The load is of sew data so we use the normal register initialization functions.
        vd = self.get_operand(instr, "vd")
        vd.randomize(prohibit_reuse=["v0", self.work_vreg])
        self.load_one_vreg_group_from_vreg_file(instr, vd.name, payload_reg, new=True)
        self.finalize_vreg_initializations(instr)

        if mask_enabled:
            self.load_v0_mask(instr, payload_reg)

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
            self.randomize_vstart(instr, int(self.emul * self.vlen) // int(self.eew))
        self.write_pre(f"\tli {mem_addr}, {self._lin_addr}")
        self.write_pre(f"\tli {payload_reg}, {str(hex(offset_bias))}")
        self.write_pre(f"\tadd {mem_addr}, {mem_addr}, {payload_reg}")  # We add the offset bias so that we can handle negative offsets within the page we set up.
        self.write_pre(f"{instr.label}: {instr.name} {vd.name}, ({mem_addr}), {vs2.name}{tail}")

        sizes = [self.vsew for offset in offsets]
        self.initialize_memory(instr, sorted(offsets), sizes, offset_bias)

    def post_setup(self, modified_arch_state, instr):
        # We don't set the config to emul and eew for the reason that the data in the indexed instructions is arranged according to vsew and vlmul
        # What we call in this setup class eew and emul pertain to the index vector only, which itself is not the subject of the post_setup
        # checking code.
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg)


class VecLoadUnitStrideFaultFirstSetup(VecLoadUnitStrideSetup):
    alias = True
