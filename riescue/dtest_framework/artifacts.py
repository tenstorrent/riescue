# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GeneratedFiles:
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
    def from_testname(cls, test_name: str, run_dir: Path) -> "GeneratedFiles":
        "Create from testname and run directory"
        return cls(
            assembly=run_dir / f"{test_name}.S",
            linker_script=run_dir / f"{test_name}.ld",
            dis=run_dir / f"{test_name}.dis",
            elf=run_dir / f"{test_name}",
            includes=[],
        )
