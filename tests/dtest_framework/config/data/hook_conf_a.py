# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Test fixture Conf that registers a PRE_LOADER hook emitting the marker ``HOOK_A``.
Used together with :mod:`hook_conf_b` to verify multi-conf injection ordering.
"""

from riescue.dtest_framework.config import Conf, FeatMgr
import riescue.lib.enums as RV


def _hook_a(featmgr: FeatMgr) -> str:
    return "HOOK_A"


class HookConfA(Conf):
    def add_hooks(self, featmgr: FeatMgr) -> None:
        featmgr.register_hook(RV.HookPoint.PRE_LOADER, _hook_a)


def setup() -> Conf:
    return HookConfA()
