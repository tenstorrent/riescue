# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging

from riescue.compliance.config import Resource
from riescue.compliance.lib.config_parser import ConfigParser
from riescue.compliance.lib.config_manager import ConfigManager

log = logging.getLogger(__name__)


class InstrGenerator:

    def __init__(self, resource_db: Resource):
        """
        Constructor for instruction generator
        1. Wrapper class for Opcode Reader, Config Reader and Instruction Builders.
        2. Generates and randomizes instruction given configuration, rpt_cnt etc.
        3. Stores the generated instructions back to resource_db.
        """

        self.resource_db = resource_db
        self.config_parser = ConfigParser(self.resource_db)
        self.config_manager = ConfigManager(self.resource_db)
        self.max_instruction_lines = self.resource_db.max_instr_per_file
        self.rng = self.resource_db.rng

    def generate_and_update(self, instruction, lines_so_far):
        # Generate code
        instruction.randomize()
        instruction.pre_setup()
        lines_so_far += len(instruction.get_pre_setup())
        if lines_so_far > self.max_instruction_lines:
            return False, lines_so_far

        # Update the resource with the new instruction object
        self.resource_db.update_instrs(instruction.label, instruction)
        log.debug("Added " + instruction.label + " instruction to resource_db.instr_tracker")
        return True, lines_so_far

    def generate_instructions_with_config_and_repeat_combinations(self, instruction_classes):
        shuffled_instruction_classes = list(instruction_classes.values())
        self.rng.shuffle(shuffled_instruction_classes)  # Shuffle here so we don't bias towards particular instructions based on how we resolve queries from input json
        lines_so_far = 0
        remaining_instruction_objects = []

        # Generate all the unelaborated instruction objects one set of configurations at a time, but start generating
        # code and keeping track of the line count so we have a chance to bail early.
        for instruction_class in shuffled_instruction_classes:
            for rpt in range(self.resource_db.rpt_cnt):
                instruction_objects = self.config_manager.generate_instruction_objects(instruction_class, rpt)
                self.rng.shuffle(instruction_objects)  # Shuffle here so we dont bias towards the last configuration
                if len(instruction_objects) > 0:
                    remaining_instruction_objects.extend(instruction_objects)

            # Generate code for one of the configurations
            if len(remaining_instruction_objects) == 0:
                return
            instruction = remaining_instruction_objects.pop()
            status, lines_so_far = self.generate_and_update(instruction, lines_so_far)
            if not status:
                return

        # Shuffle so we are biasing towards clusters of configurations for the same instruction
        self.rng.shuffle(remaining_instruction_objects)

        # Iterate through as many of the remaining as we can, but no need to pop since we're not at risk of repeating ourselves.
        for instruction in remaining_instruction_objects:
            status, lines_so_far = self.generate_and_update(instruction, lines_so_far)
            if not status:
                return
