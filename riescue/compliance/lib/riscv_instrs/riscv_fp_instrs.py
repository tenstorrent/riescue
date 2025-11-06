# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.riscv_instrs import InstrBase
from riescue.compliance.lib.riscv_registers import RiscvXregister, RiscvFregister
from riescue.compliance.lib.instr_setup import (
    FpRegRegCompareSetup,
    FloatStoreRegBasedSetup,
    FpConvertMove,
    FpLoadSetup,
    FpSqrtSetup,
    FpClassifySetup,
    FpRegRegSetup,
    FpRegRegRegSetup,
    CompDoubleSPStoreSetup,
    CompDoubleSPLoadSetup,
    C0DoubleLoadRegBasedSetup,
    C0DoubleStoreRegBasedSetup,
)

from riescue.compliance.lib.immediate import ImmediateGeneric
from riescue.compliance.config import Resource


class RiscvFpInstr(InstrBase):

    # FIXME : Make arrangements for scalar and floating point registers.
    def __init__(self, resource_db: Resource, name, label=""):
        super().__init__(resource_db, name, label)
        self._rounding_mode = ""

        # FIXME : Put this inside a function set_attributes()
        for attr in dir(self):
            if any([self._name.startswith(stem) for stem in ["fs", "fl"]]) and any([self._name.endswith(stem) for stem in ["lw", "ld", "lh", "sw", "sd", "sh"]]) and attr == "rs1":
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._srcs.append(getattr(self, attr))
            elif self._name.startswith("fmv.") and self._name.endswith(".x") and attr == "rs1":
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._srcs.append(getattr(self, attr))
            elif attr in ["rs1", "rs2", "rs3", "c_rs2"]:
                setattr(self, attr, RiscvFregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._srcs.append(getattr(self, attr))
            elif attr in ["rs1_p"]:
                setattr(
                    self,
                    attr,
                    RiscvXregister(
                        resource_db=self.resource_db,
                        name="",
                        size=3,
                        reg_manager=self._reg_manager,
                        field_name=attr,
                        exclude_regs=["x" + str(index) for index in range(8)] + ["x" + str(index) for index in range(16, 32)],
                    ),
                )
                self._srcs.append(getattr(self, attr))
            elif attr in ["rs2_p"]:
                if self._name.startswith("c.fs"):
                    setattr(
                        self,
                        attr,
                        RiscvFregister(
                            resource_db=self.resource_db,
                            name="",
                            size=3,
                            reg_manager=self._reg_manager,
                            field_name=attr,
                            exclude_regs=["f" + str(index) for index in range(8)] + ["f" + str(index) for index in range(16, 32)],
                        ),
                    )
                    self._srcs.append(getattr(self, attr))
            elif "fmv.x" in self._name and attr == "rd":
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._dests.append(getattr(self, attr))
            elif attr in ["rd"]:
                setattr(self, attr, RiscvFregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._dests.append(getattr(self, attr))
            elif attr in ["rd_p"]:
                setattr(
                    self,
                    attr,
                    RiscvFregister(
                        resource_db=self.resource_db,
                        name="",
                        size=3,
                        reg_manager=self._reg_manager,
                        field_name=attr,
                        exclude_regs=["f" + str(index) for index in range(8)] + ["f" + str(index) for index in range(16, 32)],
                    ),
                )
                self._dests.append(getattr(self, attr))
            elif "c_" in attr and "imm" in attr:
                setattr(self, attr, ImmediateGeneric(resource_db=self.resource_db, name="", size=1, field_name=attr))
                self._imms.append(getattr(self, attr))
            elif "imm" in attr and "imms" not in attr:
                setattr(self, attr, ImmediateGeneric(resource_db=self.resource_db, name="", size=1, field_name=attr))
                self._imms.append(getattr(self, attr))
            elif attr in ["rm"]:
                setattr(self, attr, "")

        self._fields = dict()
        for operand in self._srcs + self._dests + self._imms:
            self._fields[operand.field_name] = operand

        if any([self._name.startswith(compare_stem) for compare_stem in ["fle.", "feq.", "flt."]]):
            self._setup = FpRegRegCompareSetup(resource_db=self.resource_db)
        elif self._name.startswith("c."):
            if len(self._imms) == 2 and len(self._dests) == 1 and len(self._srcs) == 1:
                self._setup = C0DoubleLoadRegBasedSetup(self.resource_db)
            elif len(self._imms) == 2 and len(self._dests) == 0 and len(self._srcs) == 2:
                self._setup = C0DoubleStoreRegBasedSetup(self.resource_db)
            elif len(self._srcs) == 1 and len(self._dests) == 0 and len(self._imms) == 1:
                self._reg_manager.reserve_reg("x2", "Int")
                self._setup = CompDoubleSPStoreSetup(self.resource_db)
            elif len(self._srcs) == 0 and len(self._dests) == 1 and len(self._imms) == 2:
                self._reg_manager.reserve_reg("x2", "Int")
                self._setup = CompDoubleSPLoadSetup(self.resource_db)
            else:
                print("No setup for instr: " + self._name)
                print("Imms: " + str(len(self._imms)) + " srcs: " + str(len(self._srcs)) + " dests: " + str(len(self._dests)))
                assert False, "No setup for compressed instruction identified."
        elif "fcvt" in self._name or "fmv" in self._name:
            self._setup = FpConvertMove(resource_db=self.resource_db)

        elif self._name.startswith("fl") and any([self._name.endswith(stem) for stem in ["lw", "ld", "lh"]]):
            self._setup = FpLoadSetup(resource_db=self.resource_db)
        elif self._name.startswith("fs") and any([self._name.endswith(stem) for stem in ["sw", "sd", "sh"]]):
            self._setup = FloatStoreRegBasedSetup(resource_db=self.resource_db)
        elif self._name.startswith("fsqrt."):
            self._setup = FpSqrtSetup(resource_db=self.resource_db)
        elif self._name.startswith("fclass."):
            self._setup = FpClassifySetup(self.resource_db)
        elif len(self._imms) == 0 and len(self._srcs) == 2:
            self._setup = FpRegRegSetup(self.resource_db)
        elif len(self._imms) == 0 and len(self._srcs) == 3:
            self._setup = FpRegRegRegSetup(self.resource_db)
        self._setup.resource_db = self.resource_db
        self._setup.rng = self.resource_db.rng
