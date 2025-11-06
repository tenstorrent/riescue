# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from pathlib import Path
from typing import Any, Optional

from riescue.lib.json_argparse import JsonArgParser as JsonArgumentParser


class CmdLine:
    """Compatibility wrapper for JsonArgParser. To be deprecated

    Maintains legacy interface for existing code.

    .. seealso:: :func:`riescue.lib.json_argparse.JsonArgParser.from_json` for recommended usage in new code.
    """

    def __init__(
        self,
        cmdline_json: Path,
        args_to_process: Optional[list[str]] = None,
        parser_extensions: Optional[list[Any]] = None,
        parent_groups: Optional[list[Any]] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize CmdLine with JSON configuration file.

        :param cmdline_json: Path to JSON configuration file
        :param args_to_process: Optional list of arguments to process. None uses sys.argv
        :param parser_extensions: Iterable list of objects with an `add_args(parser)` method
        """
        self.parser = JsonArgumentParser.from_json(cmdline_json, parent_groups=parent_groups, **kwargs)

        # Parser extensions are added to the main parser. They can create subgroups and add arguments to them.
        if parser_extensions is not None:
            for obj in parser_extensions:
                if not hasattr(obj, "add_arguments"):
                    raise AttributeError(f"parser_extensions objects {obj} does not have an 'add_args' method.")
                if not callable(obj.add_arguments):
                    raise TypeError(f"parser_extensions objects {obj} 'add_arguments' method is not callable.")
                obj.add_arguments(self.parser)

        self.args = self.parser.parse_args(args_to_process)

    def parse_args(self, args: Optional[list[str]] = None) -> Any:
        """
        Placeholder for returning args

        :param args: Arguments to parse (default: None, which uses args_to_process from constructor or sys.argv)
        :return: The parsed arguments
        """
        return self.args
