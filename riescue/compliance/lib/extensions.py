from riescue.compliance.lib.groups import Group

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


class Extension:
    """
    Class for storing extension related information.
        Attributes:
            name    : Identifier for extension ['f_ext','m_ext']
            groups  : Dictionary to store Group objects defined underneath the extension.
    """

    def __init__(self, name):
        self._name = name
        self._groups = dict()

    @property
    def name(self):
        return self._name

    @property
    def groups(self) -> dict:
        return self._groups

    def add_group(self, group: Group) -> None:
        self._groups[group.name] = group

    def get_group(self, group: str) -> Group:
        return self._groups[group]

    def check_group(self, grp_name: str) -> bool:
        if grp_name in self._groups:
            return True
        return False
