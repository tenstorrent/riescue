# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Counter logic support for Riescue. Used in multiple places
"""


import json
from pathlib import Path

from riescue.lib.rand import RandNum


class Counters:
    def __init__(self, rng: RandNum):
        self.rng = rng

    """
        init_regs randomizes and programs the relevant CSRs for enabling counting in tests
    """

    def init_regs(self, event_path: Path) -> str:
        init_code = ""

        json_data = {}
        if not event_path.exists():
            raise FileNotFoundError(f"Event path {event_path} does not exist")
        with open(event_path, "r") as f:
            json_data = json.load(f)

        for i in range(3, 32):
            # pick a random event
            event = self.rng.choice(list(json_data.keys()))
            eventID = json_data[event]["event_id"]
            csr = f"mhpmcounter{i}"
            init_code += f"            # For {csr}, event {event} chosen with eventID {eventID}\n"
            init_code += f"            li t0, {eventID}\n"
            init_code += f"            csrw {csr}, t0\n"

        # inhibit values
        mcountinhibit = 0
        for i in range(32):
            # 10% chance of being inhibitied
            if self.rng.with_probability_of(10):
                mcountinhibit |= 1 << i

        mcountinhibit &= 0xFFFFFFFD  # bit 1 must be 0
        init_code += f"            li t0, {mcountinhibit}\n"
        init_code += "            csrw mcountinhibit, t0\n"

        return init_code
