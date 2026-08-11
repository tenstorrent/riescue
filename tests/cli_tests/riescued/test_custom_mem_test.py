# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class TestCustomMem(BaseRiescuedTest):
    """Runs test_custom_mem.s with the dedicated cpuconfig (mmap.custom probe regions)."""

    # phys_name -> [start, end) bound of its custom region (test_custom_mem_cpuconfig.json).
    _REGIONS = {
        "probe_phys": (0x60000000, 0x61000000),
        "probe_phys2": (0x60000000, 0x61000000),
        "probe_phys_rps": (0x60000000, 0x61000000),
        "probe_phys_rps2": (0x60000000, 0x61000000),
        "probe_phys_rps3": (0x60000000, 0x61000000),
        "probe_io_phys": (0x9C00000, 0x9C10000),
        "probe_rw_phys": (0x61000000, 0x61100000),
        "probe_ro_phys": (0x62000000, 0x62100000),
    }

    def setUp(self):
        self.testname = "dtest_framework/tests/test_custom_mem.s"
        super().setUp()

    def _assert_in_region(self, test_dir):
        """Every custom-region physical page must land inside its named region (CASE 4a:
        the PA used to draw freely, disconnected from the region)."""
        equates = test_dir / "test_custom_mem_equates.inc"
        self.assertTrue(equates.exists(), f"missing generated equates: {equates}")
        values = {name: int(val, 0) for name, val in self.equates_regex.findall(equates.read_text())}
        for phys_name, (start, end) in self._REGIONS.items():
            self.assertIn(phys_name, values, f"{phys_name} not emitted")
            addr = values[phys_name]
            self.assertTrue(start <= addr < end, f"{phys_name}=0x{addr:x} outside region [0x{start:x}, 0x{end:x})")

    def test_cli(self):
        args = [
            "--run_iss",
            "--cpuconfig",
            "dtest_framework/lib/test_custom_mem_cpuconfig.json",
        ]
        for i, _result in enumerate(self.run_riescued_generator(testname=self.testname, cli_args=args, iterations=self.iterations)):
            self._assert_in_region(self.test_dir / f"seed_{i}")
