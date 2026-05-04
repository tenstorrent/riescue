# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class SdtrigIcountTest(BaseRiescueCTest):
    "Runs SDTRIG_ICOUNT test plan"

    def test_cli(self):
        self.run_tp_mode(plan="sdtrig_icount", cli_args=["--excp_hooks", "--save_restore_gprs", "--deleg_excp_to=machine"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
