# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from coretp.step import (
    TestStep,
    Load,
    Store,
    Memory,
    Arithmetic,
    CsrRead,
    CsrWrite,
    CodePage,
    Call,
    AssertEqual,
    AssertNotEqual,
    AssertException,
)

from riescue.compliance.test_plan.actions import (
    Action,
    LoadAction,
    StoreAction,
    ArithmeticAction,
    MemoryAction,
    CsrReadAction,
    CsrWriteAction,
    CodePageAction,
    CallAction,
    AssertEqualAction,
    AssertNotEqualAction,
    AssertExceptionAction,
)


DEFAULT_MAPPINGS: list[tuple[type[TestStep], type[Action]]] = [
    (Load, LoadAction),
    (Store, StoreAction),
    (Arithmetic, ArithmeticAction),
    (Memory, MemoryAction),
    (CsrRead, CsrReadAction),
    (CsrWrite, CsrWriteAction),
    (CodePage, CodePageAction),
    (Call, CallAction),
    (AssertEqual, AssertEqualAction),
    (AssertNotEqual, AssertNotEqualAction),
    (AssertException, AssertExceptionAction),
]
