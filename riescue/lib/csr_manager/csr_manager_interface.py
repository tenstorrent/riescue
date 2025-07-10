# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.lib.rand import RandNum
from riescue.lib.csr_manager.csr_manager_config import CsrManager


class CsrManagerInterface:
    def __init__(self, rng: RandNum):
        self._csr_manager = CsrManager(rng)
        self._csr_manager.build()

    def lookup_csrs(self, match: dict, exclude: dict = {}):
        return self._csr_manager.lookup_csrs(match, exclude)

    def csr_access(self, instruction_helper, access_type, csr_config, value=None, imm=None, rs="", rd="", subfield: dict = {}):
        return self._csr_manager.csr_access(instruction_helper, access_type, csr_config, value, imm, rs, rd, subfield)

    def get_random_csr(self, match: dict, exclude: dict = {}):
        return self._csr_manager.get_random_csr(match, exclude)
