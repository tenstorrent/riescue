# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.riscv_instrs import InstrBase
from riescue.compliance.lib.riscv_registers import RiscvVregister, RiscvXregister, RiscvFregister
from riescue.compliance.lib.instr_setup import (
    VecStoreIndexedUnorderedSegmentedSetup,
    VecFpWideningAddSubSetup,
    VecFpConvertSetup,
    VecLoadIndexedUnorderedSegmentedSetup,
    OPMVV_VReg_Mask_Setup,
    VecFpRecSqrtSetup,
    VecLoadUnitStrideSetup,
    VecFpWmaBF16Setup,
    VecVRegImmMaskSetup,
    VecVRegMaskVRegMaskSetup,
    VtypeIndepVecWholeRegMoveSetup,
    VecLoadStridedSetup,
    VecLoadUnitStrideSegmentedSetup,
    VecFPSingleWidthSetup,
    VecVRegImmSetup,
    VecGatherSetup,
    VqdotSetup,
    VecStoreMaskSetup,
    VecWideningSetup,
    VecStoreUnitStrideSegmentedSetup,
    VecLoadIndexedUnorderedSetup,
    VecNarrowingSetup,
    VecRegRegSetup,
    VecVRegVRegSetup,
    VecStoreUnitStrideSetup,
    VecIntExtensionSetup,
    VecImmImmSetup,
    VecZvbcSetup,
    VecFPSplatSetup,
    VecVRegVRegMaskSetup,
    VecImmSetup,
    VecLoadStridedSegmentedSetup,
    VecLoadMaskSetup,
    VecWholeRegMoveSetup,
    VecXRegDestSetup,
    VecRegImmSetup,
    VecFpCompareSetup,
    VecFpCvtBF16Setup,
    VecVRegXRegMaskExplicitSetup,
    VecXRegSetup,
    OPMVV_Mask_Setup,
    VecVRegVRegMaskExplicitSetup,
    VecStoreWholeRegSetup,
    OPMVV_XRegDest_VReg_Mask_Setup,
    VecVRegXRegSetup,
    VecStoreStridedSegmentedSetup,
    VecVRegImmMaskExplicitSetup,
    VecLoadWholeRegSetup,
    VecStoreStridedSetup,
    VecStoreIndexedUnorderedSetup,
    VecFpSlideSetup,
    VecLoadUnitStrideFaultFirstSetup,
    VecVRegSetup,
    VecFPRegDestSetup,
    VecFPRegSetup,
)

from riescue.compliance.lib.immediate import Immediate5, Immediate10, Immediate11, Immediate6
from riescue.compliance.config import Resource


class RiscvVecInstr(InstrBase):
    def __init__(self, resource_db: Resource, name, label=""):
        super().__init__(resource_db, name, label)

        """
        #   vm doesn't appear as a field for these instructions with these endings, however, v0 in mask format is a necessary operand.
        """
        if self._name.endswith(".vvm") or self._name.endswith(".vxm") or self._name.endswith(".vim"):
            setattr(self, "vm", "")
            assert "vm" in dir(self)
        """
        #   In the ISA vm operands are required to be v0
        """
        if "vm" in dir(self):
            self._reg_manager.reserve_reg("v0", "Vector")

        VARITH_OPCODE = 0b1010111
        VCRYPTO_OPCODE = 0b1110111
        V_OPIVV_FUNCT3 = 0b000
        V_OPFVV_FUNCT3 = 0b001
        V_OPMVV_FUNCT3 = 0b010
        V_OPIVI_FUNCT3 = 0b011
        V_OPIVX_FUNCT3 = 0b100
        V_OPFVF_FUNCT3 = 0b101
        V_OPMVX_FUNCT3 = 0b110

        VLOAD_OPCODE = 0b0000111
        VSTORE_OPCODE = 0b0100111

        vector_compare_instrs = ("vmseq", "vmsne", "vmsltu", "vmslt", "vmsleu", "vmsle", "vmsgtu", "vmsgt")

        is_load_store = False
        is_arithmetic = False
        is_crypto = False
        vector_fp = False
        scalar_reg_type = RiscvXregister

        # Determine what subtype of instruction this is
        encoding = getattr(self, "encoding")
        opcode = int(encoding[31 - 6 :], base=2)
        funct3 = int(encoding[31 - 14 : 31 - 11], base=2)
        if opcode == VARITH_OPCODE:
            is_arithmetic = True
            if funct3 in [V_OPFVV_FUNCT3, V_OPFVF_FUNCT3]:
                vector_fp = True
                scalar_reg_type = RiscvFregister
        elif opcode == VLOAD_OPCODE or opcode == VSTORE_OPCODE:
            is_load_store = True
        elif opcode == VCRYPTO_OPCODE:
            is_crypto = True

        for attr in dir(self):
            if attr in ["rs1", "rs2"]:
                setattr(self, attr, scalar_reg_type(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._srcs.append(getattr(self, attr))
            elif attr in ["rd"]:
                setattr(self, attr, scalar_reg_type(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._dests.append(getattr(self, attr))
            elif attr in ["vs1", "vs2", "vs3"]:
                setattr(self, attr, RiscvVregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._srcs.append(getattr(self, attr))
            elif attr in ["vm"]:
                setattr(self, attr, RiscvVregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._srcs.append(getattr(self, attr))
                self._srcs[-1].name = "v0"
            elif attr in ["vd"]:
                setattr(self, attr, RiscvVregister(resource_db=self.resource_db, name="", size=5, reg_manager=self._reg_manager, field_name=attr))
                self._dests.append(getattr(self, attr))
            elif attr in ["zimm11"]:
                setattr(self, attr, Immediate11(resource_db=self.resource_db, name="", field_name=attr))
                self._imms.append(getattr(self, attr))
            elif attr in ["zimm10"]:
                setattr(self, attr, Immediate10(resource_db=self.resource_db, name="", field_name=attr))
                self._imms.append(getattr(self, attr))
            elif attr in ["uimm", "simm5", "zimm5"]:
                setattr(self, attr, Immediate5(resource_db=self.resource_db, name="", field_name=attr))
                self._imms.append(getattr(self, attr))
            elif "6hi" in attr:
                setattr(self, attr, Immediate6(resource_db=self.resource_db, name="", field_name=attr))
                self._imms.append(getattr(self, attr))
            elif attr in ["nf"] and "seg" in self._name:
                """
                #   Interpret nf value from instruction name for segemented l/s instructions.
                """
                index_of_seg = self._name.find("seg") + 3
                nf_value = int(self._name[index_of_seg])
                setattr(self, attr, type("nf_type", (object,), {"field_name": "nf", "value": nf_value, "randomize": (lambda self, **kwargs: None)})())

        self._fields = dict()
        for operand in self._srcs + self._dests + self._imms:
            self._fields[operand.field_name] = operand

        if vector_fp:
            # Something is setting this up, not sure what. Trying to set a default `self.group=""` in constructor causes it to fail.
            if self.group == "vec_fp_scalar_move":
                if self._name.endswith("f.s"):
                    self._setup = VecFPRegDestSetup(resource_db=self.resource_db)
                elif self._name.endswith("s.f"):
                    self._setup = VecFPRegSetup(resource_db=self.resource_db)
                else:
                    assert False, f"Unhandled instruction: {self._name}"
            elif "cvtbf16" in self._name:
                is_widening = "wcvt" in self._name
                self._setup = VecFpCvtBF16Setup(resource_db=self.resource_db, is_widening=is_widening)
            elif "vfwmaccbf16" in self._name:
                scalar_source = ".vf" in self._name
                self._setup = VecFpWmaBF16Setup(resource_db=self.resource_db, scalar_source=scalar_source)
            elif any(matcher in self._name for matcher in ["cvt", "fclass"]):
                self._setup = VecFpConvertSetup(resource_db=self.resource_db)
            elif self._name in ["vfsqrt.v", "vfrsqrt7.v", "vfrec7.v"]:
                self._setup = VecFpRecSqrtSetup(resource_db=self.resource_db, mask_required=False, fp=True)
            elif self._name.startswith(("vmfeq", "vmfne", "vmflt", "vmfle", "vmfgt", "vmfge")):
                self._setup = VecFpCompareSetup(resource_db=self.resource_db, mask_required=False, initialize_vd=True, fp=True)
            elif self._name.startswith(("vfwadd", "vfwsub", "vfwmul", "vfwmacc", "vfwnmacc", "vfwmsac", "vfwnmsac")):
                self._setup = VecFpWideningAddSubSetup(resource_db=self.resource_db, mask_required=False, initialize_vd=True, fp=True)
            elif self._name.startswith("vfslide"):
                self._setup = VecFpSlideSetup(resource_db=self.resource_db, mask_required=False, initialize_vd=True, fp=True)
            elif "fmv" in self._name:
                self._setup = VecFPSplatSetup(resource_db=self.resource_db)
            else:
                operands = []  # TODO use this information to help unify the setup classes, unless this all gets subsumed anyway
                second_operand_scalar = False
                third_operand_scalar = False
                v1_before_v2 = False
                initialize_vd = False
                mask_required = False
                widening = False
                fp = True
                no_overlap = False
                if any(fma in self._name for fma in ["fma", "fms", "fnma", "fnms"]) and "fmax" not in self._name:
                    v1_before_v2 = True
                    initialize_vd = True
                    operands = ["vd", "vs1", "vs2"]
                else:
                    operands = ["vd", "vs2", "vs1"]

                if "red" in self._name:
                    initialize_vd = True
                    if self._name.startswith("vfw"):
                        widening = True
                        no_overlap = True

                if self._name.endswith(".vf") or "merge" in self._name:
                    if v1_before_v2:
                        second_operand_scalar = True
                        operands = ["vd", "rs1", "vs2"]
                    else:
                        third_operand_scalar = True
                        operands = ["vd", "vs2", "rs1"]

                if self._name.endswith("m"):
                    mask_required = True

                self._setup = VecFPSingleWidthSetup(self.resource_db, second_operand_scalar, third_operand_scalar, v1_before_v2, initialize_vd, mask_required, fp, widening, no_overlap)

        elif not is_load_store and not is_crypto:
            if not is_arithmetic:
                if "vset" in self._name:
                    if len(self._imms) == 0 and len(self._srcs) == 2:
                        self._setup = VecRegRegSetup(resource_db=self.resource_db)
                    elif len(self._imms) == 1 and len(self._srcs) == 1:
                        self._setup = VecRegImmSetup(resource_db=self.resource_db)
                    elif len(self._imms) == 2 and len(self._srcs) == 0:
                        self._setup = VecImmImmSetup(resource_db=self.resource_db)
                elif self._name.startswith("vqdot"):
                    self._setup = VqdotSetup(resource_db=self.resource_db)
                else:
                    print("Unhandled instruction: " + str(name))
                    assert False
            else:
                if self._name.startswith("vclmul"):
                    self._setup = VecZvbcSetup(resource_db=self.resource_db)
                elif "gather" in self._name:
                    third_operand_scalar = True if self._name.endswith("vx") else False
                    third_operand_imm = True if self._name.endswith("vi") else False
                    third_operand_vector = True if self._name.endswith("vv") else False
                    ei16_mode = True if "ei16" in self._name else False
                    self._setup = VecGatherSetup(self.resource_db, last_op_scalar=third_operand_scalar, last_op_imm=third_operand_imm, last_op_vector=third_operand_vector, ei16_mode=ei16_mode)
                elif "ext.vf" in self._name:
                    self._setup = VecIntExtensionSetup(resource_db=self.resource_db)
                elif any(self._name.startswith(stem) for stem in ["vnm", "vmacc", "vmadd"]):
                    operands = []  # TODO use this information to help unify the setup classes, unless this all gets subsumed anyway
                    second_operand_scalar = False
                    third_operand_scalar = False
                    mask_required = False
                    fp = False
                    v1_before_v2 = True
                    initialize_vd = True
                    operands = ["vd", "vs1", "vs2"]

                    if self._name.endswith(".vx"):
                        second_operand_scalar = True
                        operands = ["vd", "rs1", "vs2"]

                    self._setup = VecFPSingleWidthSetup(self.resource_db, second_operand_scalar, third_operand_scalar, v1_before_v2, initialize_vd, mask_required, fp, widening=False)
                elif self._name.startswith("vw"):
                    scalar_second = False
                    scalar_third = False
                    mask_required = False
                    if self._name.endswith(".vv"):
                        self._setup = VecWideningSetup(resource_db=self.resource_db, scalar_second=scalar_second, scalar_third=scalar_third, imm_third=False, mask_required=mask_required)
                    elif self._name.endswith(".vi"):
                        self._setup = VecWideningSetup(resource_db=self.resource_db, scalar_second=scalar_second, scalar_third=scalar_third, imm_third=True, mask_required=mask_required)
                    elif self._name.endswith(".vx"):
                        if "macc" in self._name:
                            self._setup = VecWideningSetup(resource_db=self.resource_db, scalar_second=True, scalar_third=False, imm_third=False, mask_required=mask_required)
                        else:
                            self._setup = VecWideningSetup(resource_db=self.resource_db, scalar_second=scalar_second, scalar_third=True, imm_third=False, mask_required=mask_required)
                    elif self._name.endswith(".vs"):
                        no_overlap = False
                        if "red" in self._name:
                            no_overlap = True
                        self._setup = VecWideningSetup(
                            resource_db=self.resource_db, scalar_second=scalar_second, scalar_third=scalar_third, imm_third=False, mask_required=mask_required, no_overlap=no_overlap
                        )
                    else:
                        print("Unhandled instruction: " + str(name))
                        assert False
                elif self._name.endswith(tuple([".wv", "wi", ".wx"])):
                    self._setup = VecNarrowingSetup(resource_db=self.resource_db, variant=self._name[-2:])
                elif self._name.endswith(tuple([".vv", ".vs"])):
                    if self._name.startswith(vector_compare_instrs):
                        self._setup = VecVRegVRegMaskExplicitSetup(resource_db=self.resource_db)
                    if len(self._imms) == 0 and len(self._srcs) == 3:
                        self._setup = VecVRegVRegSetup(resource_db=self.resource_db)
                    if len(self._imms) == 0 and len(self._srcs) == 2:
                        self._setup = VecVRegVRegMaskSetup(resource_db=self.resource_db)  # FIXME setup class would be better named to indicate the destination is in mask format
                elif self._name.endswith(".vvm"):
                    if len(self._imms) == 0 and len(self._srcs) == 3:
                        self._setup = VecVRegVRegMaskExplicitSetup(resource_db=self.resource_db)
                elif self._name.endswith(".vx"):
                    if self._name.startswith(vector_compare_instrs):
                        self._setup = VecVRegXRegMaskExplicitSetup(resource_db=self.resource_db)
                    if len(self._imms) == 0 and len(self._srcs) == 3:
                        self._setup = VecVRegXRegSetup(resource_db=self.resource_db)
                    elif len(self._imms) == 0 and len(self._srcs) == 2:
                        self._setup = VecVRegXRegSetup(resource_db=self.resource_db)
                elif self._name.endswith(".vi"):
                    if self._name.startswith(vector_compare_instrs):
                        self._setup = VecVRegImmMaskExplicitSetup(resource_db=self.resource_db)
                    if len(self._imms) == 1 and len(self._srcs) == 2:
                        self._setup = VecVRegImmSetup(resource_db=self.resource_db)
                    elif len(self._imms) == 1 and len(self._srcs) == 1:
                        self._setup = VecVRegImmMaskSetup(resource_db=self.resource_db)
                elif self._name.endswith(".vim"):
                    if len(self._imms) == 1 and len(self._srcs) == 2:
                        self._setup = VecVRegImmMaskExplicitSetup(resource_db=self.resource_db)
                elif self._name.endswith(".vxm"):
                    if len(self._imms) == 0 and len(self._srcs) == 3:
                        self._setup = VecVRegXRegMaskExplicitSetup(resource_db=self.resource_db)
                elif self._name.endswith(".v.x"):
                    self._setup = VecXRegSetup(resource_db=self.resource_db)
                elif self._name.endswith(".v.v"):
                    self._setup = VecVRegSetup(resource_db=self.resource_db)
                elif self._name.endswith(".v.i"):
                    self._setup = VecImmSetup(resource_db=self.resource_db)
                elif self._name.endswith(".vm"):
                    self._setup = VecVRegVRegMaskSetup(resource_db=self.resource_db)  # FIXME setup class would be better named to indicate the destination is in mask format
                elif self._name.endswith(".mm"):
                    self._setup = VecVRegMaskVRegMaskSetup(resource_db=self.resource_db)
                elif self._name.endswith(".m"):
                    if isinstance(self._dests[0], RiscvXregister):
                        self._setup = OPMVV_XRegDest_VReg_Mask_Setup(resource_db=self.resource_db)
                    elif isinstance(self._dests[0], RiscvVregister):
                        self._setup = OPMVV_VReg_Mask_Setup(resource_db=self.resource_db)
                    else:
                        print("Unhandled instruction: " + str(name))
                        assert False
                elif self._name.endswith(".v"):
                    if len(self._srcs) == 2:
                        self._setup = OPMVV_VReg_Mask_Setup(resource_db=self.resource_db)
                    elif self._name.startswith("vmv"):
                        if self._name.endswith("r.v"):
                            self._setup = VtypeIndepVecWholeRegMoveSetup(resource_db=self.resource_db)
                        else:
                            self._setup = VecWholeRegMoveSetup(resource_db=self.resource_db)
                    else:
                        self._setup = OPMVV_Mask_Setup(resource_db=self.resource_db)
                elif self._name.startswith("vmv"):
                    if self._name.endswith(".s.x"):
                        self._setup = VecXRegSetup(resource_db=self.resource_db)
                    elif self._name.endswith(".x.s"):
                        self._setup = VecXRegDestSetup(resource_db=self.resource_db)
                    else:
                        print("Unhandled instruction: " + str(name))
                        assert False
                else:
                    print("Unhandled instruction: " + str(name))
                    assert False
        elif is_load_store:
            hex_opcode = getattr(self, "opcode")
            hex_mop_if_any = getattr(self, "mop", str(-1))  # memory addressing mode
            hex_24_to_20 = getattr(self, "24..20", str(-1))  # lumop / sumop for vector load store mask unit stride
            segmented = "seg" in name

            if segmented:
                if hex_24_to_20 == hex(0x0):
                    if hex_opcode == hex(0x7):
                        self._setup = VecLoadUnitStrideSegmentedSetup(resource_db=self.resource_db)
                    elif hex_opcode == hex(0x27):
                        self._setup = VecStoreUnitStrideSegmentedSetup(resource_db=self.resource_db)
                elif hex_mop_if_any == hex(0x2):
                    if hex_opcode == hex(0x7):
                        self._setup = VecLoadStridedSegmentedSetup(resource_db=self.resource_db)
                    elif hex_opcode == hex(0x27):
                        self._setup = VecStoreStridedSegmentedSetup(resource_db=self.resource_db)
                elif hex_mop_if_any == hex(0x0):
                    self._setup = VecLoadUnitStrideSegmentedSetup(resource_db=self.resource_db)  # Handles fail-only-first variant from archetectural POV
                elif hex_mop_if_any == hex(0x1) or hex_mop_if_any == hex(0x3):
                    if hex_opcode == hex(0x7):
                        self._setup = VecLoadIndexedUnorderedSegmentedSetup(resource_db=self.resource_db)
                    elif hex_opcode == hex(0x27):
                        self._setup = VecStoreIndexedUnorderedSegmentedSetup(resource_db=self.resource_db)
                    else:
                        raise ValueError(f"Unhandled instruction: {name}")
                else:
                    raise ValueError(f"Unhandled instruction: {name}")
            else:
                if hex_mop_if_any == hex(0x2):  # Strided vector load store
                    if hex_opcode == hex(0x7):
                        self._setup = VecLoadStridedSetup(resource_db=self.resource_db)
                    elif hex_opcode == hex(0x27):
                        self._setup = VecStoreStridedSetup(resource_db=self.resource_db)
                elif hex_mop_if_any == hex(0x1) or hex_mop_if_any == hex(0x3):
                    if hex_opcode == hex(0x7):
                        self._setup = VecLoadIndexedUnorderedSetup(resource_db=self.resource_db)
                    elif hex_opcode == hex(0x27):
                        self._setup = VecStoreIndexedUnorderedSetup(resource_db=self.resource_db)
                else:
                    if hex_24_to_20 == hex(0xB):  # special vector load store mask instructions
                        if hex_opcode == hex(0x7):
                            self._setup = VecLoadMaskSetup(resource_db=self.resource_db)
                        elif hex_opcode == hex(0x27):
                            self._setup = VecStoreMaskSetup(resource_db=self.resource_db)
                        else:
                            raise ValueError(f"Unhandled instruction: {name}")
                    elif hex_24_to_20 == hex(0x10):
                        if hex_opcode == hex(0x7):
                            self._setup = VecLoadUnitStrideFaultFirstSetup(resource_db=self.resource_db)
                        else:
                            raise ValueError(f"Unhandled instruction: {name}")
                    elif hex_24_to_20 == hex(0x0):
                        if hex_opcode == hex(0x7):
                            self._setup = VecLoadUnitStrideSetup(resource_db=self.resource_db)
                        elif hex_opcode == hex(0x27):
                            self._setup = VecStoreUnitStrideSetup(resource_db=self.resource_db)
                        else:
                            raise ValueError(f"Unhandled instruction: {name}")
                    elif hex_24_to_20 == hex(0x8):
                        if hex_opcode == hex(0x7):
                            self._setup = VecLoadWholeRegSetup(resource_db=self.resource_db)
                        elif hex_opcode == hex(0x27):
                            self._setup = VecStoreWholeRegSetup(resource_db=self.resource_db)
                        else:
                            raise ValueError(f"Unhandled instruction: {name}")
                    else:
                        raise ValueError(f"Unhandled instruction: {name} hex: {hex_opcode}")
        elif is_crypto:
            if self._name.startswith(("vghsh", "vgmul")):
                self._setup = VecZvbcSetup(resource_db=self.resource_db)
            else:
                self._setup = VecZvbcSetup(resource_db=self.resource_db)
        else:
            print("Unhandled instruction: " + str(name))
            assert False
        self._setup.resource_db = self.resource_db
        self._setup.rng = self.resource_db.rng
