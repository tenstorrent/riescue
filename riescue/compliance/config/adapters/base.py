# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, TypeVar, Generic
from pathlib import Path

log = logging.getLogger(__name__)

Builder = TypeVar("Builder")
T = TypeVar("T")


class BaseAdapter(ABC, Generic[Builder, T]):
    @abstractmethod
    def apply(self, builder: Builder, src: T) -> Builder: ...

    @staticmethod
    def find_config(filepath: Path) -> Path:
        """
        If absolute use that, otherwise use relative to riescue directory, then cwd

        ..warning::
            This currently prioritizes files relative to the riescue directory over files relative to the cwd, to support legacy behavior.
            This might need to change later
        """
        riescue_relative = Path(__file__).parents[3] / filepath

        if filepath.is_absolute():
            return filepath
        elif riescue_relative.exists():
            return riescue_relative
        elif filepath.exists():
            return filepath
        else:
            raise FileNotFoundError(f"Couldn't find config file {filepath}. Tried {filepath} and {riescue_relative}.")
