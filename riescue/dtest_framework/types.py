# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RdInputs:
    """
    Data structure for input files to RiescueD

    Currently supports a single .rasm file as an input, but can be extended to support multiple .rasm files in the future,
    additional assembly files, and/or C files to be compiled with RiescueD.
    Needs to have a single RiescueD Assembly file as input to get ``testname`` and ``testfile``


    :param testfile: RiescueD Assembly File (.rasm) ; to be suffixed with ``.rasm`` in the future.
    :param cpuconfig: CPU Config file with memory map and features
    """

    testfile: Path  # RiescueD Assembly File (.rasm)
    cpuconfig: Path  # CPU Config file with memory map and features
    includes: list[Path] = field(default_factory=list)  # Additional files include in assembling. FIXME: Currently unused

    @property
    def testname(self) -> str:
        """
        Return the name of the testfile

        ..note:
            This should always return the name of the test, used to create generated file names.
            The implementation may change if multiple files are supported. May need to get the name from the first file if not specified.
        """
        return self.testfile.stem


@dataclass
class RdGeneratedFiles:
    """
    Container for generated filepaths from RiescueD.
    Used to pass around generated files to different functions.
    """

    assembly: Path
    linker_script: Path
    dis: Path
    elf: Path
    includes: list[Path]  # Files included in the test
    iss_log: Optional[Path] = None

    @classmethod
    def from_testname(cls, test_name: str, run_dir: Path) -> "RdGeneratedFiles":
        "Create from testname and run directory"
        return cls(
            assembly=run_dir / f"{test_name}.S",
            linker_script=run_dir / f"{test_name}.ld",
            dis=run_dir / f"{test_name}.dis",
            elf=run_dir / f"{test_name}",
            includes=[],
        )
