# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

import riescue.lib.enums as RV
from riescue.dtest_framework.config.builder import FeatMgrBuilder
from riescue.dtest_framework.config.candidate import Candidate
from riescue.dtest_framework.parser import ParsedTestHeader
from riescue.dtest_framework.config.adapaters import TestConfigAdapter
from riescue.lib.rand import RandNum


class TestConfigAdapterTest(unittest.TestCase):
    """
    Test the TestConfigAdapter module.
    """

    def setUp(self):
        self.adapter = TestConfigAdapter()
        self.rng = RandNum(seed=0)

    def test_setup_num_cpus_valid_input(self):
        """Test setup_num_cpus with valid numeric inputs"""
        self.assertEqual(self.adapter.setup_num_cpus("1"), 1)
        self.assertEqual(self.adapter.setup_num_cpus("4"), 4)
        self.assertEqual(self.adapter.setup_num_cpus(" 2 "), 2)  # with whitespace

    def test_setup_num_cpus_plus_notation(self):
        """Test setup_num_cpus with plus notation"""
        self.assertEqual(self.adapter.setup_num_cpus("2+"), 4)
        self.assertEqual(self.adapter.setup_num_cpus("1+"), 4)

    def test_setup_num_cpus_empty_string_fails(self):
        """Test that setup_num_cpus fails with empty string"""
        with self.assertRaises(ValueError):
            self.adapter.setup_num_cpus("")

    def test_setup_num_cpus_whitespace_only_fails(self):
        """Test that setup_num_cpus fails with whitespace-only string"""
        with self.assertRaises(ValueError):
            self.adapter.setup_num_cpus("   ")

    def test_setup_invalid_num_cpus_string_fails(self):
        """Test that setup_num_cpus fails with invalid string"""
        with self.assertRaises(ValueError):
            self.adapter.setup_num_cpus("abc")

    def test_setup_arch_valid_inputs(self):
        """Test setup_arch with valid arch specifications"""
        rv32_candidate = self.adapter.setup_arch("rv32")
        self.assertEqual(rv32_candidate.choose(self.rng), RV.RiscvBaseArch.ARCH_RV32I)

        rv64_candidate = self.adapter.setup_arch("rv64")
        self.assertEqual(rv64_candidate.choose(self.rng), RV.RiscvBaseArch.ARCH_RV64I)

        any_candidate = self.adapter.setup_arch("any")
        result = any_candidate.choose(self.rng)
        self.assertIn(result, [RV.RiscvBaseArch.ARCH_RV32I, RV.RiscvBaseArch.ARCH_RV64I])

    def test_setup_arch_empty_string_fails_on_choose(self):
        """Test that setup_arch with empty string creates empty candidate that fails on choose"""
        empty_candidate = self.adapter.setup_arch("")
        with self.assertRaises(ValueError) as cm:
            empty_candidate.choose(self.rng)
        self.assertEqual(str(cm.exception), "No candidates to choose from")

    def test_setup_env_valid_inputs(self):
        """Test setup_env with valid environment specifications"""
        bare_candidate = self.adapter.setup_env("bare_metal")
        self.assertEqual(bare_candidate.choose(self.rng), RV.RiscvTestEnv.TEST_ENV_BARE_METAL)

        virt_candidate = self.adapter.setup_env("virtualized")
        self.assertEqual(virt_candidate.choose(self.rng), RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)

        any_candidate = self.adapter.setup_env("any")
        result = any_candidate.choose(self.rng)
        self.assertIn(result, [RV.RiscvTestEnv.TEST_ENV_BARE_METAL, RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED])

    def test_setup_env_empty_string_fails_on_choose(self):
        """Test that setup_env with empty string fails on choose"""
        empty_candidate = self.adapter.setup_env("")
        with self.assertRaises(ValueError):
            empty_candidate.choose(self.rng)

    def test_setup_priv_valid_inputs(self):
        """Test setup_priv with valid privilege specifications"""
        machine_candidate = self.adapter.setup_priv("machine")
        self.assertEqual(machine_candidate.choose(self.rng), RV.RiscvPrivileges.MACHINE)

        super_candidate = self.adapter.setup_priv("super")
        self.assertEqual(super_candidate.choose(self.rng), RV.RiscvPrivileges.SUPER)

        user_candidate = self.adapter.setup_priv("user")
        self.assertEqual(user_candidate.choose(self.rng), RV.RiscvPrivileges.USER)

        any_candidate = self.adapter.setup_priv("any")
        result = any_candidate.choose(self.rng)
        self.assertIn(result, [RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER, RV.RiscvPrivileges.USER])

    def test_setup_priv_empty_string_fails_on_choose(self):
        """Test that setup_priv with empty string fails on choose"""
        empty_candidate = self.adapter.setup_priv("")
        with self.assertRaises(ValueError):
            empty_candidate.choose(self.rng)

    def test_setup_secure_mode_valid_inputs(self):
        """Test setup_secure_mode with valid inputs"""
        on_candidate = self.adapter.setup_secure_mode("on")
        self.assertEqual(on_candidate.choose(self.rng), RV.RiscvSecureModes.SECURE)

        off_candidate = self.adapter.setup_secure_mode("off")
        self.assertEqual(off_candidate.choose(self.rng), RV.RiscvSecureModes.NON_SECURE)

        any_candidate = self.adapter.setup_secure_mode("any")
        result = any_candidate.choose(self.rng)
        self.assertIn(result, [RV.RiscvSecureModes.SECURE, RV.RiscvSecureModes.NON_SECURE])

    def test_setup_secure_mode_empty_string_has_default(self):
        """Test that setup_secure_mode with empty string defaults to NON_SECURE"""
        empty_candidate = self.adapter.setup_secure_mode("")
        self.assertEqual(empty_candidate.choose(self.rng), RV.RiscvSecureModes.NON_SECURE)

    def test_setup_paging_mode_valid_inputs(self):
        """Test setup_paging_mode with valid inputs"""
        disable_candidate = self.adapter.setup_paging_mode("disable")
        self.assertEqual(disable_candidate.choose(self.rng), RV.RiscvPagingModes.DISABLE)

        sv39_candidate = self.adapter.setup_paging_mode("sv39")
        self.assertEqual(sv39_candidate.choose(self.rng), RV.RiscvPagingModes.SV39)

        enable_candidate = self.adapter.setup_paging_mode("enable")
        result = enable_candidate.choose(self.rng)
        self.assertIn(result, [RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.SV48, RV.RiscvPagingModes.SV57])

    def test_setup_paging_mode_empty_string_fails_on_choose(self):
        """Test that setup_paging_mode with empty string fails on choose"""
        empty_candidate = self.adapter.setup_paging_mode("")
        with self.assertRaises(ValueError):
            empty_candidate.choose(self.rng)

    def test_setup_mp_mode_valid_inputs_parallel(self):
        """Test setup_mp_mode with valid inputs for parallel mode"""
        on = self.adapter.setup_mp("on")
        self.assertEqual(on, RV.RiscvMPEnablement.MP_ON)

        off = self.adapter.setup_mp("off")
        self.assertEqual(off, RV.RiscvMPEnablement.MP_OFF)

        any_candidate = self.adapter.setup_mp("any")
        self.assertEqual(any_candidate, [RV.RiscvMPEnablement.MP_ON, RV.RiscvMPEnablement.MP_OFF])

        no_header_candidate = self.adapter.setup_mp("")
        self.assertEqual(no_header_candidate, RV.RiscvMPEnablement.MP_OFF)

    def test_setup_mp_mode_valid_inputs(self):
        """Test setup_mp_mode with valid inputs"""
        simul_candidate = self.adapter.setup_mp_mode("simultaneous")
        self.assertEqual(simul_candidate.choose(self.rng), RV.RiscvMPMode.MP_SIMULTANEOUS)

        parallel_candidate = self.adapter.setup_mp_mode("parallel")
        self.assertEqual(parallel_candidate.choose(self.rng), RV.RiscvMPMode.MP_PARALLEL)

        any_candidate = self.adapter.setup_mp_mode("any")
        self.assertEqual(any_candidate, [RV.RiscvMPMode.MP_SIMULTANEOUS, RV.RiscvMPMode.MP_PARALLEL])

    def test_setup_mp_mode_empty_string_has_default(self):
        """Test that setup_mp_mode with empty string defaults to MP_PARALLEL"""
        empty_candidate = self.adapter.setup_mp_mode("")
        self.assertEqual(empty_candidate.choose(self.rng), RV.RiscvMPMode.MP_PARALLEL)

    def test_setup_parallel_scheduling_mode_valid_inputs(self):
        """Test setup_parallel_scheduling_mode with valid inputs"""
        rr_candidate = self.adapter.setup_parallel_scheduling_mode("round_robin")
        self.assertEqual(rr_candidate.choose(self.rng), RV.RiscvParallelSchedulingMode.ROUND_ROBIN)

        exhaustive_candidate = self.adapter.setup_parallel_scheduling_mode("exhaustive")
        self.assertEqual(exhaustive_candidate.choose(self.rng), RV.RiscvParallelSchedulingMode.EXHAUSTIVE, "Only option is exhaustive, should have picked exhaustive")

    def test_setup_parallel_scheduling_mode_none_and_empty_have_default(self):
        """Test that setup_parallel_scheduling_mode with empty defaults to ROUND_ROBIN"""
        empty_candidate = self.adapter.setup_parallel_scheduling_mode("")
        self.assertEqual(empty_candidate.choose(self.rng), RV.RiscvParallelSchedulingMode.ROUND_ROBIN)
        self.assertNotIn(RV.RiscvParallelSchedulingMode.EXHAUSTIVE, empty_candidate, "Exhaustive shouldn't have been picked if none provided")

    def test_setup_mp_valid_inputs(self):
        """Test setup_mp with valid inputs"""
        on_candidate = self.adapter.setup_mp("on")
        self.assertEqual(on_candidate.choose(self.rng), RV.RiscvMPEnablement.MP_ON)

        off_candidate = self.adapter.setup_mp("off")
        self.assertEqual(off_candidate.choose(self.rng), RV.RiscvMPEnablement.MP_OFF)

    def test_setup_mp_empty_string_has_default(self):
        """Test that setup_mp with empty string defaults to MP_OFF"""
        empty_candidate = self.adapter.setup_mp("")
        self.assertEqual(empty_candidate.choose(self.rng), RV.RiscvMPEnablement.MP_OFF)

    def test_setup_test_opts_valid_inputs(self):
        """Test setup_test_opts with valid option strings"""
        opts = self.adapter.setup_test_opts("key1=value1 key2=value2")
        self.assertEqual(opts, {"key1": "value1", "key2": "value2"})

        single_opt = self.adapter.setup_test_opts("debug=true")
        self.assertEqual(single_opt, {"debug": "true"})

    def test_setup_test_opts_empty_string(self):
        """Test that setup_test_opts with empty string returns empty dict"""
        empty_opts = self.adapter.setup_test_opts("")
        self.assertEqual(empty_opts, {})

    def test_apply_method_with_minimal_valid_header(self):
        """Test the apply method with minimal valid ParsedTestHeader"""
        builder = FeatMgrBuilder()
        valid_header = ParsedTestHeader(cpus="1", arch="rv64", env="bare_metal", priv="machine")

        result_builder = self.adapter.apply(builder, valid_header)
        self.assertIsInstance(result_builder, FeatMgrBuilder)


class TestConfigAdapterFeatureDiscovery(unittest.TestCase):
    """
    Test the TestConfigAdapter module for feature discovery.
    """

    def setUp(self):
        self.adapter = TestConfigAdapter()
        self.rng = RandNum(seed=0)

    def test_setup_features_valid_inputs(self):
        """Test setup_features with valid feature strings"""
        features = self.adapter.setup_features("ext_v.enable ext_fp.disable")
        self.assertEqual(features, ["ext_v.enable", "ext_fp.disable"])

        single_feature = self.adapter.setup_features("ext_zicbom.enable")
        self.assertEqual(single_feature, ["ext_zicbom.enable"])

    def test_setup_features_empty_string(self):
        """Test that setup_features with empty string returns empty list"""
        empty_features = self.adapter.setup_features("")
        self.assertEqual(empty_features, [])
