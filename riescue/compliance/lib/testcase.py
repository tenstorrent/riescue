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
        self.disassembly = signature.with_suffix(".dis")
        self._resource_db = resource_db

        if self._resource_db.first_pass_iss == "spike":
            self.log, self.csv_log = self.get_spike_logs()
        else:
            self.log, self.csv_log = self.get_whisper_logs()
        self.instrs = instrs

    def get_spike_logs(self) -> tuple[Path, Path]:
        "Retrive spike and csv logs for the testcase"
        return self.signature.parent / (self.signature.stem + "_spike.log"), self.signature.parent / (self.signature.stem + "_spike_csv.log")

    def get_whisper_logs(self) -> tuple[Path, Path]:
        "Retrive whisper and csv logs for the testcase"
        return self.signature.parent / (self.signature.stem + "_whisper.log"), self.signature.parent / (self.signature.stem + "_whisper_csv.log")
