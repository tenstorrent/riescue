# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from __future__ import annotations
from enum import Enum, auto, IntEnum

import riescue.lib.common as common


class MyEnum(Enum):

    def __str__(self):
        return f"{self.name}"


class Xlen(IntEnum):
    XLEN32 = 32
    XLEN64 = 64


class PmpAttributes(MyEnum):
    R = "r"
    R_W = "rw"
    R_X = "rx"
    R_W_X = "rwx"
    NONE = "none"

    def bits(self) -> int:
        if self == PmpAttributes.R:
            return 1
        elif self == PmpAttributes.R_W:
            return 3
        elif self == PmpAttributes.R_X:
            return 5
        elif self == PmpAttributes.R_W_X:
            return 7
        elif self == PmpAttributes.NONE:
            return 0
        else:
            raise ValueError(f"PMP Attributes: {self} is unrecognized")

    @classmethod
    def from_str(cls, s: str) -> PmpAttributes:
        return cls(s)


class RiscvPmpAddressMatchingModes(MyEnum):
    OFF = 0
    TOR = 1
    NA4 = 2
    NAPOT = 3

    @classmethod
    def str_to_enum(cls, mode: str) -> RiscvPmpAddressMatchingModes:
        if mode == "off":
            return cls.OFF
        elif mode == "tor":
            return cls.TOR
        elif mode == "na4":
            return cls.NA4
        elif mode == "napot":
            return cls.NAPOT
        else:
            raise ValueError(f"PMP Address Matching mode: {mode} is unrecognized")

    def encode(self) -> int:
        "returns value bit shifted to bits [4:3]"
        return self.value << 3


class PmaSupport(MyEnum):
    """
    Base class for PMA support levels.
    """


class PmaAmoLevel(PmaSupport):
    """
    "Within AMOs, there are four levels of support: AMONone, AMOSwap, AMOLogical, and AMOArithmetic." (Atomicity PMAs - AMO support levels (Section 3.6.3))
    """

    AMO_NONE = auto()  #: "AMONone indicates that no AMO operations are supported."
    AMO_SWAP = auto()  #: "AMOSwap indicates that only amoswap instructions are supported in this address range."
    AMO_LOGICAL = auto()  #: "AMOLogical indicates that swap instructions plus all the logical AMOs (amoand, amoor, amoxor) are supported."
    AMO_ARITHMETIC = auto()  #: "AMOArithmetic indicates that all RISC-V AMOs are supported."


class PmaLrScLevel(PmaSupport):
    """
    "For LR/SC, there are three levels of support indicating combinations of the reservability and eventuality
    properties: RsrvNone, RsrvNonEventual, and RsrvEventual." (Atomicity PMAs - LR/SC support levels (Section 3.6.3))
    """

    RSRV_NONE = auto()  #: "RsrvNone indicates that no LR/SC operations are supported (the location is non-reservable)."
    RSRV_NON_EVENTUAL = auto()  #: "RsrvNonEventual indicates that the operations are supported (the location is reservable), but without the eventual success guarantee"
    RSRV_EVENTUAL = auto()  #: "RsrvEventual indicates that the operations are supported and provide the eventual success guarantee."


class PmaMisalignedAtomicityGranule(PmaSupport):
    """
    "The misaligned atomicity granule PMA provides constrained support for misaligned AMOs. This PMA, if
    present, specifies the size of a misaligned atomicity granule, a naturally aligned power-of-two number of
    bytes. Specific supported values for this PMA are represented by MAGNN, e.g., MAG16 indicates the
    misaligned atomicity granule is at least 16 bytes." (Misaligned Atomicity Granule PMA (Section 3.6.3)
    """

    # "If a misaligned AMO accesses a region that does not specify a misaligned atomicity granule PMA, [...] then an exception is raised."
    MAG_NONE = 0
    MAG_16 = 16  # At least 16 bytes
    MAG_32 = 32  # At least 32 bytes
    MAG_64 = 64  # At least 64 bytes
    MAG_128 = 128  # At least 128 bytes


class PmaMemoryOrdering(PmaSupport):

    RVWMO = auto()  #: "Coherent main memory regions always have either the RVWMO or RVTSO memory model."
    RVTSO = auto()
    INCOHERENT = auto()
    IO_RELAXED = auto()
    IO_STRONG = auto()


class PmaStrongOrderingChannel(PmaSupport):
    """
    "Each strongly ordered I/O region specifies a numbered ordering channel, which is a mechanism by which
    ordering guarantees can be provided between different I/O regions." Strong Ordering Channel for I/O regions (Section 3.6.5)
    """

    CHANNEL_0_POINT_TO_POINT = 0  #: "Channel 0 is used to indicate point-to-point strong ordering only, where only accesses by the hart to the single associated I/O region are strongly ordered."
    CHANNEL_1_GLOBAL = 1  #: "Channel 1 is used to provide global strong ordering across all I/O regions."
    CHANNEL_2 = 2  #: "Other larger channel numbers provide program ordering to accesses by that hart across any regions with the same channel number."
    CHANNEL_3 = 3
    CHANNEL_4 = 4
    CHANNEL_5 = 5
    CHANNEL_6 = 6
    CHANNEL_7 = 7


class PmaCoherence(PmaSupport):
    """
    "Coherence is a property defined for a single physical address, and indicates that writes to that address by
    one agent will eventually be made visible to other coherent agents in the system."  Coherence PMAs (Section 3.6.6)
    """

    COHERENT = auto()
    INCOHERENT = auto()


class PmaCacheability(PmaSupport):
    """
    "The cacheability of a memory region should not affect the software view of the region except for
    differences reflected in other PMAs, such as main memory versus I/O classification, memory ordering,
    supported accesses and atomic operations, and coherence."Cacheability PMAs (Section 3.6.6)
    """

    CACHEABLE = auto()
    NON_CACHEABLE = auto()
    CONFIGURABLE = auto()


class PmaIdempotency(PmaSupport):
    """
    "Idempotency PMAs describe whether reads and writes to an address region are idempotent. Main memory
    regions are assumed to be idempotent. For I/O regions, idempotency on reads and writes can be specified
    separately (e.g., reads are idempotent but writes are not)." Idempotency PMAs (Section 3.6.7)
    """

    IDEMPOTENT = auto()  #: "Main memory regions are assumed to be idempotent."
    NON_IDEMPOTENT = auto()

    READ_IDEMPOTENT_WRITE_NON_IDEMPOTENT = auto()  #: "For I/O regions where reads and writes have different idempotency"
    READ_NON_IDEMPOTENT_WRITE_IDEMPOTENT = auto()


class AddressType(MyEnum):
    NONE = auto()
    LINEAR = auto()
    PHYSICAL = auto()
    MEMORY = auto()
    GUEST_LINEAR = auto()
    GUEST_PHYSICAL = auto()


class AddressQualifiers(MyEnum):
    ADDRESS_LINEAR = auto()
    ADDRESS_PHYSICAL = auto()
    ADDRESS_DRAM = auto()
    ADDRESS_MMIO = auto()
    ADDRESS_SECURE = auto()
    ADDRESS_GUEST_PHYSICAL = auto()
    ADDRESS_RESERVED = auto()


class DataType(MyEnum):
    INT8 = auto()
    INT16 = auto()
    INT32 = auto()
    INT64 = auto()
    FP8 = auto()
    FP16 = auto()
    FP32 = auto()
    FP64 = auto()

    @classmethod
    def is_int(cls, data_type: DataType) -> bool:
        return (data_type == cls.INT8) or (data_type == cls.INT16) or (data_type == cls.INT32) or (data_type == cls.INT64)

    @classmethod
    def is_fp(cls, data_type: DataType) -> bool:
        return (data_type == cls.FP8) or (data_type == cls.FP16) or (data_type == cls.FP32) or (data_type == cls.FP64)


class NumGenOps(MyEnum):
    select_special_num = auto()
    special_num = auto()
    subnormal = auto()
    fractional = auto()
    low_fidelity = auto()
    nan_box = auto()
    dist = auto()


class SpecialFpNums(MyEnum):
    canonical_nan = auto()
    pos_inf = auto()
    neg_inf = auto()
    pos_zero = auto()
    neg_zero = auto()
    pos_sat = auto()
    neg_sat = auto()


class RiscvPrivileges(MyEnum):
    MACHINE = auto()
    SUPER = auto()
    USER = auto()

    @classmethod
    def enum_to_str(cls, priv: RiscvPrivileges) -> str:
        if priv == cls.MACHINE:
            return "machine"
        elif priv == cls.SUPER:
            return "super"
        elif priv == cls.USER:
            return "user"
        else:
            raise ValueError(f"priv: {priv} is unrecognized")


class RiscvExcpCauses(MyEnum):
    # Enum class for RISCV exception causes
    INSTRUCTION_ADDRESS_MISALIGNED = 0
    INSTRUCTION_ACCESS_FAULT = 1
    ILLEGAL_INSTRUCTION = 2
    BREAKPOINT = 3
    LOAD_ADDRESS_MISALIGNED = 4
    LOAD_ACCESS_FAULT = 5
    STORE_ADDRESS_MISALIGNED = 6
    STORE_ACCESS_FAULT = 7
    ECALL_FROM_USER = 8
    ECALL_FROM_SUPER = 9
    ECALL_FROM_VS = 10  # 0xa
    ECALL_FROM_MACHINE = 11  # 0xb
    INSTRUCTION_PAGE_FAULT = 12  # 0xc
    LOAD_PAGE_FAULT = 13  # 0xd
    STORE_PAGE_FAULT = 15  # 0xf
    INSTRUCTION_GUEST_PAGE_FAULT = 20  # 0x14
    LOAD_GUEST_PAGE_FAULT = 21  # 0x15
    VIRTUAL_INSTRUCTION = 22  # 0x16
    STORE_GUEST_PAGE_FAULT = 23  # 0x17

    # Enums for interrupt related causes
    # CAUSE_SUPER_SOFTWARE_INTR = 0x80000000000000001
    # CAUSE_MACHINE_SOFTWARE_INTR = 0x80000000000000003
    # CAUSE_SUPER_TIMER_INTR = 0x80000000000000005
    # CAUSE_MACHINE_TIMER_INTR = 0x80000000000000007
    # CAUSE_SUPER_EXT_INTR = 0x80000000000000009
    # CAUSE_MACHINE_EXT_INTR = 0x8000000000000000B


class RiscvInterruptCause(MyEnum):
    SSI = 1
    MSI = 3
    STI = 5
    MTI = 7
    SEI = 9
    MEI = 11
    COI = 13


class RiscvBaseArch(MyEnum):
    ARCH_RV32I = "rv32"
    ARCH_RV64I = "rv64"


class RiscvPageSizes(MyEnum):
    S4KB = auto()
    S64KB = auto()
    S4MB = auto()
    S2MB = auto()
    S1GB = auto()
    S512GB = auto()
    S256TB = auto()

    def __str__(self):
        return self.name[1:]

    @classmethod
    def str_to_enum(cls, pagesize: str) -> "RiscvPageSizes":
        return cls["S" + pagesize.upper()]

    @classmethod
    def weights(cls, pagesize: RiscvPageSizes) -> int:
        """
        Returns the weight of the given pagesize
        """
        if pagesize == cls.S4KB:
            return 1000
        elif pagesize == cls.S64KB:
            return 200
        elif pagesize == cls.S4MB:
            return 200
        elif pagesize == cls.S2MB:
            return 200
        elif pagesize == cls.S1GB:
            return 100
        elif pagesize == cls.S512GB:
            return 0
        elif pagesize == cls.S256TB:
            return 0
        else:
            raise ValueError(f"page size: {pagesize} is unrecognized")

    @classmethod
    def memory(cls, pagesize: RiscvPageSizes) -> int:
        if pagesize == cls.S4KB:
            return 0x1000
        elif pagesize == cls.S64KB:
            return 0x10000
        elif pagesize == cls.S4MB:
            return 0x400000
        elif pagesize == cls.S2MB:
            return 0x200000
        elif pagesize == cls.S1GB:
            return 0x40000000
        elif pagesize == cls.S512GB:
            return 0x8000000000
        elif pagesize == cls.S256TB:
            return 0x1000000000000
        else:
            raise ValueError(f"page size: {pagesize} is unrecognized")

    @classmethod
    def address_mask(cls, pagesize: RiscvPageSizes):
        """
        Return the address mask for the given pagesize
        """
        memory_needed = cls.memory(pagesize)

        return 0xFFFFFFFFFFFFFFFF << (common.msb(memory_needed)) & 0xFFFFFFFFFFFFFFFF

    @classmethod
    def pt_leaf_level(cls, pagesize: RiscvPageSizes):
        """
        Return which pagetable level to stop at for the given pagesize
        """
        if pagesize == cls.S4KB:
            return 0
        elif pagesize == cls.S64KB:
            return 0
        elif pagesize == cls.S4MB:
            return 1
        elif pagesize == cls.S2MB:
            return 1
        elif pagesize == cls.S1GB:
            return 2
        elif pagesize == cls.S512GB:
            return 3
        elif pagesize == cls.S256TB:
            return 4
        else:
            raise ValueError(f"page size: {pagesize} is unrecognized")


class RiscvPagingModes(MyEnum):
    DISABLE = auto()
    SV32 = auto()
    SV39 = auto()
    SV48 = auto()
    SV57 = auto()

    @classmethod
    def max_levels(cls, mode: RiscvPagingModes) -> int:
        """
        Return number of levels for a paging mode
        """
        levels = 0
        if mode == RiscvPagingModes.SV32:
            levels = 2
        elif mode == RiscvPagingModes.SV39:
            levels = 3
        elif mode == RiscvPagingModes.SV48:
            levels = 4
        elif mode == RiscvPagingModes.SV57:
            levels = 5
        else:
            pass

        return levels

    @classmethod
    def index_bits(cls, mode: RiscvPagingModes, level: int) -> tuple[int, int]:
        """
        Return index bits per pagetable level
        """
        index_bits = None
        if mode == RiscvPagingModes.SV32:
            if level == 0:
                index_bits = (21, 12)
            elif level == 1:
                index_bits = (31, 22)
            else:
                raise ValueError(f"{mode} only has pagetable levels 0,1 and not {level}")
        elif mode == RiscvPagingModes.SV39:
            if level == 0:
                index_bits = (20, 12)
            elif level == 1:
                index_bits = (29, 21)
            elif level == 2:
                index_bits = (38, 30)
            else:
                raise ValueError(f"{mode} only has pagetable levels 0,1,2 and not {level}")
        elif mode == RiscvPagingModes.SV48:
            if level == 0:
                index_bits = (20, 12)
            elif level == 1:
                index_bits = (29, 21)
            elif level == 2:
                index_bits = (38, 30)
            elif level == 3:
                index_bits = (47, 39)
            else:
                raise ValueError(f"{mode} only has pagetable levels 0,1,2,3 and not {level}")
        elif mode == RiscvPagingModes.SV57:
            if level == 0:
                index_bits = (20, 12)
            elif level == 1:
                index_bits = (29, 21)
            elif level == 2:
                index_bits = (38, 30)
            elif level == 3:
                index_bits = (47, 39)
            elif level == 4:
                index_bits = (56, 48)
            else:
                raise ValueError(f"{mode} only has pagetable levels 0,1,2,3,4 and not {level}")
        else:
            raise ValueError("paging mode disabled doesn't have index bits")

        return index_bits

    @classmethod
    def pt_entry_size(cls, mode: RiscvPagingModes) -> int:
        """
        Return pagetable entry size in bytes
        """
        entry_size = 0
        if mode == RiscvPagingModes.SV32:
            entry_size = 4
        elif mode == RiscvPagingModes.SV39:
            entry_size = 8
        elif mode == RiscvPagingModes.SV48:
            entry_size = 8
        elif mode == RiscvPagingModes.SV57:
            entry_size = 8
        else:
            pass

        return entry_size

    @classmethod
    def supported_pagesizes(cls, mode: RiscvPagingModes) -> list[RiscvPageSizes]:
        """
        Return supported pagesizes for a page mode
        """
        pagesizes: list[RiscvPageSizes] = list()
        if mode == RiscvPagingModes.SV32:
            pagesizes = [RiscvPageSizes.S4KB, RiscvPageSizes.S4MB]
        elif mode == RiscvPagingModes.SV39:
            pagesizes = [RiscvPageSizes.S4KB, RiscvPageSizes.S64KB, RiscvPageSizes.S2MB, RiscvPageSizes.S1GB]
        elif mode == RiscvPagingModes.SV48:
            pagesizes = [RiscvPageSizes.S4KB, RiscvPageSizes.S64KB, RiscvPageSizes.S2MB, RiscvPageSizes.S1GB, RiscvPageSizes.S512GB]
        elif mode == RiscvPagingModes.SV57:
            pagesizes = [RiscvPageSizes.S4KB, RiscvPageSizes.S64KB, RiscvPageSizes.S2MB, RiscvPageSizes.S1GB, RiscvPageSizes.S512GB, RiscvPageSizes.S256TB]
        else:
            pass

        return pagesizes

    @classmethod
    def linear_addr_bits(cls, mode: RiscvPagingModes, gstage: bool = False) -> int:
        """
        Return linear address bits for a page mode
        """
        linear_addr_bits = 0
        if mode == RiscvPagingModes.SV32:
            linear_addr_bits = 32
        elif mode == RiscvPagingModes.SV39:
            linear_addr_bits = 39
        elif mode == RiscvPagingModes.SV48:
            linear_addr_bits = 48
        elif mode == RiscvPagingModes.SV57:
            linear_addr_bits = 57
        elif mode == RiscvPagingModes.DISABLE:
            linear_addr_bits = 52
        else:
            raise ValueError(f"{mode} not supported")

        # If we are in g-stage, there are extra 2-bits in the pagetables
        if gstage and (mode != RiscvPagingModes.DISABLE):
            linear_addr_bits += 0

        return linear_addr_bits

    @classmethod
    def physical_addr_bits(cls, mode: RiscvPagingModes) -> int:
        """
        Return physical address bits for a page mode
        """
        physical_addr_bits = 0
        if mode == RiscvPagingModes.SV32:
            physical_addr_bits = 32
        elif mode == RiscvPagingModes.SV39:
            physical_addr_bits = 52
        elif mode == RiscvPagingModes.SV48:
            physical_addr_bits = 52
        elif mode == RiscvPagingModes.SV57:
            physical_addr_bits = 52
        elif mode == RiscvPagingModes.DISABLE:
            physical_addr_bits = 52
        else:
            raise ValueError(f"{mode} not supported")

        return physical_addr_bits


class RiscvTestEnv(MyEnum):
    TEST_ENV_BARE_METAL = auto()
    TEST_ENV_VIRTUALIZED = auto()

    @classmethod
    def str_to_enum(cls, env: str) -> "RiscvTestEnv":
        if env == "bare_metal":
            return cls.TEST_ENV_BARE_METAL
        elif env == "virtualized":
            return cls.TEST_ENV_VIRTUALIZED
        else:
            raise ValueError(f"env: {env} is unrecognized")


class RiscvSecureModes(MyEnum):
    SECURE = auto()
    NON_SECURE = auto()

    def __bool__(self):
        return self == self.SECURE

    @classmethod
    def str_to_enum(cls, secure_mode: str) -> "RiscvSecureModes":
        if secure_mode == "secured":
            return cls.SECURE
        elif secure_mode == "non-secured":
            return cls.NON_SECURE
        else:
            raise ValueError(f"secure_mode: {secure_mode} is unrecognized")


class RiscvMPEnablement(MyEnum):
    MP_OFF = auto()
    MP_ON = auto()

    @classmethod
    def str_to_enum(cls, mp_enable: str) -> "RiscvMPEnablement":
        if mp_enable == "off":
            return cls.MP_OFF
        elif mp_enable == "on":
            return cls.MP_ON
        else:
            raise ValueError(f"mp_enable: {mp_enable} is unrecognized")


class RiscvMPMode(MyEnum):
    MP_SIMULTANEOUS = auto()
    MP_PARALLEL = auto()

    @classmethod
    def str_to_enum(cls, mp_mode: str) -> "RiscvMPMode":
        if mp_mode == "simultaneous":
            return cls.MP_SIMULTANEOUS
        elif mp_mode == "parallel":
            return cls.MP_PARALLEL
        else:
            raise ValueError(f"mp_mode: {mp_mode} is unrecognized")


class RiscvParallelSchedulingMode(MyEnum):
    ROUND_ROBIN = auto()
    EXHAUSTIVE = auto()

    @classmethod
    def str_to_enum(cls, mode: str) -> "RiscvParallelSchedulingMode":
        if mode == "round_robin":
            return cls.ROUND_ROBIN
        elif mode == "exhaustive":
            return cls.EXHAUSTIVE
        else:
            raise ValueError(f"mode: {mode} is unrecognized")


class HookPoint(MyEnum):
    """
    Enumerated hooks for runtime code.

    These are all the available hooks, they allow for inserting code at specific points in the Runtime.
    They append or replace code at the hook point.

    This describes the name of the hook; behavior of the hook is defined where the hook is used.
    """

    # Loader hooks
    #: Insert code before the loader.
    #: Can be used to insert code for custom CSRs, platform intialization code. GPRs, and changes to tvec, status, and satp will be overwritten by loader.
    PRE_LOADER = "pre_loader"
    #: Insert additional M-level configuration needed before the runtime may jump to a less privileged or virtualized mode.
    #: Code should avoid modifying trap handling or paging registers, e.g. tvec, satp, or vsatp.
    M_LOADER = "m_loader"
    POST_LOADER = "post_loader"  #: Insert code after the loader but before the test jumps to the scheduler, inside ``loader__done`` label.

    # Scheduler hooks
    PRE_DISPATCH = "pre_dispatch"  #: Insert code before the scheduler loads the next test into a0.
    POST_DISPATCH = "post_dispatch"  #: Insert code after the scheduler loads the next test into a0. Should not modify a0

    # Trap Hooks
    #: Insert code after a trap is taken. Useful for logging/bookkeeping traps. Code should resume to return code after to continue execution.
    #: ``tp`` is initialized here - push and pop any registers used by post-trap code.
    POST_TRAP = "post_trap"

    # EOT hooks
    PRE_FAIL = "pre_fail"  #: Insert code before the test is marked as failed.
    POST_FAIL = "post_fail"  #: Insert code after the test is marked as failed.
    PRE_PASS = "pre_pass"  #: Insert code before the test is marked as passed.
    POST_PASS = "post_pass"  #: Insert code after the test is marked as passed.
    PRE_HALT = "pre_halt"  #: Insert code before the test is halted.
