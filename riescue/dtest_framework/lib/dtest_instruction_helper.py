# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Minimal instruction helper for CsrManager.csr_access in Riescue-D.

CsrManager requires an instruction_helper with get_random_gpr_reserve and unreserve_regs.
This adapter provides fixed registers (t2, t3) per ;#csr_rw convention.
"""

from typing import List, Optional


class DtestInstructionHelper:
    """Minimal instruction helper for CsrManager.csr_access in Riescue-D."""

    _REGS = ["t2", "t3", "t4"]  # t2=value/result, t3/t4=scratch

    def __init__(self, exclude: Optional[List[str]] = None) -> None:
        self._reserved: list[str] = []
        self._exclude = exclude or []

    def get_random_gpr_reserve(self, kind: str) -> str:
        available = [r for r in self._REGS if r not in self._exclude and r not in self._reserved]
        if not available:
            raise RuntimeError(f"No GPR available; reserved={self._reserved}, exclude={self._exclude}")
        r = available[0]
        self._reserved.append(r)
        return r

    def unreserve_regs(self, regs: List[str]) -> None:
        self._reserved.clear()
