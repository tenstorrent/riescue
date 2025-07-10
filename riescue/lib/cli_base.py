#! /usr/bin/env python3
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
import abc
from pathlib import Path

"""
Base CLI script

**Template for Extending:**

.. code-block:: python

from cli_base import CliBase

class Foo(CliBase):
    def __init__(self):
        pass

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("--foo", type=str, help="Some helpful help message")
        # optional, add argument group
        parser_group = parser.add_argument_group(title="Option group", description="Purpose of option group, e.g. output options")
        parser_group.add_argument("--bar", type=str, help="Some helpful help message, related to option group")


    def run(self):
        # Whatever CLI program needs to do
        pass

if __name__ == "__main__":
    Foo.run_commandline()

"""


class CliBase(abc.ABC):
    prog = "SomeCliScript"
    description = "aaaa"

    @abc.abstractmethod
    def run(self):
        pass

    @staticmethod
    @abc.abstractmethod
    def add_arguments(parser):
        print("Inside cli_script add_args, override me")

    @classmethod
    def commandline(cls):
        parser = argparse.ArgumentParser(prog=cls.prog, description=cls.description, formatter_class=argparse.RawTextHelpFormatter)
        cls.add_arguments(parser)
        args = parser.parse_args()
        return cls(**vars(args))

    @classmethod
    def run_cli(cls, **kwargs):
        c = cls.commandline(**kwargs)
        c.run()

    # common helper methods
    @staticmethod
    def check_valid_file(file: str) -> Path:
        if not isinstance(file, Path):
            file = Path(file)
        if not file.exists():
            raise FileNotFoundError(f"No file {file.name} at path {file}")
        return file


class CliCmdBase(CliBase):
    """
    Interactive version of CliBase
    """

    pass
