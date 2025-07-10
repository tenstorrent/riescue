# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field

import riescue.lib.enums as RV

log = logging.getLogger(__name__)


@dataclass
class MemRange:
    "Memory range with start address, end address, size, and secure flag"

    start: int = 0
    size: int = 0
    secure: bool = False

    def __post_init__(self):
        self.end: int = self.start + self.size - 1

    @classmethod
    def from_dict(cls, name: str, d: dict, secure: bool = False):
        start = d.get("address")
        size = d.get("size")
        if start is None or size is None:
            raise ValueError(f"Require start and size for range: {name} in cpu config")
        try:
            start = int(start, 0)
        except ValueError:
            raise ValueError(f"Invalid start address for range: {name} in cpu config")
        try:
            size = int(size, 0)
        except ValueError:
            raise ValueError(f"Invalid size for range: {name} in cpu config")
        secure = d.get("secure", secure) or secure
        return cls(start=start, size=size, secure=secure)


@dataclass(frozen=True)
class Memory:
    """
    Memory object with DRAM, IO, secure, and reserved ranges. Defaults to non-secure DRAM and IO ranges.
    Default DRAM Range is 0x80000000 - 0xFFFFFFFFFFFFFFFF
    Default IO Range is 0x0 - 0x7FFFFFFF

    Frozen after construction to prevent modification of ranges.
    Construct with `from_cpuconfig`
    """

    dram_ranges: list[MemRange] = field(default_factory=lambda: [MemRange(0x8000_0000, 2**56, False)])
    io_ranges: list[MemRange] = field(default_factory=lambda: [MemRange(0x0, 0x8000_0000)])
    secure_ranges: list[MemRange] = field(default_factory=list)
    reserved_ranges: list[MemRange] = field(default_factory=list)

    @classmethod
    def determine_mmio_region_address_qualifier(cls, disallow_mmio: bool, test_access: str):
        if disallow_mmio or test_access == "reserved":
            return RV.AddressQualifiers.ADDRESS_RESERVED
        elif test_access == "available":
            return RV.AddressQualifiers.ADDRESS_MMIO
        else:
            raise ValueError(f"Unknown test_access level {test_access}")

    @classmethod
    def from_cpuconfig(cls, cfg: dict, disallow_mmio: bool):
        """
        Constructs memory object from cpuconfig dictionary.


        Expects keys starting with "dram" to be DRAM ranges.
        Expects keys starting with "secure" to be secure ranges.
        Expects key "io" to have "items" key with list of IO ranges.

        TODO: Reevaluate if io should be a top-level key or just another item that starts with "io"
        TODO: or if DRAM ranges should be under a DRAM key


        Example JSON:

        .. code-block:: JSON
            {
                "mmap": {
                    "dram": {
                        "address": "0x8000_0000",
                        "size": "0x8000_0000"
                    },
                    "secure": {
                        "address": "0x8000_0000",
                        "size": "0x8000_0000"
                    },
                    "io": {
                        "io0": {
                            "address" : "0x0",
                            "size" : "0x1_0000"
                        },
                        "io1": {
                            "address" : "0x1_0000",
                            "size" : "0x1_0000"
                        },
                    },
                }
            }
        """
        io_ranges = []
        secure_ranges = []
        reserved_ranges = []
        mmap = cfg.get("mmap", {})

        dram_ranges = [MemRange.from_dict(key, value) for key, value in mmap.items() if key.startswith("dram")]
        for range in dram_ranges:
            log.info(f"DRAM range: 0x{range.start:0X} - 0x{range.end:0x}")
            # Also handle secure range
            if range.secure:
                secure_ranges.append(range)

        # Another way to specify secure range is with top level "secure*" key
        explicit_secure_ranges = [MemRange.from_dict(key, value, secure=True) for key, value in mmap.items() if key.startswith("secure")]
        for range in explicit_secure_ranges:
            log.info(f"Secure range: 0x{range.start:0X} - 0x{range.end:0x}")
            secure_ranges.append(range)

        io_items = mmap.get("io", {}).get("items", {})
        for name, info in io_items.items():
            # Every named region is off limits for testing purposes
            address_qualifier = cls.determine_mmio_region_address_qualifier(disallow_mmio, info.get("test_access", "reserved"))
            range = MemRange.from_dict(name, info)
            if address_qualifier == RV.AddressQualifiers.ADDRESS_MMIO:
                io_ranges.append(range)
                log.info(f"MMIO range: 0x{range.start:0x} - 0x{range.end:0x}")
            elif address_qualifier == RV.AddressQualifiers.ADDRESS_RESERVED:
                reserved_ranges.append(range)
                log.info(f"Reserved range: 0x{range.start:0x} - 0x{range.end:0x}")

        if not dram_ranges and not io_ranges:
            # No ranges to add. Use defaults
            return cls()
        else:
            return cls(dram_ranges, io_ranges, secure_ranges, reserved_ranges)
