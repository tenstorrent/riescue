# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescuec.base import BaseRiescueCTest


class HypervisorTest(BaseRiescueCTest):
    "Runs Hypervisor test plan"

    def test_cli_m_bare_metal(self):
        cli_args = ["--test_priv_mode", "machine"]
        self.run_tp_mode(plan="zicond", cli_args=cli_args)

    def test_cli_s_bare_metal(self):
        cli_args = ["--test_priv_mode", "super"]
        self.run_tp_mode(plan="zicond", cli_args=cli_args)

    def test_cli_u_bare_metal(self):
        cli_args = ["--test_priv_mode", "user"]
        self.run_tp_mode(plan="zicond", cli_args=cli_args)

    def test_cli_s_virtualized(self):
        cli_args = ["--test_priv_mode", "super", "--test_env", "virtualized"]
        self.run_tp_mode(plan="zicond", cli_args=cli_args)

    def test_cli_u_virtualized(self):
        cli_args = ["--test_priv_mode", "user", "--test_env", "virtualized"]
        self.run_tp_mode(plan="zicond", cli_args=cli_args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
