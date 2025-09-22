# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging

from riescue.compliance.config import Resource

log = logging.getLogger(__name__)


class InstrOrganizer:

    def __init__(self, resource_db: Resource):
        self.resource_db = resource_db
        self.rng = self.resource_db.rng

    def shuffle_instrs(self):
        log.info("Shuffling instructions...")
        instrs = self.resource_db.instr_tracker
        x = list(instrs.items())
        self.rng.shuffle(x)
        print(dict(x).keys())
        log.info(f"Instr_tracker post shuffle: {dict(x).keys()}")
        self.resource_db.instr_tracker = dict(x)
