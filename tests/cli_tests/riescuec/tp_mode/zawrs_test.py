# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest
from pathlib import Path

from tests.cli_tests.riescuec.base import BaseRiescueCTest

# Path to the implementation-specific custom mapping conf
# Note that custom_mapping_conf will have to be filled out with implementation specific behaviour (ie, wait timeouts, pma, etc.)
CUSTOM_MAPPING_CONF = Path(__file__).resolve().parents[4] / "riescue/compliance/test_plan/implementation_specific/custom_mapping.py"
WHISPER_CONFIG_JSON = "fill_any_private_csr_config_here"


class ZawrsTest(BaseRiescueCTest):
    "Runs ZAWRS (Wait on Reservation Set) test plan"

    def test_cli(self):
        self.run_tp_mode(plan="zawrs", cli_args=["--deleg_excp_to", "machine", "--conf", str(CUSTOM_MAPPING_CONF), "--whisper_config_json", str(WHISPER_CONFIG_JSON)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
