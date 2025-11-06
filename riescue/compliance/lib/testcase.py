# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from riescue.compliance.config import Resource
from riescue.compliance.lib.riscv_instrs import InstrBase


class TestCase:
    """
    Class for tracking the contents of a testcase
        Attributes:
            signature : Testcase signature for the ISS passes and post-processing
                        Currently, this is the input file name without the file extensioon.
                        e.g if input file is rv64IMFDV.json, then signature is rv64IMDV.
            testname  : Riescue D input file name ".s"
            disassembly     : Disassembly file name for post processing
            log, csv_log    : Spike/Whisper raw log and the CSV version for parsing.
            resource_db     : Handle to the Resource class
            instrs          : Dictionary of all the instructions belonging to a testcase.

    """

    def __init__(self, signature: Path, instrs: list[InstrBase], resource_db: Resource) -> None:
        self.signature = signature
        self.testname = signature.with_suffix(".s")
        self._resource_db = resource_db

        self.disassembly = signature.with_suffix(".dis")
        if self._resource_db.first_pass_iss == "spike":
            self.log = signature.parent / (signature.stem + "_spike.log")
            self.csv_log = signature.parent / (signature.stem + "_spike_csv.log")
        else:
            self.log = signature.parent / (signature.stem + "_whisper.log")
            self.csv_log = signature.parent / (signature.stem + "_whisper_csv.log")
        self.instrs = instrs
