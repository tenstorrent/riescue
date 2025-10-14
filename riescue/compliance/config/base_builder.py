# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path


class BaseBuilder:
    """
    Configuration for different Modes

    Configuration makes different modes easier to test. Also allows for a standard interface for ``Mode`` classes.
    Public interface is the ``RiescueC`` class with different ``run_<mode>`` methods.
    """

    @staticmethod
    def find_config(filepath: Path) -> Path:
        """
        If absolute use that, otherwise use relative to riescue directory, then cwd

        ..warning::
            This currently prioritizes files relative to the riescue directory over files relative to the cwd, to support legacy behavior.
            This might need to change later
        """
        riescue_relative = Path(__file__).parents[2] / filepath

        if filepath.is_absolute():
            return filepath
        elif riescue_relative.exists():
            return riescue_relative
        elif filepath.exists():
            return filepath
        else:
            raise FileNotFoundError(f"Couldn't find config file {filepath}. Tried {filepath} and {riescue_relative}.")
