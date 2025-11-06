# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

import argparse
import json
from pathlib import Path
from typing import Any, Optional


def auto_base_int(x: str) -> int:
    "Used by CmdLine as a 'type' to auto-cast strings/integers to an int. Adds help message for errors"
    valid_vals = """
    Values should be prefixed with 0x, 0b, or nothing e.g.:
        binary  0b011
        hex     0x3
        decimal 3"""
    try:
        return int(x, 0)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid hex value: {x} - Expected valid base {valid_vals}") from e


class JsonArgParser(argparse.ArgumentParser):
    """Extended argparse.ArgumentParser that configures from JSON.

    Creates parsers with arguments defined in a JSON configuration file.

    The recommended way to use this class is via :meth:`from_json` class method.

    Usage:
    .. code-block:: python

        parser = JsonArgParser.from_json("path/to/cmdline.json")
        Foo.add_args(parser)
        args = parser.parse_args()
    """

    type_map = {"int": int, "str": str, "hex": auto_base_int, "binary": auto_base_int, "auto_int": auto_base_int, "bool": bool, "float": float, "Path": Path, "list": list}

    @classmethod
    def from_json(cls, cmdline_json: Path, parent_groups: Optional[list[Any]] = None, **kwargs: Any) -> "JsonArgParser":
        """
        Configure parser from JSON file with argument definitions. Requires an `args` key in the JSON that has a list of `_groups` and arguments passed to `add_argument()`
        Other top-level keys are passed to the ArgumentParser constructor.

        :param json_path: Path to JSON configuration file
        :type json_path: str or Path
        :return: Returns self for method chaining
        :rtype: self

        Example JSON file:

        .. code-block:: json

            {
                "prog": "Name of the program",
                "description": "Description of the command-line arguments",
                "args": {
                    "output": {
                        "name": [
                            "--output_file",
                            "-o"
                        ],
                        "help": "Example output arg"
                    },
                    "_groups": {
                        "subgroup1": {
                            "description": "An arbitrary subgroup",
                            "foo": {
                                "name": [
                                    "--foo"
                                ],
                                "type": "int",
                                "help": "Example foo arg"
                            }
                        }
                    }
                }
            }
        """
        with open(cmdline_json, "r") as f:
            cmdline_content = json.load(f)
        if "args" not in cmdline_content:
            cmdline_keys = ", ".join(f"'{k}'" for k in cmdline_content.keys())  # checks which keys we have
            raise ValueError(f"Expected an 'args' key in the cmdline JSON file, instead got keys: {cmdline_keys}")

        argument_info = cmdline_content.pop("args")  # removes args key from json and assigns its contents
        parser = cls(**cmdline_content)  # unpacks dictionary contents to send to constructor

        # Parent groups are added to the main parser as a group. These can be overriddef by the json file.
        if parent_groups is not None:
            for parent in parent_groups:
                if not hasattr(parent, "add_arguments"):
                    raise AttributeError(f"parents objects {parent} does not have an 'add_args' method.")
                if not callable(parent.add_arguments):
                    raise TypeError(f"parents objects {parent} 'add_args' method is not callable.")
                parent_group = parser.add_argument_group(parent.__name__)
                parent.add_arguments(parent_group)

        parser.add_json_args(argument_info)
        return parser

    def __init__(self, formatter_class: type[argparse.HelpFormatter] = argparse.RawTextHelpFormatter, **kwargs: Any) -> None:
        super().__init__(formatter_class=formatter_class, **kwargs)

    def add_json_args(self, argument_dict: dict[str, Any]) -> None:
        """
        Add arguments to the parser from a dictionary.

        :param argument_dict: Dictionary containing argument information
        :type argument_dict: dict

        `argument_dict` should be of the for:

        .. code-block:: python

            {
                "dest" : {
                    "name": ["--arg_name", "-a"],
                    "help": "Example output arg",
                    ...
                },
            }

        `dest` is the name of the argument. Valid subkeys are keyword arguments to `argparse.ArgumentParser.add_argument()`.

        Example valid subkeys are:
            - `name`: List of strings for the argument name and aliases
            - `help`: String for the help text
            - `type`: String for the type of the argument
            - `default`: String for the default value of the argument
            - `choices`: List of strings for the choices of the argument
            - `actions`: string for the actions for arg parser
        """
        for k, v in argument_dict.items():
            if k == "_groups":
                for group_name, group_args in v.items():
                    group = self.add_argument_group(group_name, description=group_args.pop("description", ""))
                    for arg_dest, arg_dict in group_args.items():
                        self._add_arg(arg_dest, arg_dict, parser=group)
            else:
                self._add_arg(k, v, parser=None)

    def _add_arg(self, dest: str, arg_item: dict[str, Any], parser: Optional[Any] = None) -> None:
        """
        Pops name out of arg_item dictionary, casts type from type_map, and calls parser.add_argument()
        """

        if "name" not in arg_item:
            raise argparse.ArgumentError(None, f"Missing 'name' field in argument {dest} : {arg_item} ")
        name_list = arg_item.pop("name")
        # Cast type here
        if "type" in arg_item:
            arg_type = self.type_map.get(arg_item["type"])
            if arg_type is None:
                raise argparse.ArgumentError(None, f"Invalid type '{arg_item['type']}' specified for argument {dest}. If you need a custom type, add it to the type_map in the CmdLine class.")
            arg_item["type"] = arg_type

        # Adding default/choices to help string if present
        if "help" in arg_item:
            if "default" in arg_item:
                arg_item["help"] += '\ndefault: "%(default)s"'
            if "choices" in arg_item:
                arg_item["help"] += "\nChoices: [%(choices)s]"
        if parser is None:
            parser = self
        parser.add_argument(*name_list, dest=dest, **arg_item)

    def print_help(self, file: Optional[Any] = None):
        super().print_help()

        # Default helper formatter class does not handle hierarchial groups
        # Loop through all groups and print hierarchial groups
        def recurse_subgroups(fmt: argparse.HelpFormatter, group: Any, parent_title: Optional[str] = None) -> None:
            if group._action_groups is not None:
                parent_title = group.title if parent_title is None else f"{parent_title} -> {group.title}"
                for g in group._action_groups:
                    title = g.title if parent_title is None else f"{parent_title} -> {g.title}"
                    fmt.start_section(title)
                    fmt.add_text(g.description)
                    fmt.add_arguments(g._group_actions)
                    fmt.end_section()
                    recurse_subgroups(fmt, g, parent_title=parent_title)

        fmt = self._get_formatter()
        for group in self._action_groups:
            recurse_subgroups(fmt, group)
        print(fmt.format_help())
