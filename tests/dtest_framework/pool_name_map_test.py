# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Pool map-name append/split (plain join; map names ban '.')."""

import unittest

from riescue.dtest_framework.parser import ParsedPageMap
from riescue.dtest_framework.pool import Pool


def _pool_with_maps(*map_names: str) -> Pool:
    """Pool with parsed page maps registered so split_name_map can validate."""
    pool = Pool()
    for name in map_names:
        pool.add_parsed_page_map(ParsedPageMap(name=name, mode="sv39"))
    return pool


class TestPoolNameMapEncoding(unittest.TestCase):
    """Combined names are ``lin.map``; map names may not contain the separator."""

    def test_separator_is_dot(self):
        self.assertEqual(Pool._map_separator, ".")

    def test_append_and_split_roundtrip_with_underscores(self):
        pool = _pool_with_maps("map_hart_0")
        lin = "shared_buf_a"
        map_name = "map_hart_0"
        combined = pool.append_map_to_name(lin, map_name)
        self.assertEqual(combined, "shared_buf_a.map_hart_0")
        self.assertEqual(pool.split_name_map(combined), (lin, map_name))

    def test_split_is_stateless_given_maps(self):
        """Any Pool that knows the map can split a combined name."""
        combined = Pool().append_map_to_name("shared_buf_a", "map_hart_0")
        self.assertEqual(_pool_with_maps("map_hart_0").split_name_map(combined), ("shared_buf_a", "map_hart_0"))
        self.assertEqual(_pool_with_maps("map_hart_0").split_name_map("shared_buf_a_map_hart_0"), ("", ""))

    def test_multiple_underscore_maps(self):
        pool = _pool_with_maps("map_hart_0", "map_hart_1")
        pairs = [
            ("shared_buf_a", "map_hart_0"),
            ("shared_buf_a", "map_hart_1"),
            ("scratch_lin_0", "map_hart_0"),
            ("scratch_lin_0", "map_hart_1"),
        ]
        for lin, map_name in pairs:
            combined = pool.append_map_to_name(lin, map_name)
            self.assertEqual(combined, f"{lin}.{map_name}")
            self.assertEqual(pool.split_name_map(combined), (lin, map_name))

    def test_dots_inside_lin_roundtrip(self):
        pool = _pool_with_maps("map_hart_0")
        lin = "FADD.D_0"
        map_name = "map_hart_0"
        combined = pool.append_map_to_name(lin, map_name)
        self.assertEqual(combined, "FADD.D_0.map_hart_0")
        self.assertEqual(pool.split_name_map(combined), (lin, map_name))

    def test_runs_of_dots_inside_lin(self):
        pool = _pool_with_maps("c")
        lin = "a..b"
        map_name = "c"
        combined = pool.append_map_to_name(lin, map_name)
        self.assertEqual(combined, "a..b.c")
        self.assertEqual(pool.split_name_map(combined), (lin, map_name))

    def test_parse_page_map_rejects_any_separator(self):
        """;#page_map name= is validated as soon as the directive is parsed."""
        from pathlib import Path

        from riescue.dtest_framework.parser import Parser

        parser = Parser(filename=Path("dummy.s"), pool=Pool())
        line = ";#page_map(name=b.ad, mode=sv39);"
        with self.assertRaises(ValueError) as ctx:
            parser.parse_page_maps(line)
        msg = str(ctx.exception)
        self.assertIn("must not contain map separator", msg)
        self.assertIn(line.strip(), msg)
        self.assertEqual(parser.pool.get_parsed_page_maps(), {})

    def test_lin_name_may_end_with_separator(self):
        """A trailing separator on the lin half round-trips via rsplit."""
        pool = _pool_with_maps("c")
        lin = "ab."
        map_name = "c"
        combined = pool.append_map_to_name(lin, map_name)
        self.assertEqual(combined, "ab..c")
        self.assertEqual(pool.split_name_map(combined), (lin, map_name))

    def test_bare_name_without_separator_does_not_split(self):
        self.assertEqual(_pool_with_maps("map_hart_0").split_name_map("shared_buf_a"), ("", ""))

    def test_os_shared_maps_are_not_known_for_split(self):
        """map_os / map_hyp never validate a combined name — only private maps do."""
        pool = _pool_with_maps("map_os", "map_hyp")
        combined = pool.append_map_to_name("shared_buf_a", "map_os")
        self.assertEqual(pool.split_name_map(combined), ("", ""))
        # A private map still works when registered alongside OS maps.
        pool.add_parsed_page_map(ParsedPageMap(name="map_hart_0", mode="sv39"))
        private = pool.append_map_to_name("shared_buf_a", "map_hart_0")
        self.assertEqual(pool.split_name_map(private), ("shared_buf_a", "map_hart_0"))

    def test_bare_name_with_dot_does_not_split_without_known_map(self):
        """Compliance/vector names like vzext.vf4 must not be treated as combined."""
        name = "vreg_inits_0_vzext.vf4_0_m4_16_0_1_vsetvli_zero_mask_sv57_super_lin"
        # No maps registered yet (same window as process_raw_parsed_page_mappings).
        self.assertEqual(Pool().split_name_map(name), ("", ""))
        # map_os alone must not count as a private map for validation.
        self.assertEqual(_pool_with_maps("map_os").split_name_map(name), ("", ""))

    def test_add_parsed_page_mapping_keeps_dotted_lin_name(self):
        from riescue.dtest_framework.parser import ParsedPageMapping

        pool = Pool()
        name = "vreg_inits_0_vzext.vf4_0_m4_16_0_1_vsetvli_zero_mask_sv57_super_lin"
        ppm = ParsedPageMapping(lin_name=name, phys_name="phys0", page_maps=[])
        pool.add_parsed_page_mapping(ppm)
        self.assertIn((name, "map_os"), pool.get_parsed_page_mappings())
        # Must not collapse to the prefix before the instruction-name dot.
        self.assertNotIn(("vreg_inits_0_vzext", "map_os"), pool.get_parsed_page_mappings())

    def test_offset_lin_name_roundtrip(self):
        pool = _pool_with_maps("map_hart_0")
        lin = "buf_base_lin+0x1000"
        map_name = "map_hart_0"
        combined = pool.append_map_to_name(lin, map_name)
        self.assertEqual(combined, "buf_base_lin+0x1000.map_hart_0")
        self.assertEqual(pool.split_name_map(combined), (lin, map_name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
