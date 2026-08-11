# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Semantic contract tests for the ``riemap`` CLI entry point."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from riescue.riemap import cli
from riescue.riemap.json_frontend import PageTableConfig, generate_page_tables

SEED = 12345
CONFIG = {
    "mmap": [["0x80000000", "0x81000000"]],
    "spaces": {
        "single": {
            "paging_mode": "sv39",
            "pages": [
                {
                    "id": "page",
                    "va": "0x40000000",
                    "pa": "0x80010000",
                    "attributes": {"v": 1, "r": 1, "w": 1},
                }
            ],
        }
    },
}


def _semantic_fingerprint(output) -> str:
    """Address-independent summary of generated page-table structure."""
    spaces = {}
    for space_id, space in sorted(output.spaces.items()):
        pages = {}
        for page_id, va_map in sorted(space.pages.items()):
            pages[page_id] = sorted(
                json.dumps(
                    {
                        "level_count": len(entry.ptes),
                        "stages": [p.stage for p in entry.ptes],
                        "levels": [p.level for p in entry.ptes],
                    },
                    sort_keys=True,
                )
                for entry in va_map.values()
            )
        spaces[space_id] = {
            "paging_mode": space.paging_mode,
            "gstage_paging_mode": space.gstage_paging_mode,
            "has_top_base": space.top_base_addr is not None,
            "has_gstage_top_base": space.gstage_top_base_addr is not None,
            "page_ids": sorted(pages),
            "pages": pages,
        }
    return json.dumps(
        {
            "entry_count": len(output.entries),
            "space_ids": sorted(output.spaces),
            "spaces": spaces,
        },
        sort_keys=True,
    )


class TestRieMapCliContract(unittest.TestCase):
    def test_cli_writes_valid_json_matching_generate_page_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            out_path = Path(tmp) / "out.json"
            config_path.write_text(json.dumps(CONFIG))
            argv = ["riemap", str(config_path), str(out_path), "--seed", str(SEED)]
            buf = io.StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
                cli.main()
            stdout = buf.getvalue()
            self.assertTrue(out_path.exists(), "CLI must write the output file")
            self.assertIn("Generated page tables at", stdout)
            self.assertIn("Total PTEs (merged):", stdout)
            self.assertIn("Total spaces:", stdout)

            with out_path.open() as fh:
                written = json.load(fh)
            self.assertIn("entries", written)
            self.assertIn("spaces", written)
            self.assertGreater(len(written["entries"]), 0)
            self.assertGreater(len(written["spaces"]), 0)

            for space in written["spaces"].values():
                self.assertIn("paging_mode", space)
                self.assertIn("pages", space)
                for va_map in space["pages"].values():
                    for entry in va_map.values():
                        self.assertIn("ptes", entry)
                        self.assertGreater(len(entry["ptes"]), 0)
                        for pte in entry["ptes"]:
                            self.assertIn("address", pte)
                            self.assertIn("level", pte)

            direct = generate_page_tables(
                PageTableConfig.from_dict(CONFIG),
                seed=SEED,
            )
            serialized_direct = json.loads(json.dumps(direct.to_dict()))
            self.assertEqual(written, serialized_direct)

    def test_same_seed_is_semantically_deterministic(self):
        first = generate_page_tables(PageTableConfig.from_dict(CONFIG), seed=SEED)
        second = generate_page_tables(PageTableConfig.from_dict(CONFIG), seed=SEED)
        self.assertEqual(_semantic_fingerprint(first), _semantic_fingerprint(second))


if __name__ == "__main__":
    unittest.main()
