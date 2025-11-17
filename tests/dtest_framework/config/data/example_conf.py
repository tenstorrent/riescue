# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Example Conf
"""

from riescue.dtest_framework.config import Conf, FeatMgr, FeatMgrBuilder, Candidate
import riescue.lib.enums as RV


class CandidateConf(Conf):
    """
    Example Conf. This one just sets the priv candidate to MACHINE.
    """

    def pre_build(self, featmgr_builder: FeatMgrBuilder) -> None:
        featmgr_builder.priv_mode = Candidate(RV.RiscvPrivileges.MACHINE)


class PrivConfig(Conf):
    """
    Example Conf. This one just sets the priv candidate to Supervisor
    """

    def post_build(self, featmgr: FeatMgr) -> None:
        featmgr.priv_mode = RV.RiscvPrivileges.SUPER


class ExampleConf(Conf):
    """
    Example Conf for file loading
    """

    def pre_build(self, featmgr_builder: FeatMgrBuilder) -> None:
        featmgr_builder.paging_mode = Candidate(RV.RiscvPagingModes.SV39)

    def post_build(self, featmgr: FeatMgr) -> None:
        featmgr.priv_mode = RV.RiscvPrivileges.SUPER


def setup() -> Conf:
    return ExampleConf()
