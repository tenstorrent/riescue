# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC

import vsc
from riescue.compliance.config import Resource
from riescue.compliance.lib.common import lmul_map

# FIXME: Is this used? what's vsc?


@vsc.randobj
class VectorRegisterFile:
    """
    Collection of registers.
    Has constraints to shape the organization of registers underneath.
    Can hold vsetvli configs ?
    """

    def __init__(self, resource_db, lmul, sew):
        self.lmul = lmul_map[lmul]
        self.sew = sew
        self.resource_db = resource_db
        self.vlen = self.resource_db.vlen
        self.num_vregs = 32
        self.avail_vregs = vsc.rand_list_t(vsc.rand_int8_t(), int(self.num_vregs / self.lmul))

    @vsc.constraint
    def lmul_c(self):
        with vsc.foreach(self.avail_vregs, it=True) as vreg:
            vreg in vsc.rangelist((0, self.num_vregs - 1))
            vreg % self.lmul == 0

        vsc.unique(self.avail_vregs)
