# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import numpy as np

from enum import Enum
from riescue.lib.register import Register
from riescue.compliance.config import Resource


class IntConstraintType(Enum):
    Zero = 0
    Positive = 1
    Negative = 2
    OnlyMSB = 3
    AllOnes = 4
    AllButMSB = 5


int_type_weights = {IntConstraintType.Zero: 1, IntConstraintType.Positive: 2, IntConstraintType.Negative: 2, IntConstraintType.OnlyMSB: 1, IntConstraintType.AllOnes: 1, IntConstraintType.AllButMSB: 1}


def twos_compliment(val, bits=64):
    bitmask = (1 << bits) - 1
    ones_compliment = ~val
    return (ones_compliment + 1) & bitmask


int_type_generators = {
    IntConstraintType.Zero: lambda x, resource_db: 0,
    IntConstraintType.Positive: lambda x, resource_db: x.rng.random_in_bitrange(1, x.resource_db.xlen - 1),
    IntConstraintType.Negative: lambda x, resource_db: twos_compliment(x.rng.randint(1, 1 << (x.resource_db.xlen - 1))),
    IntConstraintType.OnlyMSB: lambda x, resource_db: 1 << (x.resource_db.xlen - 1),
    IntConstraintType.AllOnes: lambda x, resource_db: (1 << x.resource_db.xlen) - 1,
    IntConstraintType.AllButMSB: lambda x, resource_db: ((1 << x.resource_db.xlen) - 1) >> 1,
}


custom_width_int_type_generators = {
    IntConstraintType.Zero: lambda x, resource_db: 0,
    IntConstraintType.Positive: lambda x, resource_db: resource_db.rng.random_in_bitrange(1, x - 1),
    IntConstraintType.Negative: lambda x, resource_db: twos_compliment(resource_db.rng.randint(1, 1 << (x - 1))),
    IntConstraintType.OnlyMSB: lambda x, resource_db: 1 << (x - 1),
    IntConstraintType.AllOnes: lambda x, resource_db: (1 << x) - 1,
    IntConstraintType.AllButMSB: lambda x, resource_db: ((1 << x) - 1) >> 1,
}


def get_nbit_value_random_type(resource_db, bits: int):
    int_type = resource_db.rng.random_choice_weighted(int_type_weights)
    int_type_generator = custom_width_int_type_generators[int_type]
    return int_type_generator(bits, resource_db)


# implements a < b
def sign_aware_comparison_op(a, b, unsigned=False, bit_length=64):
    if unsigned:
        return a < b

    # is msb of a set?
    a_msb = a >> (bit_length - 1)
    # is msb of b set?
    b_msb = b >> (bit_length - 1)

    if a_msb == 1 and b_msb == 0:  # a is negative, b is positive
        return True
    elif a_msb == 0 and b_msb == 1:  # a is positive, b is negative
        return False
    elif a_msb == 0 and b_msb == 0:  # a is positive, b is positive
        return a < b
    else:  # a is negative, b is negative
        return twos_compliment(a) > twos_compliment(b)


class RiscvRegister(Register):

    def __init__(self, name, size, resource_db: Resource, value=0, reg_format=None, reg_manager=None, field_name="", field_type="", exclude_regs: list = None):
        super().__init__(name, size, value, reg_format)
        self.is_initialized = False
        self.resource_db = resource_db
        self.rng = self.resource_db.rng
        self.reg_manager = reg_manager
        self._field_name = field_name
        self._field_type = field_type
        self._exclude_regs = exclude_regs if exclude_regs else list()

    def randomize(self):
        avail_regs = (reg for reg in self.reg_manager.get_avail_regs("Int") if reg not in self._exclude_regs)
        self._name = next(avail_regs)

        # based on their weights, decide which type of value to generate
        int_type = self.rng.random_choice_weighted(int_type_weights)
        # based on the int type get the generator function
        int_type_generator = int_type_generators[int_type]
        self._value = int_type_generator(self, self.resource_db)

        self.reg_manager.update_avail_regs("Int", self._name)
        self.is_initialized = True

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value

    @property
    def field_type(self):
        return self._field_type

    @property
    def field_name(self):
        return self._field_name

    def __str__(self):
        return self._name + " : " + str(self._value)


class RiscvXregister(RiscvRegister):

    def __init__(self, name, size, resource_db: Resource, value=0, reg_format=None, reg_manager=None, field_name="", field_type="", exclude_regs: list = None):
        super().__init__(name, size, resource_db, value, reg_format, reg_manager, field_name, field_type, exclude_regs)


class RiscvFregister(RiscvRegister):

    def __init__(self, name, size, resource_db: Resource, value=0, reg_format=None, reg_manager=None, field_name="", field_type="", exclude_regs: list = None, fp_bitness=16):
        super().__init__(name, size, resource_db, value, reg_format, reg_manager, field_name, field_type, exclude_regs)
        self.fp_bitness = fp_bitness

    def randomize(self):
        avail_regs = (reg for reg in self.reg_manager.get_avail_regs("Float") if reg not in self._exclude_regs)
        self._name = next(avail_regs)

        self._value = None  # This needs to be initialized with a different mechanism.

        self.reg_manager.update_avail_regs("Float", self._name)
        self.is_initialized = True


class RiscvVregister(RiscvRegister):

    def __init__(self, name, size, resource_db: Resource, value=0, reg_format=None, reg_manager=None, field_name="", field_type=""):
        super().__init__(name, size, resource_db, value, reg_format, reg_manager, field_name, field_type)

    def randomize(self, prohibit_reuse=None):
        avail_regs = self.reg_manager.get_avail_regs("Vector")
        reg_reserved = False
        if prohibit_reuse is None:
            prohibit_reuse = list()

        if len(avail_regs) == 0:
            avail_regs = [reg for reg in self.reg_manager.get_used_vregs() if reg not in prohibit_reuse]
            reg_reserved = True
            # print("Exhausted available vector registers, making a random selection from those vregs already used")

        self._name = self.rng.random_entry_in(avail_regs)

        count = 0
        while self._name in prohibit_reuse:
            self._name = self.rng.random_entry_in(avail_regs)
            count += 1
            assert count < 20, f"Unable to find a vector register. prohibit_reuse: {prohibit_reuse}, avail_regs: {avail_regs}"

        if not reg_reserved:
            self.reg_manager.update_avail_regs("Vector", self._name)
        self.is_initialized = True
