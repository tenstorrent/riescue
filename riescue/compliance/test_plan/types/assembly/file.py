# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from .base import AssemblyBase
from .header import Header
from .segment import DataSegment, TextSegment


class AssemblyFile(AssemblyBase):
    """
    Assembly file of a generated assembly test. Contains a header and body
    """

    def __init__(self, header: Header, data: DataSegment, code: TextSegment):
        self.header = header
        self.data = data
        self.code = code

    def emit(self) -> str:
        return self.header.emit() + "\n\n" + self.code.emit() + "\n\n" + self.data.emit()
