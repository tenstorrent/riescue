# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Example Conf that leads to an infinite loop. Used to check that conf is passed through correclty.
"""

from riescue.dtest_framework.config import Conf, FeatMgr, FeatMgrBuilder, Candidate
import riescue.lib.enums as RV


class LoopConf(Conf):
    """
    Example conf.py used to configure paging mode and priv mode
    """

    def infinite_loop(self, featmgr: FeatMgr) -> str:
        return """
    infinte_loop:
    j infinte_loop
"""

    def add_hooks(self, featmgr: FeatMgr) -> None:
        featmgr.register_hook(RV.HookPoint.POST_LOADER, self.infinite_loop)


def setup() -> Conf:
    return LoopConf()
