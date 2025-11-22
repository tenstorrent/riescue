# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from coretp.step import (
    TestStep,
    Load,
    Store,
    Memory,
    ModifyPte,
    Arithmetic,
    CsrRead,
    CsrWrite,
    CodePage,
    Call,
    AssertEqual,
    AssertNotEqual,
    AssertException,
    LoadImmediateStep,
    MemAccess,
    System,
)
from coretp.step.memory import ReadLeafPTE, WriteLeafPTE, ReadPTE, WritePTE

from riescue.compliance.test_plan.actions import (
    Action,
    LoadAction,
    StoreAction,
    ArithmeticAction,
    MemoryAction,
    CsrReadAction,
    CsrWriteAction,
    ReadLeafPteAction,
    WriteLeafPteAction,
    ReadPteAction,
    WritePteAction,
    CodePageAction,
    CallAction,
    ModifyPteAction,
    AssertEqualAction,
    AssertNotEqualAction,
    AssertExceptionAction,
    LiAction,
    MemAccessAction,
    SystemAction,
)


DEFAULT_MAPPINGS: list[tuple[type[TestStep], type[Action]]] = [
    (Load, LoadAction),
    (Store, StoreAction),
    (Arithmetic, ArithmeticAction),
    (Memory, MemoryAction),
    (CsrRead, CsrReadAction),
    (CsrWrite, CsrWriteAction),
    (ReadLeafPTE, ReadLeafPteAction),
    (CodePage, CodePageAction),
    (Call, CallAction),
    (AssertEqual, AssertEqualAction),
    (AssertNotEqual, AssertNotEqualAction),
    (AssertException, AssertExceptionAction),
    (LoadImmediateStep, LiAction),
    (MemAccess, MemAccessAction),
    (ModifyPte, ModifyPteAction),
    (ReadPTE, ReadPteAction),
    (WritePTE, WritePteAction),
    (WriteLeafPTE, WriteLeafPteAction),
    (System, SystemAction),
]
