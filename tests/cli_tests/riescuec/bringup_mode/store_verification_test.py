# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Regression tests for the store instruction verification bug.

Bug summary:
    StoreSetup.post_setup() / CStoreComponent.post_setup() iterated over all 8
    bytes of the ISS stdata field (which is always zero-extended to 64 bits),
    expecting zeros for bytes not written by the store. When memory was
    pre-initialized with random data the unwritten bytes retained their initial
    values, causing the generated test to fail with tohost=3 on Whisper.

    Affected instructions: SB, SH, SW (integer), FSW (FP), C.SW/C.SH (compressed).
    SD/FSD were unaffected since they write all 8 bytes.

    Fix: get_store_size() in utils.py derives the store width from the instruction
    name.  All three post_setup() implementations (StoreSetup, CStoreComponent in
    utils.py, CStoreComponent in compressed.py) now call get_store_size() and only
    emit verification checks for the bytes actually written.

    See: RIESCUE_STORE_INSTRUCTION_BUG.md / PATCH_SPECIFICATION.md
"""

import unittest

from riescue.compliance import BringupMode
from riescue.compliance.config import ResourceBuilder
from riescue.lib.toolchain import Toolchain, Whisper, Spike
from riescue.compliance.lib.instr_setup.integer.load_store import StoreSetup
from riescue.compliance.lib.instr_setup.utils import get_store_size
from tests.cli_tests.riescuec.base import BaseRiescueCTest


# ---------------------------------------------------------------------------
# Unit tests – verify get_store_size() covers integer, FP, and compressed
# stores, and that the byte extraction logic is correct.
# ---------------------------------------------------------------------------


class TestGetStoreSize(unittest.TestCase):
    """Unit tests for the shared get_store_size() utility function."""

    def _make_setup(self):
        """Return a StoreSetup instance with a minimal stub resource_db."""

        class _Rng:
            def random_nbit(self, n):
                return 0

            def random_entry_in(self, seq):
                return seq[0]

        class _ResourceDb:
            rng = _Rng()
            big_endian = False

        # Bypass __init__ address generation by constructing directly
        setup = StoreSetup.__new__(StoreSetup)
        setup.resource_db = _ResourceDb()
        return setup

    # -- Integer stores ------------------------------------------------------

    def test_sb_returns_1(self):
        self.assertEqual(get_store_size("sb"), 1)

    def test_sh_returns_2(self):
        self.assertEqual(get_store_size("sh"), 2)

    def test_sw_returns_4(self):
        self.assertEqual(get_store_size("sw"), 4)

    def test_sd_returns_8(self):
        self.assertEqual(get_store_size("sd"), 8)

    def test_case_insensitive(self):
        self.assertEqual(get_store_size("SH"), 2)
        self.assertEqual(get_store_size("SW"), 4)

    def test_unknown_defaults_to_8(self):
        self.assertEqual(get_store_size("unknown_instr"), 8)

    def test_sd_not_caught_by_sw_substring(self):
        """Ensure 'sd' check fires before 'sw' — 'sd' must not return 4."""
        self.assertEqual(get_store_size("sd"), 8)

    # -- FP stores -----------------------------------------------------------

    def test_fsw_returns_4(self):
        """fsw stores a 32-bit float (4 bytes); 'sw' substring must match."""
        self.assertEqual(get_store_size("fsw"), 4)

    def test_fsd_returns_8(self):
        """fsd stores a 64-bit double (8 bytes); 'sd' substring must match."""
        self.assertEqual(get_store_size("fsd"), 8)

    def test_fsw_not_caught_by_fsd_rule(self):
        """'sd' must NOT match 'fsw' — 'fsw' contains s,w not s,d."""
        self.assertNotEqual(get_store_size("fsw"), 8)

    # -- Compressed stores ---------------------------------------------------

    def test_c_sw_returns_4(self):
        """c.sw stores 4 bytes; substring 'sw' must match inside 'c.sw'."""
        self.assertEqual(get_store_size("c.sw"), 4)

    def test_c_sd_returns_8(self):
        """c.sd stores 8 bytes; substring 'sd' must match inside 'c.sd'."""
        self.assertEqual(get_store_size("c.sd"), 8)

    def test_c_sh_returns_2(self):
        """c.sh (Zcb) stores 2 bytes; substring 'sh' must match inside 'c.sh'."""
        self.assertEqual(get_store_size("c.sh"), 2)

    def test_c_fsw_returns_4(self):
        """c.fsw stores 4 bytes; 'sw' must match in 'c.fsw' without 'sd' firing first."""
        self.assertEqual(get_store_size("c.fsw"), 4)

    def test_c_fsd_returns_8(self):
        """c.fsd stores 8 bytes; 'sd' must match in 'c.fsd'."""
        self.assertEqual(get_store_size("c.fsd"), 8)

    def test_c_fsw_not_caught_by_c_fsd_rule(self):
        """'sd' must NOT match 'c.fsw' — 'c.fsw' contains s,w not s,d."""
        self.assertNotEqual(get_store_size("c.fsw"), 8)

    # -- Byte extraction logic -----------------------------------------------

    def test_sw_byte_count_from_stdata(self):
        """
        SW: only 4 byte checks emitted from a 64-bit zero-extended stdata.

        stdata = 0x000000003e4c1362 (SW zero-extended to 64 bits)
        Expected: bytes [0x62, 0x13, 0x4c, 0x3e] (little-endian, 4 bytes only)
        """
        store_size = get_store_size("sw")
        stdata_value = 0x000000003E4C1362
        byte_values = [f"{(stdata_value >> (8 * i)) & 0xFF:02x}" for i in range(store_size)]
        self.assertEqual(len(byte_values), 4)
        self.assertEqual(byte_values, ["62", "13", "4c", "3e"])

    def test_fsw_byte_count_from_stdata(self):
        """
        FSW: only 4 byte checks emitted — same width as SW.

        This is the exact scenario from the failing test:
        stdata = 0x00000000beb85d4d (FSW zero-extended to 64 bits)
        Expected: bytes [0x4d, 0x5d, 0xb8, 0xbe] (little-endian, 4 bytes only)

        Before the fix, all 8 bytes were checked; byte 4 (0xff from
        pre-initialized memory at offset 0x367) caused tohost=3.
        """
        store_size = get_store_size("fsw")
        stdata_value = 0x00000000BEB85D4D
        byte_values = [f"{(stdata_value >> (8 * i)) & 0xFF:02x}" for i in range(store_size)]
        self.assertEqual(len(byte_values), 4)
        self.assertEqual(byte_values, ["4d", "5d", "b8", "be"])

    def test_sh_byte_count_from_stdata(self):
        """
        SH: only 2 byte checks — the original bug report scenario.

        stdata = 0x000000000000e9b4 (SH zero-extended to 64 bits)
        Expected: bytes [0xb4, 0xe9] (little-endian, 2 bytes only)

        Before the fix, bytes 2-7 were checked against 0x00 but memory
        had been pre-initialized with 0xd610de6d, causing tohost=3.
        """
        store_size = get_store_size("sh")
        stdata_value = 0x000000000000E9B4
        byte_values = [f"{(stdata_value >> (8 * i)) & 0xFF:02x}" for i in range(store_size)]
        self.assertEqual(len(byte_values), 2)
        self.assertEqual(byte_values, ["b4", "e9"])

    def test_sb_byte_count_from_stdata(self):
        """Verify that for SB, only 1 byte check would be emitted."""
        store_size = get_store_size("sb")
        stdata_value = 0x00000000000000AB
        byte_values = [f"{(stdata_value >> (8 * i)) & 0xFF:02x}" for i in range(store_size)]
        self.assertEqual(len(byte_values), 1)
        self.assertEqual(byte_values, ["ab"])


# ---------------------------------------------------------------------------
# Integration tests – run BringupMode with store instructions through the ISS
# pipeline and verify they produce a valid ELF (i.e. the generated test passed
# the ISS, meaning tohost=1, not tohost=3).
# ---------------------------------------------------------------------------


class TestStoreBringupIntegration(BaseRiescueCTest):
    """
    Integration tests that exercise store instructions through the full bringup
    pipeline (generate -> compile -> ISS).

    These tests would have failed before the fix with:
        ToolchainError: Whisper Failed: write to tohost failure: 3

    Seed 0 is intentional: it is the seed that originally exposed the bug.
    """

    def default_toolchain(self) -> Toolchain:
        return Toolchain(whisper=Whisper(), spike=Spike())

    def _run_bringup_with_instrs(self, instrs: list, testcase_name: str):
        """Helper: run BringupMode for the given instruction list and check the ELF."""
        resource = ResourceBuilder().build(seed=0, run_dir=self.test_dir)
        resource.testcase_name = testcase_name
        resource.include_instrs = instrs
        runner = BringupMode(self.test_dir)
        elf = runner.generate(resource, toolchain=self.default_toolchain())
        self.check_valid_elf(elf)
        return elf

    def test_sh_passes_iss(self):
        """
        SH (store halfword) with seed 0 produced tohost=3 before the fix.

        The pre-initialized memory word was 0xd610de6d; the SH instruction wrote
        0xe9b4 at offset 0x0. The old code expected bytes [0xb4, 0xe9, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00] but bytes 2-3 were [0x10, 0xd6] from the init word.
        """
        self._run_bringup_with_instrs(["sh"], "sh_test")

    def test_sb_passes_iss(self):
        """SB (store byte) – only 1 byte written, 7 bytes were incorrectly verified."""
        self._run_bringup_with_instrs(["sb"], "sb_test")

    def test_sw_passes_iss(self):
        """SW (store word) – only 4 bytes written, 4 bytes were incorrectly verified."""
        self._run_bringup_with_instrs(["sw"], "sw_test")

    def test_sd_passes_iss(self):
        """SD (store doubleword) – writes all 8 bytes, was correct before fix too."""
        self._run_bringup_with_instrs(["sd"], "sd_test")

    def test_all_integer_stores_pass_iss(self):
        """Run all four integer store instructions together in one test."""
        self._run_bringup_with_instrs(["sb", "sh", "sw", "sd"], "all_stores_test")

    def test_sh_multiple_seeds(self):
        """
        SH should pass across a range of seeds, not just seed 0.

        Runs seeds 0-4. With the buggy code any seed would fail since a 32-bit
        random init word has a ~(1/2^16) chance of having zero upper bytes.
        """
        for seed in range(5):
            with self.subTest(seed=seed):
                resource = ResourceBuilder().build(seed=seed, run_dir=self.test_dir / f"seed_{seed}")
                resource.testcase_name = f"sh_seed{seed}"
                resource.include_instrs = ["sh"]
                runner = BringupMode(self.test_dir / f"seed_{seed}")
                elf = runner.generate(resource, toolchain=self.default_toolchain())
                self.check_valid_elf(elf)


# ---------------------------------------------------------------------------
# Unit tests – AMO / LR / SC instruction names in get_store_size()
# ---------------------------------------------------------------------------


class TestGetStoreSizeAtomics(unittest.TestCase):
    """
    Bug: get_store_size() did not handle AMO or LR/SC instruction names.

    Problems before the fix:
      - amoadd.w, amominu.w, etc. fell through all substring checks and
        returned the default of 8 (should be 4).
      - amoswap.w/d both matched the "sw" substring and returned 4 (the .d
        variant should return 8).

    Fix: AMO/LR/SC names are detected by their prefix (startswith "amo",
    "lr.", "sc.") and the width is taken from the trailing "w" or "d" of
    the base instruction name.
    """

    # -- AMO .w variants (32-bit) -----------------------------------------------

    def test_amoadd_w_returns_4(self):
        """amoadd.w writes one 32-bit word."""
        self.assertEqual(get_store_size("amoadd.w"), 4)

    def test_amoswap_w_returns_4(self):
        """amoswap.w: 'sw' substring previously caused a false positive — fix must override."""
        self.assertEqual(get_store_size("amoswap.w"), 4)

    def test_amominu_w_returns_4(self):
        """amominu.w: no legacy substring match → was defaulting to 8 before fix."""
        self.assertEqual(get_store_size("amominu.w"), 4)

    def test_amomin_w_returns_4(self):
        self.assertEqual(get_store_size("amomin.w"), 4)

    def test_amomax_w_returns_4(self):
        self.assertEqual(get_store_size("amomax.w"), 4)

    def test_amomaxu_w_returns_4(self):
        self.assertEqual(get_store_size("amomaxu.w"), 4)

    def test_amoor_w_returns_4(self):
        self.assertEqual(get_store_size("amoor.w"), 4)

    def test_amoand_w_returns_4(self):
        self.assertEqual(get_store_size("amoand.w"), 4)

    def test_amoxor_w_returns_4(self):
        self.assertEqual(get_store_size("amoxor.w"), 4)

    # -- AMO .d variants (64-bit) -----------------------------------------------

    def test_amoadd_d_returns_8(self):
        """amoadd.d writes one 64-bit doubleword."""
        self.assertEqual(get_store_size("amoadd.d"), 8)

    def test_amoswap_d_returns_8(self):
        """amoswap.d: 'sw' substring was previously giving 4 — fix must give 8."""
        self.assertEqual(get_store_size("amoswap.d"), 8)

    def test_amominu_d_returns_8(self):
        self.assertEqual(get_store_size("amominu.d"), 8)

    def test_amomin_d_returns_8(self):
        self.assertEqual(get_store_size("amomin.d"), 8)

    def test_amomax_d_returns_8(self):
        self.assertEqual(get_store_size("amomax.d"), 8)

    # -- SC / LR ----------------------------------------------------------------

    def test_sc_w_returns_4(self):
        """sc.w (store-conditional word) writes 4 bytes on success."""
        self.assertEqual(get_store_size("sc.w"), 4)

    def test_sc_d_returns_8(self):
        """sc.d (store-conditional doubleword) writes 8 bytes on success."""
        self.assertEqual(get_store_size("sc.d"), 8)

    def test_lr_w_returns_4(self):
        """lr.w is a load-reserve; get_store_size() returns 4 (LR has no write,
        so the value is unused in practice, but it must not crash or return wrong)."""
        self.assertEqual(get_store_size("lr.w"), 4)

    def test_lr_d_returns_8(self):
        self.assertEqual(get_store_size("lr.d"), 8)

    # -- Byte-extraction regression tests for .w AMOs ---------------------------

    def test_amoadd_w_byte_count_from_stdata(self):
        """
        AMO.W post_setup() must only check 4 bytes even though Whisper reports
        stdata as a 64-bit zero-extended value.

        Example: amoadd.w writes 0x2c67af6d → stdata = 0x000000002c67af6d
        Expected bytes (little-endian, 4 only): [6d, af, 67, 2c]
        """
        store_size = get_store_size("amoadd.w")
        stdata_value = 0x000000002C67AF6D
        byte_values = [f"{(stdata_value >> (8 * i)) & 0xFF:02x}" for i in range(store_size)]
        self.assertEqual(len(byte_values), 4)
        self.assertEqual(byte_values, ["6d", "af", "67", "2c"])

    def test_amoswap_d_byte_count_from_stdata(self):
        """AMO.D post_setup() must check all 8 bytes."""
        store_size = get_store_size("amoswap.d")
        stdata_value = 0xDEADBEEFCAFE1234
        byte_values = [f"{(stdata_value >> (8 * i)) & 0xFF:02x}" for i in range(store_size)]
        self.assertEqual(len(byte_values), 8)
        self.assertEqual(byte_values, ["34", "12", "fe", "ca", "ef", "be", "ad", "de"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
