# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from riescue.dtest_framework.generator.generator import Generator
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.config.memory import Memory, DramRange
from riescue.dtest_framework.parser import ParsedPmaHint
from riescue.dtest_framework.lib.discrete_test import DiscreteTest
import riescue.lib.enums as RV
from riescue.lib.rand import RandNum


class PmaIntegrationTest(unittest.TestCase):
    """Test PMA hint integration with generator"""

    def setUp(self):
        """Set up test fixtures"""
        self.rng = RandNum(seed=42)
        self.pool = Pool()
        # Add a dummy discrete_test to pool to prevent IndexError in Loader
        # This is needed when bringup_pagetables is True
        dummy_test = DiscreteTest(name="dummy_test", priv=RV.RiscvPrivileges.MACHINE)
        self.pool.discrete_tests["dummy_test"] = dummy_test

        # Use MagicMock with spec_set=False to allow attribute assignment
        # Then explicitly configure all needed attributes
        self.featmgr = MagicMock(spec=FeatMgr)
        self.featmgr.memory = Memory(dram_ranges=[DramRange(start=0x80000000, size=0x10000000)])
        self.featmgr.cpu_config = None
        self.featmgr.addrgen_limit_indices = False
        self.featmgr.addrgen_limit_way_predictor_multihit = False
        # Mock feature attribute for Runtime initialization
        self.featmgr.feature = MagicMock()
        self.featmgr.feature.is_enabled = MagicMock(return_value=True)
        self.featmgr.num_cpus = 1
        # Set paging_mode to a valid enum value (required by Generator.__init__)
        self.featmgr.paging_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.paging_g_mode = RV.RiscvPagingModes.DISABLE
        self.featmgr.env = RV.RiscvTestEnv.TEST_ENV_BARE_METAL
        # Disable bringup_pagetables to avoid Loader requiring discrete_tests
        # Use configure_mock to ensure it's properly set
        self.featmgr.configure_mock(bringup_pagetables=False)

    def test_generator_with_pma_hint(self):
        """Test generator processes PMA hints"""
        # Add a PMA hint to pool
        hint = ParsedPmaHint(name="test_hint", combinations=[{"memory_type": "memory", "cacheability": "cacheable", "rwx": "rwx"}])
        self.pool.add_parsed_pma_hint(hint)

        # Create generator
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Check that PMA regions were generated
        consolidated = generator.pool.pma_regions.consolidated_entries()
        # Should have at least the generated region (plus default DRAM/IO regions)
        self.assertGreater(len(consolidated), 0)

        # Check that our generated region exists
        generated_regions = [r for r in consolidated if r.pma_name.startswith("pma_test_hint")]
        self.assertGreater(len(generated_regions), 0)

    def test_generator_with_pma_config(self):
        """Test generator processes PMA config from cpu_config"""
        from riescue.dtest_framework.config.pma_config import PmaConfig, PmaRegionConfig, PmaAttributes

        # Create PMA config
        region_cfg = PmaRegionConfig(name="config_region", base=0x90000000, size=0x1000000, attributes=PmaAttributes(memory_type="memory", cacheability="cacheable"))
        pma_config = PmaConfig(regions=[region_cfg])

        # Mock cpu_config with PMA config
        self.featmgr.cpu_config = MagicMock()
        self.featmgr.cpu_config.pma_config = pma_config

        # Create generator
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Check that configured region exists
        consolidated = generator.pool.pma_regions.consolidated_entries()
        config_regions = [r for r in consolidated if r.pma_name == "config_region"]
        self.assertEqual(len(config_regions), 1)
        self.assertEqual(config_regions[0].pma_address, 0x90000000)

    def test_generator_with_hints_and_config(self):
        """Test generator with both hints and config"""
        from riescue.dtest_framework.config.pma_config import PmaConfig, PmaRegionConfig, PmaAttributes

        # Add hint
        hint = ParsedPmaHint(name="hint1", combinations=[{"memory_type": "memory", "cacheability": "noncacheable", "rwx": "rwx"}])
        self.pool.add_parsed_pma_hint(hint)

        # Add config
        region_cfg = PmaRegionConfig(name="config_region", base=0x90000000, size=0x1000000, attributes=PmaAttributes(memory_type="memory"))
        pma_config = PmaConfig(regions=[region_cfg])
        self.featmgr.cpu_config = MagicMock()
        self.featmgr.cpu_config.pma_config = pma_config

        # Create generator
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Check both exist
        consolidated = generator.pool.pma_regions.consolidated_entries()
        config_regions = [r for r in consolidated if r.pma_name == "config_region"]
        hint_regions = [r for r in consolidated if r.pma_name.startswith("pma_hint1")]

        self.assertEqual(len(config_regions), 1)
        self.assertGreater(len(hint_regions), 0)

    def test_generator_without_pma(self):
        """Test generator works without PMA hints/config"""
        # Create generator without hints or config
        generator = Generator(self.rng, self.pool, self.featmgr)

        # Should still work - just uses default PMA regions
        consolidated = generator.pool.pma_regions.consolidated_entries()
        self.assertGreater(len(consolidated), 0)  # Should have default DRAM/IO regions


if __name__ == "__main__":
    unittest.main()
