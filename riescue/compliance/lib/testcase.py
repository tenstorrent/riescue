from riescue.compliance.config import Resource
from riescue.compliance.lib.riscv_instrs import InstrBase

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


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

    def __init__(self, signature: str, instrs: list[InstrBase], resource_db: Resource) -> None:
        self._signature = signature
        self._testname = signature + ".s"
        self._resource_db = resource_db
        self._disassembly = signature + ".dis"
        if self._resource_db.first_pass_iss == "spike":
            self._log = signature + "_spike.log"
            self._csv_log = signature + "_spike_csv.log"
        else:
            self._log = signature + "_whisper.log"
            self._csv_log = signature + "_whisper_csv.log"
        self._instrs = instrs

    @property
    def testname(self) -> str:
        return self._testname

    @property
    def disassembly(self) -> str:
        return self._disassembly

    @property
    def log(self) -> str:
        return self._log

    @property
    def csv_log(self) -> str:
        return self._csv_log

    @property
    def instrs(self) -> list[InstrBase]:
        return self._instrs

    @property
    def signature(self) -> str:
        return self._signature

    def get_spike_logs(self) -> tuple[str, ...]:
        return tuple([self._signature + "_spike.log", self._signature + "_spike_csv.log"])

    def get_whisper_logs(self) -> tuple[str, ...]:
        return tuple([self._signature + "_whisper.log", self._signature + "_whisper.log"])
