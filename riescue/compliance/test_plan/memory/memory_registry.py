# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any, TYPE_CHECKING, Optional

from coretp.rv_enums import PmpAttribute

from riescue.compliance.test_plan.types import Page, DataPage
from riescue.dtest_framework.config.memory import Memory as MemoryMap
from riescue.dtest_framework.config.memory import DramRange
from riescue.compliance.config import TpCfg
import riescue.lib.enums as RV

if TYPE_CHECKING:
    from riescue.compliance.test_plan.actions import MemoryAction, CodePageAction, RequestPmpAction

log = logging.getLogger(__name__)


class MemoryRegistry:
    """
    Registry for memory labels and page information.

    Contains data and code pages.

    :param code: list of code page labels
    :param data: list of data page labels

    """

    def __init__(self, cfg: Optional[TpCfg] = None, verbose: bool = True):
        self._data: dict[str, DataPage] = {}
        self._code: dict[str, Page] = {}
        self.verbose = verbose

        if cfg is None:
            self.cfg = TpCfg()
        else:
            self.cfg = cfg

    def allocate_data(self, name: str, data_memory: "MemoryAction", **kwargs: Any) -> None:
        self._data[name] = DataPage(
            name=name,
            size=data_memory.size,
            page_size=data_memory.page_size,
            flags=data_memory.flags,
            exclude_flags=data_memory.exclude_flags,
            data=data_memory.data,
            num_pages=data_memory.num_pages,
            modify=data_memory.modify,
            modify_leaf=data_memory.modify_leaf,
            modify_nonleaf=data_memory.modify_nonleaf,
            or_mask=data_memory.or_mask,
            # Pass inline g-stage and nonleaf fields
            nonleaf_flags=data_memory.nonleaf_flags,
            nonleaf_exclude_flags=data_memory.nonleaf_exclude_flags,
            leaf_gleaf_flags=data_memory.leaf_gleaf_flags,
            leaf_gleaf_exclude_flags=data_memory.leaf_gleaf_exclude_flags,
            vleaf_page_size=data_memory.vleaf_page_size,
            nonleaf_gleaf_flags=data_memory.nonleaf_gleaf_flags,
            nonleaf_gleaf_exclude_flags=data_memory.nonleaf_gleaf_exclude_flags,
            vnonleaf_page_size=data_memory.vnonleaf_page_size,
            leaf_gnonleaf_flags=data_memory.leaf_gnonleaf_flags,
            leaf_gnonleaf_exclude_flags=data_memory.leaf_gnonleaf_exclude_flags,
            nonleaf_gnonleaf_flags=data_memory.nonleaf_gnonleaf_flags,
            nonleaf_gnonleaf_exclude_flags=data_memory.nonleaf_gnonleaf_exclude_flags,
            **kwargs,
        )

    def allocate_code(self, name: str, code_page: "CodePageAction") -> None:
        self._code[name] = Page(
            name=name,
            size=code_page.size,
            page_size=code_page.page_size,
            flags=code_page.flags,
            exclude_flags=code_page.exclude_flags,
            num_pages=code_page.num_pages,
            modify=code_page.modify,
            modify_leaf=code_page.modify_leaf,
            modify_nonleaf=code_page.modify_nonleaf,
            or_mask=code_page.or_mask,
            # Pass inline g-stage and nonleaf fields
            nonleaf_flags=code_page.nonleaf_flags,
            nonleaf_exclude_flags=code_page.nonleaf_exclude_flags,
            leaf_gleaf_flags=code_page.leaf_gleaf_flags,
            leaf_gleaf_exclude_flags=code_page.leaf_gleaf_exclude_flags,
            vleaf_page_size=code_page.vleaf_page_size,
            nonleaf_gleaf_flags=code_page.nonleaf_gleaf_flags,
            nonleaf_gleaf_exclude_flags=code_page.nonleaf_gleaf_exclude_flags,
            vnonleaf_page_size=code_page.vnonleaf_page_size,
            leaf_gnonleaf_flags=code_page.leaf_gnonleaf_flags,
            leaf_gnonleaf_exclude_flags=code_page.leaf_gnonleaf_exclude_flags,
            nonleaf_gnonleaf_flags=code_page.nonleaf_gnonleaf_flags,
            nonleaf_gnonleaf_exclude_flags=code_page.nonleaf_gnonleaf_exclude_flags,
        )

    def request_data(self, name: str, request_memory: "MemoryAction") -> None:
        """
        Requests a data section at build time.

        If region can not be requested or allocated, no region is allocated in memory map
        """

        # assuming pmp is only use for request right now. Future work should be arbitrary requests

        if isinstance(request_memory, RequestPmpAction):
            requested_memory = self._request_pmp_region(name, request_memory)
        else:
            raise ValueError(f"Unsupported memory action type: {type(request_memory)}")

        if requested_memory is None:
            log.warning(f"Failed to request memory {name} {type(request_memory)}")
            return
        and_mask = (1 << (requested_memory.end ^ requested_memory.start).bit_length()) - 1
        self.allocate_data(name, request_memory, and_mask=f"0x{and_mask:x}")

    def _request_pmp_region(self, name: str, request_memory: "RequestPmpAction") -> Optional[DramRange]:
        "Checks if a PMP region can be requested. If not, attempts to split a configurable DRAM region"

        # check if a region matches the attributes of the request; cast to Rv.PmpAttribute
        requested_region = request_memory.pmp_attributes
        if requested_region == PmpAttribute.READ:
            rv_pma_attr = RV.PmpAttributes.R
        elif requested_region == (PmpAttribute.READ | PmpAttribute.WRITE):
            rv_pma_attr = RV.PmpAttributes.R_W
        elif requested_region == (PmpAttribute.READ | PmpAttribute.EXECUTE):
            rv_pma_attr = RV.PmpAttributes.R_X
        elif requested_region == (PmpAttribute.READ | PmpAttribute.WRITE | PmpAttribute.EXECUTE):
            rv_pma_attr = RV.PmpAttributes.R_W_X
        else:
            raise ValueError(f"Unsupported PMP attributes: {requested_region}")

        memory_map = self.cfg.featmgr.memory
        for dram_range in memory_map.dram_ranges:
            if dram_range.permissions == rv_pma_attr:
                return dram_range

        # if no region matches the attributes, try to split a configurable region
        new_drams: list[DramRange] = []
        new_dram: Optional[DramRange] = None
        found_configurable = False

        for dram_range in memory_map.dram_ranges:
            if dram_range.configurable:
                dram0, new_dram = dram_range.split(request_memory.size // 2)  # split in half to get requested data.
                found_configurable = True
                new_dram.permissions = rv_pma_attr
                memory_map.dram_ranges.append(dram0)
                memory_map.dram_ranges.append(new_dram)
                break
            else:
                new_drams.append(dram_range)
        if found_configurable:
            # create a new Memory object (since frozen) with the requested attributes
            new_memory_map = MemoryMap(
                dram_ranges=new_drams,
                io_ranges=memory_map.io_ranges,
                secure_ranges=memory_map.secure_ranges,
                reserved_ranges=memory_map.reserved_ranges,
            )
            self.cfg.featmgr.memory = new_memory_map
            return new_dram
        return None

    def request_successful(self, name: str) -> bool:
        "Checks if a request was successful."
        return name in self._data

    @property
    def data(self) -> list[DataPage]:
        return list(self._data.values())

    @property
    def code(self) -> list[Page]:
        return list(self._code.values())

    def get_data_page(self, name: str) -> DataPage:
        if name not in self._data:
            raise IndexError(f"No data page with label {name} found in registry")
        return self._data[name]

    def get_code_page(self, name: str) -> Page:
        if name not in self._code:
            raise IndexError(f"No code page with label {name} found in registry")
        return self._code[name]

    def is_memory_label(self, name: str) -> bool:
        return name in self._data or name in self._code
