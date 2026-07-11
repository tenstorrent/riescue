# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for discontiguous / arbitrary hart-id support in the RiescueD runtime.

Covers the shared pieces every generator relies on:
- ``Routines.place_hartid_to_index`` conversion helper + ``VariableManager`` table emission
- ``VariableManager`` emitting the lookup table + mhartid->index conversion only when
  hart IDs are discontiguous (default contiguous output is unchanged)
- ``HartContext`` storing each hart's real mhartid value
"""

import unittest

import riescue.lib.enums as RV
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.runtime import Runtime
from riescue.dtest_framework.runtime.loader import Loader
from riescue.dtest_framework.runtime.variable.manager import VariableManager
from riescue.dtest_framework.runtime.variable.hart_memory import HartContext
from riescue.lib.rand import RandNum


class RoutinesHartIdTest(unittest.TestCase):
    def test_hartid_to_index_uses_unique_labels(self):
        code = Routines.place_hartid_to_index("t0", "t2", "t3", RV.Xlen.XLEN64, label_suffix="abc")
        self.assertIn(".L_hartid_to_index_loop_abc", code)
        self.assertIn(".L_hartid_to_index_done_abc", code)
        # searches the shared table and shifts by log2(8)=3 for the .dword stride
        self.assertIn("hart_id_table", code)
        self.assertIn("srli t0, t0, 3", code)

    def test_hartid_to_index_rv32_shift(self):
        code = Routines.place_hartid_to_index("t0", "t2", "t3", RV.Xlen.XLEN32, label_suffix="rv32")
        self.assertIn("lw t3, 0(t2)", code)
        self.assertIn("srli t0, t0, 2", code)  # log2(4) for the .word stride

    def test_hartid_to_index_is_a_plain_lookup(self):
        # The index lookup assumes the mhartid is valid (validated once in the loader), so it
        # carries no bounds check / end-marker of its own.
        code = Routines.place_hartid_to_index("t0", "t2", "t3", RV.Xlen.XLEN64, label_suffix="nb")
        self.assertNotIn("hart_id_table_end", code)


class VariableManagerHartIdTest(unittest.TestCase):
    def _vm(self, hart_ids):
        count = len(hart_ids) if hart_ids is not None else 3
        return VariableManager(
            data_section_name="hart_context",
            xlen=RV.Xlen.XLEN64,
            hart_count=count,
            amo_enabled=True,
            hart_ids=hart_ids,
        )

    def test_contiguous_default_has_no_table_or_conversion(self):
        vm = self._vm(None)
        self.assertFalse(vm.discontiguous_hartids)
        code = vm.initialize(scratch_regs=["mscratch"])
        self.assertNotIn("hart_id_table", code)
        self.assertNotIn("_hartid_to_index_loop", code)

    def test_explicit_contiguous_matches_default(self):
        vm = self._vm([0, 1, 2])
        self.assertFalse(vm.discontiguous_hartids)
        code = vm.initialize(scratch_regs=["mscratch"])
        self.assertNotIn("hart_id_table", code)

    def test_discontiguous_emits_table_and_conversion(self):
        vm = self._vm([0, 2, 4])
        self.assertTrue(vm.discontiguous_hartids)
        code = vm.initialize(scratch_regs=["mscratch"])
        self.assertIn("hart_id_table:", code)
        self.assertIn(".dword 4", code)
        self.assertIn("_hartid_to_index_loop", code)

    def test_single_discontiguous_hart_emits_table(self):
        vm = self._vm([5])
        self.assertTrue(vm.discontiguous_hartids)
        code = vm.initialize(scratch_regs=["mscratch"])
        self.assertIn("hart_id_table:", code)
        self.assertIn(".dword 5", code)

    def test_rv32_table_uses_word_directive(self):
        vm = VariableManager(
            data_section_name="hart_context",
            xlen=RV.Xlen.XLEN32,
            hart_count=2,
            amo_enabled=True,
            hart_ids=[0, 7],
        )
        code = vm.initialize(scratch_regs=["mscratch"])
        self.assertIn("hart_id_table:", code)
        self.assertIn(".word 7", code)
        self.assertNotIn(".dword", code)

    def test_hart_ids_length_must_match_count(self):
        with self.assertRaises(ValueError):
            VariableManager(
                data_section_name="hart_context",
                xlen=RV.Xlen.XLEN64,
                hart_count=2,
                amo_enabled=True,
                hart_ids=[0, 2, 4],
            )

    def test_discontiguous_table_emits_end_marker(self):
        # The end marker lets the loader's validation bound its search of hart_id_table.
        vm = self._vm([0, 2, 4])
        code = vm.initialize(scratch_regs=["mscratch"])
        self.assertIn("hart_id_table:", code)
        self.assertIn("hart_id_table_end:", code)

    def test_hart_index_variable_always_registered(self):
        # hart_index exists for both contiguous and discontiguous so GET_HART_INDEX has no
        # special case. get_variable would raise if it were missing.
        self.assertIsNotNone(self._vm(None).get_variable("hart_index"))
        self.assertIsNotNone(self._vm([0, 2, 4]).get_variable("hart_index"))


class LoaderValidateHartidTest(unittest.TestCase):
    """The loader validates each hart's mhartid exactly once, before hart_context_loader."""

    def _loader(self, num_cpus, hart_ids=None) -> Loader:
        featmgr = FeatMgr(num_cpus=num_cpus, hart_ids=hart_ids)
        runtime = Runtime(rng=RandNum(seed=1), pool=Pool(), featmgr=featmgr)
        loader = runtime._modules["loader"]
        assert isinstance(loader, Loader)
        return loader

    def test_single_hart_emits_no_validation(self):
        # Single-hart tests keep the byte-for-byte unchanged boot path.
        self.assertEqual(self._loader(num_cpus=1).validate_hartid(), "")
        self.assertEqual(self._loader(num_cpus=1, hart_ids=[5]).validate_hartid(), "")

    def test_contiguous_mp_range_checks_mhartid(self):
        code = self._loader(num_cpus=2).validate_hartid()
        self.assertIn("csrr t0, mhartid", code)
        self.assertIn("li t1, 2", code)
        self.assertIn("bgeu t0, t1, loader__bad_hartid", code)
        self._assert_coordinated_fail(code)

    def test_discontiguous_mp_searches_table(self):
        code = self._loader(num_cpus=2, hart_ids=[0, 2]).validate_hartid()
        self.assertIn("csrr t0, mhartid", code)
        self.assertIn("la t1, hart_id_table", code)
        # Bounded by the end marker: walking off the end fails the test.
        self.assertIn("la t2, hart_id_table_end", code)
        self.assertIn("beq t1, t2, loader__bad_hartid", code)
        self.assertIn("ld t2, 0(t1)", code)
        self._assert_coordinated_fail(code)

    def _assert_coordinated_fail(self, code):
        # A bad hart must join the coordinated MP end-of-test, not write tohost directly.
        self.assertIn("loader__bad_hartid:", code)
        self.assertIn("li gp, 0", code)
        self.assertIn("j eot__end_test", code)
        self.assertNotIn("j eot__failed", code)


class HartContextMhartidTest(unittest.TestCase):
    def _ctx_with_mhartid(self):
        ctx = HartContext(xlen=RV.Xlen.XLEN64, amo_enabled=True)
        ctx.register(name="mhartid", value=0, description="mhartid")
        return ctx

    def test_allocate_stores_real_mhartid_value(self):
        ctx = self._ctx_with_mhartid()
        # hart at sequential index 1 has real mhartid 2
        code = ctx.allocate(hart_id=1, mhartid_value=2)
        self.assertIn("hart_context_1:", code)
        # the mhartid variable is emitted with its real value on the same (commented) line
        mhartid_lines = [ln for ln in code.splitlines() if "mhartid" in ln]
        self.assertTrue(mhartid_lines, "expected an mhartid variable line")
        self.assertTrue(any(" 2 " in ln for ln in mhartid_lines), mhartid_lines)

    def test_allocate_defaults_mhartid_to_index(self):
        ctx = self._ctx_with_mhartid()
        code = ctx.allocate(hart_id=3)  # no mhartid_value -> defaults to hart_id
        mhartid_lines = [ln for ln in code.splitlines() if "mhartid" in ln]
        self.assertTrue(any(" 3 " in ln for ln in mhartid_lines), mhartid_lines)

    def test_allocate_stores_sequential_hart_index(self):
        ctx = self._ctx_with_mhartid()
        ctx.register(name="hart_index", value=0, description="hart_index")
        # hart at sequential index 1 with real mhartid 2: hart_index stores 1, mhartid stores 2
        code = ctx.allocate(hart_id=1, mhartid_value=2)
        index_lines = [ln for ln in code.splitlines() if "hart_index" in ln]
        self.assertTrue(any(" 1 " in ln for ln in index_lines), index_lines)


if __name__ == "__main__":
    unittest.main()
