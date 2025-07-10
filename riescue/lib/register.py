import riescue.lib.common as common
import riescue.lib.register_format as RF

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


class Register:
    def __init__(self, name, size, value=0, reg_format=None):
        self._name = name
        self.size = size
        self._value = value
        self._register_format = reg_format

    @property
    def register_format(self):
        return self._register_format

    @register_format.setter
    def register_format(self, format):
        self._register_format = format

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, _name):
        self._name = _name

    def get(self, field, lsb=None):
        """
        Get the value of a field/bit for this register
        field could be:

        - one-bit
        - field of multiple bits
        - bit_high if bit_lown exists
        """
        if lsb is None:
            if not isinstance(field, int):
                if isinstance(field, set):
                    msb = field[0]
                    lsb = field[1]

                    return self.bits(msb, lsb)

                elif isinstance(field, RF.RegisterFormat):
                    msb = (field.value)[0]
                    lsb = (field.value)[1]

                    return self.bits(msb, lsb)

                else:
                    return self.bitn(field)
            else:
                return self.bitn(field.value)
        else:
            msb = field
            if not isinstance(field, int):
                msb = field.value
            if not isinstance(lsb, int):
                lsb = lsb.value

            return self.bits(msb, lsb)

    def set(self, field, *args):
        if len(args) == 1:
            if not isinstance(field, int):
                if isinstance(field.value, set):
                    msb = (field.value)[0]
                    lsb = (field.value)[1]
                    value = args[0]

                    self.set_bits(msb, lsb, value)
                elif isinstance(field, RF.RegisterFormat):
                    msb = (field.value)[0]
                    lsb = (field.value)[1]
                    value = args[0]

                    self.set_bits(msb, lsb, value)
                else:
                    value = args[0]
                    self.set_bitn(field.value, value)
            else:
                return self.set_bitn(field, args[0])
        elif len(args) == 2:
            msb = field
            lsb = args[0]
            value = args[1]
            if not isinstance(field, int):
                msb = field.value
            if not isinstance(lsb, int):
                lsb = field.value

            return self.set_bits(msb, lsb, value)

    def bitn(self, bit):
        """
        Get one-bit value out of Register
        """
        if not isinstance(bit, int):
            bit = bit.value

        msg = f"Illegal access to bit {bit} of {self.register_format}"
        msg += f" size={self.size}"
        assert self.size > bit, msg

        return common.bitn(self._value, bit)

    def set_bitn(self, bit, value):
        if not isinstance(bit, int):
            bit = bit.value

        msg = f"Illegal access: size={self.size} <= bit={bit}"
        assert self.size > bit, msg

        self._value = common.set_bitn(self._value, bit, value)

        return self._value

    def bits(self, bit_hi, bit_lo):
        if not isinstance(bit_hi, int):
            bit_hi = bit_hi.value
        if not isinstance(bit_lo, int):
            bit_lo = bit_lo.value

        msg = f"Illegal access: size={self.size} <= bit={bit_hi}"
        assert self.size > bit_hi, msg

        msg = f"Illegal access: size={self.size} <= bit={bit_lo}"
        assert self.size > bit_lo, msg

        return common.bits(self._value, bit_hi, bit_lo)

    def set_bits(self, bit_hi, bit_lo, value):
        if not isinstance(bit_hi, int):
            bit_hi = bit_hi.value
        if not isinstance(bit_lo, int):
            bit_lo = bit_lo.value

        msg = f"Illegal access: size={self.size} <= bit={bit_hi}"
        assert self.size > bit_hi, msg

        msg = f"Illegal access: size={self.size} <= bit={bit_lo}"
        assert self.size > bit_lo, msg

        self._value = common.set_bits(self._value, bit_hi, bit_lo, value)

        return self._value
