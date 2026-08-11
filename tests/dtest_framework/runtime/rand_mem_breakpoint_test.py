# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the random-memory-breakpoint icount park mode.

``apply_icount_park`` exists so that architectural coverage of icount-gated state is produced by
ordinary random tests: every icount coverpoint is gated on ``tdata1.type == 3 && tselect == 8``, and
a directed scenario cannot establish that beyond its own body because each discrete test saves and
restores the trigger CSRs around itself. The invariants pinned here are the ones that decide whether
the park is both effective (slot 8 selected, correct fields) and safe (cannot raise an exception).
"""

import unittest
from unittest.mock import MagicMock

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.sdtrig import TriggerAction
from riescue.dtest_framework.runtime.rand_mem_breakpoint import (
    _ICOUNT_PARK_ACTIONS,
    _ICOUNT_PARK_COUNT_RANGE,
    _ICOUNT_SLOT,
    apply,
    apply_icount_park,
)
from riescue.lib.rand import RandNum

_TRACE_ACTION_VALUES = {TriggerAction.TRACE_ON.value, TriggerAction.TRACE_OFF.value, TriggerAction.TRACE_NOTIFY.value}


def _featmgr(park_pct=100, sdtrig_enabled=True):
    """Minimal FeatMgr stand-in: only the attributes apply_icount_park touches."""
    fm = MagicMock()
    fm.rand_mem_icount_park_pct = park_pct
    fm.feature.is_feature_enabled.return_value = sdtrig_enabled
    fm.registered = []
    fm.register_hook.side_effect = lambda point, hook: fm.registered.append((point, hook))
    return fm


def _pool(trigger_config_indices=()):
    pool = MagicMock()
    cfgs = []
    for index in trigger_config_indices:
        cfg = MagicMock()
        cfg.index = index
        cfgs.append(cfg)
    pool.get_parsed_trigger_configs.return_value = cfgs
    return pool


def _emitted(fm):
    """Run the registered M_LOADER hook and return its assembly."""
    points = [p for p, _ in fm.registered]
    assert points == [RV.HookPoint.M_LOADER], f"expected one M_LOADER hook, got {points}"
    return fm.registered[0][1](fm)


def _park_tdata1(asm):
    """Pull the parked tdata1 immediate out of the emitted assembly."""
    for line in asm.split("\n"):
        if "li" in line and "t0," in line and "0x" in line:
            return int(line.split("0x")[1].strip(), 16)
    raise AssertionError(f"no tdata1 immediate found in:\n{asm}")


class TestIcountParkGating(unittest.TestCase):
    def test_off_by_default(self):
        fm = _featmgr(park_pct=0)
        apply_icount_park(fm, _pool(), RandNum(seed=1))
        self.assertEqual(fm.registered, [], "pct=0 must register nothing")

    def test_skipped_when_sdtrig_disabled(self):
        fm = _featmgr(park_pct=100, sdtrig_enabled=False)
        apply_icount_park(fm, _pool(), RandNum(seed=1))
        self.assertEqual(fm.registered, [], "must not arm a trigger the cpuconfig does not implement")

    def test_pct_100_always_arms(self):
        for seed in range(8):
            fm = _featmgr(park_pct=100)
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            self.assertEqual(len(fm.registered), 1, f"seed={seed} must arm at pct=100")

    def test_conflicting_slot8_config_stands_down(self):
        fm = _featmgr(park_pct=100)
        apply_icount_park(fm, _pool(trigger_config_indices=(_ICOUNT_SLOT,)), RandNum(seed=1))
        self.assertEqual(fm.registered, [], "must not fight a ;#trigger_config that already owns slot 8")

    def test_unrelated_trigger_configs_do_not_block(self):
        """Unlike the watchpoint path, the park only owns slot 8, so other slots are none of its business."""
        fm = _featmgr(park_pct=100)
        apply_icount_park(fm, _pool(trigger_config_indices=(0, 1, 4, 5)), RandNum(seed=1))
        self.assertEqual(len(fm.registered), 1, "configs on other slots must not disable the park")

    def test_independent_of_watchpoint_gate(self):
        """The park must not consult rand_mem_breakpoint_pct -- that is the point of a separate gate."""
        fm = _featmgr(park_pct=100)
        fm.rand_mem_breakpoint_pct = 0
        apply_icount_park(fm, _pool(), RandNum(seed=1))
        self.assertEqual(len(fm.registered), 1, "park must fire with the watchpoint feature fully off")

    def test_stands_down_when_inject_already_claimed_the_slot(self):
        """--rand_mem_inject_icount_pct arms slot 8 with a live re-arm handler; the park must not clobber it."""
        fm = _featmgr(park_pct=100)
        apply_icount_park(fm, _pool(), RandNum(seed=1), icount_slot_claimed=True)
        self.assertEqual(fm.registered, [], "park must yield to an injected icount trigger")

    def test_parks_when_inject_did_not_claim_the_slot(self):
        fm = _featmgr(park_pct=100)
        apply_icount_park(fm, _pool(), RandNum(seed=1), icount_slot_claimed=False)
        self.assertEqual(len(fm.registered), 1, "park must proceed when the slot is free")


class TestApplyReportsIcountSlotClaim(unittest.TestCase):
    """apply() must report whether it put an injected icount trigger on slot 8.

    The park reads that flag to decide whether to stand down, so a wrong answer here means either a
    silently clobbered injected trigger or a park that never happens.
    """

    def _watchpoint_featmgr(self, inject_pct):
        fm = MagicMock()
        fm.rand_mem_breakpoint_pct = 100
        fm.num_cpus = 1
        fm.feature.is_feature_enabled.return_value = True
        fm.rand_mem_n_triggers = 1
        fm.rand_mem_max_fires = 2
        fm.rand_mem_inject_icount_pct = inject_pct
        fm.rand_mem_icount_density = RV.RandMemIcountDensity.MODERATE
        fm.medeleg = 0
        fm.medeleg_forced = False
        fm.registered = []
        fm.register_hook.side_effect = lambda point, hook: fm.registered.append((point, hook))
        return fm

    def _watchpoint_pool(self):
        pool = MagicMock()
        pool.get_parsed_trigger_configs.return_value = []
        pool.get_parsed_rand_mem_bp_pool.return_value = ["watch_a", "watch_b"]
        return pool

    def test_returns_false_when_off(self):
        fm = self._watchpoint_featmgr(inject_pct=0)
        fm.rand_mem_breakpoint_pct = 0
        self.assertIs(apply(fm, self._watchpoint_pool(), RandNum(seed=1)), False)

    def test_returns_false_without_icount_injection(self):
        fm = self._watchpoint_featmgr(inject_pct=0)
        self.assertIs(apply(fm, self._watchpoint_pool(), RandNum(seed=1)), False)

    def test_returns_true_with_icount_injection(self):
        fm = self._watchpoint_featmgr(inject_pct=100)
        self.assertIs(apply(fm, self._watchpoint_pool(), RandNum(seed=1)), True)


class TestIcountParkEmission(unittest.TestCase):
    def test_tselect_written_last_so_slot8_stays_selected(self):
        fm = _featmgr()
        apply_icount_park(fm, _pool(), RandNum(seed=3))
        asm = _emitted(fm)
        csr_writes = [ln.strip() for ln in asm.split("\n") if "csrw" in ln]
        self.assertTrue(csr_writes, "expected CSR writes")
        self.assertIn(f"tselect, {_ICOUNT_SLOT}", csr_writes[-1], f"last CSR write must select slot {_ICOUNT_SLOT}, got {csr_writes[-1]!r}")

    def test_parked_trigger_is_icount_type(self):
        for seed in range(8):
            fm = _featmgr()
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            tdata1 = _park_tdata1(_emitted(fm))
            self.assertEqual((tdata1 >> 60) & 0xF, 3, f"seed={seed}: type must be icount")

    def test_action_is_always_a_trace_action(self):
        """A breakpoint or debug-mode action would fire into a handler that does not exist."""
        for seed in range(32):
            fm = _featmgr()
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            tdata1 = _park_tdata1(_emitted(fm))
            self.assertIn(tdata1 & 0x3F, _TRACE_ACTION_VALUES, f"seed={seed}: action must be a trace action")

    def test_count_stays_in_the_high_range(self):
        lo, hi = _ICOUNT_PARK_COUNT_RANGE
        for seed in range(16):
            fm = _featmgr()
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            count = (_park_tdata1(_emitted(fm)) >> 10) & 0x3FFF
            self.assertGreaterEqual(count, lo, f"seed={seed}: count below range")
            self.assertLessEqual(count, hi, f"seed={seed}: count above range")

    def test_at_least_one_priv_mode_is_always_enabled(self):
        """A park with no mode enabled never counts, so it would sample nothing useful."""
        for seed in range(32):
            fm = _featmgr()
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            tdata1 = _park_tdata1(_emitted(fm))
            bits = [(tdata1 >> b) & 1 for b in (9, 7, 6, 26, 25)]  # m, s, u, vs, vu
            self.assertGreater(sum(bits), 0, f"seed={seed}: no privilege mode enabled")

    def test_fields_vary_across_seeds(self):
        """Randomization has to actually move, or the park contributes one bin instead of many."""
        seen = set()
        for seed in range(40):
            fm = _featmgr()
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            tdata1 = _park_tdata1(_emitted(fm))
            seen.add((tdata1 & 0x3F, (tdata1 >> 24) & 1, (tdata1 >> 8) & 1, tuple((tdata1 >> b) & 1 for b in (9, 7, 6, 26, 25))))
        self.assertGreater(len(seen), 10, f"expected varied (action, hit, pending, priv) combinations, got {len(seen)}")

    def test_both_polarities_of_hit_and_pending_appear(self):
        hits, pendings = set(), set()
        for seed in range(40):
            fm = _featmgr()
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            tdata1 = _park_tdata1(_emitted(fm))
            hits.add((tdata1 >> 24) & 1)
            pendings.add((tdata1 >> 8) & 1)
        self.assertEqual(hits, {0, 1}, "hit must be randomized over both values")
        self.assertEqual(pendings, {0, 1}, "pending must be randomized over both values")

    def test_every_priv_mode_gets_exercised_across_seeds(self):
        enabled_somewhere = set()
        for seed in range(60):
            fm = _featmgr()
            apply_icount_park(fm, _pool(), RandNum(seed=seed))
            tdata1 = _park_tdata1(_emitted(fm))
            for name, bit in [("m", 9), ("s", 7), ("u", 6), ("vs", 26), ("vu", 25)]:
                if (tdata1 >> bit) & 1:
                    enabled_somewhere.add(name)
        self.assertEqual(enabled_somewhere, {"m", "s", "u", "vs", "vu"}, "every mode should appear over a seed sweep")

    def test_tdata2_cleared(self):
        """icount does not use tdata2; clearing it also feeds the tdata2==0 coverage bin."""
        fm = _featmgr()
        apply_icount_park(fm, _pool(), RandNum(seed=5))
        self.assertIn("csrw  tdata2, x0", _emitted(fm))

    def test_park_actions_list_excludes_exception_raising_actions(self):
        self.assertNotIn(TriggerAction.BREAKPOINT, _ICOUNT_PARK_ACTIONS)
        self.assertNotIn(TriggerAction.DEBUG_MODE, _ICOUNT_PARK_ACTIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
