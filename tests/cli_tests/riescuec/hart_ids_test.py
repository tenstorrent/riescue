# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""CLI test for RiescueC --hart_ids (discontiguous hart IDs), simulated on Whisper.

Both compliance passes run on Whisper (RiescueC defaults the first pass to Spike), which
presents the discontiguous mhartid values {0, 2} via the committed
``whisper_config_discontiguous.json`` topology (cores=2, harts=1, core_hart_id_offset=2).
"""

import unittest

from riescue.riescuec import RiescueC


class RiescueCHartIdsTest(unittest.TestCase):
    def test_discontiguous_hart_ids_runs_on_whisper(self):
        args = (
            "--mode bringup --json compliance/tests/rv_a/rv64a.json "
            "--max_instrs 5000 --rpt_cnt 1 --seed 1 "
            "--num_cpus 2 --hart_ids 0,2 "
            "--first_pass_iss whisper --second_pass_iss whisper "
            "--whisper_config_json dtest_framework/lib/whisper_config_discontiguous.json"
        )
        # run_cli raises ToolchainError if either ISS pass fails; returning cleanly means both passed.
        RiescueC.run_cli(args=args.split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
