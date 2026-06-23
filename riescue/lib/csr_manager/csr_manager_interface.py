# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from typing import Any, Optional

from riescue.lib.rand import RandNum
from riescue.lib.csr_manager.csr_manager_config import CsrManager, csr_asm_name


class CsrManagerInterface:
    _csr_config_path: Optional[Path] = None

    @classmethod
    def set_csr_config_path(cls, path: Optional[Path]) -> None:
        cls._csr_config_path = Path(path) if path is not None else None

    def __init__(self, rng: RandNum, feature_discovery=None):
        self._csr_manager = CsrManager(rng, feature_discovery=feature_discovery)
        if self._csr_config_path is not None:
            self._csr_manager.build(override_json_path=self._csr_config_path)
        else:
            self._csr_manager.build()

    @staticmethod
    def _asm_keyed(csr_dict: dict) -> dict:
        """Replace Custom CSR symbolic names with their hex addresses in returned dicts.
        GAS only knows standard RISC-V CSR names; custom ones must be numeric."""
        return {csr_asm_name(k, v): v for k, v in csr_dict.items()}

    def lookup_csrs(self, match: dict, exclude: dict = {}):
        return self._asm_keyed(self._csr_manager.lookup_csrs(match, exclude))

    def csr_access(
        self,
        instruction_helper: Any,
        access_type: str,
        csr_config: dict[str, Any],
        value: Optional[int] = None,
        imm: Optional[int] = None,
        rs: str = "",
        rd: str = "",
        subfield: Optional[dict[str, str]] = None,
        value_in_reg: bool = False,
    ) -> str:
        return self._csr_manager.csr_access(instruction_helper, access_type, csr_config, value, imm, rs, rd, subfield or {}, value_in_reg)

    def get_random_csr(self, match: dict, exclude: dict = {}):
        return self._asm_keyed(self._csr_manager.get_random_csr(match, exclude))

    def lookup_csr_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """
        Look up CSR config by name (case-insensitive).
        Returns {asm_name: CsrConfig} or None if not found.
        """
        name_upper = name.upper()
        name_lower = name.lower()
        for key, config in self._csr_manager.CSR_Reg.items():
            if key.upper() == name_upper or key.lower() == name_lower:
                return self._asm_keyed({key: config})
            cfg_name = config.config.get("name", "")
            if isinstance(cfg_name, str) and (cfg_name.upper() == name_upper or cfg_name.lower() == name_lower):
                return self._asm_keyed({key: config})
        return None

    def lookup_csr_by_address(self, addr: int) -> Optional[dict[str, Any]]:
        """
        Look up CSR config by 12-bit CSR address.
        Returns {csr_name: CsrConfig} or None if not found.
        """
        addr = addr & 0xFFF  # 12-bit CSR address
        for key, config in self._csr_manager.CSR_Reg.items():
            cfg_addr = config.config.get("address")
            if cfg_addr is None:
                continue
            if isinstance(cfg_addr, int):
                if (cfg_addr & 0xFFF) == addr:
                    return self._asm_keyed({key: config})
            else:
                try:
                    parsed = int(str(cfg_addr), 16)
                    if (parsed & 0xFFF) == addr:
                        return self._asm_keyed({key: config})
                except ValueError:
                    try:
                        parsed = int(str(cfg_addr), 10)
                        if (parsed & 0xFFF) == addr:
                            return self._asm_keyed({key: config})
                    except ValueError:
                        pass
        return None
