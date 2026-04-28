# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.riescuec import RiescueC
from tests.cli_tests.riescuec.base import BaseRiescueCTest

# Config derived from ACT4 whisper-rv64-max:
# https://github.com/riscv/riscv-arch-test/tree/act4/config/whisper/whisper-rv64-max
ACT4_CONFIG = "dtest_framework/lib/act4_whisper_cpu_config.json"
ACT4_WHISPER_CONFIG = "dtest_framework/lib/act4_whisper_config.json"

# Config derived from ACT4 spike-rv64-max:
# https://github.com/riscv/riscv-arch-test/tree/act4/config/spike/spike-rv64-max
ACT4_SPIKE_CONFIG = "dtest_framework/lib/act4_spike_cpu_config.json"
# ISA string from ACT4 spike-rv64-max run_cmd.txt
ACT4_SPIKE_ISA = (
    "rv64imafdcbv_zicbom_zicboz_zicbop_zicfilp_zicond_zicsr_zicntr_zicclsm"
    "_zifencei_zihintntl_zihintpause_zihpm_zimop_zabha_zacas_zawrs_zfa"
    "_zfbfmin_zfh_zcb_zcmop_zbc_zkn_zks_zkr_zvfbfmin_zvfbfwma_zvfh_zvbb"
    "_zvbc_zvkg_zvkned_zvknha_zvknhb_zvksed_zvksh_zvkt_sscofpmf_smcntrpmf"
    "_sstc_svinval"
)


class Act4Rv64MaxBringupTest(BaseRiescueCTest):
    """
    RiescueC bringup-mode tests using the ACT4 whisper-rv64-max config.
    """

    def test_rv64i(self):
        "RV64I bringup test with ACT4 whisper-rv64-max config"
        args = (
            "--json compliance/tests/rv_i/rv64i.json"
            f" --cpuconfig {ACT4_CONFIG}"
            f" --whisper_config_json {ACT4_WHISPER_CONFIG}"
            " --first_pass_iss whisper --second_pass_iss whisper"
            " --max_instrs 5000 --rpt_cnt 5 --seed 0 --setup_pmp"
        )
        RiescueC.run_cli(args=args.split())

    def test_rv64i_virtualized(self):
        "RV64I bringup test in virtualized supervisor mode with ACT4 whisper-rv64-max config"
        args = (
            "--json compliance/tests/rv_i/rv64i.json"
            f" --cpuconfig {ACT4_CONFIG}"
            f" --whisper_config_json {ACT4_WHISPER_CONFIG}"
            " --privilege_mode supervisor --test_env virtualized"
            " --first_pass_iss whisper --second_pass_iss whisper"
            " --max_instrs 5000 --rpt_cnt 5 --seed 0 --setup_pmp"
        )
        RiescueC.run_cli(args=args.split())


class Act4Rv64MaxTpTest(BaseRiescueCTest):
    """
    RiescueC TP-mode tests using the ACT4 whisper-rv64-max config.
    """

    def test_tp_zicond(self):
        "Zicond TP test with ACT4 whisper-rv64-max config"
        cli_args = ["--cpuconfig", ACT4_CONFIG, "--whisper_config_json", ACT4_WHISPER_CONFIG, "--setup_pmp"]
        self.run_tp_mode(plan="zicond", cli_args=cli_args)


class Act4SpikeRv64MaxBringupTest(BaseRiescueCTest):
    """
    RiescueC bringup-mode tests using the ACT4 spike-rv64-max config.
    """

    def test_rv64i(self):
        "RV64I bringup test with ACT4 spike-rv64-max config"
        args = (
            "--json compliance/tests/rv_i/rv64i.json"
            f" --cpuconfig {ACT4_SPIKE_CONFIG}"
            f" --spike_isa {ACT4_SPIKE_ISA}"
            " --iss spike"
            " --first_pass_iss spike --second_pass_iss spike"
            " --max_instrs 5000 --rpt_cnt 5 --seed 0 --setup_pmp"
            " --third_party_spike"
            " --spike_arg=-m0x80000000:0x800000000"
        )
        RiescueC.run_cli(args=args.split())

    def test_rv64i_supervisor(self):
        "RV64I bringup test in supervisor mode with ACT4 spike-rv64-max config"
        args = (
            "--json compliance/tests/rv_i/rv64i.json"
            f" --cpuconfig {ACT4_SPIKE_CONFIG}"
            f" --spike_isa {ACT4_SPIKE_ISA}"
            " --iss spike"
            " --privilege_mode supervisor"
            " --first_pass_iss spike --second_pass_iss spike"
            " --max_instrs 5000 --rpt_cnt 5 --seed 0 --setup_pmp"
            " --third_party_spike"
            " --spike_arg=-m0x80000000:0x800000000"
        )
        RiescueC.run_cli(args=args.split())


class Act4SpikeRv64MaxTpTest(BaseRiescueCTest):
    """
    RiescueC TP-mode tests using the ACT4 spike-rv64-max config.
    """

    def test_tp_zicond(self):
        "Zicond TP test with ACT4 spike-rv64-max config"
        cli_args = [
            "--cpuconfig",
            ACT4_SPIKE_CONFIG,
            "--spike_isa",
            ACT4_SPIKE_ISA,
            "--iss",
            "spike",
            "--setup_pmp",
            "--third_party_spike",
            "--spike_arg=-m0x80000000:0x800000000",
        ]
        self.run_tp_mode(plan="zicond", cli_args=cli_args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
