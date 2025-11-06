# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from typing import Any, Optional

from riescue.compliance.lib.config_manager import ConfigManager
from riescue.compliance.lib.register_manager import RegisterManager
from riescue.compliance.lib.instr_setup.base import InstrSetup
from riescue.compliance.config import Resource


class InstrBase:
    """
    Base class for all RISC-V instructions
        Attributes:
            name  : instruction mnemonic
            srcs  : list of source operands (registers) (if any)
            dests : list of destination operands (registers) (if any)
            imms  : list of immediate fields (if any)
    """

    def __init__(self, resource_db: Resource, name: str, label=""):
        """Constructor for base instruction. Initializes name of instruction"""
        self._name = name
        self._srcs = []
        self._dests = []
        self._imms = []
        self._label = label
        if self._label == "":
            self._label = name + "_test"
        self._setup = InstrSetup(resource_db=resource_db)
        self._reg_manager = RegisterManager(resource_db=resource_db, name=self._label, config=None)
        self.resource_db = resource_db
        self.config = None
        self.test_config: dict[str, Any] = {}
        self.first_pass_snippet: list[str] = []  # Although this gets overwritten in the test generator, we initialize it as a list to show what type we expect.
        self.line_count_estimate = 0
        self.data_section: list[str] = []
        # self._rng           = self.resource_db.rng

    @property
    def name(self) -> str:
        """Getter for instruction name"""
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        """Setter for instruction name"""
        self._name = name

    @property
    def label(self) -> str:
        """Getter for instruction label"""
        return self._label

    @label.setter
    def label(self, label: str) -> None:
        """Setter for instruction label"""
        self._label = label

    @property
    def setup(self) -> InstrSetup:
        """Getter for the setup object"""
        return self._setup

    @setup.setter
    def setup(self, setup: InstrSetup) -> None:
        """Setter for the setup object"""
        self._setup = setup

    @property
    def srcs(self) -> list:
        """Returns list of available source operands (registers) else empty list"""
        return self._srcs

    @srcs.setter
    def srcs(self, srcs: list) -> None:
        """Setter for instruction sources"""
        self._srcs = srcs

    @property
    def dests(self) -> list:
        """Returns list of available destination operands (registers) else empty list"""
        return self._dests

    @dests.setter
    def dests(self, dests: list) -> None:
        """Setter for instruction destinations"""
        self._dests = dests

    @property
    def imms(self) -> list:
        """Returns list of available immediate fields else empty list"""
        return self._imms

    @imms.setter
    def imms(self, imms: list) -> None:
        """Setter for instruction immediates"""
        self._imms = imms

    @property
    def config_manager(self) -> ConfigManager:
        return self._config_manager

    @config_manager.setter
    def config_manager(self, config_manager):
        self._config_manager = config_manager

    @property
    def reg_manager(self) -> RegisterManager:
        return self._reg_manager

    @reg_manager.setter
    def reg_manager(self, reg_manager: RegisterManager) -> None:
        self._reg_manager = reg_manager

    @property
    def instr_type(self) -> str:
        return self.type

    @instr_type.setter
    def instr_type(self, instr_type: str) -> None:
        self.type = instr_type

    def get_pre_setup(self) -> list[str]:
        return self._setup.pre_setup_instrs

    def get_post_setup(self, modified_arch_state: Optional[list[str]]) -> list[str]:
        self._setup.post_setup(modified_arch_state, self)
        return self._setup.post_setup_instrs

    def pre_setup(self) -> None:
        self._setup.pre_setup(self)

    def post_setup(self, modified_arch_state: str, instr):
        return self._setup.post_setup(modified_arch_state, instr)

    # def get_operands(self):
    #     return self.operands

    def randomize(self) -> None:
        """
        Randomizes instruction operands sources (registers/immediate fields)
        and destination operands (registers)
        """
        # if self.type == "Vec":
        #    self._reg_manager.randomize_regs(self.config)

        for src in self._srcs:
            src.randomize()

        for dest in self._dests:
            dest.randomize()

        for imm in self._imms:
            imm.randomize()

    def __str__(self):
        return self._name
