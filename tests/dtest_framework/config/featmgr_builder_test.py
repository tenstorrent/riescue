# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
import argparse
import logging
import tempfile
import json
from unittest.mock import patch
from pathlib import Path

from riescue.lib.rand import RandNum
from riescue.lib.enums import RiscvPrivileges, RiscvPagingModes, RiscvTestEnv, RiscvBaseArch, RiscvSecureModes
from riescue.dtest_framework.config import FeatMgrBuilder, FeatMgr
from riescue.dtest_framework.config.candidate import Candidate
from riescue.dtest_framework.parser import ParsedTestHeader
from riescue.dtest_framework.config.conf import Conf
import riescue.lib.enums as RV

from tests.dtest_framework.config.data.example_conf import CandidateConf, PrivConfig


class FeatMgrBuilderBase(unittest.TestCase):
    "Standalone class so tests aren't repeated"

    def setUp(self):
        self.rng = RandNum(seed=42)  # Fixed seed for reproducibility
        self.builder = FeatMgrBuilder()

    def parse_args(self, args: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        self.builder.add_arguments(parser)
        return parser.parse_args(args)


class TestFeatMgrBuilder(FeatMgrBuilderBase):

    def test_default_construction(self):
        """
        Test builder creates FeatMgr with default values
        Don't use randomization here, since any changes to config will change the values

        Machine + paging selection should also pick DISABLED paging mode since --enable_machine_paging is not set
        """
        # making it so RNG only picks one choice for each field
        self.builder.priv_mode = Candidate(RiscvPrivileges.MACHINE)
        self.builder.paging_mode = Candidate(RiscvPagingModes.SV39)
        self.builder.env = Candidate(RiscvTestEnv.TEST_ENV_BARE_METAL)
        featmgr = self.builder.build(rng=self.rng)

        self.assertIsInstance(featmgr, FeatMgr)
        self.assertEqual(featmgr.priv_mode, RiscvPrivileges.MACHINE)
        self.assertEqual(featmgr.paging_mode, RiscvPagingModes.DISABLE)
        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_BARE_METAL)

    def test_with_args(self):
        """Test builder applies command line arguments correctly"""
        args = self.parse_args(
            [
                "--counter_event_path",
                "events.txt",
                "--test_priv_mode",
                "SUPER",
                "--test_paging_mode",
                "SV39",
                "--test_env",
                "virtualized",
            ]
        )
        featmgr = self.builder.with_args(args).build(rng=self.rng)

        self.assertEqual(featmgr.counter_event_path, Path("events.txt"))
        self.assertEqual(featmgr.paging_mode, RiscvPagingModes.SV39)
        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED)

    def test_with_test_header(self):
        """Test builder applies test header configuration correctly"""
        header = ParsedTestHeader(
            cpus="4",
            arch="rv64i",
            priv="user",
            secure_mode="on",
            env="bare_metal",
            features="wysiwyg.enable rv64.enable",
        )
        self.builder.with_test_header(header)
        self.assertEqual(self.builder.featmgr.num_cpus, 4)
        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(self.builder.featmgr.num_cpus, 4)
        self.assertEqual(featmgr.arch, RiscvBaseArch.ARCH_RV64I)
        self.assertEqual(featmgr.priv_mode, RiscvPrivileges.USER)
        self.assertEqual(featmgr.secure_mode, RiscvSecureModes.SECURE)
        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_BARE_METAL)

    def test_priority_override(self):
        """Test that configuration sources override each other in correct priority"""
        header = ParsedTestHeader(priv="user", paging="sv39", env="bare_metal")
        args = self.parse_args(["--test_priv_mode", "SUPER", "--test_paging_mode", "sv48", "--test_env", "virtualized"])
        featmgr = self.builder.with_test_header(header).with_args(args).build(rng=self.rng)
        self.assertEqual(featmgr.priv_mode, RiscvPrivileges.SUPER)
        self.assertEqual(featmgr.paging_mode, RiscvPagingModes.SV48)
        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED)

    def test_machine_mode_paging_constraints(self):
        """Test that machine mode enforces correct paging constraints"""
        args = self.parse_args(["--test_priv_mode", "machine", "--test_paging_mode", "sv39", "--test_paging_g_mode", "sv48"])

        featmgr = self.builder.with_args(args).build(rng=self.rng)

        self.assertEqual(featmgr.priv_mode, RiscvPrivileges.MACHINE)
        self.assertEqual(featmgr.paging_mode, RiscvPagingModes.DISABLE, "machine mode should force paging to disable")
        self.assertEqual(featmgr.paging_g_mode, RiscvPagingModes.SV48, "Manually setting guest stage paging mode should not be overridden")

    def test_secure_mode_pmp_setup(self):
        """Test that secure mode properly configures PMP setup"""
        args = self.parse_args(["--test_secure_mode", "on"])

        featmgr = self.builder.with_args(args).build(rng=self.rng)

        self.assertEqual(featmgr.secure_mode, RiscvSecureModes.SECURE)
        self.assertTrue(featmgr.setup_pmp)

    def test_tohost_argument(self):
        """Test that secure mode properly configures PMP setup"""
        args = self.parse_args(["--tohost", "0xBEEF"])

        featmgr = self.builder.with_args(args).build(rng=self.rng)

        self.assertEqual(featmgr.io_htif_addr, 0xBEEF)

    def test_paging_mode_supervisor(self):
        """Test that paging mode gets set correctly with supervisor mode"""
        header = ParsedTestHeader(
            priv="super",
            paging="sv39",
        )

        featmgr = self.builder.with_test_header(header).build(rng=self.rng)
        self.assertEqual(featmgr.paging_mode, RiscvPagingModes.SV39)

    def test_mp_mode_off(self):
        header = ParsedTestHeader(
            priv="machine super user any",
            cpus="1",
            env="virtualized bare_metal",
            paging="sv39 sv48 sv57 disable any",
            features="not_hooked_up_yet ext_v.enable ext_fp.disable",
        )

        args = self.parse_args([])
        self.builder = self.builder.with_test_header(header).with_args(args)

        self.assertEqual(self.builder.mp, [RV.RiscvMPEnablement.MP_OFF], "Tests should default to MP_OFF")

    def test_mp_mode_default(self):
        header = ParsedTestHeader(
            priv="machine super user any",
            cpus="1",
            env="virtualized bare_metal",
            paging="sv39 sv48 sv57 disable any",
            features="not_hooked_up_yet ext_v.enable ext_fp.disable",
        )

        args = self.parse_args([])
        feat_mgr = self.builder.with_test_header(header).with_args(args).build(rng=self.rng)
        self.assertEqual(feat_mgr.mp, RV.RiscvMPEnablement.MP_OFF, "Tests should default to MP_OFF")
        self.assertEqual(feat_mgr.mp_mode, RV.RiscvMPMode.MP_PARALLEL, "Tests should default to MP_PARALLEL")

    def test_mp_header_on(self):
        """
        Not sure what the expected behavior is here. ;#test.mp  doesn't seem useful if we are going to fall back to cpus > 2?

        Assuming that the intended behavior is that mp mode set to on in the test header means that test should ALWAYS run with MP_ON, regardless of num cpus or other non-mp fields
        """
        header = ParsedTestHeader(
            mp="on",
        )
        feat_mgr = self.builder.with_test_header(header).build(rng=self.rng)
        self.assertEqual(feat_mgr.mp, RV.RiscvMPEnablement.MP_ON, "Tests should default to MP_ON when mp is set to on ")

    def test_mp_header_on_conflicting_args(self):
        """
        Currently legal behavior
        -> Does it make sense to allow for MP_ON when num_cpus is 1 AND mp is set to MP_ON in the header?
        """
        header = ParsedTestHeader(
            mp="on",
            cpus="1",
        )
        args = self.parse_args(["--mp", "off"])
        feat_mgr = self.builder.with_test_header(header).with_args(args).build(rng=self.rng)
        self.assertEqual(feat_mgr.mp, RV.RiscvMPEnablement.MP_OFF, "Tests should default to MP_OFF when mp is set to off on the command line")

    def test_parallel_scheduling_mode_exhaustive(self):
        """
        Checking that ;#test.parallel_scheduling_mode works as expected
        """
        header = ParsedTestHeader(
            cpus="2",
            parallel_scheduling_mode="exhaustive",
        )
        feat_mgr = self.builder.with_test_header(header).build(rng=self.rng)
        self.assertEqual(feat_mgr.parallel_scheduling_mode, RV.RiscvParallelSchedulingMode.EXHAUSTIVE, "Tests should default to EXHAUSTIVE when parallel_scheduling_mode is set to exhaustive")

    def test_parallel_scheduling_mode_round_robin(self):

        header = ParsedTestHeader(
            cpus="2",
            parallel_scheduling_mode="round_robin",
        )
        feat_mgr = self.builder.with_test_header(header).build(rng=self.rng)
        self.assertEqual(feat_mgr.parallel_scheduling_mode, RV.RiscvParallelSchedulingMode.ROUND_ROBIN, "Tests should default to ROUND_ROBIN when parallel_scheduling_mode is set to round_robin")

    def test_force_alignment_arg(self):
        """
        Test that force alignment is set correctly
        """
        header = ParsedTestHeader(
            priv="machine super user any",
            cpus="1",
            env="virtualized bare_metal",
            paging="sv39 sv48 sv57 disable any",
            features="not_hooked_up_yet ext_v.enable ext_fp.disable",
        )

        parser = argparse.ArgumentParser()
        self.builder.add_arguments(parser)
        args = parser.parse_args(["--force_alignment"])
        self.builder = self.builder.with_test_header(header).with_args(args)

        featmgr = self.builder.build(rng=self.rng)

        self.assertTrue(featmgr.force_alignment)
        self.assertFalse(featmgr.mp_mode_on())

    def test_paging_mode_machine(self):
        """Test that paging mode gets set correctly with supervisor mode"""
        header = ParsedTestHeader(
            priv="machine",
            paging="sv39",
        )

        featmgr = self.builder.with_test_header(header).build(rng=self.rng)
        self.assertEqual(featmgr.paging_mode, RiscvPagingModes.DISABLE)

    def test_tohost_auto_arg(self):
        """the args "--tohost auto" should resut in a io_htif_addr of None"""
        logging.disable(logging.WARNING)
        args = self.parse_args(["--tohost", "auto"])
        self.builder = self.builder.with_args(args)

        featmgr = self.builder.build(rng=self.rng)
        self.assertIsNone(featmgr.io_htif_addr)

    def test_supervisor_virtualized_trap_delegation(self):
        """
        Tests targeting supervisor mode when randomly environment selects virtualized should default to delegating traps to supervisor mode, not machine mode.

        Also checking that --test_env=virtualized overrides the test header's bare_metal
        """
        header = ParsedTestHeader(
            priv="supervisor",
            env="any",
        )

        self.builder = self.builder.with_test_header(header)
        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        self.assertEqual(featmgr.priv_mode, RiscvPrivileges.SUPER)
        self.assertEqual(featmgr.deleg_excp_to, RiscvPrivileges.SUPER)

    def test_eot_args(self):
        "Test that --eot_fail_value and --eot_pass_value work"
        args = self.parse_args(["--eot_fail_value", "0x1000", "--eot_pass_value", "0x2000"])
        self.builder = self.builder.with_args(args)

        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.eot_fail_value, 0x1000)
        self.assertEqual(featmgr.eot_pass_value, 0x2000)

    def test_envcfg_args(self):
        "Check that --menvcfg, --henvcfg, and --senvcfg work"
        args = self.parse_args(["--menvcfg", "0x70", "--henvcfg", "0x70", "--senvcfg", "0x70", "--test_paging_mode", "disable", "--test_priv_mode", "user"])
        self.builder = self.builder.with_args(args)

        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.menvcfg, 0x70)
        self.assertEqual(featmgr.henvcfg, 0x70)
        self.assertEqual(featmgr.senvcfg, 0x70)

    def test_repeat_times_arg(self):
        "Check that --repeat_times=1 is respected"
        args = self.parse_args(["--repeat_times=1"])
        self.builder = self.builder.with_args(args)

        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.repeat_times, 1)

    def test_linux_mode(self):
        "Check that --linux_mode is respected"
        args = self.parse_args(["--linux_mode"])
        self.builder = self.builder.with_args(args)

        featmgr = self.builder.build(rng=self.rng)

        self.assertTrue(featmgr.linux_mode)

    def test_paging_g_mode_sv39(self):
        "Check that --test_paging_g_mode=sv39 is respected"
        args = self.parse_args(["--test_paging_g_mode", "sv39"])
        self.builder = self.builder.with_args(args)

        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.paging_g_mode, RiscvPagingModes.SV39)
        self.assertIn(featmgr.paging_mode, [RV.RiscvPagingModes.DISABLE, RV.RiscvPagingModes.SV39])

    def test_deleg_excp_to_super(self):
        """Test deleg_excp_to argument"""
        args = self.parse_args(["--deleg_excp_to", "super"])
        self.builder = self.builder.with_args(args)
        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.deleg_excp_to, RV.RiscvPrivileges.SUPER)

    def test_deleg_excp_to_machine(self):
        "Check that --delege_excep_to machine works"
        args = self.parse_args(["--deleg_excp_to", "machine"])
        self.builder = self.builder.with_args(args)
        featmgr = self.builder.build(rng=self.rng)
        self.assertEqual(featmgr.deleg_excp_to, RV.RiscvPrivileges.MACHINE)

    def test_supported_priv_modes(self):
        "Check that supported_priv_modes get set correctly from the command line"

        # check default
        args = self.parse_args([])
        self.builder = self.builder.with_args(args)
        featmgr = self.builder.build(rng=self.rng)
        self.assertEqual(featmgr.supported_priv_modes, {RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER, RV.RiscvPrivileges.USER})

        # check M
        args = self.parse_args(["--supported_priv_modes", "M"])
        self.builder = self.builder.with_args(args)
        featmgr = self.builder.build(rng=self.rng)
        self.assertEqual(featmgr.supported_priv_modes, {RV.RiscvPrivileges.MACHINE})
        self.assertEqual(featmgr.priv_mode, RV.RiscvPrivileges.MACHINE, "Only supporting machine mode should set priv_mode to machine")

        # check S
        args = self.parse_args(["--supported_priv_modes", "MS"])
        self.builder = self.builder.with_args(args)
        featmgr = self.builder.build(rng=self.rng)
        self.assertEqual(featmgr.supported_priv_modes, {RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER})
        self.assertIn(featmgr.priv_mode, [RV.RiscvPrivileges.MACHINE, RV.RiscvPrivileges.SUPER], "Only supporting supervisor mode should set priv_mode to machine or supervisor")

    def test_supported_priv_modes_invalid_combo(self):
        """
        Picking conflicting priv modes and supported priv modes should raise an informative error
        """
        args = self.parse_args(["--supported_priv_modes", "MS", "--test_priv_mode", "user"])
        self.builder = self.builder.with_args(args)
        with self.assertRaises(ValueError):
            self.builder.build(rng=self.rng)


class TestFeatMgrBuilderVirtualization(FeatMgrBuilderBase):
    """
    Tests for FeatMgrBuilder that target virtualization.

    """

    def test_virtualized_header_any(self):
        """
        Tests targeting supervisor mode when randomly environment selects virtualized should default to delegating traps to supervisor mode, not machine mode.

        Also checking that --test_env=virtualized overrides the test header's bare_metal
        """
        header = ParsedTestHeader(
            priv="supervisor",
            env="any",
        )

        args = self.parse_args([])
        self.builder = self.builder.with_test_header(header).with_args(args)
        featmgr = self.builder.build(rng=self.rng)

        self.assertNotEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED, "virtualized should only be picked when --test_env=virtualized is specified or --test_env_any is set")

    def test_virtualized_header_any_with_args(self):
        """
        Tests targeting supervisor mode when randomly environment selects virtualized should default to delegating traps to supervisor mode, not machine mode.

        Also checking that --test_env=virtualized overrides the test header's bare_metal
        """
        header = ParsedTestHeader(
            priv="supervisor",
            env="any",
        )

        args = self.parse_args(["--test_env", "virtualized"])
        self.builder = self.builder.with_test_header(header).with_args(args)
        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED, "virtualized should be picked when --test_env=virtualized is specified")

    def test_virtualized_header_virtualized(self):
        """
        Tests targeting supervisor mode when randomly environment selects virtualized should default to delegating traps to supervisor mode, not machine mode.

        Also checking that --test_env=virtualized overrides the test header's bare_metal
        """
        header = ParsedTestHeader(
            priv="supervisor",
            env="virtualized",
        )

        args = self.parse_args([])
        self.builder = self.builder.with_test_header(header).with_args(args)
        featmgr = self.builder.build(rng=self.rng)

        self.assertNotEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED, "virtualized should only be picked when --test_env=virtualized is specified or --test_env_any is set")

    def test_virtualized_header_virtualized_with_args(self):
        """
        Tests targeting supervisor mode when randomly environment selects virtualized should default to delegating traps to supervisor mode, not machine mode.

        Also checking that --test_env=virtualized overrides the test header's bare_metal
        """
        header = ParsedTestHeader(
            priv="supervisor",
            env="virtualized",
            opts="nop?",  # not sure if this is used or not at this point
        )

        args = self.parse_args(["--test_env", "virtualized"])
        self.builder = self.builder.with_test_header(header).with_args(args)
        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED, "virtualized should be picked when --test_env=virtualized is specified")

    def test_csr_init_args(self):
        """
        Test that --csr_init works
        """
        header = ParsedTestHeader(
            priv="super",
            env="virtualized",
            paging="sv39 sv48 sv57",
            paging_g="any",
        )

        args = self.parse_args(["--csr_init", "mstatus=0x8000000A00046800", "--test_priv", "super", "--test_env", "virtualized", "--deleg_excp_to", "machine"])
        self.builder = self.builder.with_args(args)
        featmgr = self.builder.build(rng=self.rng)

        self.assertEqual(featmgr.csr_init, ["mstatus=0x8000000A00046800"])
        self.assertEqual(featmgr.priv_mode, RiscvPrivileges.SUPER)
        self.assertEqual(featmgr.env, RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        self.assertEqual(featmgr.deleg_excp_to, RiscvPrivileges.SUPER, "Currently only support HS mode when virtualized is enabled ")


class TestConfWithBuilder(FeatMgrBuilderBase):
    """
    Tests for Conf that target FeatMgrBuilder.
    """

    def test_conf_prebuild_library(self):
        """
        Test that Conf class passed as an instantiated class works correctly and that the pre_build method is called.
        This should take priority over the test header, and arguments
        """
        self.builder.conf = CandidateConf()
        self.builder.with_test_header(ParsedTestHeader(priv="super"))
        args = self.parse_args(["--test_priv_mode", "user"])
        self.builder.with_args(args)
        for _ in range(10):
            featmgr = self.builder.build(rng=self.rng)
            self.assertEqual(featmgr.priv_mode, RiscvPrivileges.MACHINE)

    def test_conf_postbuild_library(self):
        """
        Test that Conf class passed as an instantiated class works correctly and that the post_build method is called.
        This should take priority over the test header, and arguments
        """
        self.builder.conf = PrivConfig()
        self.builder.with_test_header(ParsedTestHeader(priv="machine"))
        args = self.parse_args(["--test_priv_mode", "user"])
        self.builder.with_args(args)
        for _ in range(10):
            featmgr = self.builder.build(rng=self.rng)
            self.assertEqual(featmgr.priv_mode, RiscvPrivileges.SUPER)


class TestFeatMgrBuilderFeatureDiscovery(FeatMgrBuilderBase):
    """
    Tests for FeatMgrBuilder that target feature and feature discovery.

    Note feature discovery only works when cpuconfig gets parsed first
    """

    def test_feature_discovery_wysiwyg_header(self):
        """
        Test that feature discovery works with wysiwyg in header
        """
        header = ParsedTestHeader(
            priv="machine",
            paging="sv39",
            features="ext_i.enable wysiwyg",
        )
        config = {
            "mmap": {"dram": {"dram0": {"address": "0x8000_0000", "size": "0x1000_0000"}}},
            "features": {"zba": {"enabled": True}},
            "isa": ["c"],
            "mp": "on",
            "mp_mode": "simultaneous",
            "reset_pc": "0x1000",
        }
        with tempfile.NamedTemporaryFile(mode="w") as f:
            json.dump(config, f)
            f.flush()
            featmgr = self.builder.with_test_header(header).with_cpu_json(Path(f.name)).build(rng=self.rng)

        self.assertTrue(featmgr.wysiwyg)
