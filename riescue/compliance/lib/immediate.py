# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional

from riescue.compliance.config import Resource


class Immediate:
    """
    Data type class to store and randomize immediate values for
    integer instructions.
        Attributes :
            name : Identifier for the immediate value
            size : Bit width of the immediate.
            value : Numerical value stored.
            resource_db : Handle to Resource for extracting rng.
            rng     : Handle to random number generator.
            mask        : Bit mask depending on size of immediate.
            field_name  : Spec defined identifier such as imm12 (required for FE JSON generation)
            field_type  : Used by FE JSON generation.
            aligned     : Determines if immediate value should be aligned to int(aligned); None skips
            TO BE REMOVED :
            is_initialized : Boolean to check if the class if already randomized
    """

    def __init__(self, name, size, resource_db: Resource, value="", field_name="", field_type="", aligned=None):
        self._name = name
        self._size = size
        self._value = value
        self.resource_db = resource_db
        self._rng = self.resource_db.rng
        self._is_initialized = False
        self._mask = (1 << size) - 1
        self._field_name = field_name
        self._field_type = field_type
        self._aligned = aligned

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._name = name

    @property
    def size(self):
        return self._size

    @size.setter
    def size(self, size):
        self._size = size

    # FIXME: property should name a type returned and make use of self._is_initialized or raise an error here if there's no value set.
    # FIXME; Setter should set self._is_initialized to True.
    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value

    @property
    def field_name(self):
        return self._field_name

    @property
    def field_type(self):
        return self._field_type

    def random_bits(self, a, b) -> int:
        "Random bits between range a,b. If Immediate aligned, aligns value"
        bits = self._rng.random_in_bitrange(a, b)
        if self._aligned is not None:
            bits = (bits // self._aligned) * self._aligned
        return bits

    def randomize(self) -> None:
        """
        Assigns a random value defined by  parameter
        """
        self._value = self._rng.random_in_range(10, 20)
        self._is_initialized = True


class Immediate12(Immediate):

    def __init__(self, name, resource_db: Resource, size=12, **kwargs):
        super().__init__(name=name, size=size, resource_db=resource_db, **kwargs)

    def randomize(self):
        """
        Assigns a random value with a value between 0 - (2**12)-1
        """
        self._value = self.random_bits(1, 12)
        self._is_initialized = True


class Immediate20(Immediate):

    def __init__(self, name, resource_db: Resource, size=20, **kwargs):
        super().__init__(name=name, size=size, resource_db=resource_db, **kwargs)

    def randomize(self):
        """
        Assigns a random value with a value between 0 - (2**20)-1
        """
        self._value = self._rng.random_nbit(20)
        self._is_initialized = True


class Immediate10(Immediate):

    def __init__(self, name, resource_db: Resource, size=10, **kwargs):
        super().__init__(name=name, size=size, resource_db=resource_db, **kwargs)

    def randomize(self):
        self._value = self.random_bits(1, 10)
        self._is_initialized = True


class Immediate11(Immediate):

    def __init__(self, name, resource_db: Resource, size=11, **kwargs):
        super().__init__(name=name, size=size, resource_db=resource_db, **kwargs)

    def randomize(self):
        self._value = self.random_bits(1, 11)
        self._is_initialized = True


class Immediate6(Immediate):

    def __init__(self, name, resource_db: Resource, size=6, **kwargs):
        super().__init__(name=name, size=size, resource_db=resource_db, **kwargs)

    def randomize(self):
        self._value = self.random_bits(1, 6)
        self._is_initialized = True


class Immediate5(Immediate):

    def __init__(self, name, resource_db: Resource, size=5, **kwargs):
        super().__init__(name=name, size=size, resource_db=resource_db, **kwargs)

    def randomize(self):
        """
        Assigns a random value with a value between 0 - (2**5)-1
        """
        self._value = self.random_bits(1, 5)
        self._is_initialized = True


class ImmediateGeneric(Immediate):

    def __init__(self, name, resource_db: Resource, size=5, value_avoid_ranges=[], divisibility_reqs=[], **kwargs):
        self._value_avoid_ranges = value_avoid_ranges
        self._divisibility_reqs = divisibility_reqs
        super().__init__(name=name, size=size, resource_db=resource_db, **kwargs)

    def is_value_in_a_range(self, value: int, a_range: tuple) -> bool:
        return (value >= a_range[0]) and (value <= a_range[1])

    def is_value_divisible(self, value: int, a_divisor: int) -> bool:
        return value % a_divisor == 0

    def randomize(self):
        # FIXME look at all the other Immediate classes, they should be using random_nbit too, otherwise the top bit can never be set in the _value.
        self._is_initialized = False

        while not self._is_initialized:
            self._value = self._rng.random_nbit(self.size)

            if any((self.is_value_in_a_range(self._value, avoid_range) for avoid_range in self._value_avoid_ranges)):
                continue

            if any((not self.is_value_divisible(self._value, divisor) for divisor in self._divisibility_reqs)):
                continue

            self._is_initialized = True
