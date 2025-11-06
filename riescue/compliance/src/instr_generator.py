# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

import logging
from typing import Sequence

from riescue.compliance.config import Resource
from riescue.compliance.lib.config_parser import ConfigParser
from riescue.compliance.lib.config_manager import ConfigManager
from riescue.compliance.lib.riscv_instrs.base import InstrBase

log = logging.getLogger(__name__)


class InstrGenerator:
    """
    Orchestrator class that generates instructions assembly code for all instructions.
    1. Wrapper class for Opcode Reader, Config Reader and Instruction Builders.
    2. Generates and randomizes instruction given configuration, rpt_cnt etc.

    :param resource_db: Resource configuration database containing test parameters
    """

    def __init__(self, resource_db: Resource):
        self._config_parser = ConfigParser(resource_db)  #: Config parser handles user and default configuration files
        self._config_manager = ConfigManager(resource_db)
        self._max_instruction_lines = resource_db.max_instr_per_file
        self._lines_so_far = 0  #: Lines of assembly generated. Reset when :py:meth:`generate_instructions_assembly` is called.
        self._instr_instances: dict[str, InstrBase] = {}  #: Dictionary of instruction instances by label
        self._repeat_count = resource_db.rpt_cnt
        self.rng = resource_db.rng

    def generate_instructions(self, instruction_classes: Sequence[type[InstrBase]]) -> dict[str, InstrBase]:
        """
        Instantiates ``InstrBase`` objects with Configurations for ``Resource.rpt_cnt`` times.
        Calls :py:meth:`InstrBase.randomize` and :py:meth:`InstrBase.pre_setup` for each instruction, generating intial assembly.

        Shuffles instructions to avoid bias: generates one instruction per type first, then remaining instructions in shuffled order.
        Bails early if line limits exceeded.

        :param instruction_classes: Dictionary mapping instruction names to instruction classes
        """

        self._lines_so_far = 0
        self._instr_instances: dict[str, InstrBase] = {}
        shuffled_instruction_classes: list[type[InstrBase]] = list(instruction_classes)

        self.rng.shuffle(shuffled_instruction_classes)  # Shuffle
        remaining_instruction_objects: list[InstrBase] = []

        # Generate all the unelaborated instruction objects one set of configurations at a time, but start generating
        # code and keeping track of the line count so we have a chance to bail early.
        for instruction_class in shuffled_instruction_classes:
            for rpt in range(self._repeat_count):
                instruction_objects = self._config_manager.generate_instruction_objects(instruction_class, rpt)
                self.rng.shuffle(instruction_objects)  # Shuffle here so we dont bias towards the last configuration
                if len(instruction_objects) > 0:
                    remaining_instruction_objects.extend(instruction_objects)

            # Generate code for one of the configurations
            if len(remaining_instruction_objects) == 0:
                return self._instr_instances
            instruction = remaining_instruction_objects.pop()
            status = self._generate_and_update(instruction)
            if not status:
                return self._instr_instances

        # Shuffle so we are biasing towards clusters of configurations for the same instruction
        self.rng.shuffle(remaining_instruction_objects)

        # Iterate through as many of the remaining as we can, but no need to pop since we're not at risk of repeating ourselves.
        for instruction in remaining_instruction_objects:
            status = self._generate_and_update(instruction)
            if not status:
                return self._instr_instances
        return self._instr_instances

    def _generate_and_update(self, instruction: InstrBase):
        """
        Randomizes and sets up instruction classes.

        If the line count exceeds the limit, returns False.
        If successful, returns True and updates the instruction instance dictionary.
        """
        # Generate code
        instruction.randomize()
        instruction.pre_setup()
        self._lines_so_far += len(instruction.get_pre_setup())
        if self._lines_so_far > self._max_instruction_lines:
            return False
        self._instr_instances[instruction.label] = instruction
        log.debug("Added " + instruction.label + " instruction to instr_instances")
        return True
