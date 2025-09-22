# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import sys
from riescue.compliance.config import Resource
from riescue.compliance.lib.common import lmul_map


class RegisterManager:
    """
    Named-singleton class for managing registers defined for an
    instruction.
        Attributes :
            _avail_int_regs : list of available integer registers.
            _avail_fp_regs  : list of available FP registers.
    """

    __instance = dict()

    @staticmethod
    def get_instance(name=""):
        """
        Check if instance of a certain instruction, idetified by
        unique label, already exists. If yes, return the instance.
        If not, create one and register it with the label
        """
        if RegisterManager.__instance[name] is None:
            RegisterManager.__instance[name] = RegisterManager()
        return RegisterManager.__instance[name]

    def __init__(self, resource_db: Resource, name="", config=None):
        """
        Constructor for the singleton class.
        """
        self.resource_db = resource_db
        if self.resource_db.wysiwyg:
            self._avail_int_regs = ["x" + str(i) for i in range(1, 31)]
        elif self.resource_db.num_cpus > 1:
            self._avail_int_regs = ["x" + str(i) for i in range(1, 32) if all(i != x for x in [2, 9])]  # if i != 9] #x9 reserved for hartid,
        else:
            self._avail_int_regs = ["x" + str(i) for i in range(1, 32) if i != 2]  # x2 reserved for stack pointer
        self._avail_fp_regs = ["f" + str(i) for i in range(0, 32)]
        self._avail_vec_regs = ["v" + str(i) for i in range(0, 32)]

        self.resource_db.rng.shuffle(self._avail_int_regs)
        self.resource_db.rng.shuffle(self._avail_fp_regs)
        self.resource_db.rng.shuffle(self._avail_vec_regs)

        self._reserve_regs = []
        self._latest_lmul_val = 1

        try:
            if RegisterManager.__instance[name] is None:
                RegisterManager.__instance[name] = self
            else:
                RegisterManager.__instance[name] = self
        except KeyError:
            RegisterManager.__instance[name] = self

    def shuffle_iregs(self):
        self.resource_db.rng.shuffle(self._avail_int_regs)

    def shuffle_scalar_regs(self):
        self.resource_db.rng.shuffle(self._avail_int_regs)
        self.resource_db.rng.shuffle(self._avail_fp_regs)

    def reinit_vregs(self):
        self._avail_vec_regs = ["v" + str(i) for i in range(0, 32)]
        self._latest_lmul_val = 1
        self._reserve_regs = []  # .append(reg_name)

    def check_for_overlap_with_reserved(self, lmul, initial_list, reserved_list):
        temp_avail_regs = []

        for reg in initial_list:
            reg_ok = True
            first_reg_index = int(reg[1:])
            last_reg_index = first_reg_index + lmul
            for reg_index in range(first_reg_index, last_reg_index):
                if ("v" + str(reg_index)) in reserved_list:
                    reg_ok = False
                    break
            if reg_ok:
                temp_avail_regs.append(reg)

        return temp_avail_regs

    def randomize_regs(self, config=None, preserve_old_availabilities=True, preserve_old_reservations=True):
        if config:
            lmul_num = lmul_map[config.vlmul]
            if lmul_num < 1:
                lmul_num = 1

            self._latest_lmul_val = lmul_num
            temp_avail_regs = ["v" + str(i) for i in range(0, 32, lmul_num)]

            if not preserve_old_reservations:
                for reg in self._reserve_regs:
                    self._avail_vec_regs.append(reg)
                self._reserve_regs = []

            if preserve_old_availabilities:
                self._avail_vec_regs = [reg for reg in self._avail_vec_regs if reg in temp_avail_regs if reg not in self._reserve_regs]
            else:
                self._avail_vec_regs = [reg for reg in temp_avail_regs if reg not in self._reserve_regs]

            # Prune available regs that for the current lmul_setting, while not already reserved, would overlap with reserved regs
            temp_avail_regs = self.check_for_overlap_with_reserved(lmul_num, self._avail_vec_regs, self._reserve_regs)
            self._avail_vec_regs = temp_avail_regs

            self.resource_db.rng.shuffle(self._avail_vec_regs)

    def set_avail_regs(self, reglist: list = [], regtype: str = ""):
        first_letter = ""

        # If reglist is not empty, then get the first letter of the first element and store it in first_letter in lowercase
        if reglist:
            first_letter = reglist[0][0].lower()
        elif regtype != "":
            first_letter = regtype[0].lower()
        else:
            raise ValueError("Either reglist or regtype must be provided")

        if first_letter == "i":
            self._avail_int_regs = reglist
        elif first_letter == "f":
            self._avail_fp_regs = reglist
        elif first_letter == "v":
            self._avail_vec_regs = reglist
        else:
            raise ValueError(f"Unsupported Register Type: {regtype}")

    def get_avail_regs(self, register_type: str = "Int") -> None:
        """
        Provides list of available registers defined by the
        register_type.
        Default : Integer registers : x0 - x31
                  FP      registers : f0 - f31
        """
        first_letter = register_type[0].lower()

        if first_letter == "i":
            return self._avail_int_regs
        elif first_letter == "f":
            return self._avail_fp_regs
        elif first_letter == "v":
            return self._avail_vec_regs
        else:
            raise ValueError(f"Unsupported Register Type: {register_type}")

    def reserve_overlap_regs(self, reg_name, lmul):
        first_reg_index = int(reg_name[1:])
        last_reg_index = first_reg_index + lmul
        for reg_index in range(first_reg_index, last_reg_index, 1):
            self._reserve_regs.append("v" + str(reg_index))

    def update_avail_regs(self, register_type: str = "Int", reg: str = "") -> None:
        """
        Removes the used register specified by reg from the list of
        available registers.
        """
        first_letter = reg[0].lower()

        if first_letter == "x":
            self._avail_int_regs.remove(reg)
            self._avail_int_regs.insert(len(self._avail_int_regs), reg)
        elif first_letter == "f":
            self._avail_fp_regs.remove(reg)
            self._avail_fp_regs.insert(len(self._avail_fp_regs), reg)
        elif first_letter == "v":
            self._avail_vec_regs.remove(reg)
            self.reserve_overlap_regs(reg, self._latest_lmul_val)
        else:
            raise ValueError(f"Unsupported Register Type: {register_type}")

    def reserve_reg(self, reg_name: str = "", register_type: str = ""):
        first_letter = reg_name[0].lower()

        if first_letter == "x":
            if reg_name in self._avail_int_regs and reg_name not in self._reserve_regs:
                self._avail_int_regs.remove(reg_name)
        elif first_letter == "f":
            if reg_name in self._avail_fp_regs and reg_name not in self._reserve_regs:
                self._avail_fp_regs.remove(reg_name)
        elif first_letter == "v":
            if reg_name in self._avail_vec_regs and reg_name not in self._reserve_regs:
                self._avail_vec_regs.remove(reg_name)
        else:
            raise ValueError(f"Unsupported Register Type: {register_type}")

        self._reserve_regs.append(reg_name)

    def unreserve_reg(self, reg_name: str = "", register_type: str = "Int"):
        first_letter = reg_name[0].lower()

        if first_letter == "x":
            if reg_name in self._reserve_regs and reg_name not in self._avail_int_regs:
                self._avail_int_regs.insert(len(self._avail_int_regs), reg_name)
        elif first_letter == "f":
            if reg_name in self._reserve_regs and reg_name not in self._avail_fp_regs:
                self._avail_fp_regs.insert(len(self._avail_fp_regs), reg_name)
        elif first_letter == "v":
            if reg_name in self._reserve_regs and reg_name not in self._avail_vec_regs:
                self._avail_vec_regs.insert(len(self._avail_vec_regs), reg_name)
        else:
            raise ValueError(f"Unsupported Register Type: {register_type}")

        self._reserve_regs.remove(reg_name)

    def get_used_vregs(self):
        lmul_num = self._latest_lmul_val
        use_lmul = int(lmul_num) if lmul_num >= 1 else 1
        reusable_regs = [reg for reg in self._reserve_regs if int(reg[1:]) % use_lmul == 0]
        return reusable_regs

    def get_random_reg(self, reg_type="Int"):
        result_regs = self.get_avail_regs(reg_type)
        if len(result_regs) == 0:
            return None
        result_reg = result_regs[0]
        self.update_avail_regs(reg_type, result_reg)
        return result_reg
