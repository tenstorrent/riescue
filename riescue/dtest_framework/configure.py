# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from argparse import Namespace
from pathlib import Path

from riescue.lib.rand import RandNum
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.featmanager import TestConfig, FeatMgr
from riescue.dtest_framework.lib.discrete_test import DiscreteTest


def configure(rng: RandNum, pool: Pool, json_config: Path, cmdline_args: Namespace) -> FeatMgr:
    """
    Creates TestConfig instance and FeatMgr instance.

    #TODO: Should this be part of the TestConfig class? And part of the FeatMgr class?

    The basic configuration will be following:
        - number of cpus
        - test environment
        - test privilege
        - test paging mode

    :param rng: Random number generator instance
    :type rng: RandNum
    :param pool: Resource pool instance
    :type pool: Pool
    :param json_config: Path to the json configuration file
    :type json_config: Path
    :param cmdline_args: Command line arguments
    :type cmdline_args: Namespace
    """

    test_config = TestConfig(rng=rng, cmdline_args=cmdline_args)

    test_config.setup_test(
        cpu_header=pool.parsed_test_header.cpus,
        arch_header=pool.parsed_test_header.arch,
        env_header=pool.parsed_test_header.env,
        priv_header=pool.parsed_test_header.priv,
        secure_header=pool.parsed_test_header.secure_mode,
        paging_header=pool.parsed_test_header.paging,
        mp_header=pool.parsed_test_header.mp,
        mp_mode_header=pool.parsed_test_header.mp_mode,
        paging_g_header=pool.parsed_test_header.paging_g,
        features=pool.parsed_test_header.features,
        opts=pool.parsed_test_header.opts,
        parallel_scheduling_mode_header=pool.parsed_test_header.parallel_scheduling_mode if hasattr(pool.parsed_test_header, "parallel_scheduling_mode") else None,
    )

    featmgr = FeatMgr(rng=rng, pool=pool, config_path=json_config, test_config=test_config, cmdline=cmdline_args)

    for test in pool.parsed_discrete_tests.keys():
        dtest = DiscreteTest(name=test, priv=featmgr.priv_mode)
        pool.add_discrete_test(dtest)

    return featmgr
