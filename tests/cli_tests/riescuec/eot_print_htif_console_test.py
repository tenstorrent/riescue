# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.riescuec import RiescueC


class EotPrintHtifConsoleTest(unittest.TestCase):
    """Verify that --eot_print_htif_console and --print_rvcp_passes generate without error
    for Riescue-C compliance tests."""

    def test_eot_print_htif_console(self):
        """RVCP FAIL lines are injected before ;#test_failed() when --eot_print_htif_console is set."""
        args = "--json compliance/tests/rv_i/rv64i.json " "--rpt_cnt 1 --seed 0 " "--eot_print_htif_console"
        RiescueC.run_cli(args=args.split())

    def test_print_rvcp_passes(self):
        """RVCP PASSED lines are injected before ;#test_passed() when --print_rvcp_passes is set."""
        args = "--json compliance/tests/rv_i/rv64i.json " "--rpt_cnt 1 --seed 0 " "--print_rvcp_passes"
        RiescueC.run_cli(args=args.split())

    def test_both_flags(self):
        """Both RVCP PASSED and FAIL lines are injected when both flags are set."""
        args = "--json compliance/tests/rv_i/rv64i.json " "--rpt_cnt 1 --seed 0 " "--eot_print_htif_console --print_rvcp_passes"
        RiescueC.run_cli(args=args.split())

    def test_combined_compliance_tests_with_htif(self):
        """HTIF output is correctly associated with test1 when --cct (combine_compliance_tests) is set."""
        args = "--json compliance/tests/rv_i/rv64i.json " "--rpt_cnt 1 --seed 0 -cct 1 " "--eot_print_htif_console --print_rvcp_passes"
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
