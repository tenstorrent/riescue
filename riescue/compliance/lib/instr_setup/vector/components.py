# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import copy
import math
from riescue.compliance.lib.common import lmul_map


class VecUnitStrideComponent:
    """EEW for vector load store are in the instruction name itself, last or second to last substring before .v suffix"""

    """Works for strided as well"""

    def extract_eew_unit_stride(self, instruction_name):
        name = copy.deepcopy(instruction_name)
        if "ff" not in name:
            name = name[: name.find(".v")]

            name = name[::-1]
            name = name[: name.find("e")]
            name = name[::-1]

            eew = int(name)
            self.eew = eew
            return eew
        else:
            name = copy.deepcopy(instruction_name)
            name = name[: name.find("ff.v")]

            name = name[::-1]
            name = name[: name.find("e")]
            name = name[::-1]

            eew = int(name)
            self.eew = eew
            return eew

    def extract_eew(self, instruction_name):
        return self.extract_eew_unit_stride(instruction_name)


class VecStoreComponent:
    def post_setup_a(self, modified_arch_state, instr):
        if modified_arch_state is None or "_mask_" in instr.label:
            print(f"No updates for instr label: {instr.label}")

            """Determine if the mask in the lower bits of v0 masks out the max number of active elements we can expect"""
            #
            #   Keeping this here for when we actually do second pass with masking.
            #
            # vl = instr.config.avl
            # vset_instr = instr.config.vset_instr
            # if vl == "vlmax":
            #     if vset_instr == "vsetivli":
            #         vl = 31
            #     else:
            #         vl = 256
            # else:
            #     vl = int(vl)
            # v0_elsize = 64
            # self.check_v0_mask_against_max_elements(self.emul, self.vlen, self.eew, vl, instr.label, v0_elsize)
            assert "_mask_" in instr.label or "_zero_" in instr.label, f"No updates. ERROR: {instr.name} CONFIG: {instr.config}"

            """Every operation was masked out, no architectural state change to match"""
            if not self.resource_db.wysiwyg:
                if self.j_pass_ok():
                    self.write_post(";#test_passed()")
            else:
                self.write_post("\tadd x31,x31,x0")

            return

        mod_mem_loc = modified_arch_state[4]
        mem_locs = mod_mem_loc.split(";")
        mod_mem_vals = modified_arch_state[5]
        values = mod_mem_vals.split(";")

        turn_hex_into_byte_array = lambda x: [x[i : i + 2] for i in range(0, len(x), 2)]
        filtered_mem_ranges = []
        for value, address in zip(values, mem_locs):
            address_num = int(address, 16)
            if len(value) % 2 != 0:
                value = "0" + value
            address_max = address_num + int(len(value) // 2)
            filtered_mem_ranges.append((address_num, address_max, value))
        filtered_mem_ranges.sort(key=lambda x: x[0])
        overlapping_indices = set()
        for i in range(len(filtered_mem_ranges)):
            for j in range(i + 1, len(filtered_mem_ranges)):
                if filtered_mem_ranges[i][1] > filtered_mem_ranges[j][0]:
                    overlapping_indices.add(i)
                    overlapping_indices.add(j)
        temp_mem_ranges = []
        for i in range(len(filtered_mem_ranges)):
            if i not in overlapping_indices:
                temp_mem_ranges.append(filtered_mem_ranges[i])
        filtered_mem_ranges = temp_mem_ranges
        byte_values_and_addresses = []
        for address, _, value in filtered_mem_ranges:
            if len(value) % 2 != 0:
                value = "0" + value
            offset = 0
            for byte in reversed(turn_hex_into_byte_array(value)):
                address_num = address + offset
                address_string = hex(address_num)
                byte_values_and_addresses.append((byte, address_string))
                offset += 1

        result_reg1 = self.get_random_reg(instr.reg_manager, "Int")
        result_reg2 = self.get_random_reg(instr.reg_manager, "Int")
        base_addr_reg = self.get_random_reg(instr.reg_manager, "Int")

        """
        #   Ignore cases where there are more than one byte value per address, indicating an overlapping write.
        """
        for byte_values, mem_loc in byte_values_and_addresses:
            # if len(byte_values) > 1:
            #     continue
            byte_value = byte_values
            mem_addr_argument = "(" + base_addr_reg + ")"
            self.write_post(f'\tli {result_reg1}, {"0x"+byte_value}')
            self.write_post(f"\tli {base_addr_reg}, {mem_loc}")
            self.write_post(f"\tlbu {result_reg2}, {mem_addr_argument}")

            if self.resource_db.wysiwyg:
                temp_reg = self.get_random_reg(instr.reg_manager, "Int")
                self.write_post(f"\tsub {temp_reg}, {result_reg1}, {result_reg2}")
                self.write_post(f"\tadd x31, x31, {temp_reg}")
            else:
                self.write_post(f"\tbne {result_reg1}, {result_reg2}, 1f")

        if self.j_pass_ok():
            self.write_post(";#test_passed()")
        else:
            self.write_post("\tj 2f")
        self.write_post("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_post(";#test_failed()")
        self.write_post("\t2:\n")

    def post_setup(self, modified_arch_state, instr):
        data_size_in_bits = self.vsew if "ei" in instr.name else self.eew  # Data size follows vtype for indexed stores
        data_size_in_bytes = data_size_in_bits // 8

        mod_mem_loc = None
        mem_locs = None
        mod_mem_vals = None
        values = None
        if modified_arch_state is not None:
            mod_mem_loc = modified_arch_state[4]
            mod_mem_vals = modified_arch_state[5]
            mem_locs = mod_mem_loc.split(";")
            values = mod_mem_vals.split(";")

            # sometimes whisper has memloc and memvals in next index. check if equals is there before proceeding. Don't know why.
            # lin addresses are split into modified_arch_state[4]. replace every phys address in mem_locs prior to doing so
            if "=" not in mod_mem_loc:
                mod_lin_addresses = modified_arch_state[4]
                lin_addresses = mod_lin_addresses.split(" ")

                mod_mem_loc = modified_arch_state[5]
                mem_locs = mod_mem_loc.split(" ")
                values = [""]

                if "=" in mod_mem_loc:
                    old_mem_locs = mem_locs
                    mem_locs = []
                    for index, old_mem_loc in enumerate(old_mem_locs):
                        _, val = old_mem_loc.split("=")
                        mem_locs.append(f"{lin_addresses[index]}={val}")

        if modified_arch_state is None or "_mask_" in instr.label or mem_locs[0] == "":
            print(f"No updates for instr label: {instr.label}, config: {instr.config}")

            """Determine if the mask in the lower bits of v0 masks out the max number of active elements we can expect"""
            #
            #   Keeping this here for when we actually do second pass with masking.
            #
            # vl = instr.config.avl
            # vset_instr = instr.config.vset_instr
            # if vl == "vlmax":
            #     if vset_instr == "vsetivli":
            #         vl = 31
            #     else:
            #         vl = 256
            # else:
            #     vl = int(vl)
            # v0_elsize = 64
            # self.check_v0_mask_against_max_elements(self.emul, self.vlen, self.eew, vl, instr.label, v0_elsize)
            assert "_mask_" in instr.label or "_zero_" in instr.label or "_nonzero_" in instr.label, f"No updates. ERROR: {instr.name} CONFIG: {instr.config}"

            """Every operation was masked out, no architectural state change to match"""
            if not self.resource_db.wysiwyg:
                if self.j_pass_ok():
                    self.write_post(";#test_passed()")
            else:
                self.write_post("\tadd x31,x31,x0")

            return

        # Whisper gives also the physical address for no good reason, ignore it but be agnostic of Spike format.
        filtered_values = [val.split("=")[-1] for val in values]
        values = filtered_values

        # Goofy output from whisper script, all update info in mem_locs array
        if len(values) < len(mem_locs) or values[0] == "":
            values = []
            filtered_mem_locs = []
            for mem_loc in mem_locs:
                mem_loc_split = mem_loc.split(" ")
                for split_loc in mem_loc_split:
                    addr, val = split_loc.split("=")
                    filtered_mem_locs.append(addr)
                    values.append(val)
            mem_locs = filtered_mem_locs

        filtered_mem_ranges = []
        for value, address in zip(values, mem_locs):
            address_num = None
            try:
                address_num = int(address, 16)
            except Exception:
                # FIXME: except should be specific
                assert False, f"Address {address} is not a valid hex number, {modified_arch_state}"
            if len(value) % (data_size_in_bytes) != 0:
                gap = data_size_in_bytes - len(value)
                value = "0" * gap + value
            address_max = address_num + int(data_size_in_bytes)
            filtered_mem_ranges.append((address_num, address_max, value))
        filtered_mem_ranges.sort(key=lambda x: x[0])

        overlapping_indices = set()
        for i in range(len(filtered_mem_ranges)):
            for j in range(i + 1, len(filtered_mem_ranges)):
                if filtered_mem_ranges[i][1] > filtered_mem_ranges[j][0]:
                    overlapping_indices.add(i)
                    overlapping_indices.add(j)
        temp_mem_ranges = []
        for i in range(len(filtered_mem_ranges)):
            if i not in overlapping_indices:
                temp_mem_ranges.append(filtered_mem_ranges[i])
        filtered_mem_ranges = temp_mem_ranges

        byte_values_and_addresses = []
        for address, _, value in filtered_mem_ranges:
            address_num = address
            address_string = hex(address_num)
            byte_values_and_addresses.append((value, address_string))

        result_reg1 = self.get_random_reg(instr.reg_manager, "Int")
        result_reg2 = self.get_random_reg(instr.reg_manager, "Int")
        base_addr_reg = self.get_random_reg(instr.reg_manager, "Int")

        """
        #   Ignore cases where there are more than one byte value per address, indicating an overlapping write.
        """
        load_instr = "lbu"
        mask = "0xff"
        need_mask = True
        if data_size_in_bits == 8:
            load_instr = "lbu"
            mask = "0xff"
            need_mask = True
        elif data_size_in_bits == 16:
            load_instr = "lhu"
            mask = "0xffff"
            need_mask = True
        elif data_size_in_bits == 32:
            load_instr = "lw"
            mask = "0xffffffff"
            need_mask = True
        elif data_size_in_bits == 64:
            load_instr = "ld"
            mask = "0xffffffffffffffff"
            need_mask = False

        mask_reg = None
        if need_mask:
            mask_reg = self.get_random_reg(instr.reg_manager, "Int")
            self.write_post(f"\tli {mask_reg}, {mask}")

        for byte_values, mem_loc in byte_values_and_addresses:
            byte_value = byte_values
            mem_addr_argument = "(" + base_addr_reg + ")"
            self.write_post(f'\tli {result_reg1}, {"0x"+byte_value}')
            self.write_post(f"\tli {base_addr_reg}, {mem_loc}")
            self.write_post(f"\t{load_instr} {result_reg2}, {mem_addr_argument}")
            if need_mask:
                self.write_post(f"\tand {result_reg2}, {result_reg2}, {mask_reg}")

            if self.resource_db.wysiwyg:
                temp_reg = self.get_random_reg(instr.reg_manager, "Int")
                self.write_post(f"\tsub {temp_reg}, {result_reg1}, {result_reg2}")
                self.write_post(f"\tadd x31, x31, {temp_reg}")
            else:
                self.write_post(f"\tbne {result_reg1}, {result_reg2}, 1f")

        if self.j_pass_ok():
            self.write_post(";#test_passed()")
        else:
            self.write_post("\tj 2f")
        self.write_post("\t1:")
        if not self.resource_db.wysiwyg:
            self.write_post(";#test_failed()")
        self.write_post("\t2:\n")


class VecLoadComponent:
    def post_setup(self, modified_arch_state, instr):
        active_elts = min(min(self.numerical_vl, self.vlen * self.vlmul / self.vsew), (self.vlen * self.emul) / self.eew)
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts, working_eew=self.eew, working_emul=self.emul)


class VecStridedComponent:
    def compute_memory_footprint_bytes(self, emul, vlen, eew, stride):
        num_elts = int(emul * vlen) // eew
        if stride > (eew // 8):
            return num_elts * stride
        else:
            return num_elts * eew // 8


class VecIndexedComponent:

    def get_values_from_reg_group(self, _vreg_val_dict, _vreg_name, _vlen, _vsew):
        bits_per_byte = 8
        chars_per_byte = 2

        # Note this gets the values for the whole group despite refering to only the lead register in the group because of how values are loaded into the group
        # with slide.
        _values_temp = copy.deepcopy(_vreg_val_dict[_vreg_name])
        assert _values_temp[0][:2] == "0x"

        _values_temp = "".join("".join("0" for pad in range(_vsew // bits_per_byte * chars_per_byte - len(value[2:]))) + value[2:] for value in _values_temp)

        return _values_temp

    def extract_index_bit_width_from_name(self, instr_name):
        name = copy.deepcopy(instr_name)
        name = name[: name.find(".v")]
        name = name[::-1]
        name = name[: name.find("i")]
        name = name[::-1]
        index_bit_width = int(name)
        self.eew = index_bit_width

        return index_bit_width

    def slice_hex_string(self, index_bit_width, vlmul, vlen, hex_string):
        bits_per_byte = 8
        chars_per_byte = 2
        lsb = 0
        msb = index_bit_width
        num_hex_chars_whole_group = (int(vlmul * vlen) // bits_per_byte) * chars_per_byte
        offsets = list()

        while msb <= int(vlmul * vlen) and msb < (len(hex_string) * 4):
            msb_bytes = msb // bits_per_byte
            lsb_bytes = lsb // bits_per_byte
            left_slice_index = num_hex_chars_whole_group - msb_bytes * chars_per_byte
            right_slice_index = num_hex_chars_whole_group - lsb_bytes * chars_per_byte
            offset_element_string = hex_string[left_slice_index:right_slice_index]
            offset_hex = int(offset_element_string, 16)
            offsets.insert(0, offset_hex)  # The rightmost digits in the value_string are the first element offsets
            msb = msb + index_bit_width
            lsb = lsb + index_bit_width

        return offsets

    def extract_offsets(self, instr_name, vreg_val_dict, vlmul, vlen, vsew, vs2_name):
        offsets = list()
        index_bit_width = self.extract_index_bit_width_from_name(instr_name)
        value_string = self.get_values_from_reg_group(vreg_val_dict, vs2_name, vlen, vsew)
        offsets = self.slice_hex_string(index_bit_width, vlmul, vlen, value_string)
        return offsets

    def compute_memory_footprint_bytes(self, vsew, offsets):
        assert isinstance(offsets[0], int)
        vsew_bytes = vsew // 8
        return (vsew_bytes + max(offsets)) - (min(offsets) - vsew_bytes)

    def compute_emul(self, eew, sew, lmul):
        emul = (eew / sew) * lmul
        assert emul <= 8 and emul >= 0.125, "It is an architectural limitation that emul must fall within a certain range."
        self.emul = emul
        return emul

    def extract_eew(self, instruction_name):
        return self.extract_index_bit_width_from_name(instruction_name)

    # Incorporates an element mask argument that gets forwarded to the call to load_one_vreg_group_from_vreg_file
    def setup_ls_index_vec_operand(self, element_mask, instruction, operand_name, payload_xreg_name=None, emul_override=None, eew_override=None, was_vset_called=False):
        if payload_xreg_name is None:
            payload_xreg_name = self.get_random_reg(instruction.reg_manager, "Int")

        # Set the vector config accounting for the load store instruction name and remember the old vlmul
        _old_vlmul = self.vlmul
        self.setup_ls_initialization_config(instruction, False, payload_xreg_name, emul_override, eew_override, was_vset_called)
        _new_vlmul = self.vlmul

        # We need to choose a working vreg according to which is greater, emul or vlmul
        _max_vlmul = max(_old_vlmul, _new_vlmul)
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        self.set_vreg_choices_with_emul(instruction, lmul_map[_max_vlmul], narrow_from_old_choices, maintain_reserved_registers)
        work_vreg = self.get_random_reg(instruction.reg_manager, "Vector")

        # Now set the vreg choices to reflect the emul and the previously selected working vreg
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        self.set_vreg_choices_with_emul(instruction, lmul_map[_new_vlmul], narrow_from_old_choices, maintain_reserved_registers)
        # Select the emul conforming register
        instruction._fields[operand_name].randomize()
        # Load the special emul configured vreg using the work vreg we selected earlier
        self.load_one_vreg_group_from_vreg_file(instruction, instruction._fields[operand_name].name, payload_xreg_name, work_vreg, element_mask, new=True, label_addendum="_ls_idx")
        self.finalize_vreg_initializations(instruction)

        # Reset the configuration to the basic one for this instruction instance
        dont_restore = _old_vlmul == _new_vlmul
        self.restore_config(instruction, dont_restore, payload_xreg_name)

        # Reset the vector register choices to reflect the basic configration as well as the registers we have just selected.
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        self.set_vreg_choices_with_emul(instruction, lmul_map[self.vlmul], narrow_from_old_choices, maintain_reserved_registers)

        return work_vreg


class SegmentedComponent:
    @classmethod
    def extract_nf(cls, instr_name: str) -> int:
        name = copy.deepcopy(instr_name)
        name = name[name.find("seg") + 3 :]

        nf = int(name[0])
        return nf

    def setup_nf_ls_vec_operand(self, instruction, operand_name, payload_xreg_name=None, was_vset_called=False, prohibited_reuse=[]):
        if payload_xreg_name is None:
            payload_xreg_name = self.get_random_reg(instruction.reg_manager, "Int")

        # extract the nf value from the instruction name
        self.nf_number = self.extract_nf(instruction.name)

        # Set the vector config accounting for the load store instruction name and remember the old vlmul
        _old_vlmul = self.vlmul
        self.setup_ls_initialization_config(instruction, False, payload_xreg_name, None, None, was_vset_called)
        _new_vlmul = self.vlmul
        _max_vlmul = max(_old_vlmul, _new_vlmul)

        # Clamp vlmul so that we don't attempt an illegal configuration with nf
        if (_max_vlmul * self.nf_number) > 8:
            self.vlmul = math.floor(8 / self.nf_number)
            _old_vlmul = self.vlmul
            self.setup_ls_initialization_config(instruction, False, payload_xreg_name, None, None, was_vset_called)
            _new_vlmul = self.vlmul
            _max_vlmul = max(_old_vlmul, _new_vlmul)

        # We need to choose a working vreg according to which is greater, emul or vlmul
        _max_vlmul = max(_old_vlmul, _new_vlmul)
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        self.set_vreg_choices_with_emul(instruction, lmul_map[_max_vlmul], narrow_from_old_choices, maintain_reserved_registers)
        work_vreg = self.get_lowest_index_reg(instruction.reg_manager, "Vector")  # Get the lowest index reg group to leave maximum room for the expansive requirements of nf groups
        prohibited_reuse.append(work_vreg)
        # Now set the vreg choices to reflect the emul and the previously selected working vreg
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        # Select the nf and emul conforming register
        _use_vlmul = max(1.0, _new_vlmul)  # Assuming that successive fields imply successive reg indices even if lmul < 1

        _effective_min_vlmul = self.nf_number * _use_vlmul
        assert _effective_min_vlmul <= 8.0, f"Effective minimum vlmul is {_effective_min_vlmul} which is greater than 8.0"
        if _effective_min_vlmul not in [1.0, 2.0, 4.0, 8.0]:
            for lmul in [1.0, 2.0, 4.0, 8.0]:
                if _effective_min_vlmul < lmul:
                    _effective_min_vlmul = lmul
                    break
        self.set_vreg_choices_with_emul(instruction, lmul_map[_effective_min_vlmul], narrow_from_old_choices, maintain_reserved_registers)
        instruction._fields[operand_name].randomize()
        count = 0
        while instruction._fields[operand_name].name in prohibited_reuse and count < 10:
            instruction._fields[operand_name].randomize()
            count += 1
        assert instruction._fields[operand_name].name not in prohibited_reuse, "Could not find a register that was not in the prohibited reuse list"
        prohibited_reuse = []
        assert int(instruction._fields[operand_name].name[1:]) < 32, "Register index is greater than 31"

        # Load the special emul configured vregs using the work vreg we selected earlier
        self.set_vreg_choices_with_emul(instruction, lmul_map[max(1.0, _new_vlmul)], narrow_from_old_choices, maintain_reserved_registers)
        base_reg_index = int(instruction._fields[operand_name].name[1:])
        first = True
        for field_num in range(self.nf_number):
            _reg_index = base_reg_index + math.floor(field_num * _new_vlmul)
            assert _reg_index < 32, "Register index is greater than 31"
            _reg_name = "v" + str(_reg_index)
            label_addendum = "_nf" + str(self.nf_number)
            self.load_one_vreg_group_from_vreg_file(instruction, _reg_name, payload_xreg_name, new=first, label_addendum=label_addendum)

        self.finalize_vreg_initializations(instruction)

        # Reset the configuration to the basic one for this instruction instance
        dont_restore = _old_vlmul == _new_vlmul
        self.restore_config(instruction, dont_restore, payload_xreg_name)

        # Reset the vector register choices to reflect the basic configration as well as the registers we have just selected.
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        self.set_vreg_choices_with_emul(instruction, lmul_map[self.vlmul], narrow_from_old_choices, maintain_reserved_registers)

        return work_vreg
