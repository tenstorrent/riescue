# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import argparse
import tempfile
from pathlib import Path

import riescue.lib.enums as RV

from riescue.dtest_framework.config.builder import FeatMgrBuilder
from riescue.dtest_framework.config.adapaters import CliAdapter
from riescue.dtest_framework.config.cmdline import add_arguments
from riescue.lib.rand import RandNum


class CliAdapterTest(unittest.TestCase):
    """Tests for CLI adapter (and indirectly cmdline.py)"""

    def setUp(self):
        self.adapter = CliAdapter()
        self.parser = argparse.ArgumentParser()
        add_arguments(self.parser)

        self.rng = RandNum(seed=0)
        self.builder = FeatMgrBuilder(rng=self.rng)
        self.temp_dir = tempfile.mkdtemp()

    def test_num_cpus_override(self):
        """Test that num_cpus command line argument overrides default"""
        args = self.parser.parse_args(args=["--num_cpus", "4"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(result.featmgr.num_cpus, 4)
        self.assertEqual([m for m in result.mp], [RV.RiscvMPEnablement.MP_ON])

    def test_mp_enablement_explicit_override(self):
        """Test MP enablement explicitly set overrides num_cpus auto-setting"""
        args = self.parser.parse_args(args=["--num_cpus", "4", "--mp", "off"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(result.featmgr.num_cpus, 4)
        self.assertEqual([m for m in result.mp], [RV.RiscvMPEnablement.MP_OFF])

    def test_mp_enablement_single_cpu_no_auto_mp(self):
        """Test single CPU doesn't auto-enable MP"""
        args = self.parser.parse_args(args=["--num_cpus", "1"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(result.featmgr.num_cpus, 1)
        self.assertIsNotNone(result.mp)

    def test_secure_mode_on_enables_pmp(self):
        """Test secure mode 'on' automatically enables PMP setup"""
        args = self.parser.parse_args(args=["--test_secure_mode", "on"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual([s for s in result.secure_mode], [RV.RiscvSecureModes.SECURE])
        self.assertTrue(result.featmgr.setup_pmp)

    def test_secure_mode_random_distribution(self):
        """Test secure mode 'random' creates weighted distribution"""
        args = self.parser.parse_args(args=["--test_secure_mode", "random"])
        result = self.adapter.apply(self.builder, args)
        candidates = [s for s in result.secure_mode]
        self.assertIn(RV.RiscvSecureModes.SECURE, candidates)
        self.assertIn(RV.RiscvSecureModes.NON_SECURE, candidates)
        secure_count = candidates.count(RV.RiscvSecureModes.SECURE)
        non_secure_count = candidates.count(RV.RiscvSecureModes.NON_SECURE)
        total = secure_count + non_secure_count
        self.assertAlmostEqual(secure_count / total, 0.2, places=1)
        self.assertAlmostEqual(non_secure_count / total, 0.8, places=1)

    def test_fe_tb_disables_paging_with_machine_mode(self):
        """Test FE_TB mode forces paging to disable with machine privilege"""
        args = self.parser.parse_args(args=["--fe_tb", "--test_priv_mode", "machine"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual([p for p in result.paging_mode], [RV.RiscvPagingModes.DISABLE])
        self.assertEqual([p for p in result.paging_g_mode], [RV.RiscvPagingModes.DISABLE])

    def test_machine_mode_disables_guest_paging(self):
        """Test machine privilege mode disables guest stage paging unless explicitly set"""
        args = self.parser.parse_args(args=["--test_priv_mode", "machine"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(len(result.paging_g_mode), 1)
        self.assertEqual(result.paging_g_mode.choose(self.rng), RV.RiscvPagingModes.DISABLE)

    def test_paging_mode_any_override(self):
        """Test paging mode 'ANY' or 'ENABLE' doesn't set specific mode"""
        args = self.parser.parse_args(args=["--test_paging_mode", "ANY"])
        result = self.adapter.apply(self.builder, args)
        count = len(result.paging_mode)
        self.assertGreater(count, 1, "ANY should set multiple paging candidates")

    def test_paging_mode_specific_value(self):
        """Test specific paging mode value gets set correctly"""
        args = self.parser.parse_args(args=["--test_paging_mode", "sv39"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual([p for p in result.paging_mode], [RV.RiscvPagingModes.SV39])

    def test_enum_conversion_invalid_mp_mode(self):
        """Test invalid MP mode string raises ValueError"""
        args = self.parser.parse_args(args=["--mp_mode", "invalid"])
        with self.assertRaises(ValueError):
            self.adapter.apply(self.builder, args)

    def test_privilege_mode_case_insensitive(self):
        """Test privilege mode is case insensitive"""
        args = self.parser.parse_args(args=["--test_priv_mode", "user"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual([p for p in result.priv_mode], [RV.RiscvPrivileges.USER])

        args = self.parser.parse_args(args=["--test_priv_mode", "MACHINE"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual([p for p in result.priv_mode], [RV.RiscvPrivileges.MACHINE])

    def test_probability_arguments_set_correctly(self):
        """Test probability arguments are set when provided"""
        args = self.parser.parse_args(args=["--a_d_bit_randomization", "50", "--secure_access_probability", "75", "--secure_pt_probability", "25", "--pbmt_ncio_randomization", "30"])
        result = self.adapter.apply(self.builder, args).featmgr
        self.assertEqual(result.a_d_bit_randomization, 50)
        self.assertEqual(result.secure_access_probability, 75)
        self.assertEqual(result.secure_pt_probability, 25)
        self.assertEqual(result.pbmt_ncio_randomization, 30)

    def test_store_true_flags_set_correctly(self):
        """Test store_true flags are properly transferred"""
        args = self.parser.parse_args(args=["--tohost_nonzero_terminate", "--single_assembly_file", "--force_alignment", "--c_used", "--big_endian"])
        result = self.adapter.apply(self.builder, args).featmgr
        self.assertTrue(result.tohost_nonzero_terminate)
        self.assertTrue(result.single_assembly_file)
        self.assertTrue(result.force_alignment)
        self.assertTrue(result.c_used)
        self.assertTrue(result.big_endian)

    def test_mutually_exclusive_bss_flags(self):
        """Test small_bss and big_bss can be set simultaneously (no mutual exclusion)"""
        args = self.parser.parse_args(args=["--small_bss", "--big_bss"])
        result = self.adapter.apply(self.builder, args).featmgr
        self.assertTrue(result.small_bss)
        self.assertTrue(result.big_bss)

    def test_complex_virtualization_setup(self):
        """Test complex virtualization environment setup"""
        args = self.parser.parse_args(args=["--test_env", "virtualized", "--test_priv_mode", "super", "--test_paging_g_mode", "sv39", "--vmm_hooks"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual([e for e in result.env], [RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED])
        self.assertEqual([p for p in result.priv_mode], [RV.RiscvPrivileges.SUPER])
        self.assertEqual([p for p in result.paging_g_mode], [RV.RiscvPagingModes.SV39])
        self.assertTrue(result.featmgr.vmm_hooks)

    def test_multiprocessor_complex_setup(self):
        """Test complex multiprocessor configuration"""
        args = self.parser.parse_args(args=["--num_cpus", "8", "--mp", "on", "--mp_mode", "parallel", "--parallel_scheduling_mode", "exhaustive"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(result.featmgr.num_cpus, 8)
        self.assertEqual([m for m in result.mp], [RV.RiscvMPEnablement.MP_ON])
        self.assertEqual([m for m in result.mp_mode], [RV.RiscvMPMode.MP_PARALLEL])
        self.assertEqual([s for s in result.parallel_scheduling_mode], [RV.RiscvParallelSchedulingMode.EXHAUSTIVE])

    def test_pma_pmp_configuration(self):
        """Test PMA/PMP related configuration"""
        args = self.parser.parse_args(args=["--setup_pmp", "--needs_pma", "--num_pmas", "32"])
        result = self.adapter.apply(self.builder, args).featmgr
        self.assertTrue(result.setup_pmp)
        self.assertTrue(result.needs_pma)
        self.assertEqual(result.num_pmas, 32)

    def test_interrupt_and_exception_handling(self):
        """Test interrupt and exception handling configuration"""
        args = self.parser.parse_args(args=["--user_interrupt_table", "--excp_hooks", "--interrupts_enabled", "--skip_instruction_for_unexpected", "--disable_wfi_wait"])
        result = self.adapter.apply(self.builder, args).featmgr
        self.assertTrue(result.user_interrupt_table)
        self.assertTrue(result.excp_hooks)
        self.assertTrue(result.interrupts_enabled)
        self.assertTrue(result.skip_instruction_for_unexpected)
        self.assertTrue(result.disable_wfi_wait)

    def test_address_generation_flags(self):
        """Test address generation related flags"""
        args = self.parser.parse_args(args=["--reserve_partial_phys_memory", "--all_4kb_pages", "--disallow_mmio", "--addrgen_limit_way_predictor_multihit", "--addrgen_limit_indices"])
        result = self.adapter.apply(self.builder, args).featmgr
        self.assertTrue(result.reserve_partial_phys_memory)
        self.assertTrue(result.all_4kb_pages)
        self.assertTrue(result.disallow_mmio)
        self.assertTrue(result.addrgen_limit_way_predictor_multihit)
        self.assertTrue(result.addrgen_limit_indices)

    def test_tohost_argument(self):
        """Test tohost argument"""
        args = self.parser.parse_args(args=["--tohost", "0xBEEF"])
        result = self.adapter.apply(self.builder, args).featmgr
        self.assertEqual(result.io_htif_addr, 0xBEEF)

    def test_deleg_excp_to_super(self):
        """Test deleg_excp_to argument"""
        args = self.parser.parse_args(args=["--deleg_excp_to", "super"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(result.deleg_excp_to, RV.RiscvPrivileges.SUPER)

    def test_deleg_excp_to_machine(self):
        "Check that --delege_excep_to machine works"
        args = self.parser.parse_args(args=["--deleg_excp_to", "machine"])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(result.deleg_excp_to, RV.RiscvPrivileges.MACHINE)

    def test_deleg_excp_to_any(self):
        "Check that leaving deleg_excp_to unset works"
        args = self.parser.parse_args(args=[])
        result = self.adapter.apply(self.builder, args)
        self.assertEqual(result.deleg_excp_to, [RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER])
