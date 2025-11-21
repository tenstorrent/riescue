# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Any, TYPE_CHECKING

from riescue.compliance.test_plan.types import Page, DataPage

if TYPE_CHECKING:
    from riescue.compliance.test_plan.actions import MemoryAction, CodePageAction


class MemoryRegistry:
    """
    Registry for memory labels and page information.

    Contains data and code pages.

    :param code: list of code page labels
    :param data: list of data page labels

    """

    def __init__(self, verbose: bool = True):
        self._data: dict[str, DataPage] = {}
        self._code: dict[str, Page] = {}
        self.verbose = verbose

    def allocate_data(self, name: str, data_memory: "MemoryAction") -> None:
        self._data[name] = DataPage(
            name=name,
            size=data_memory.size,
            page_size=data_memory.page_size,
            flags=data_memory.flags,
            exclude_flags=data_memory.exclude_flags,
            data=data_memory.data,
            num_pages=data_memory.num_pages,
            modify=data_memory.modify,
            or_mask=data_memory.or_mask,
        )

    def allocate_code(self, name: str, code_page: "CodePageAction") -> None:
        self._code[name] = Page(
            name=name,
            size=code_page.size,
            page_size=code_page.page_size,
            flags=code_page.flags,
        )

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
