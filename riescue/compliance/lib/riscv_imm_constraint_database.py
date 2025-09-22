# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from collections import OrderedDict
from riescue.compliance.config import Resource


class imm_constraints:

    def choose_from_ranges(self, acceptable_ranges=[]):
        if len(acceptable_ranges) == 1:
            return self.resource_db.rng.randrange(acceptable_ranges[0][0], acceptable_ranges[0][1] + 1)

        weights = dict()

        for a_range in acceptable_ranges:
            weights[a_range] = a_range[1] - a_range[0]

        kv_list = [[key, value] for key, value in weights.items()]

        for index in range(1, len(kv_list)):
            kv_list[index][1] += kv_list[index - 1][1]

        randnum = self.resource_db.rng.randrange(0, kv_list[-1][1])

        for item in kv_list:
            if randnum < item[1]:
                return self.resource_db.rng.randrange(item[0][0], item[0][1] + 1)

    def twos_complement(self, value, num_bits):
        if value & (1 << (num_bits - 1)) > 0:
            value = value - (1 << num_bits)

        return value

    def get_constraints(self, instruction_name):
        return self.data.get(instruction_name, None)

    def get_constraint(self, instruction_name, field_name):
        instr_dict = self.data.get(instruction_name, None)
        if instr_dict:
            field_dict = instr_dict.get(field_name, None)

            if field_dict:
                return field_dict["value_generator"]

        return None

    def get_sole_value_generator(self, instruction_name):
        instr_dict = self.data.get(instruction_name, None)
        if instr_dict:
            for key, value in instr_dict.items():
                if isinstance(value, OrderedDict):
                    for key, v2 in value.items():
                        if key == "value_generator":
                            return v2

        return None

    def call_until_nonzero(self, function):
        result = function()

        while result == 0:
            result = function()

        if result == -0 or result == 0:
            assert False

        return result

    # Calls value function until constraint function is satisfied
    def call_until_satisfied(self, value_function, constraint_function):
        result = value_function()

        while constraint_function(result) == 0:
            result = value_function()

        return result

    def call_until_nonnegative(self, function, bits):
        value = function()
        result = self.twos_complement(value, bits)

        while result < 0:
            value = function()
            result = self.twos_complement(value, bits)

        if result == -0 or result == 0:
            assert False

        return value

    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db
        self.data = OrderedDict()
        self.bits = self.resource_db.rng.get_rand_bits

        """
        #
        #   Data is organized by instruction name at the top level,
        #   The next level is immediate operand name as it appears in the spec, according to its role in the instruction,
        #   rather than as it might appear in third party interpretations of the spec.
        #
        #   For example, for the instruction 'c.ldsp', rather than list a c_uimm9sp{hi, lo} and maybe the size of these fields,
        #   instead a generator for 'offset' for 'c.ldsp' is provided that produces a legal value for this operand at an assembly code
        #   level.
        #
        #   This provides an alternative to the ambiguous, ad-hoc and manual process of explicitly synthesizing constraints in the
        #   instruction setup code.
        #
        """
        self.data["c.lwsp"] = OrderedDict()
        self.data["c.lwsp"]["offset"] = OrderedDict()
        self.data["c.lwsp"]["format"] = "CI"
        self.data["c.lwsp"]["offset"]["value_generator"] = lambda: self.bits(8) & 0b11111100

        self.data["c.ldsp"] = OrderedDict()
        self.data["c.ldsp"]["offset"] = OrderedDict()
        self.data["c.ldsp"]["format"] = "CI"
        self.data["c.ldsp"]["offset"]["value_generator"] = lambda: self.bits(9) & 0b111111000

        self.data["c.fldsp"] = OrderedDict()
        self.data["c.fldsp"]["offset"] = OrderedDict()
        self.data["c.fldsp"]["format"] = "CI"
        self.data["c.fldsp"]["offset"]["value_generator"] = lambda: self.bits(9) & 0b111111000

        self.data["c.swsp"] = OrderedDict()
        self.data["c.swsp"]["offset"] = OrderedDict()
        self.data["c.swsp"]["format"] = "CSS"
        self.data["c.swsp"]["offset"]["value_generator"] = lambda: self.bits(8) & 0b11111100

        self.data["c.sdsp"] = OrderedDict()
        self.data["c.sdsp"]["offset"] = OrderedDict()
        self.data["c.sdsp"]["format"] = "CSS"
        self.data["c.sdsp"]["offset"]["value_generator"] = lambda: self.bits(9) & 0b111111000

        self.data["c.fsdsp"] = OrderedDict()
        self.data["c.fsdsp"]["offset"] = OrderedDict()
        self.data["c.fsdsp"]["format"] = "CSS"
        self.data["c.fsdsp"]["offset"]["value_generator"] = lambda: self.bits(9) & 0b111111000

        self.data["c.lw"] = OrderedDict()
        self.data["c.lw"]["offset"] = OrderedDict()
        self.data["c.lw"]["format"] = "CL"
        self.data["c.lw"]["offset"]["value_generator"] = lambda: self.bits(7) & 0b1111100

        self.data["c.ld"] = OrderedDict()
        self.data["c.ld"]["offset"] = OrderedDict()
        self.data["c.ld"]["format"] = "CL"
        self.data["c.ld"]["offset"]["value_generator"] = lambda: self.bits(8) & 0b11111000

        self.data["c.fld"] = OrderedDict()
        self.data["c.fld"]["offset"] = OrderedDict()
        self.data["c.fld"]["format"] = "CL"
        self.data["c.fld"]["offset"]["value_generator"] = lambda: self.bits(8) & 0b11111000

        self.data["c.sw"] = OrderedDict()
        self.data["c.sw"]["offset"] = OrderedDict()
        self.data["c.sw"]["format"] = "CS"
        self.data["c.sw"]["offset"]["value_generator"] = lambda: self.bits(7) & 0b1111100

        self.data["c.sd"] = OrderedDict()
        self.data["c.sd"]["offset"] = OrderedDict()
        self.data["c.sd"]["format"] = "CS"
        self.data["c.sd"]["offset"]["value_generator"] = lambda: self.bits(8) & 0b11111000

        self.data["c.fsd"] = OrderedDict()
        self.data["c.fsd"]["offset"] = OrderedDict()
        self.data["c.fsd"]["format"] = "CS"
        self.data["c.fsd"]["offset"]["value_generator"] = lambda: self.bits(8) & 0b11111000

        # FIXME Not actually used yet, needs to be re-evaluated
        self.data["c.j"] = OrderedDict()
        self.data["c.j"]["offset"] = OrderedDict()
        self.data["c.j"]["format"] = "CJ"
        self.data["c.j"]["offset"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.twos_complement(self.bits(12) & 0b111111111110, 12))

        self.data["c.beqz"] = OrderedDict()
        self.data["c.beqz"]["offset"] = OrderedDict()
        self.data["c.beqz"]["format"] = "CB"
        self.data["c.beqz"]["offset"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.twos_complement(self.bits(9) & 0b111111110, 9))

        self.data["c.bnez"] = OrderedDict()
        self.data["c.bnez"]["offset"] = OrderedDict()
        self.data["c.bnez"]["format"] = "CB"
        self.data["c.bnez"]["offset"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.twos_complement(self.bits(9) & 0b111111110, 9))

        self.data["c.li"] = OrderedDict()
        self.data["c.li"]["imm"] = OrderedDict()
        self.data["c.li"]["format"] = "CI"
        self.data["c.li"]["imm"]["value_generator"] = lambda: self.twos_complement(self.bits(6), 6)

        self.data["c.lui"] = OrderedDict()
        self.data["c.lui"]["nzimm"] = OrderedDict()
        self.data["c.lui"]["format"] = "CI"
        self.data["c.lui"]["nzimm"]["value_generator"] = lambda: self.choose_from_ranges([(0xFFFE0, 0xFFFFF), (0x1, 0x1F)])

        self.data["c.addi"] = OrderedDict()
        self.data["c.addi"]["nzimm"] = OrderedDict()
        self.data["c.addi"]["format"] = "CI"
        self.data["c.addi"]["nzimm"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.twos_complement(self.bits(6), 6))

        self.data["c.addiw"] = OrderedDict()
        self.data["c.addiw"]["imm"] = OrderedDict()
        self.data["c.addiw"]["format"] = "CI"
        self.data["c.addiw"]["imm"]["value_generator"] = lambda: self.twos_complement(self.bits(6), 6)

        self.data["c.addi16sp"] = OrderedDict()
        self.data["c.addi16sp"]["nzimm"] = OrderedDict()
        self.data["c.addi16sp"]["format"] = "CI"
        self.data["c.addi16sp"]["nzimm"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.twos_complement(self.bits(10) & 0b1111110000, 10))

        self.data["c.addi4spn"] = OrderedDict()
        self.data["c.addi4spn"]["nzuimm"] = OrderedDict()
        self.data["c.addi4spn"]["format"] = "CIW"
        self.data["c.addi4spn"]["nzuimm"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.bits(10) & 0b1111111100)

        self.data["c.slli"] = OrderedDict()
        self.data["c.slli"]["shamt"] = OrderedDict()
        self.data["c.slli"]["format"] = "CI"
        self.data["c.slli"]["shamt"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.bits(6))

        self.data["c.srli"] = OrderedDict()
        self.data["c.srli"]["shamt"] = OrderedDict()
        self.data["c.srli"]["format"] = "CB"
        self.data["c.srli"]["shamt"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.bits(6))

        self.data["c.srai"] = OrderedDict()
        self.data["c.srai"]["shamt"] = OrderedDict()
        self.data["c.srai"]["format"] = "CB"
        self.data["c.srai"]["shamt"]["value_generator"] = lambda: self.call_until_nonzero(lambda: self.bits(6))

        self.data["c.andi"] = OrderedDict()
        self.data["c.andi"]["imm"] = OrderedDict()
        self.data["c.andi"]["format"] = "CB"
        self.data["c.andi"]["imm"]["value_generator"] = lambda: self.twos_complement(self.bits(6), 6)

        self.data["fsw"] = OrderedDict()
        self.data["fsw"]["imm"] = OrderedDict()
        self.data["fsw"]["imm"]["value_generator"] = lambda: self.call_until_satisfied(lambda: self.bits(11), lambda x: x < (0x800 - 8))  # FIXME Preventing negative offsets

        self.data["flw"] = OrderedDict()
        self.data["flw"]["imm"] = OrderedDict()
        self.data["flw"]["imm"]["value_generator"] = lambda: self.call_until_satisfied(lambda: self.bits(11), lambda x: x < (0x800 - 8))  # FIXME Preventing negative offsets

        self.data["fsd"] = OrderedDict()
        self.data["fsd"]["imm"] = OrderedDict()
        self.data["fsd"]["imm"]["value_generator"] = lambda: self.call_until_satisfied(lambda: self.bits(11), lambda x: x < (0x800 - 8))  # FIXME Preventing negative offsets

        self.data["fld"] = OrderedDict()
        self.data["fld"]["imm"] = OrderedDict()
        self.data["fld"]["imm"]["value_generator"] = lambda: self.call_until_satisfied(lambda: self.bits(11), lambda x: x < (0x800 - 8))  # FIXME Preventing negative offsets

        self.data["flh"] = OrderedDict()
        self.data["flh"]["imm"] = OrderedDict()
        self.data["flh"]["imm"]["value_generator"] = lambda: self.call_until_satisfied(lambda: self.bits(11), lambda x: x < (0x800 - 8))  # FIXME Preventing negative offsets

        self.data["fsh"] = OrderedDict()
        self.data["fsh"]["imm"] = OrderedDict()
        self.data["fsh"]["imm"]["value_generator"] = lambda: self.call_until_satisfied(lambda: self.bits(11), lambda x: x < (0x800 - 8))  # FIXME Preventing negative offsets
