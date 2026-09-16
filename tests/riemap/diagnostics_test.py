# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest
from types import SimpleNamespace

import riescue.lib.enums as RV
from riescue.dtest_framework.generator.pt_request_builder import _failure_labels
from riescue.riemap.addrgen.exceptions import AddrGenError
from riescue.riemap.errors import (
    AddressSpaceExhausted,
    ConstraintConflict,
    FailureKind,
    FailureParticipant,
    FailurePhase,
    FailureSite,
    PlanningExhausted,
    RieMapError,
)
from riescue.riemap.layout import TopologyConflict
from riescue.riemap.request import Page, Space


class TestRieMapDiagnostics(unittest.TestCase):
    def test_report_contains_stable_category_and_actionable_sections(self):
        page = object()
        error = ConstraintConflict(
            "leaf/pointer conflict",
            kind=FailureKind.LEAF_POINTER,
            phase=FailurePhase.TOPOLOGY,
            summary="A 2 MiB leaf and a deeper walk require the same PTE.",
            reason="One PTE cannot be both a leaf and a pointer.",
            participants=[
                FailureParticipant(page, "leaf", "2 MiB mapping"),
            ],
            site=FailureSite(level=1, slot=0x38, span=(0x7000000, 0x7200000)),
            context={"page_size": 0x200000},
            hints=["Use 4 KiB leaves throughout this 2 MiB span."],
        )
        error.add_labels({page: "map_os/data"})

        text = str(error)
        self.assertTrue(text.startswith("leaf/pointer conflict"))
        self.assertIn("RieMap failure [topology.leaf_pointer]", text)
        self.assertIn("One PTE cannot be both a leaf and a pointer.", text)
        self.assertIn("map_os/data", text)
        self.assertIn("slot=0x38", text)
        self.assertIn("page_size: 0x200000", text)
        self.assertIn("Possible fixes:", text)

    def test_existing_exception_type_contracts_are_preserved(self):
        self.assertTrue(issubclass(AddrGenError, RieMapError))
        self.assertTrue(issubclass(TopologyConflict, ValueError))
        self.assertTrue(issubclass(TopologyConflict, ConstraintConflict))
        self.assertTrue(issubclass(AddressSpaceExhausted, RieMapError))
        self.assertTrue(issubclass(PlanningExhausted, RieMapError))

    def test_plain_addrgen_error_keeps_legacy_message(self):
        error = AddrGenError("could not allocate page")
        self.assertTrue(str(error).startswith("could not allocate page"))
        self.assertEqual(error.phase, FailurePhase.DECLARATION)

    def test_riescued_failure_labels_include_directive_names(self):
        space = Space(RV.RiscvPagingModes.SV39)
        page = Page(space)
        translator = SimpleNamespace(
            spaces_by_name={"map_os": space},
            names_by_id={"page::map_os": ("data_lin", "data_phys")},
            addr_names_by_id={},
        )

        labels = _failure_labels(translator, {"page::map_os": page})

        self.assertEqual(labels[space], "address space 'map_os'")
        self.assertEqual(labels[page], "page::map_os (data_lin -> data_phys)")


if __name__ == "__main__":
    unittest.main()
