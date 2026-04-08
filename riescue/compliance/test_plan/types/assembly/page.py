# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Any, Union
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
    page_size: Optional[Union[PageSize, tuple[PageSize, ...]]] = None
    flags: Optional[PageFlags] = PageFlags.VALID | PageFlags.READ | PageFlags.WRITE | PageFlags.EXECUTE
    exclude_flags: Optional[PageFlags] = None
    page_cross_en: bool = False
    num_pages: Optional[int] = 1
    and_mask: str = "0xffff_ffff_f000"  # Mask for generating memory addresses
    or_mask: str = ""
    modify: bool = False
    modify_leaf: bool = False
    modify_nonleaf: bool = False
    buffer_page: bool = True  #: Indicates that the memory isn't shared with other tests and memory after shouldn't be accessed. Adds a buffer page after the memory.

    # VS-stage non-leaf attributes
    nonleaf_flags: Optional[PageFlags] = None
    nonleaf_exclude_flags: Optional[PageFlags] = None

    # G-stage attributes: VS-leaf × G-leaf
    leaf_gleaf_flags: Optional[PageFlags] = None
    leaf_gleaf_exclude_flags: Optional[PageFlags] = None
    vleaf_page_size: Optional[Union[PageSize, tuple[PageSize, ...]]] = None

    # G-stage attributes: VS-nonleaf × G-leaf
    nonleaf_gleaf_flags: Optional[PageFlags] = None
    nonleaf_gleaf_exclude_flags: Optional[PageFlags] = None
    vnonleaf_page_size: Optional[Union[PageSize, tuple[PageSize, ...]]] = None

    # G-stage attributes: VS-leaf × G-nonleaf
    leaf_gnonleaf_flags: Optional[PageFlags] = None
    leaf_gnonleaf_exclude_flags: Optional[PageFlags] = None

    # G-stage attributes: VS-nonleaf × G-nonleaf
    nonleaf_gnonleaf_flags: Optional[PageFlags] = None
    nonleaf_gnonleaf_exclude_flags: Optional[PageFlags] = None

    def __post_init__(self):
        self.phys_name = f"{self.name}_phys"

    @staticmethod
    def _fmt_page_sizes(ps: Union[PageSize, tuple[PageSize, ...]]) -> str:
        """Format a single PageSize or tuple of PageSizes as a comma-joined quoted list, e.g. '4kb','2mb'"""
        sizes = ps if isinstance(ps, tuple) else (ps,)
        return ",".join(f"'{p.name.lower().replace('size_', '')}b'" for p in sizes)

    def _generate_gstage_flags(self, gflags: Optional[PageFlags], gexclude_flags: Optional[PageFlags], vs_level: str, g_level: str) -> str:
        """
        Generate g-stage flag attributes for page_mapping directive.

        :param gflags: G-stage flags to set
        :param gexclude_flags: G-stage flags to exclude/clear
        :param vs_level: "leaf" or "nonleaf" for VS-stage
        :param g_level: "gleaf" or "gnonleaf" for G-stage
        """
        if gflags is None and gexclude_flags is None:
            return ""

        flags_dict = {}
        if gflags is not None:
            if PageFlags.VALID in gflags:
                flags_dict[f"v_{vs_level}_{g_level}"] = 1
            if PageFlags.READ in gflags:
                flags_dict[f"r_{vs_level}_{g_level}"] = 1
            if PageFlags.WRITE in gflags:
                flags_dict[f"w_{vs_level}_{g_level}"] = 1
            if PageFlags.EXECUTE in gflags:
                flags_dict[f"x_{vs_level}_{g_level}"] = 1
            if PageFlags.USER in gflags:
                flags_dict[f"u_{vs_level}_{g_level}"] = 1
            if PageFlags.GLOBAL in gflags:
                flags_dict[f"g_{vs_level}_{g_level}"] = 1
            if PageFlags.ACCESSED in gflags:
                flags_dict[f"a_{vs_level}_{g_level}"] = 1
            if PageFlags.DIRTY in gflags:
                flags_dict[f"d_{vs_level}_{g_level}"] = 1

        if gexclude_flags is not None:
            if PageFlags.VALID in gexclude_flags:
                flags_dict[f"v_{vs_level}_{g_level}"] = 0
            if PageFlags.READ in gexclude_flags:
                flags_dict[f"r_{vs_level}_{g_level}"] = 0
            if PageFlags.WRITE in gexclude_flags:
                flags_dict[f"w_{vs_level}_{g_level}"] = 0
            if PageFlags.EXECUTE in gexclude_flags:
                flags_dict[f"x_{vs_level}_{g_level}"] = 0
            if PageFlags.USER in gexclude_flags:
                flags_dict[f"u_{vs_level}_{g_level}"] = 0
            if PageFlags.GLOBAL in gexclude_flags:
                flags_dict[f"g_{vs_level}_{g_level}"] = 0
            if PageFlags.ACCESSED in gexclude_flags:
                flags_dict[f"a_{vs_level}_{g_level}"] = 0
            if PageFlags.DIRTY in gexclude_flags:
                flags_dict[f"d_{vs_level}_{g_level}"] = 0

        return ", ".join(f"{k}={v}" for k, v in flags_dict.items())

    def _generate_vs_nonleaf_flags(self, nonleaf_flags: Optional[PageFlags], nonleaf_exclude_flags: Optional[PageFlags]) -> str:
        """
        Generate VS-stage non-leaf flag attributes for page_mapping directive.

        :param nonleaf_flags: VS-stage non-leaf flags to set
        :param nonleaf_exclude_flags: VS-stage non-leaf flags to exclude/clear
        """
        if nonleaf_flags is None and nonleaf_exclude_flags is None:
            return ""

        flags_dict = {}
        if nonleaf_flags is not None:
            if PageFlags.VALID in nonleaf_flags:
                flags_dict["v_nonleaf"] = 1
            if PageFlags.READ in nonleaf_flags:
                flags_dict["r_nonleaf"] = 1
            if PageFlags.WRITE in nonleaf_flags:
                flags_dict["w_nonleaf"] = 1
            if PageFlags.EXECUTE in nonleaf_flags:
                flags_dict["x_nonleaf"] = 1
            if PageFlags.USER in nonleaf_flags:
                flags_dict["u_nonleaf"] = 1
            if PageFlags.GLOBAL in nonleaf_flags:
                flags_dict["g_nonleaf"] = 1
            if PageFlags.ACCESSED in nonleaf_flags:
                flags_dict["a_nonleaf"] = 1
            if PageFlags.DIRTY in nonleaf_flags:
                flags_dict["d_nonleaf"] = 1

        if nonleaf_exclude_flags is not None:
            if PageFlags.VALID in nonleaf_exclude_flags:
                flags_dict["v_nonleaf"] = 0
            if PageFlags.READ in nonleaf_exclude_flags:
                flags_dict["r_nonleaf"] = 0
            if PageFlags.WRITE in nonleaf_exclude_flags:
                flags_dict["w_nonleaf"] = 0
            if PageFlags.EXECUTE in nonleaf_exclude_flags:
                flags_dict["x_nonleaf"] = 0
            if PageFlags.USER in nonleaf_exclude_flags:
                flags_dict["u_nonleaf"] = 0
            if PageFlags.GLOBAL in nonleaf_exclude_flags:
                flags_dict["g_nonleaf"] = 0
            if PageFlags.ACCESSED in nonleaf_exclude_flags:
                flags_dict["a_nonleaf"] = 0
            if PageFlags.DIRTY in nonleaf_exclude_flags:
                flags_dict["d_nonleaf"] = 0

        return ", ".join(f"{k}={v}" for k, v in flags_dict.items())

    def emit(self) -> str:
        "Generate code needed to request memory allocation"
        code: list[str] = []

        page_flags = []
        if self.flags is not None:
            # these flags are always set or unset
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
        if self.exclude_flags is not None:
            if PageFlags.VALID in self.exclude_flags:
                page_flags.append("v=0")
            if PageFlags.READ in self.exclude_flags:
                page_flags.append("r=0")
            if PageFlags.WRITE in self.exclude_flags:
                page_flags.append("w=0")
            if PageFlags.EXECUTE in self.exclude_flags:
                page_flags.append("x=0")
            if PageFlags.USER in self.exclude_flags:
                page_flags.append("u=0")
            if PageFlags.GLOBAL in self.exclude_flags:
                page_flags.append("g=0")
            if PageFlags.ACCESSED in self.exclude_flags:
                page_flags.append("a=0")
            if PageFlags.DIRTY in self.exclude_flags:
                page_flags.append("d=0")
        if self.modify:
            page_flags.append("modify_pt=1")
        if self.modify_leaf:
            page_flags.append("modify_leaf_pt=1")
        if self.modify_nonleaf:
            page_flags.append("modify_nonleaf_pt=1")
        page_flags_str = ", ".join(page_flags)

        # Generate multiple page mappings based on num_pages
        pages_to_generate = self.num_pages or 1

        # Normalize page_size to a tuple so single and multi-size cases are handled uniformly
        # Use SIZE_4K as the default if page_size is None
        if self.page_size is None:
            page_sizes: tuple[PageSize, ...] = (PageSize.SIZE_4K,)
        elif isinstance(self.page_size, tuple):
            page_sizes = self.page_size
        else:
            page_sizes = (self.page_size,)
        page_size_bytes = min(ps.value for ps in page_sizes)

        # Calculate total memory requirements; if size is unspecified, derive it
        total_memory_needed = page_size_bytes * pages_to_generate
        size = self.size if self.size is not None else total_memory_needed

        # Determine appropriate first page size based on total memory requirements
        if pages_to_generate > 1:
            if total_memory_needed > PageSize.SIZE_1G.value:  # > 1GB
                first_page_size_str = "'1gb'"
                first_page_size_bytes = PageSize.SIZE_1G.value
            elif total_memory_needed > PageSize.SIZE_2M.value:  # > 2MB
                first_page_size_str = "'1gb'"  # Use 1GB to cover more than 2MB
                first_page_size_bytes = PageSize.SIZE_1G.value
            elif total_memory_needed > PageSize.SIZE_4K.value:  # > 4KB
                first_page_size_str = "'2mb'"  # Use 2MB to cover multiple 4KB pages
                first_page_size_bytes = PageSize.SIZE_2M.value
            else:
                first_page_size_str = "'4kb'"
                first_page_size_bytes = PageSize.SIZE_4K.value
        else:
            # Single page - format all sizes as pre-quoted, comma-joined list elements
            first_page_size_str = self._fmt_page_sizes(page_sizes)
            first_page_size_bytes = page_size_bytes

        # Alignment mask: all 1s except the low bits of first_page_size_bytes,
        # so the base VA/PA are aligned correctly for superpages across the full 64-bit space.
        and_mask = f"0x{(~(first_page_size_bytes - 1)) & 0xFFFF_FFFF_FFFF_FFFF:016x}"

        or_mask = self.or_mask

        lin_size = size + 0x1000 if self.buffer_page else size  # just make the VM a bit larger than the actual memory, and don't map it
        if self.start_addr is None:
            lin = f";#random_addr(name={self.name},  type=linear, size=0x{lin_size:x}, and_mask={and_mask}"
            phys = f";#random_addr(name={self.phys_name},  type=physical, size=0x{size:x}, and_mask={and_mask}"
            if or_mask:
                lin += f", or_mask={or_mask}"
                phys += f", or_mask={or_mask}"
            # code.append(f";#random_addr(name={self.name},  type=linear, size=0x{size:x}, and_mask={and_mask}")
            # code.append(f";#random_addr(name={self.phys_name},  type=physical, size=0x{self.size:x}, and_mask={and_mask}, or_mask={or_mask})")
        else:
            lin = f";#reserve_memory(name={self.name}, start_addr=0x{self.start_addr:x},  type=linear, size=0x{lin_size:x}"
            phys = f";#reserve_memory(name={self.phys_name}, start_addr=0x{self.start_addr:x},  type=physical, size=0x{size:x}"
            if or_mask:
                lin += f", or_mask={or_mask}"
                phys += f", or_mask={or_mask}"

            # code.append(f";#reserve_memory(name={self.name}, start_addr=0x{self.start_addr:x},  type=linear, size=0x{size:x}, or_mask={or_mask})")
            # code.append(f";#reserve_memory(name={self.phys_name}, start_addr=0x{self.start_addr:x},  type=physical, size=0x{self.size:x}, or_mask={or_mask})")
        code.append(lin + ")")
        code.append(phys + ")")

        for i in range(pages_to_generate):
            if i == 0:
                # First page uses base names and calculated page size
                lin_name = self.name
                phys_name = self.phys_name
                page_mapping_str = f"lin_name={lin_name}, phys_name={phys_name}"
                page_mapping_str += f", pagesize=[{first_page_size_str}]"

                if page_flags_str:
                    page_mapping_str += f", {page_flags_str}"

                # Add VS-stage non-leaf flags
                vs_nonleaf_flags_str = self._generate_vs_nonleaf_flags(self.nonleaf_flags, self.nonleaf_exclude_flags)
                if vs_nonleaf_flags_str:
                    page_mapping_str += f", {vs_nonleaf_flags_str}"

                # Add g-stage page sizes if provided
                if self.vleaf_page_size is not None:
                    page_mapping_str += f", gstage_vs_leaf_pagesize=[{self._fmt_page_sizes(self.vleaf_page_size)}]"
                if self.vnonleaf_page_size is not None:
                    page_mapping_str += f", gstage_vs_nonleaf_pagesize=[{self._fmt_page_sizes(self.vnonleaf_page_size)}]"

                # Generate g-stage flag strings for each combination independently
                leaf_gleaf_flags = self._generate_gstage_flags(self.leaf_gleaf_flags, self.leaf_gleaf_exclude_flags, "leaf", "gleaf")
                nonleaf_gleaf_flags = self._generate_gstage_flags(self.nonleaf_gleaf_flags, self.nonleaf_gleaf_exclude_flags, "nonleaf", "gleaf")
                leaf_gnonleaf_flags = self._generate_gstage_flags(self.leaf_gnonleaf_flags, self.leaf_gnonleaf_exclude_flags, "leaf", "gnonleaf")
                nonleaf_gnonleaf_flags = self._generate_gstage_flags(self.nonleaf_gnonleaf_flags, self.nonleaf_gnonleaf_exclude_flags, "nonleaf", "gnonleaf")

                # Append g-stage flags to page_mapping_str
                if leaf_gleaf_flags:
                    page_mapping_str += f", {leaf_gleaf_flags}"
                if nonleaf_gleaf_flags:
                    page_mapping_str += f", {nonleaf_gleaf_flags}"
                if leaf_gnonleaf_flags:
                    page_mapping_str += f", {leaf_gnonleaf_flags}"
                if nonleaf_gnonleaf_flags:
                    page_mapping_str += f", {nonleaf_gnonleaf_flags}"
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
