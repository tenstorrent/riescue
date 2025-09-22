# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.lib.instr_setup.base import InstrSetup


class FenceSetup(InstrSetup):
    def pre_setup(self, instr):
        tso = self._rng.random_nbit(1)

        if tso > 0:
            self._asm_instr = "\tfence rw, rw"
        else:
            generate_iorw = lambda: "".join([c if self._rng.random_nbit(1) == 1 else "" for c in "iorw"])
            pred = generate_iorw()
            succ = ""
            # Only can have a successor if pred was something
            if pred != "":
                while succ in ["", ", "]:
                    succ = ", " + generate_iorw()
            self._asm_instr = f"\tfence {pred} {succ}"

        self.write_pre(f"{instr.label} : ")
        self.write_pre(self._asm_instr)

    def post_setup(self, modified_state, instr):
        pass


class FenceImmSetup(InstrSetup):
    def pre_setup(self, instr):
        self._asm_instr = "\tfence.i"
        self.write_pre(f"{instr.label} : ")
        self.write_pre(self._asm_instr)

    def post_setup(self, modified_state, instr):
        pass
