# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class SmstateenSsstateenTest(BaseRiescueCTest):
    "Runs SMSTATEEN/SSSTATEEN test plan"

    def test_cli(self):
        self.run_tp_mode(
            plan="smstateen_ssstateen", cli_args=["--deleg_excp_to", "machine", "--whisper_config_json", "external/riescue_repo/riescue/dtest_framework/lib/whisper_TP_ONLY_stateen_config.json"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
