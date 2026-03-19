# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
"""
Unit tests for the Zicsr test plan.

Tests CsrDirectAccessAction which handles CsrDirectAccess TestStep for direct
CSR instruction access. Covers:
- csrrw, csrrs, csrrc (register-based operations)
- csrrwi, csrrsi, csrrci (immediate-based operations)
- CSR randomization based on privilege mode
- Various src1 configurations (immediate, step dependency, x0)
"""
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class ZicsrTest(BaseRiescueCTest):
    """
    Runs ZICSR test plan.

    Tests CsrDirectAccessAction with various CSR operations:
    - Register-based: csrrw, csrrs, csrrc
    - Immediate-based: csrrwi, csrrsi, csrrci
    """

    def test_zicsr_tp(self):
        """Run the full zicsr test plan."""
        self.run_tp_mode(plan="zicsr")


if __name__ == "__main__":
    unittest.main(verbosity=2)
