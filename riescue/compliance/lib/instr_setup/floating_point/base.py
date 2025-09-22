# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.base import InstrSetup
from riescue.compliance.lib.instr_setup.utils import float_to_hex
from riescue.compliance.lib.immediate import Immediate12
from riescue.compliance.config import Resource


# FIXME: This should probably be in FpSetup method
# from fp_info.fp_info import fp_classify
def do_load_fp_regs(resource_db: Resource):
    return resource_db.load_fp_regs


class FpSetup(InstrSetup):
    frm_to_value = {"rne": 0b000, "rtz": 0b001, "rdn": 0b010, "rup": 0b011, "rmm": 0b100, "dyn": 0b111}

    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db
        self._offset = Immediate12("", resource_db=self.resource_db, value=0, field_name="imm12", field_type="immediate")
        self._offset.randomize()
        self._lin_addr = ""
        self._phy_addr = ""
        super().__init__(resource_db)
        self.setup_csrs()

    def setup_csrs(self):
        pass
        # self.write_setup(f'\tli t0, 0x8000000080006600')
        # self.write_setup(f'\tcsrs mstatus, t0')

        # Bit 4 represents the Double Precision Extension.
        # It should be turned off, as all results are extended to 64 bit.
        # self.write_setup(f'\tli t0, 0xfffffff7')
        # self.write_setup(f'\tcsrs misa, t0')

    def configure_dynamic_rm(self, instr):
        temp_reg = self.get_random_reg(instr.reg_manager)
        frm_value = self.frm_to_value[instr.config.frm.lower()]
        self.write_pre(f"\tli {temp_reg}, {frm_value}")
        self.write_pre(f"\tfsrm x0, {temp_reg}")

    def get_rounding_mode(self, instr) -> str:
        assert instr.config.dynamic_rm is not None, "Need to set dynamic_rm for any instruction with a rounding mode in order to specify static or dynamic rounding, whatever the mode"
        assert instr.config.frm is not None, "Need to set frm for any instruction with a rounding mode"
        if instr.config.dynamic_rm == "static":
            return f"{instr.config.frm.lower()}"
        elif instr.config.dynamic_rm == "dynamic":
            self.configure_dynamic_rm(instr)
            return "dyn"
        else:
            assert False, "Invalid dynamic_rm value"


# FIXME: Should this be in FpSetup?
class FloatComponent:
    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db

    def setup_csrs(self, gpr_to_use):
        if self.resource_db.wysiwyg:
            mstatus_fsvs_set = "0x2200"
            self.write_pre(f"\tli {gpr_to_use}, {mstatus_fsvs_set}")
            self.write_pre(f"\tcsrrs x0, mstatus, {gpr_to_use}")

    def float_to_hex(self, value, num_bytes: int) -> str:
        return float_to_hex(value, num_bytes, reg_size=8)
