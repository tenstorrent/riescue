# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""G-stage attribute-precedence regressions.

Assertions use decoded PTE fields rather than allocation-dependent addresses.
"""

import unittest

from riescue.riemap.json_frontend import PageTableConfig, generate_page_tables

SEED = 12345
A_BIT = 1 << 6
CONFIG = {
    "mmap": [["0x80000000", "0x100000000"]],
    "spaces": {
        "space4": {
            "twostage": True,
            "paging_mode": "disable",
            "gstage_paging_mode": "sv48",
            "pages": [
                {
                    "id": "gstage_only_forced",
                    "va": "0x60000000",
                    "pa": "0xd8000000",
                    "attributes": {
                        "size": "4kb",
                        "v": 1,
                        "u": 1,
                        "r": 1,
                        "w": 1,
                        "a_leaf_gleaf": 0,
                        "v_nonleaf_gnonleaf": 0,
                    },
                }
            ],
        },
        "space8": {
            "twostage": True,
            "paging_mode": "sv39",
            "gstage_paging_mode": "sv39",
            "pages": [
                {
                    "id": "priority_collision",
                    "va": "0x30000000",
                    "pa": "0xa0000000",
                    "attributes": {
                        "size": "4kb",
                        "v": 1,
                        "u": 1,
                        "r": 1,
                        "w": 1,
                        "a_level0_gleaf": 1,
                        "a_leaf_gleaf": 0,
                    },
                }
            ],
        },
        "space9": {
            "twostage": True,
            "paging_mode": "sv39",
            "gstage_paging_mode": "sv39",
            "pages": [
                {
                    "id": "force_insignificant_gleaf",
                    "va": "0x10000000",
                    "pa": "0x90000000",
                    "attributes": {
                        "size": "4kb",
                        "v": 1,
                        "u": 1,
                        "r": 1,
                        "w": 1,
                        "a_level0_glevel0": 0,
                        "a_leaf_gleaf": 1,
                    },
                }
            ],
        },
    },
}


def _datapage_gstage_leaf(entry):
    """The stage-2 leaf PTE translating the data-page GPA -- the target of the
    ``_leaf_gleaf`` knob (see resolve.gstage_leaf_attrs_for).

    Two-stage walk emits the data page's g-stage walk (root first, leaf last)
    after all VS steps, so it is the trailing stage-2 run. Its leaf is the
    min-level entry, i.e. the last PTE in the walk."""
    last_stage1 = max(i for i, p in enumerate(entry.ptes) if p.stage == 1)
    tail = entry.ptes[last_stage1 + 1 :]
    assert tail and all(p.stage == 2 for p in tail), "expected trailing stage-2 data-page g-walk"
    return min(tail, key=lambda p: p.level)


class TestGateGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = PageTableConfig.from_dict(CONFIG)
        cls.out = generate_page_tables(cfg, seed=SEED)

    def _gstage_leaf_a_bit(self, space_id, page_id):
        space = self.out.spaces[space_id]
        (entry,) = space.pages[page_id].values()
        g_leaf = _datapage_gstage_leaf(entry)
        pte_val = self.out.entries[g_leaf.address]
        return (pte_val & A_BIT) >> 6

    def test_space8_priority_collision_gleaf_A_is_0(self):
        # a_leaf_gleaf:0 (2-token) must WIN over a_level0_gleaf:1 (1-token) -> A=0.
        self.assertEqual(self._gstage_leaf_a_bit("space8", "priority_collision"), 0)

    def test_space4_gonly_forcing_reaches_the_gstage_tree(self):
        # A bare g-stage space (twostage + paging_mode disable) has no VS stage, so every
        # VS selector collapses onto its own g-stage tree: a_leaf_gleaf lands on the leaf
        # and v_nonleaf_gnonleaf on the pointer above it.
        (entry,) = self.out.spaces["space4"].pages["gstage_only_forced"].values()
        self.assertEqual({p.stage for p in entry.ptes}, {2}, "a bare g-stage walk is entirely stage 2")
        by_level = {p.level: self.out.entries[p.address] for p in entry.ptes}
        self.assertEqual((by_level[0] & A_BIT) >> 6, 0, "a_leaf_gleaf:0 never reached the g-stage leaf")
        self.assertEqual(by_level[1] & 1, 0, "v_nonleaf_gnonleaf:0 never reached the level-1 pointer PTE")

    def test_space9_insignificant_force_gleaf_A_is_1(self):
        # a_leaf_gleaf:1 (force == insignificant default) must beat
        # a_level0_glevel0:0 (concrete) -> A=1.
        self.assertEqual(self._gstage_leaf_a_bit("space9", "force_insignificant_gleaf"), 1)


if __name__ == "__main__":
    unittest.main()
