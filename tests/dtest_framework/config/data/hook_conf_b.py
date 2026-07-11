# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Test fixture Conf that registers a PRE_LOADER hook emitting the marker ``HOOK_B``.
Used together with :mod:`hook_conf_a` to verify multi-conf injection ordering.
"""

from riescue.dtest_framework.config import Conf, FeatMgr
import riescue.lib.enums as RV


def _hook_b(featmgr: FeatMgr) -> str:
    return "HOOK_B"


class HookConfB(Conf):
    def add_hooks(self, featmgr: FeatMgr) -> None:
        featmgr.register_hook(RV.HookPoint.PRE_LOADER, _hook_b)


def setup() -> Conf:
    return HookConfB()
