# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class Ssu64xlTest(BaseRiescueCTest):
    "Runs SSU64XL test plan"

    def test_cli(self):
        self.run_tp_mode(plan="ssu64xl", cli_args=["--test_priv_mode", "user", "--deleg_excp_to", "machine"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
