# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""What the generator says, and where it says it.

riemap is a library first: every diagnostic has to go through a logger so a consumer can
silence it, and nothing may write to stdout behind the caller's back (the CLI owns stdout
for its own summary). These tests pin both halves -- the level a message comes out at, and
the fact that stdout stays empty.
"""

import io
import logging
import unittest
from contextlib import redirect_stdout

import riescue.lib.enums as RV
from riescue.riemap.addrgen import address_generator
from riescue.riemap.cli import _build_parser
from riescue.riemap.json_frontend import PageTableConfig, generate_page_tables
from riescue.riemap.resolve import filter_size_attribute

BASE_MMAP = [["0x80000000", "0x80000000000000"]]
_PAGES = [{"num_pages": 2, "id": "m", "attributes": {"v": 1, "r": 1, "w": 1}}]


class _Capture:
    """Collect records from ``riescue.riemap`` (and its children) at ``level``.

    ``assertLogs`` cannot be used here: it fails a test that logs nothing, and "logs
    nothing above INFO" is one of the things being asserted.
    """

    def __init__(self, level: str):
        self.logger = logging.getLogger("riescue.riemap")
        self.level = getattr(logging, level)
        self.records: list = []

    def __enter__(self) -> "_Capture":
        self._handler = logging.Handler()
        self._handler.emit = self.records.append  # type: ignore[method-assign]
        self._old_level = self.logger.level
        self.logger.addHandler(self._handler)
        self.logger.setLevel(self.level)
        return self

    def __exit__(self, *exc) -> bool:
        self.logger.removeHandler(self._handler)
        self.logger.setLevel(self._old_level)
        return False

    def messages(self, level: str = "DEBUG"):
        floor = getattr(logging, level)
        return [r.getMessage() for r in self.records if r.levelno >= floor]


def _generate(spaces, level="INFO"):
    """Generate page tables; return ``(output, capture, captured stdout)``."""
    cfg = PageTableConfig.from_dict({"mmap": BASE_MMAP, "spaces": spaces})
    buf = io.StringIO()
    with _Capture(level) as cap:
        with redirect_stdout(buf):
            out = generate_page_tables(cfg, seed=1)
    return out, cap, buf.getvalue()


class TestFilterSizeLogging(unittest.TestCase):
    def test_filtered_sizes_are_logged_not_printed(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertLogs("riescue.riemap.resolve", level="INFO") as captured:
                kept = filter_size_attribute(["4kb", "512gb"], RV.RiscvPagingModes.SV39)
        self.assertEqual(kept, ["4kb"])
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelno, logging.INFO)
        self.assertIn("512gb", captured.output[0])
        self.assertEqual(buf.getvalue(), "", "filter_size_attribute must not write to stdout")

    def test_nothing_is_logged_when_every_size_is_valid(self):
        logger = logging.getLogger("riescue.riemap.resolve")
        with self.assertLogs(logger, level="INFO") as captured:
            logger.info("sentinel")
            filter_size_attribute(["4kb", "2mb"], RV.RiscvPagingModes.SV39)
        self.assertEqual(len(captured.records), 1, "a fully-valid size list must log nothing")


class TestGenerationLogging(unittest.TestCase):
    """What the generator decided, at the level it should be asked for: INFO is the
    per-space narrative (one line per space, page counts, one line per tree built), DEBUG
    adds the per-spec / per-page / per-allocation detail, and a clean run says nothing above
    INFO. None of it goes to stdout."""

    def test_info_names_every_space_once(self):
        _out, cap, stdout = _generate({"a": {"paging_mode": "sv39", "pages": _PAGES}, "b": {"paging_mode": "sv48", "pages": _PAGES}})
        msgs = cap.messages("INFO")
        for space_id in ("a", "b"):
            self.assertEqual(sum(1 for m in msgs if m == f"Processing space '{space_id}'"), 1, f"space '{space_id}' not announced exactly once")
            self.assertIn(f"Resolved 2 pages for space '{space_id}'", msgs)
        self.assertEqual(stdout, "", "generation must not write to stdout")

    def test_info_reports_one_tree_build_per_table_bearing_space(self):
        _out, cap, _stdout = _generate({"a": {"paging_mode": "sv39", "pages": _PAGES}})
        self.assertEqual([m for m in cap.messages("INFO") if m.startswith("Generating ")], ["Generating VS-stage page tables (mode=SV39)"])

    def test_a_bare_gstage_space_is_reported_as_gstage(self):
        _out, cap, _stdout = _generate({"g": {"twostage": True, "paging_mode": "disable", "gstage_paging_mode": "sv39", "pages": _PAGES}})
        self.assertEqual([m for m in cap.messages("INFO") if m.startswith("Generating ")], ["Generating G-stage page tables (mode=SV39)"])

    def test_a_paging_disabled_space_reports_no_tree_build(self):
        _out, cap, _stdout = _generate({"off": {"paging_mode": "disable", "pages": _PAGES}})
        self.assertEqual([m for m in cap.messages("INFO") if m.startswith("Generating ")], [])

    def test_debug_reports_every_pages_va_and_pa(self):
        out, cap, _stdout = _generate({"a": {"paging_mode": "sv39", "pages": _PAGES}}, level="DEBUG")
        placed = [m for m in cap.messages() if m.startswith("Placed page ")]
        self.assertEqual(len(placed), 2)
        for va, entry in out.spaces["a"].pages["m"].items():
            self.assertTrue(any(f"VA=0x{va:016x}" in m and f"PA=0x{entry.pa:016x}" in m for m in placed), f"no DEBUG line reports VA {va:#x} -> PA {entry.pa:#x}")

    def test_debug_reports_each_spec_and_each_resolved_page(self):
        _out, cap, _stdout = _generate({"a": {"paging_mode": "sv39", "pages": _PAGES}}, level="DEBUG")
        msgs = cap.messages()
        self.assertEqual(sum(1 for m in msgs if m.startswith("Processing page spec ")), 1)
        self.assertEqual(sum(1 for m in msgs if "resolved attributes" in m), 2)
        self.assertTrue(any(m.startswith("Solving ") for m in msgs), "the allocator reports nothing")

    def test_a_clean_run_is_silent_above_info(self):
        _out, cap, _stdout = _generate({"a": {"paging_mode": "sv39", "pages": _PAGES}}, level="DEBUG")
        self.assertEqual(cap.messages("WARNING"), [], "a clean run must log nothing at WARNING or above")


class TestCliDefaults(unittest.TestCase):
    """The CLI's parser is built separately from ``main`` so its defaults can be asserted
    without running a generation."""

    def _args(self, extra=()):
        return _build_parser().parse_args(["cfg.json", "out.json", *extra])

    def test_seed_defaults_to_one(self):
        # Every recorded output in the tree was generated at seed 1; a random default would
        # silently re-baseline all of them.
        self.assertEqual(self._args().seed, 1)
        self.assertEqual(self._args(["--seed", "7"]).seed, 7)

    def test_the_generator_is_silent_by_default(self):
        self.assertEqual(self._args().log_level, "WARNING")

    def test_addrgen_stays_dampened_when_the_generator_goes_to_debug(self):
        args = self._args(["--log-level", "DEBUG"])
        self.assertEqual(args.log_level, "DEBUG")
        self.assertEqual(args.addrgen_log_level, "WARNING", "--log-level DEBUG must not turn on the address generator's ~1200-lines-per-page output")
        self.assertEqual(self._args(["--addrgen-log-level", "DEBUG"]).addrgen_log_level, "DEBUG")

    def test_addrgen_is_a_child_of_the_riemap_logger(self):
        # Dampening works by setting a level on that subtree, which only helps if every
        # addrgen module's logger really is under it.
        self.assertTrue(address_generator.log.name.startswith("riescue.riemap.addrgen"))


if __name__ == "__main__":
    unittest.main()
