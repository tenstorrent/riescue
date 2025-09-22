# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from riescue.compliance.lib.riscv_instrs import InstrBase
from riescue.compliance.lib.riscv_registers import RiscvXregister
from riescue.compliance.lib.instr_setup import (
    RegImmSetup,
    AtomicMemoryOperationsSetup,
    SingleRegSetup,
    DestRegSrcImmSetup,
    BranchSetup,
    FenceImmSetup,
    LoadSetup,
    JumpSetup,
    DestRegSrcImmSrcPCSetup,
    RegRegSetup,
    StoreSetup,
    FenceSetup,
    LoadReservedSetup,
    CompressedBranchSetup,
    C1CompIntRegImmSrxiSetup,
    C2CompIntRegRegSetup,
    C2CompIntRegRegMoveSetup,
    CSS_C2_CompIntSPStoreSetup,
    C1CompIntConGenSetupLUI,
    C1CompIntImmSetup,
    C0CompIntRegRegLoadSetup,
    CAC1CompIntRegRegSetup,
    CI_C2_CompIntSPLoadSetup,
    C0CompIntRegImmSetup,
    CompressedJumpSetup,
    C1CompIntRegImmAddiwSetup,
    C1NopSetup,
    C1CompIntConGenSetup,
    C1CompIntRegImmSetup,
    C1CompIntRegImmSlliSetup,
    C0CompIntRegRegStoreSetup,
    CBC1CompIntRegImmSetup,
)
from riescue.compliance.lib.immediate import Immediate12, Immediate20, ImmediateGeneric
from riescue.compliance.config import Resource


class RiscvIntInstr(InstrBase):
    """
    RISCV Integer instruction base class.
    """

    def __init__(self, resource_db: Resource, name, label=""):
        super().__init__(resource_db, name, label)

        # for attr in variable_fields:
        for attr in self.__dir__():
            if attr in ["rs1", "rs2", "rs3", "c_rs2"]:
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._srcs.append(getattr(self, attr))
            elif attr in ["rs1_p", "rs2_p"]:
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
            elif attr in ["rd_rs1", "rd_rs1_n0"]:
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr, exclude_regs=["x0"]))
                self._srcs.append(getattr(self, attr))
                self._dests.append(getattr(self, attr))
            elif attr == "rd_rs1_p":
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
                self._dests.append(getattr(self, attr))
            elif attr in ["c_rs2_n0", "c_rs1_n0", "rs1_n0", "rs1_n0"]:
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr, exclude_regs=["x0"]))
                self._srcs.append(getattr(self, attr))
            elif attr in ["rd"]:
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._dests.append(getattr(self, attr))
            elif attr in ["rd_n0"]:
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr, exclude_regs=["x0"]))
                self._dests.append(getattr(self, attr))
            elif attr in ["rd_n2"]:
                setattr(self, attr, RiscvXregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr, exclude_regs=["x0", "x2"]))
                self._dests.append(getattr(self, attr))
            elif attr in ["rd_p"]:
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
                self._dests.append(getattr(self, attr))
            elif attr in ["aq"]:
                setattr(self, attr, ImmediateGeneric(resource_db=self.resource_db, name="", size=1, field_name=attr))
                self._imms.append(getattr(self, attr))
            elif attr in ["rl"]:
                setattr(self, attr, ImmediateGeneric(resource_db=self.resource_db, name="", size=1, field_name=attr))
                self._imms.append(getattr(self, attr))
            elif attr in ["imm20", "jimm20"]:
                setattr(self, attr, Immediate20(resource_db=self.resource_db, name=""))
                self._imms.append(getattr(self, attr))
            elif attr in ["imm12", "imm12hi", "bimm12hi", "imm12lo", "bimm12lo", "zimm"]:
                setattr(self, attr, Immediate12(resource_db=self.resource_db, name="", field_name=attr))
                self._imms.append(getattr(self, attr))
            elif "c_" in attr and "imm" in attr:
                setattr(self, attr, ImmediateGeneric(resource_db=self.resource_db, name="", size=1, field_name=attr))
                self._imms.append(getattr(self, attr))
            elif attr in ["shamt", "shamtw", "shamtd"]:
                setattr(self, attr, ImmediateGeneric(resource_db=self.resource_db, name="", size=5, field_name=attr))
                self._imms.append(getattr(self, attr))

        self._fields = dict()
        for operand in self._srcs + self._dests + self._imms:
            self._fields[operand.field_name] = operand

        if self._name in ["lb", "lh", "lw", "lbu", "lhu", "ld", "lwu"]:
            self._setup = LoadSetup(self.resource_db)
        elif self._name == "fence":
            self._setup = FenceSetup(self.resource_db)
        elif self._name == "fence.i":
            self._setup = FenceImmSetup(self.resource_db)
        elif self._name.startswith("c."):
            if self._name == "c.nop":
                self._setup = C1NopSetup(self.resource_db)
            elif len(self._srcs) == 1 and len(self._dests) == 0 and len(self._imms) == 1:
                self._setup = CSS_C2_CompIntSPStoreSetup(self.resource_db)
            elif len(self._srcs) == 2 and len(self._dests) == 1 and len(self._imms) == 0:
                if "p" in self._srcs[0].field_name:
                    self._setup = CAC1CompIntRegRegSetup(self.resource_db)
                else:
                    self._setup = C2CompIntRegRegSetup(self.resource_db)
            elif len(self._srcs) == 2 and len(self._dests) == 0 and len(self._imms) == 2:
                self._setup = C0CompIntRegRegStoreSetup(self.resource_db)
            elif len(self._srcs) == 1 and len(self._dests) == 1 and len(self._imms) == 2:
                if self._name == "c.addiw":
                    self._setup = C1CompIntRegImmAddiwSetup(self.resource_db)
                elif self._name == "c.slli":
                    self._setup = C1CompIntRegImmSlliSetup(self.resource_db)
                elif self._name in ["c.srai", "c.srli"]:
                    self._setup = C1CompIntRegImmSrxiSetup(self.resource_db)
                elif self._name in ["c.ld", "c.lw"]:
                    self._setup = C0CompIntRegRegLoadSetup(self.resource_db)
                elif "p" in self._srcs[0].field_name:
                    self._setup = CBC1CompIntRegImmSetup(self.resource_db)
                else:
                    self._setup = C1CompIntRegImmSetup(self.resource_db)
            elif len(self._srcs) == 0 and len(self._dests) == 0 and len(self._imms) == 2:
                self._setup = C1CompIntImmSetup(self.resource_db)
            elif len(self._srcs) == 0 and len(self._dests) == 1 and len(self._imms) == 1:
                self._setup = C0CompIntRegImmSetup(self.resource_db)
            elif len(self._srcs) == 0 and len(self._dests) == 1 and len(self._imms) == 2:
                if "n2" in self._dests[0].field_name:
                    self._setup = C1CompIntConGenSetupLUI(self.resource_db)
                elif "sp" in self._name:
                    self._setup = CI_C2_CompIntSPLoadSetup(self.resource_db)
                else:
                    self._setup = C1CompIntConGenSetup(self.resource_db)
            elif len(self._srcs) == 1 and len(self._dests) == 1 and len(self._imms) == 0:
                self._setup = C2CompIntRegRegMoveSetup(self.resource_db)
            elif len(self._srcs) == 1 and len(self._dests) == 0 and len(self._imms) == 0:
                self._setup = CompressedJumpSetup(self.resource_db)
            elif len(self._srcs) == 0 and len(self._dests) == 0 and len(self._imms) == 1:
                self._setup = CompressedJumpSetup(self.resource_db)
            elif len(self._srcs) == 1 and len(self._dests) == 0 and len(self._imms) == 2:
                if self._name not in ["c.bnez", "c.beqz"]:
                    assert False
                self._setup = CompressedBranchSetup(self.resource_db)
            else:
                print("No setup for instr: " + self._name)
                print("Imms: " + str(len(self._imms)) + " srcs: " + str(len(self._srcs)) + " dests: " + str(len(self._dests)))
                assert False, "No setup for compressed instruction identified."
        elif self._name.startswith("amo") or self._name.startswith("sc."):
            self._setup = AtomicMemoryOperationsSetup(self.resource_db)
        elif self._name.startswith("lr."):
            self._setup = LoadReservedSetup(self.resource_db)
        elif self._name in ["sb", "sh", "sw", "sd"]:
            self._setup = StoreSetup(self.resource_db)
        elif self._name in ["beq", "bne", "blt", "bge", "bgeu", "bltu"]:
            self._setup = BranchSetup(32, self.resource_db)
        elif self._name in ["jal", "jalr"]:
            self._setup = JumpSetup(self.resource_db)
        elif len(self._imms) == 1 and len(self._srcs) == 1:
            self._setup = RegImmSetup(self.resource_db)
        elif len(self._imms) == 1 and len(self._dests) == 1:
            if "auipc" in self._name:
                self._setup = DestRegSrcImmSrcPCSetup(self.resource_db)
            else:
                self._setup = DestRegSrcImmSetup(self.resource_db)
        elif len(self._imms) == 0 and len(self._srcs) == 2:
            self._setup = RegRegSetup(self.resource_db)
        elif len(self._srcs) == 1 and len(self._imms) == 0:
            self._setup = SingleRegSetup(self.resource_db)
        else:
            print("No setup for instr: " + self._name)
            print("Imms: " + str(len(self._imms)) + " srcs: " + str(len(self._srcs)) + " dests: " + str(len(self._dests)))
            assert False, "No setup for instruction identified."
        self._setup.resource_db = self.resource_db
        self._setup.rng = self.resource_db.rng
