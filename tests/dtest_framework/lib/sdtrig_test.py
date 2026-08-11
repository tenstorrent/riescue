# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Sdtrig tdata1 encoders and the trigger slot capability map.

The slot map and the mcontrol6 bit layout are the two places where a silent mistake produces a
trigger that arms nothing and a test that passes anyway, so both are pinned here against the
tdata1 write masks from ``dtest_framework/lib/whisper_config.json``.
"""

import unittest

from riescue.dtest_framework.lib.sdtrig import (
    EXEC_TRIGGER_SLOTS,
    ICOUNT_TRIGGER_SLOTS,
    LOAD_STORE_TRIGGER_SLOTS,
    TriggerAction,
    TriggerMatch,
    TriggerType,
    build_tdata1_icount,
    build_tdata1_mcontrol6,
    legal_slots_for,
    size_to_encoding,
    slot_supports,
)

# tdata1 write masks from the "triggers" array in dtest_framework/lib/whisper_config.json.
WMASK_EXEC = 0xF800000001C077DC  # slots 0-3: execute writable, load/store read-only 0
WMASK_LOAD_STORE = 0xF800000001C077DB  # slots 4-7: load/store writable, execute read-only 0
WMASK_ICOUNT = 0xF800000007FFFFC7  # slot 8


class TestSlotCapability(unittest.TestCase):
    """The slot map must match the per-slot tdata1 write masks, bit for bit."""

    def test_exec_slots_only_accept_execute(self):
        for index in EXEC_TRIGGER_SLOTS:
            self.assertTrue(slot_supports(index, TriggerType.EXECUTE))
            self.assertFalse(slot_supports(index, TriggerType.LOAD))
            self.assertFalse(slot_supports(index, TriggerType.STORE))
            self.assertFalse(slot_supports(index, TriggerType.LOAD_STORE))
            self.assertFalse(slot_supports(index, TriggerType.ICOUNT))

    def test_load_store_slots_reject_execute_and_icount(self):
        for index in LOAD_STORE_TRIGGER_SLOTS:
            self.assertTrue(slot_supports(index, TriggerType.LOAD))
            self.assertTrue(slot_supports(index, TriggerType.STORE))
            self.assertTrue(slot_supports(index, TriggerType.LOAD_STORE))
            self.assertFalse(slot_supports(index, TriggerType.EXECUTE))
            self.assertFalse(slot_supports(index, TriggerType.ICOUNT))

    def test_icount_slot_only_accepts_icount(self):
        for index in ICOUNT_TRIGGER_SLOTS:
            self.assertTrue(slot_supports(index, TriggerType.ICOUNT))
            self.assertFalse(slot_supports(index, TriggerType.EXECUTE))
            self.assertFalse(slot_supports(index, TriggerType.LOAD))

    def test_legal_slots_reported_for_diagnostics(self):
        self.assertEqual(legal_slots_for(TriggerType.EXECUTE), [0, 1, 2, 3])
        self.assertEqual(legal_slots_for(TriggerType.LOAD_STORE), [4, 5, 6, 7])
        self.assertEqual(legal_slots_for(TriggerType.ICOUNT), [8])

    def test_itrigger_etrigger_are_unconstrained(self):
        # No slot advertises these in tinfo, so the map must not claim to know; masking is the ISS's job.
        self.assertTrue(slot_supports(0, TriggerType.ITRIGGER))
        self.assertTrue(slot_supports(8, TriggerType.ETRIGGER))
        self.assertEqual(legal_slots_for(TriggerType.ITRIGGER), [])

    def test_access_type_bits_agree_with_write_masks(self):
        """A supported (slot, type) pair must leave its access-type bit set after masking."""
        execute = build_tdata1_mcontrol6(TriggerType.EXECUTE, priv_mode=("m",))
        load = build_tdata1_mcontrol6(TriggerType.LOAD, priv_mode=("m",))
        store = build_tdata1_mcontrol6(TriggerType.STORE, priv_mode=("m",))

        self.assertEqual((execute & WMASK_EXEC) & 0b100, 0b100, "execute bit must survive on an exec slot")
        self.assertEqual((load & WMASK_LOAD_STORE) & 0b001, 0b001, "load bit must survive on an LS slot")
        self.assertEqual((store & WMASK_LOAD_STORE) & 0b010, 0b010, "store bit must survive on an LS slot")

        # The wrong slot silently drops the access-type bit -- this is the bug the map exists to prevent.
        self.assertEqual((load & WMASK_EXEC) & 0b011, 0, "load/store bits must be masked away on an exec slot")
        self.assertEqual((execute & WMASK_LOAD_STORE) & 0b100, 0, "execute bit must be masked away on an LS slot")


class TestSizeEncoding(unittest.TestCase):
    """Debug Spec mcontrol6 size field: 0=any, 1=1B, 2=2B, 3=4B, 5=8B."""

    def test_spec_encodings(self):
        self.assertEqual(size_to_encoding(0), 0)
        self.assertEqual(size_to_encoding(1), 1)
        self.assertEqual(size_to_encoding(2), 2)
        self.assertEqual(size_to_encoding(4), 3)
        self.assertEqual(size_to_encoding(8), 5)

    def test_unknown_size_falls_back_to_any(self):
        self.assertEqual(size_to_encoding(3), 0)
        self.assertEqual(size_to_encoding(64), 0)

    def test_coretp_encoder_agrees(self):
        """coretp builds tdata1 values too; a divergent size map would emit a different trigger."""
        from coretp.step.debug import _size_to_encoding as coretp_size_to_encoding

        for size in (0, 1, 2, 4, 8, 3, 64):
            self.assertEqual(coretp_size_to_encoding(size), size_to_encoding(size), f"size={size} diverges")


class TestMcontrol6Layout(unittest.TestCase):
    """Field positions per RISC-V Debug Spec 1.0 mcontrol6."""

    def test_field_positions(self):
        val = build_tdata1_mcontrol6(TriggerType.EXECUTE, priv_mode=("m", "s", "u", "vs", "vu"))
        self.assertEqual((val >> 60) & 0xF, 6, "type[63:60]")
        self.assertEqual((val >> 24) & 1, 1, "vs[24]")
        self.assertEqual((val >> 23) & 1, 1, "vu[23]")
        self.assertEqual((val >> 6) & 1, 1, "m[6]")
        self.assertEqual((val >> 4) & 1, 1, "s[4]")
        self.assertEqual((val >> 3) & 1, 1, "u[3]")

    def test_each_priv_bit_is_pinned_individually(self):
        """Enable one mode at a time so a swapped pair (e.g. vs<->vu) cannot hide."""
        for mode, bit in [("m", 6), ("s", 4), ("u", 3), ("vs", 24), ("vu", 23)]:
            val = build_tdata1_mcontrol6(TriggerType.EXECUTE, priv_mode=(mode,))
            enabled = [b for b in (6, 4, 3, 24, 23) if (val >> b) & 1]
            self.assertEqual(enabled, [bit], f"priv_mode=({mode!r},) must set only bit {bit}, got bits {enabled}")

    def test_coretp_priv_bits_match_riescued(self):
        """coretp has its own copy of the encoder; a divergent priv-bit layout arms the wrong modes."""
        from coretp.step.debug import TriggerType as CoretpTriggerType
        from coretp.step.debug import build_tdata1_mcontrol6 as coretp_build

        for mode in ("m", "s", "u", "vs", "vu"):
            mine = build_tdata1_mcontrol6(TriggerType.EXECUTE, size=0, priv_mode=(mode,))
            theirs = coretp_build(trigger_type=CoretpTriggerType.EXECUTE, size=0, priv_mode=(mode,))
            self.assertEqual(mine, theirs, f"encoders diverge for priv_mode=({mode!r},)")

    def test_match_field_position(self):
        for match in TriggerMatch:
            val = build_tdata1_mcontrol6(TriggerType.EXECUTE, match=match, priv_mode=("m",))
            self.assertEqual((val >> 7) & 0xF, match.value, f"match[10:7] for {match.name}")

    def test_action_field_position(self):
        for action in TriggerAction:
            val = build_tdata1_mcontrol6(TriggerType.EXECUTE, action=action, priv_mode=("m",))
            self.assertEqual((val >> 12) & 0xF, action.value, f"action[15:12] for {action.name}")

    def test_priv_modes_are_independent(self):
        val = build_tdata1_mcontrol6(TriggerType.EXECUTE, priv_mode=("s",))
        self.assertEqual((val >> 4) & 1, 1, "s enabled")
        self.assertEqual((val >> 6) & 1, 0, "m not enabled")
        self.assertEqual((val >> 3) & 1, 0, "u not enabled")

    def test_hit_field_positions(self):
        """hit0 is bit 22 (writable, hardware sets it on a fire); hit1 is bit 25 (read-only 0).

        Getting these two swapped is silent: the value still writes, but hit0 lands in a read-only
        bit so no hit is ever observed, and hit1 lands in a writable bit that the coverpoint
        declares illegal.
        """
        from coretp.step.debug import TriggerType as CoretpTriggerType
        from coretp.step.debug import build_tdata1_mcontrol6 as coretp_build

        hit0_only = coretp_build(trigger_type=CoretpTriggerType.EXECUTE, priv_mode=("m",), hit0=1)
        self.assertEqual((hit0_only >> 22) & 1, 1, "hit0 must be bit 22")
        self.assertEqual((hit0_only >> 25) & 1, 0, "hit0 must not land on hit1's bit")
        self.assertEqual((hit0_only & WMASK_EXEC) >> 22 & 1, 1, "hit0 is writable, so it must survive the mask")

        hit1_only = coretp_build(trigger_type=CoretpTriggerType.EXECUTE, priv_mode=("m",), hit1=1)
        self.assertEqual((hit1_only >> 25) & 1, 1, "hit1 must be bit 25")
        self.assertEqual((hit1_only >> 22) & 1, 0, "hit1 must not land on hit0's bit")
        self.assertEqual((hit1_only & WMASK_EXEC) >> 25 & 1, 0, "hit1 is read-only, so the mask must drop it")

        uncertain_only = coretp_build(trigger_type=CoretpTriggerType.EXECUTE, priv_mode=("m",), uncertain=1)
        self.assertEqual((uncertain_only >> 26) & 1, 1, "uncertain must be bit 26")

        select_only = coretp_build(trigger_type=CoretpTriggerType.EXECUTE, priv_mode=("m",), select=1)
        self.assertEqual((select_only >> 21) & 1, 1, "select must be bit 21")

        uncertainen_only = coretp_build(trigger_type=CoretpTriggerType.EXECUTE, priv_mode=("m",), uncertainen=1)
        self.assertEqual((uncertainen_only >> 5) & 1, 1, "uncertainen must be bit 5")


class TestWarlReadOnlyProbe(unittest.TestCase):
    """The SID_SDTRIG_045 pattern: write every RO/unimplemented field, read back all zeros.

    The read-only-field coverpoints gate on the *written* rs1 value having each bit set but bin the
    *read-back* field, and several of them declare the set state illegal -- so the written value must
    set every gate bit while the masked read-back must be zero in all of them.
    """

    # Built the same way SID_SDTRIG_045 builds it, via the coretp encoder that owns those params.
    def _probe_value(self):
        from coretp.step.debug import TriggerType as CoretpTriggerType
        from coretp.step.debug import build_tdata1_mcontrol6 as coretp_build

        val = coretp_build(
            trigger_type=CoretpTriggerType.EXECUTE,
            priv_mode=("m",),
            dmode=1,
            uncertain=1,
            hit1=1,
            select=1,
            size=8,
            chain=1,
            uncertainen=1,
        )
        return (val & ~(0xF << 12)) | (0x8 << 12)  # action[15:12] = bit 15 only (read-only 0)

    def test_written_value_sets_every_gate_bit(self):
        val = self._probe_value()
        for name, bit in [("dmode", 59), ("uncertain", 26), ("hit1", 25), ("select", 21), ("chain", 11), ("uncertainen", 5)]:
            self.assertEqual((val >> bit) & 1, 1, f"{name} must be set in the written value")
        self.assertNotEqual((val >> 16) & 0x7, 0, "size[18:16] must be non-zero in the written value")
        self.assertNotEqual((val >> 12) & 0xF, 0, "action[15:12] must be non-zero in the written value")

    def test_read_back_lands_on_legal_bins(self):
        read_back = self._probe_value() & WMASK_EXEC
        self.assertEqual((read_back >> 60) & 0xF, 6, "type must still read back as mcontrol6")
        for name, bit in [("uncertain", 26), ("hit1", 25), ("select", 21), ("chain", 11), ("uncertainen", 5)]:
            self.assertEqual((read_back >> bit) & 1, 0, f"{name} is read-only 0; a 1 would hit an illegal bin")
        self.assertEqual((read_back >> 16) & 0x7, 0, "size is read-only 0")
        self.assertEqual((read_back >> 12) & 0xF, 0, "action must read back 0 so the breakpoint bin still hits")

    def test_action_bit_choice_matters(self):
        """Using a writable action bit would leave action != 0 and miss the breakpoint bin."""
        from coretp.step.debug import TriggerType as CoretpTriggerType
        from coretp.step.debug import build_tdata1_mcontrol6 as coretp_build

        naive = coretp_build(trigger_type=CoretpTriggerType.EXECUTE, priv_mode=("m",))
        naive = (naive & ~(0xF << 12)) | (0x1 << 12)  # action=1 uses writable bit 12
        self.assertNotEqual((naive & WMASK_EXEC) >> 12 & 0xF, 0, "bits 14:12 are writable, so action would stick")


class TestIcountLayout(unittest.TestCase):
    """Field positions for icount (type=3), and the parked-trigger encoding."""

    def test_field_positions(self):
        val = build_tdata1_icount(count=0x3FFF, priv_mode=("m", "s", "u", "vs", "vu"), pending=1)
        self.assertEqual((val >> 60) & 0xF, 3, "type[63:60]")
        self.assertEqual((val >> 26) & 1, 1, "vs[26]")
        self.assertEqual((val >> 25) & 1, 1, "vu[25]")
        self.assertEqual((val >> 10) & 0x3FFF, 0x3FFF, "count[23:10]")
        self.assertEqual((val >> 9) & 1, 1, "m[9]")
        self.assertEqual((val >> 8) & 1, 1, "pending[8]")
        self.assertEqual((val >> 7) & 1, 1, "s[7]")
        self.assertEqual((val >> 6) & 1, 1, "u[6]")

    def test_each_priv_bit_is_pinned_individually(self):
        """icount uses a different priv-bit layout from mcontrol6, so pin each bit on its own."""
        for mode, bit in [("m", 9), ("s", 7), ("u", 6), ("vs", 26), ("vu", 25)]:
            val = build_tdata1_icount(count=1, priv_mode=(mode,))
            enabled = [b for b in (9, 7, 6, 26, 25) if (val >> b) & 1]
            self.assertEqual(enabled, [bit], f"priv_mode=({mode!r},) must set only bit {bit}, got bits {enabled}")

    def test_park_value_survives_the_icount_slot_mask(self):
        """SID_SDTRIG_I028 parks with count=max; the count and type must survive slot 8's mask."""
        val = build_tdata1_icount(count=0x3FFF, priv_mode=("m", "s", "u"))
        read_back = val & WMASK_ICOUNT
        self.assertEqual((read_back >> 60) & 0xF, 3, "type must stay icount")
        self.assertEqual((read_back >> 10) & 0x3FFF, 0x3FFF, "full count field must be writable")
        self.assertEqual((read_back >> 24) & 1, 0, "hit starts clear so a fire is detectable")

    def test_action_field_is_six_bits(self):
        for action in TriggerAction:
            val = build_tdata1_icount(count=1, action=action, priv_mode=("m",))
            self.assertEqual(val & 0x3F, action.value, f"action[5:0] for {action.name}")

    def test_legal_actions_avoid_the_illegal_bin_range(self):
        """The icount action coverpoint declares 5..63 illegal, so every encoder value must be < 5."""
        for action in TriggerAction:
            self.assertLess(action.value, 5, f"{action.name} would land in the illegal action range")


if __name__ == "__main__":
    unittest.main(verbosity=2)
