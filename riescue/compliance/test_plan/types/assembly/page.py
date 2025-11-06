# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Any
from dataclasses import dataclass, field

from coretp.rv_enums import PageSize, PageFlags

from .base import AssemblyBase


@dataclass
class Page(AssemblyBase):
    """
    Data structure for page of memory

    :meth:`header()`: emits string of memory header for RiescueD.

    :param name: Unique identifier for the memory region. Every name should be unique.
    :type name: str
    :param size: Size of memory region in bytes, defaults to 4 KiB
    :type size: int
    :param start_addr: Start address of the memory region. If left blank, uses ;#random_addr()
    :type start_addr: Optional[int]


    :param alignment: Memory alignment requirements
    :type alignment: Optional[int]
    :param pma_config: Physical Memory Attributes configuration
    :type pma_config: Optional[Dict[str, Any]]
    """

    name: str
    size: int = 0x1000
    start_addr: Optional[int] = None
    alignment: Optional[int] = None
    pma_config: Optional[dict[str, Any]] = None
    page_size: Optional[PageSize] = None
    flags: Optional[PageFlags] = PageFlags.VALID | PageFlags.READ | PageFlags.WRITE | PageFlags.EXECUTE
    page_cross_en: bool = False
    num_pages: Optional[int] = 1
    and_mask: str = "0xffff_ffff_f000"  # Mask for generating memory addresses
    or_mask: str = "0x00000000"  # unused, will be implemented later
    modify: bool = False
    buffer_page: bool = True  #: Indicates that the memory isn't shared with other tests and memory after shouldn't be accessed. Adds a buffer page after the memory.

    def __post_init__(self):
        self.phys_name = f"{self.name}_phys"

    def emit(self) -> str:
        "Generate code needed to request memory allocation"
        code: list[str] = []

        page_flags = []
        if self.flags is not None:
            if PageFlags.VALID in self.flags:
                page_flags.append("v=1")
            else:
                page_flags.append("v=0")
            if PageFlags.READ in self.flags:
                page_flags.append("r=1")
            else:
                page_flags.append("r=0")
            if PageFlags.WRITE in self.flags:
                page_flags.append("w=1")
            else:
                page_flags.append("w=0")
            if PageFlags.EXECUTE in self.flags:
                page_flags.append("x=1")
            else:
                page_flags.append("x=0")
            if PageFlags.USER in self.flags:
                page_flags.append("u=1")
            if PageFlags.GLOBAL in self.flags:
                page_flags.append("g=1")
            if PageFlags.ACCESSED in self.flags:
                page_flags.append("a=1")
            if PageFlags.DIRTY in self.flags:
                page_flags.append("d=1")
        if self.modify:
            page_flags.append("modify_pt=1")
        page_flags_str = ", ".join(page_flags)

        # Generate multiple page mappings based on num_pages
        pages_to_generate = self.num_pages or 1

        # Get page size in bytes directly from enum
        page_size_bytes = self.page_size.value if self.page_size is not None else 0x1000

        # Calculate total memory requirements
        total_memory_needed = page_size_bytes * pages_to_generate

        # Validate that total memory requirements don't exceed physical memory size
        if total_memory_needed > self.size:
            raise ValueError(
                f"Total memory required ({total_memory_needed:#x} bytes) exceeds "
                f"allocated physical memory size ({self.size:#x} bytes). "
                f"Either reduce num_pages ({pages_to_generate}) or page_size ({page_size_bytes:#x}), "
                f"or increase the total size."
            )

        # Determine appropriate first page size based on total memory requirements
        if pages_to_generate > 1:
            if total_memory_needed > PageSize.SIZE_1G.value:  # > 1GB
                first_page_size_str = "1gb"
                first_page_size_bytes = PageSize.SIZE_1G.value
            elif total_memory_needed > PageSize.SIZE_2M.value:  # > 2MB
                first_page_size_str = "1gb"  # Use 1GB to cover more than 2MB
                first_page_size_bytes = PageSize.SIZE_1G.value
            elif total_memory_needed > PageSize.SIZE_4K.value:  # > 4KB
                first_page_size_str = "2mb"  # Use 2MB to cover multiple 4KB pages
                first_page_size_bytes = PageSize.SIZE_2M.value
            else:
                first_page_size_str = "4kb"
                first_page_size_bytes = PageSize.SIZE_4K.value
        else:
            # Single page - use configured page_size or default
            if self.page_size is not None:
                first_page_size_str = self.page_size.name.lower().replace("size_", "") + "b"
                first_page_size_bytes = page_size_bytes
            else:
                first_page_size_str = "4kb"
                first_page_size_bytes = PageSize.SIZE_4K.value

        # Choose alignment mask based on the computed first page size (after upsizing),
        # so the base VA/PA are aligned correctly for superpages.
        if first_page_size_bytes == PageSize.SIZE_256T.value:
            and_mask = "0xffff_0000_0000_0000"
        elif first_page_size_bytes == PageSize.SIZE_512G.value:
            and_mask = "0xffff_ff80_0000_0000"
        elif first_page_size_bytes == PageSize.SIZE_1G.value:
            and_mask = "0xffff_c000_0000"
        elif first_page_size_bytes == PageSize.SIZE_2M.value:
            and_mask = "0xff_ffe0_0000"
        elif self.modify:
            and_mask = "0xffff_ffff_ffff_f000"
        else:
            # default to 4KB alignment
            and_mask = "0xffff_ffff_f000"

        size = self.size
        if self.buffer_page:
            size += 0x1000  # just make the VM a bit larger than the actual memory, and don't map it
        if self.start_addr is None:
            code.append(f";#random_addr(name={self.name},  type=linear, size=0x{size:x}, and_mask={and_mask})")
            code.append(f";#random_addr(name={self.phys_name},  type=physical, size=0x{self.size:x}, and_mask={and_mask})")
        else:
            code.append(f";#reserve_memory(name={self.name}, start_addr=0x{self.start_addr:x},  type=linear, size=0x{size:x})")
            code.append(f";#reserve_memory(name={self.phys_name}, start_addr=0x{self.start_addr:x},  type=physical, size=0x{self.size:x})")

        for i in range(pages_to_generate):
            if i == 0:
                # First page uses base names and calculated page size
                lin_name = self.name
                phys_name = self.phys_name
                page_mapping_str = f"lin_name={lin_name}, phys_name={phys_name}"
                page_mapping_str += f", pagesize=['{first_page_size_str}']"

                if page_flags_str:
                    page_mapping_str += f", {page_flags_str}"
            else:
                # Subsequent pages use offset names and 4kb page size
                # Start subsequent pages after the first page size + 0x1000 for each page
                offset = i * 0x1000  # i=1 gives 0x200000 + 0x1000 = 0x201000
                lin_name = f"{self.name}+0x{offset:x}"
                phys_name = f"{self.phys_name}+0x{offset:x}"
                page_mapping_str = f"lin_name={lin_name}, phys_name={phys_name}"

                # Use 4kb page size for subsequent mappings
                page_mapping_str += ", pagesize=['4kb']"

                if page_flags_str:
                    page_mapping_str += f", {page_flags_str}"

            code.append(f";#page_mapping({page_mapping_str})")

        if self.alignment is not None:
            code.append(f".align {self.alignment}")

        code.append(f";#init_memory @{self.name}")
        return "\n".join(code)


@dataclass
class DataPage(Page):
    """
    Data structure for page of memory that includes data contents.

    This can be more strongly typed in the future, ie. data can be a list of bytes, strings, integers etc.
    For now this can be a simple placeholder assuming data as a list of strings.
    """

    data: list[str] = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()

    def emit(self) -> str:
        """
        Calls ``emit()`` from ``Page`` to generate header.

        Currently only supports data as a list of strings, all others will cause an error
        """
        return super().emit() + "\n" + "\n".join(d for d in self.data) + "\n"
