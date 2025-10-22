# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import copy
import math

from riescue.compliance.lib.instr_setup.base import InstrSetup
from riescue.compliance.lib.instr_setup.utils import FpLoadUtil
from riescue.compliance.lib.instr_setup.vector.utilities import VecInstrVregInitializer
from riescue.compliance.lib.common import lmul_map
from riescue.compliance.lib.riscv_registers import get_nbit_value_random_type


class VectorInstrSetup(InstrSetup):

    def __init__(self, resource_db):
        super().__init__(resource_db)
        self.vsew = 32
        self.vlmul = 1
        self.num_vregs = 32
        self.vlen = self.resource_db.vlen
        self.vl = None
        self.old_config = dict()
        self.vreg_elvals = dict()
        self.vreg_initializers = []
        self.vset_instruction = None

    def get_a_new_vreg_initializer(self, instr, label_addendum) -> VecInstrVregInitializer:
        temp_regs = []
        if len(self.vreg_initializers) == 0:
            temp_regs = [self.get_random_reg(instr.reg_manager, "Int"), self.get_random_reg(instr.reg_manager, "Int")]
        else:
            temp_regs = self.vreg_initializers[-1].temp_regs
        return VecInstrVregInitializer(instr.label + label_addendum, temp_regs, self.vl)

    def get_vreg_initializer(self, instr, new, label_addendum):
        if len(self.vreg_initializers) == 0 or new:
            self.vreg_initializers.append(self.get_a_new_vreg_initializer(instr, label_addendum))

        return self.vreg_initializers[-1]

    def request_to_initialize_vreg_with_values(self, instr, vreg, eew, lmul, elvals, vl, new, label_addendum):
        if label_addendum != "":
            assert new, "Label addendum is only supported for new vreg initializations, and normally to distinguish ls operands"

        vreg_initializer = self.get_vreg_initializer(instr, new, label_addendum)
        vreg_initializer.add_vreg(vreg, elvals, vl, eew, lmul)

    def finalize_vreg_initializations(self, instr):
        assert len(self.vreg_initializers) > 0, "No vreg initializers to finalize!"
        vreg_initializer = self.get_vreg_initializer(instr, new=False, label_addendum="")
        self.write_pre(vreg_initializer.get_mem_setups())
        self.write_pre(vreg_initializer.get_vloads(active_config=False, instr=instr, setup=self))
        self.write_data(instr, vreg_initializer.get_mem_inits())

    def extract_config(self, instr):
        # check if instr.config has vset_instr

        if not hasattr(instr.config, "vset_instr"):
            assert False, f"No vset instruction found in {instr.name} configuration! config: {instr.config}"
        self.vset_instruction = instr.config.vset_instr
        self.vsew = int(instr.config.vsew)
        self.vlmul = lmul_map[instr.config.vlmul]
        self.vl = instr.config.avl
        if hasattr(instr.config, "vxrm"):
            self.vxrm = instr.config.vxrm
        if hasattr(instr.config, "vstart"):
            self.vstart = instr.config.vstart

        # try to interpret self.vl as an int
        try:
            self.numerical_vl = int(self.vl)
        except Exception:
            # FIXME: except should be specific
            if self.vl == "vlmax" or self.vl == "zero":
                self.numerical_vl = 31
            else:
                self.numerical_vl = 9999

    def give_config(self):
        return {"vsew": self.vsew, "vlmul": self.vlmul, "num_vregs": self.num_vregs, "vlen": self.vlen, "vl": self.vl}

    def replace_config(self, instr, config_dict, dont_generate, payload_reg=None, was_vset_called=False):
        if was_vset_called and all([config_dict[key] == getattr(self, key) for key in config_dict.keys()]):
            return

        self.vsew = config_dict["vsew"]
        self.vlmul = config_dict["vlmul"]
        self.num_vregs = config_dict["num_vregs"]
        self.vlen = config_dict["vlen"]
        self.vl = config_dict["vl"]
        if payload_reg is None:
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        self.vector_config(instr, payload_reg, dont_generate)

    def restore_config(self, instr, dont_generate, payload_reg=None):
        assert len(self.old_config) > 0, "No old config to restore!"
        # if all([self.old_config[key] == getattr(self, key) for key in self.old_config.keys()]):
        #    print(f"Interal state of {instr.name} is already in the old config.")

        if payload_reg is None:
            payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        self.replace_config(instr, self.old_config, dont_generate, payload_reg)

    def make_vtype_code(self, vma, vta, vlmul, vsew):
        vsew_code_dict = {8: 0b000, 16: 0b001, 32: 0b010, 64: 0b011}
        vlmul_code_dict = {"m1": 0b000, "m2": 0b001, "m4": 0b010, "m8": 0b011, "mf2": 0b111, "mf4": 0b110, "mf8": 0b101}
        vma = 1 if vma == "1" or vma == 1 else 0
        vta = 1 if vta == "1" or vta == 1 else 0
        vlmul = vlmul_code_dict[lmul_map[vlmul]]
        vsew = vsew_code_dict[vsew]

        return (vma << 7) | (vta << 6) | (vsew << 3) | vlmul

    def vector_config(self, instr, vl_setting_reg_name, dont_generate):
        if len(self.old_config) == 0:
            self.old_config = self.give_config()

        if dont_generate:
            return

        vset_instruction = self.vset_instruction
        vl = self.vl  # = "vlmax" if instr.config.avl == "vlmax" else self.vl
        tail_setting = instr.config.vta
        mask_setting = instr.config.vma
        end_settings = f"{'ta' if tail_setting == '1' else 'tu'}, {'ma' if mask_setting == '1' else 'mu'}"

        if vset_instruction == "vsetivli":
            if vl == "vlmax":
                vl = 31
            elif vl == "zero":  # NOTE zero means 0 here because we're governing instruction execution rather than GPR initialization.
                vl = 0
            elif isinstance(vl, int) and vl > 31:
                vl = 31

            if self.resource_db.vector_bringup:

                # Add nops before and after vset*
                for _ in range(7):
                    self.write_pre("\t.nop")
                self.write_pre(f"\tvsetivli x0, {hex(vl)}, e{self.vsew}, {lmul_map[self.vlmul]}, {end_settings}")
                for _ in range(7):
                    self.write_pre("\t.nop")

            else:
                self.write_pre(f"\tvsetivli x5, {hex(vl)}, e{self.vsew}, {lmul_map[self.vlmul]}, {end_settings}")
        else:
            if vl == "vlmax":
                vl_setting_reg_name = "x0"
            else:
                while vl_setting_reg_name == "x0":
                    vl_setting_reg_name = self.get_random_reg(instr.reg_manager, "Int")
                if vl == "zero":
                    self.write_pre(f"\tli {vl_setting_reg_name},0")
                else:
                    self.write_pre(f"\tli {vl_setting_reg_name},{hex(vl)}")

        if vset_instruction == "vsetvli":
            self.write_pre(f"\tvsetvli x5, {vl_setting_reg_name}, e{self.vsew}, {lmul_map[self.vlmul]}, {end_settings}")
        elif vset_instruction == "vsetvl":
            vtype_reg_name = self.get_random_reg(instr.reg_manager, "Int")
            vtype_code = self.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=self.vlmul, vsew=self.vsew)
            self.write_pre(f"\tli {vtype_reg_name}, {hex(vtype_code)}")
            self.write_pre(f"\tvsetvl x5, {vl_setting_reg_name}, {vtype_reg_name}")

    """
    #   The register manager is originally configured to adjust the randomly selectible vector registers to correspond with the a vector configuration
    #   including the VLMUL and VSEW values. However, vector load store instructions use an EMUL value and EEW implied in the instruction mnemonic or encoding.
    #   This means that the register manager must be made to determine legal vector register indices according to the EMUL value instead of LMUL.
    """

    def set_vreg_choices_with_emul(self, instruction, emul, preserve_old_reg_availabilities=True, preserve_existing_reservations=True, reserved_register_names=[]) -> None:
        emul_use = "m1" if lmul_map[emul] < 1 else emul
        config_interface_anonymous_object = type("config_type", (object,), {"vlmul": emul_use})()

        instruction._reg_manager.randomize_regs(config_interface_anonymous_object, preserve_old_reg_availabilities, preserve_existing_reservations)

    def vector_frontmatter_common(self, instr, set_vreg_choices=True, overload_vsew=-1, overload_vlmul=-1):
        if set_vreg_choices:
            instr.reg_manager.reinit_vregs()
        self.extract_config(instr)
        if overload_vsew > 0:
            self.vsew = overload_vsew
        if overload_vlmul > 0:
            self.vlmul = overload_vlmul
        narrow_from_old_choices = False
        maintain_reserved_registers = False
        if set_vreg_choices:
            self.set_vreg_choices_with_emul(instr, lmul_map[self.vlmul], narrow_from_old_choices, maintain_reserved_registers)

        if "vm" in instr._fields and hasattr(instr.config, "masking") and instr.config.masking == "mask":
            instr._reg_manager.reserve_reg("v0", "Vector")
            instr._fields["vm"].name = "v0"
        payload_reg = self.get_random_reg(instr.reg_manager, "Int")
        if set_vreg_choices:
            self.work_vreg = self.get_random_reg(instr.reg_manager, "Vector")
        else:
            self.work_vreg = None
        self.vector_config(instr, payload_reg, False)

        return payload_reg, self.work_vreg

    def sign_extend(self, value, sew, bits):

        # Find the bits required to represent a number
        if value != 0:
            bit_width = int(max(sew, math.log(value, 2) + 1))
        else:
            bit_width = sew

        # Convert the number to "bit_width" long list of bits.
        output = [1 if value & (1 << (bit_width - 1 - n)) else 0 for n in range(bit_width)]

        # Find the MSB and pad it to the required length of bits as specified.
        msb = output[0]
        padding = bits - len(output)
        for _ in range(padding):
            output.insert(0, msb)
        result = hex(int("".join([str(x) for x in output]), 2))
        return result

    def generate_elements(self, vsew, eew, element_mask):
        elements = []

        if vsew == eew:
            elements = [get_nbit_value_random_type(self.resource_db, vsew) & element_mask]

        elif vsew < eew:
            data = get_nbit_value_random_type(self.resource_db, eew) & element_mask
            select_mask = int("0x" + "".join(["ff" for byte in range(vsew // 8)]), 16)

            for elt in range(int(eew // vsew)):
                elements.append(data & select_mask)
                select_mask = select_mask << vsew
            elements = reversed(elements)

        else:
            elements = 0
            for subelement in range(int(vsew // eew)):
                data = get_nbit_value_random_type(eew) & element_mask
                elements = (elements << eew) | data
            elements = [elements]

        return elements

    def generate_fp_elements(self, vsew, eew, element_mask, fp_type=""):
        elements = []

        class dummy_fp(FpLoadUtil):
            _aux_offsets = [0]

            def __init__(self, resource_db):
                super().__init__(resource_db)

        fp_util = dummy_fp(self.resource_db)
        dummy = lambda: None
        dummy.name = "bypass_dummy" if fp_type == "" else fp_type  # Force bypass of FPgen database, which for sure doesnt have anything vector related in it.

        if vsew == eew:
            elements = fp_util.generate_fp_values(instr=dummy, num_bytes=vsew // 8)

        elif vsew < eew:
            data = fp_util.generate_fp_values(instr=dummy, num_bytes=eew // 8)[0]
            if isinstance(data, str):
                data = int(data, 16)
            data = data & element_mask
            select_mask = int("0x" + "".join(["ff" for byte in range(vsew // 8)]), 16)

            for elt in range(int(eew // vsew)):
                elements.append(data & select_mask)
                select_mask = select_mask << vsew
            elements = reversed(elements)

        else:
            elements = 0
            for subelement in range(int(vsew // eew)):
                data = fp_util.generate_fp_values(instr=dummy, num_bytes=eew // 8)[0] & element_mask
                elements = (elements << eew) | data
            elements = [elements]

        return elements

    def load_one_vreg_group_from_vreg_file(
        self,
        instr,
        vreg_name,
        payload_xreg_name=None,
        work_vreg_name=None,
        element_mask=None,
        eew=None,
        vl=None,
        new=False,
        label_addendum="",
        fp=False,
        ignore_alignment=False,
        max_elval=None,
        override_request=False,
        fp_type="",
        require_one_element=False,
        widen_request=False,
    ):
        # assert lmul_setting in "m1m2m4m8"
        vreg_index = int(vreg_name[1:])
        assert ignore_alignment or (self.vlmul < 1 or (vreg_index % int(self.vlmul)) == 0)
        work_vreg = work_vreg_name

        if eew is None:
            eew = self.vsew

        if element_mask is None:
            element_mask = int("0x" + "".join(["ff" for byte in range(eew // 8)]), 16)

        if vl is None:
            vl = self.vl

        max_index = 0
        if vl == "vlmax" or vl == "zero":
            if require_one_element:
                max_index = int((self.vlen * max(1, self.vlmul)) // self.vsew)
            else:
                max_index = int((self.vlen * self.vlmul) // self.vsew)
        else:
            if instr.config.vset_instr == "vsetivli":
                max_index = min(vl, 31)
            else:
                max_index = vl

        # Reset vl to None
        vl = None

        self.vreg_elvals[vreg_name] = []
        element_index = 0
        while element_index < max_index:
            data = self.generate_elements(self.vsew, eew, element_mask) if not fp else self.generate_fp_elements(self.vsew, eew, element_mask, fp_type)

            for elt in data:
                if max_elval is not None and isinstance(max_elval, int):
                    elt = min(elt, max_elval)
                    if elt == max_elval:
                        elt = self._rng.randint(0, max_elval)
                data_string = str(hex(elt)) if not fp else elt
                self.vreg_elvals[vreg_name].append(data_string)
                element_index += 1

        if not override_request:
            if not widen_request:
                self.request_to_initialize_vreg_with_values(instr, vreg_name, self.vsew, self.vlmul, self.vreg_elvals[vreg_name], max_index, new, label_addendum)
            else:
                self.request_to_initialize_vreg_with_values(instr, vreg_name, eew, self.vlmul, self.vreg_elvals[vreg_name], max_index, new, label_addendum)

        return work_vreg

    """
    #  Spec 4.5 Mask Register Layout says: "A vector mask occupies only one vector register regardless of SEW and LMUL."
    """

    def load_v0_mask(self, instr, payload_xreg_name, work_vreg_name=None):
        self.old_config = self.give_config()
        temp_config = copy.deepcopy(self.old_config)
        temp_config["vlmul"] = 1
        temp_config["vsew"] = 64

        self.replace_config(instr, temp_config, False, payload_xreg_name)
        self.load_one_vreg_group_from_vreg_file(instr, "v0", payload_xreg_name, work_vreg_name, element_mask=None, eew=None, vl=None, new=True, label_addendum="_mask")
        self.finalize_vreg_initializations(instr)
        self.restore_config(instr, False, payload_xreg_name)

    def check_v0_mask_against_max_elements(self, vlmul, vlen, vsew, vl, label, v0_elsize):
        if "_mask_" in label:  # FIXME Disabling checking any masked instructions for now.
            return

        """Determine if the mask in the lower bits of v0 masks out the max number of active elements we can expect"""
        max_active_elements = min(int(vlmul * vlen // vsew), vl)
        if max_active_elements >= v0_elsize:
            for elt in range(0, max_active_elements, v0_elsize):
                index = elt // v0_elsize
                assert self.vreg_elvals["v0"][index] == "0x0", f"At index: {index} mask value: {self.vreg_elvals['v0'][index]} nonzero for no-update instr {label}"
        else:
            mask = int("0b" + "".join(["0" for _ in range(v0_elsize - max_active_elements)]) + "".join(["1" for _ in range(max_active_elements)]), 2)
            elval = int(self.vreg_elvals["v0"][0], 16)
            assert (elval & mask) == 0, f"At index: {0} mask value: {self.vreg_elvals['v0'][0]} nonzero for no-update instr {label}"

    # FIXME needs to be made to work with wysiwyg
    def post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1, working_eew=-1, working_emul=-1, vtype_needs_changing=False):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        old_vlmul = self.vlmul
        old_vsew = self.vsew
        if working_eew != -1:
            self.vsew = working_eew
            vtype_needs_changing = True
        if working_emul != -1:
            self.vlmul = working_emul
            vtype_needs_changing = True

        if vtype_needs_changing:
            vtype_reg = self.get_random_reg(instr.reg_manager, "Int")
            vl_reg = self.get_random_reg(instr.reg_manager, "Int")
            tail_setting = instr.config.vta
            mask_setting = instr.config.vma
            vtype_code = self.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=self.vlmul, vsew=self.vsew)
            self.write_pre(f"\tli {vtype_reg}, {hex(vtype_code)}")
            self.write_pre(f"\tli {vl_reg}, {self.numerical_vl}")
            self.write_pre(f"# Checking vtype: {vtype_code}, vl: {self.numerical_vl}, vlmul: {self.vlmul}, vsew: {self.vsew}")
            self.write_pre(f"\tvsetvl x5, {vl_reg}, {vtype_reg}")

        # if hasattr(self, "first_vsew"):
        #     assert self.vsew == self.first_vsew, f"VSEW changed from {self.first_vsew} to {self.vsew} during instruction setup"
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
            nf = 1
            if hasattr(self, "nf_number"):
                nf = self.nf_number
            if not instr.config.vset_instr == "vsetivli":
                regs_we_should_have = [reg for reg in range(dest_reg, dest_reg + int(max(1, self.vlmul) * nf), int(max(1, self.vlmul)))]
            else:
                regs_we_should_have = []
                for reg in range(dest_reg, dest_reg + int(max(1, self.vlmul) * nf), int(max(1, self.vlmul))):
                    if (1 + reg - dest_reg) * self.vlen < (30 * self.vsew):
                        regs_we_should_have.append(reg)

            element_offsets_we_should_have = dict()
            element_offsets_we_do_have = dict()

            lmul_to_use_eowsh = self.vlmul
            vsew_to_use_eowsh = self.vsew
            if (
                "vrev8" not in instr.name and "vlm" in instr.name or ("8" in instr.name and (self.vset_instruction == "vsetivli" or (isinstance(self.vl, int) and (8 * self.vl) < self.vlen)))
            ):  # FIXME this was an old hack and doesn't account for the idea of self.numerical_vl
                lmul_to_use_eowsh = 1
                vsew_to_use_eowsh = 8
                vl = None
                vl_setting_reg_name = None
                vtype_reg_name = self.get_random_reg(instr.reg_manager, "Int")
                if self.vl == "vlmax" or self.vl == "zero":
                    vl_setting_reg_name = "x0"
                    vl = -1
                else:  # FIXME this branch is unlikely ever exercised
                    while vl_setting_reg_name == "x0":
                        vl_setting_reg_name = self.get_random_reg(instr.reg_manager, "Int")
                    vl = self.vl
                self.write_pre(f"\tli {vl_setting_reg_name},{hex(vl)}")
                vtype_code = self.make_vtype_code(vma=instr.config.vma, vta=instr.config.vta, vlmul=lmul_to_use_eowsh, vsew=vsew_to_use_eowsh)
                self.write_pre(f"\tli {vtype_reg_name}, {hex(vtype_code)}")
                self.write_pre(f"\tvsetvl x5, {vl_setting_reg_name}, {vtype_reg_name}")

            if isinstance(self.vl, int):
                effective_vl = self.vl
            elif self.vl == "vlmax" or self.vl == "zero":
                effective_vl = self.numerical_vl
            else:
                effective_vl = 99999

            if "vslide" in instr.name:
                slide_amount = None
                if "vi" in instr.name:
                    slide_amount = self.get_operand(instr, "simm5").value
                elif "vx" in instr.name:
                    slide_amount = self.get_operand(instr, "rs1").value
                effective_vl = effective_vl - slide_amount

            """
            #   Regs we should have includes those first regs of register groups implied by the combination of Vd, effective lmul and NF value.
            """
            for reg in regs_we_should_have:
                element_offsets_we_should_have[reg] = list()
                first_offset = 0
                last_offset = None
                if instr.config.avl == "vlmax":
                    if self.vset_instruction == "vsetivli":
                        els_per_reg = None
                        reg_group_size = lmul_to_use_eowsh * self.vlen
                        vlmul_for_last_offset = self.vlmul
                        if hasattr(self, "emul") and self.emul > 0:
                            reg_group_size = self.emul * self.vlen
                            vlmul_for_last_offset = self.emul
                        if reg_group_size > self.vlen:
                            els_per_reg = int(self.vlen // vsew_to_use_eowsh)
                        else:
                            els_per_reg = int(reg_group_size // vsew_to_use_eowsh)

                        last_offset = min(min(vsew_to_use_eowsh * 30, int(effective_vl * vsew_to_use_eowsh)), int(vsew_to_use_eowsh * els_per_reg * vlmul_for_last_offset))
                    else:
                        last_offset = int(lmul_to_use_eowsh * self.vlen)
                else:
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

        self.vlmul = old_vlmul
        self.vsew = old_vsew

    def turbo_post_setup_all_regs_updates(self, modified_arch_state, instr, work_vreg_name, active_elts=-1, working_eew=-1, working_emul=-1):
        assert work_vreg_name != "" and work_vreg_name is not None, "Work vreg name cannot be empty or None"
        self._post_setup_instrs = []

        old_vlmul = self.vlmul
        old_vsew = self.vsew
        vtype_needs_changing = False
        if working_eew != -1:
            self.vsew = working_eew
            vtype_needs_changing = True
        if working_emul != -1:
            self.vlmul = working_emul
            vtype_needs_changing = True

        if vtype_needs_changing:
            vtype_reg = self.get_random_reg(instr.reg_manager, "Int")
            vl_reg = self.get_random_reg(instr.reg_manager, "Int")
            tail_setting = instr.config.vta
            mask_setting = instr.config.vma
            vtype_code = self.make_vtype_code(vma=mask_setting, vta=tail_setting, vlmul=self.vlmul, vsew=self.vsew)
            self.write_pre(f"\tli {vtype_reg}, {hex(vtype_code)}")
            self.write_pre(f"\tli {vl_reg}, {self.numerical_vl}")
            self.write_pre(f"# Checking vtype: {vtype_code}, vl: {self.numerical_vl}, vlmul: {self.vlmul}, vsew: {self.vsew}")
            self.write_pre(f"\tvsetvl x5, {vl_reg}, {vtype_reg}")

        if modified_arch_state is None or "_mask_" in instr.label:
            # If this his instruction wasn't masked it was supposed to definitely do something.
            excludes = ["vmv", "vcompress.vm"]
            assert ("_mask_" in instr.label or "_zero_" in instr.label) or any(exclude in instr.label for exclude in excludes), f"No updates. ERROR: {instr.label} CONFIG: {instr.config}"

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
        if len(vrs) > 0:
            dest_reg = int(self.get_operand(instr, "vd").name[1:])
            nf = 1
            if hasattr(self, "nf_number"):
                nf = self.nf_number

            regs_we_should_have = []
            for reg in range(dest_reg, dest_reg + int(max(1, self.vlmul) * nf), 1):
                if (reg - dest_reg) * self.vlen < (self.numerical_vl * self.vsew):
                    regs_we_should_have.append(reg)

            vals_for_regs_we_should_have = dict()
            for reg_index in regs_we_should_have:
                vals_for_regs_we_should_have[reg_index] = vrs.get(reg_index, "00".join("" for byte in range(self.vlen // 8)))

            # break vals into element size chunks
            for key, value in vals_for_regs_we_should_have.items():
                vals = ["0x" + value[i : i + self.vsew // 4] for i in range(0, len(value), self.vsew // 4)]
                vals.reverse()
                vals_for_regs_we_should_have[key] = vals
                assert (len(value) * 4) <= self.vlen, "value is length: " + str(len(value)) + " and vlen is: " + str(self.vlen) + "value is " + value

            joined_values = []
            for reg in regs_we_should_have[::-1]:
                joined_values = vals_for_regs_we_should_have[reg] + joined_values

            working_vec_reg = work_vreg_name
            temp_regs = [self.get_random_reg(instr.reg_manager, "Int") for reg in range(3)]

            vreg_initializer = VecInstrVregInitializer(instr.label + "_post", temp_regs, self.vl)
            vreg_initializer.add_vreg(work_vreg_name, joined_values, len(joined_values), self.vsew, self.vlmul)

            compare_dest = "v0" if (dest_reg != 0 and working_vec_reg != "v0") else None
            if compare_dest is None:
                src_reg_1 = self.get_operand(instr, "vs1")
                if src_reg_1 is not None:
                    src_reg_1 = src_reg_1.name
                src_reg_2 = self.get_operand(instr, "vs2")
                if src_reg_2 is not None:
                    src_reg_2 = src_reg_2.name
                if src_reg_1 is not None and src_reg_1 != working_vec_reg:
                    sri1 = int(src_reg_1[1:])
                    if sri1 != 0 and sri1 != dest_reg:
                        compare_dest = src_reg_1
                elif src_reg_2 is not None and src_reg_2 != working_vec_reg:
                    sri2 = int(src_reg_2[1:])
                    if sri2 != 0 and sri2 != dest_reg:
                        compare_dest = src_reg_2

            if compare_dest is None:
                prohibited_regs = []
                for reg in range(0, int(min(1, self.vlmul))):
                    prohibited_regs.append(reg)
                for reg in range(dest_reg, dest_reg + max(1, self.vlmul)):
                    prohibited_regs.append(reg)
                for reg in range(int(working_vec_reg[1:]), int(working_vec_reg[1:]) + max(1, self.vlmul)):
                    prohibited_regs.append(reg)
                okay_regs = [reg for reg in range(31) if (reg not in prohibited_regs and reg % min(1, self.vlmul) == 0)]
                compare_dest = "v" + str(okay_regs[0])

            vreg_initializer.add_vreg(compare_dest, ["0x0" for blah in range(32)], 32, 8, 1)

            self.write_pre(vreg_initializer.get_mem_setups())
            self.write_pre(vreg_initializer.get_vloads(active_config=True, instr=instr, setup=self))
            self.write_data(instr, vreg_initializer.get_mem_inits())
            self.write_pre(f"\tvmsne.vv {compare_dest}, v{dest_reg}, {working_vec_reg}")
            self.write_pre(f"\tvfirst.m {temp_regs[0]}, {compare_dest}")
            self.write_pre(f"\tli {temp_regs[1]}, -1")  # -1 means no differences seen.
            self.write_pre(f"\tbeq {temp_regs[0]}, {temp_regs[1]}, 3f")
            top_el_num = min(self.numerical_vl, len(joined_values) - 1, int(self.vlmul * self.vlen // self.vsew) - 1)
            self.write_pre(f"\tli {temp_regs[1]}, {top_el_num}")
            self.write_pre(f"\tblt {temp_regs[0]}, {temp_regs[1]}, 1f")  # If the difference is outside the elements we care about, disregard
            self.write_pre("\t3:")

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

        self.vlmul = old_vlmul
        self.vsew = old_vsew


class VecLoadStoreBase(VectorInstrSetup):
    def __init__(self, resource_db):
        super().__init__(resource_db)
        self.emul = -1
        self.eew = -1

    """
    #   The expression used to calculate emul is from the riscv-v-spec-1.0 document section "7.3 Vector Load/Store Width Encoding", first paragraph.
    """

    def compute_emul(self, eew, sew, lmul):
        emul = (eew / sew) * lmul
        assert emul <= 8 and emul >= 0.125, "It is an architectural limitation that emul must fall within a certain range."
        self.emul = emul
        return emul

    """
    #   Pop the original vector config and replace the active vector config with one that corresponds to the current vector load store instruction.
    """

    def setup_ls_initialization_config(self, instruction, dont_generate, payload_xreg_name=None, emul_override=None, eew_override=None, was_vset_called=False):
        emul = emul_override
        eew = eew_override
        previous_vsew = self.vsew
        if eew is None or emul is None:
            eew = self.extract_eew(instruction.name)
            emul = self.compute_emul(eew, self.vsew, self.vlmul)
        else:
            self.eew = eew
            self.emul = emul

        self.old_config = self.give_config()
        temp_config = {"vsew": eew, "vlmul": emul, "num_vregs": self.num_vregs, "vlen": self.vlen, "vl": self.vl}
        self.replace_config(instruction, temp_config, dont_generate, payload_xreg_name, was_vset_called)

        assert self.old_config["vsew"] == previous_vsew, f"VSEW changed from {previous_vsew} to {self.old_config['vsew']} during instruction setup"

    """
    #   The emul is often different from the vlmul and so the vector registers that interact with a vector instruction must be initialized taking
    #   emul into consideration.
    """

    def setup_ls_vec_operand(
        self,
        instruction,
        operand_name,
        payload_xreg_name=None,
        emul_override=None,
        eew_override=None,
        was_vset_called=False,
        prohibited_reuse=[],
        return_operand=False,
        maintain_reserved=True,
        also_abide_emul=False,
    ):
        if payload_xreg_name is None:
            payload_xreg_name = self.get_random_reg(instruction.reg_manager, "Int")

        # Set the vector config accounting for the load store instruction name and remember the old vlmul
        _old_vlmul = self.vlmul
        self.setup_ls_initialization_config(instruction, False, payload_xreg_name, emul_override, eew_override, was_vset_called)
        _new_vlmul = self.vlmul

        # We need to choose a working vreg according to which is greater, emul or vlmul
        _max_vlmul = max(_old_vlmul, _new_vlmul)
        narrow_from_old_choices = False
        maintain_reserved_registers = maintain_reserved
        self.set_vreg_choices_with_emul(instruction, lmul_map[_max_vlmul], narrow_from_old_choices, maintain_reserved_registers)
        work_vreg = None if len(prohibited_reuse) == 0 else prohibited_reuse[0]
        if not return_operand:
            count = 0
            while work_vreg in prohibited_reuse:
                work_vreg = self.get_random_reg(reg_manager=instruction._reg_manager, reg_type="vec")
                if work_vreg not in prohibited_reuse:
                    break
                count += 1
                assert count < 20, f"avail {instruction._reg_manager._avail_vec_regs}.\n prohibit {prohibited_reuse}.\n work_vreg {work_vreg}.\n"

        # replace wvr in prohibited_reuse with the actual register name
        # this is used in the case we want to select vd but dont want it to collide with work vreg
        if "wvr" in prohibited_reuse:
            prohibited_reuse.remove("wvr")
            prohibited_reuse.append(work_vreg)

        # Now set the vreg choices to reflect the emul and the previously selected working vreg
        narrow_from_old_choices = False
        maintain_reserved_registers = maintain_reserved
        if also_abide_emul:
            self.set_vreg_choices_with_emul(instruction, lmul_map[_max_vlmul], narrow_from_old_choices, maintain_reserved_registers)
        else:
            self.set_vreg_choices_with_emul(instruction, lmul_map[_new_vlmul], narrow_from_old_choices, maintain_reserved_registers)
        # Select the emul conforming register
        instruction._fields[operand_name].randomize(prohibit_reuse=prohibited_reuse)

        if return_operand:
            work_vreg = instruction._fields[operand_name].name

        # Load the special emul configured vreg using the work vreg we selected earlier
        self.load_one_vreg_group_from_vreg_file(instruction, instruction._fields[operand_name].name, payload_xreg_name, new=True, label_addendum="_ls")
        self.finalize_vreg_initializations(instruction)

        # Reset the configuration to the basic one for this instruction instance
        # dont_restore = (_old_vlmul == _new_vlmul)
        dont_restore = False
        self.restore_config(instruction, dont_restore, payload_xreg_name)

        # Reset the vector register choices to reflect the basic configration as well as the registers we have just selected.
        narrow_from_old_choices = False
        maintain_reserved_registers = maintain_reserved
        self.set_vreg_choices_with_emul(instruction, lmul_map[self.vlmul], narrow_from_old_choices, maintain_reserved_registers)

        return work_vreg

    def get_compatible_ls_vregs(self, instruction, num_vregs, payload_xreg_name=None, emul_override=None, eew_override=None):
        if payload_xreg_name is None:
            payload_xreg_name = self.get_random_reg(instruction.reg_manager, "Int")

        # Set the vector config accounting for the load store instruction name and remember the old vlmul
        _old_vlmul = self.vlmul
        self.setup_ls_initialization_config(instruction, True, payload_xreg_name, emul_override, eew_override)
        _new_vlmul = self.vlmul

        # We need to choose a working vreg according to which is greater, emul or vlmul
        _max_vlmul = max(_old_vlmul, _new_vlmul)
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        self.set_vreg_choices_with_emul(instruction, lmul_map[_max_vlmul], narrow_from_old_choices, maintain_reserved_registers)

        regs = []
        for i in range(num_vregs):
            regs.append(self.get_random_reg(instruction.reg_manager, "Vector"))
        # Reset the configuration to the basic one for this instruction instance
        self.restore_config(instruction, False, payload_xreg_name)

        # Reset the vector register choices to reflect the basic configration as well as the registers we have just selected.
        narrow_from_old_choices = False
        maintain_reserved_registers = True
        self.set_vreg_choices_with_emul(instruction, lmul_map[self.vlmul], narrow_from_old_choices, maintain_reserved_registers)

        return regs

    """
    #   Same as in the scalar version of this method, but it is duplicated here to avoid inheriting the parent class twice.
    #   FIXME needs to handle the multiple page case possible with some of the more exotic load store instructions.
    """

    def setup_memory(self, label, size, setup_phase):
        self._lin_addr = label + "_lin"
        self._phy_addr = label + "_phy"
        if setup_phase == "pre_setup":
            self._pre_setup_instrs.append(f";#random_addr(name={self._lin_addr}, type=linear, size={size}, and_mask=0xfffffffffffff000)")
            self._pre_setup_instrs.append(f";#random_addr(name={self._phy_addr}, type=physical, size={size}, and_mask=0xfffffffffffff000)")
            self._pre_setup_instrs.append(f";#page_mapping(lin_name={self._lin_addr}, phys_name={self._phy_addr}, v=1, r=1, w=1, a=1, d=1)")
        else:
            self._post_setup_instrs.append(f";#random_addr(name={self._lin_addr}, type=linear, size={size}, and_mask=0xfffffffffffff000)")
            self._post_setup_instrs.append(f";#random_addr(name={self._phy_addr}, type=physical, size={size}, and_mask=0xfffffffffffff000)")
            self._post_setup_instrs.append(f";#page_mapping(lin_name={self._lin_addr}, phys_name={self._phy_addr}, v=1, r=1, w=1, a=1, d=1)")

    """
    #  Every double word chunk is initialized for each continuous region for an offset
    """

    def initialize_memory_disjoint_random(self, instr, offsets, sizes, offset_bias):
        self.write_data(instr, f";#init_memory @{self._lin_addr}")

        for offset, size in zip(offsets, sizes):
            num_chunks = size // 64 + (1 if size % 64 > 0 else 0)

            for chunk in range(int(num_chunks)):
                random_word = self._rng.random_nbit(64)
                self.write_data(instr, f"\t.org {str(hex(offset + 8 * chunk + offset_bias))}")
                self.write_data(instr, f"\t.dword {str(hex(random_word))}")

    """
    #   This method prevents us from attempting to initialize the same address of memory multiple times or for different sizes.
    #   Memory transactions are quantized to be divisible into double word chunks.
    """

    def consolidate_offsets(self, offsets, sizes, minimum_size=64):
        filtered_sizes = [int(size) if size > minimum_size else minimum_size for size in sizes]
        zipped_offsets_and_sizes = zip(offsets, filtered_sizes)
        sorted_zipped = sorted(zipped_offsets_and_sizes)

        current_offset = sorted_zipped[0][0]
        current_size = sorted_zipped[0][1]

        new_offsets = []
        new_sizes = []

        offset_no_overlap = lambda cur_size, cur_off, new_off: (((cur_size // 8) + cur_off) < new_off)
        bits_outside_of_overlap = lambda new_off, new_size, cur_off, cur_size: 8 * ((new_off + new_size // 8) - (current_offset + current_size // 8))

        for offset, size in sorted_zipped:
            if offset != current_offset:
                if offset_no_overlap(current_size, current_offset, offset):  # This offset is disjoint from the current offset and accumulated size
                    new_offsets.append(current_offset)
                    new_sizes.append(current_size)
                    current_offset = offset
                    current_size = size
                else:  # This offset adjoins or overlaps with the current offset and accumulated size, use the current offset and adjust the size to fully encompass next chunk
                    outside_overlap = bits_outside_of_overlap(offset, size, current_offset, current_size)
                    current_size = current_size + outside_overlap
                    remainder = current_size % 64
                    if remainder > 0:
                        current_size = current_size + 64 - remainder
                    current_offset = current_offset
            elif size > current_size:  # Same offset but a larger size, use the larger size
                current_size = size
            else:  # We have a repeated offset, size pair, or the size is less, ignore
                continue

        if len(new_offsets) == 0 or new_offsets[-1] != current_offset:
            new_offsets.append(current_offset)
            new_sizes.append(current_size)

        return new_offsets, new_sizes

    def initialize_memory(self, instr, offsets, sizes, offset_bias=0):
        new_offsets, new_sizes = self.consolidate_offsets(offsets, sizes)
        self.initialize_memory_disjoint_random(instr, new_offsets, new_sizes, offset_bias)

    def post_setup(self, modified_arch_state, instr):
        active_elts = min(min(self.numerical_vl, self.vlen * self.vlmul / self.vsew), (self.vlen * self.emul) / self.vsew)
        self.post_setup_all_regs_updates(modified_arch_state, instr, self.work_vreg, active_elts)
