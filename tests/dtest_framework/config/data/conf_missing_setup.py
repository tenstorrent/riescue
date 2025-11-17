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

    It doesn't include any setup(), should raise a RuntimeError when imported
    """

    def pre_build(self, featmgr_builder: FeatMgrBuilder) -> None:
        featmgr_builder.priv_mode = Candidate(RV.RiscvPrivileges.MACHINE)
