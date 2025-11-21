# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from .action import Action, CodeMixin
from .registry import ActionRegistry
from .label import LabelAction
from .li import LiAction
from .memory import MemoryAction, CodePageAction, StackPageAction
from .arithmetic import ArithmeticAction
from .load import LoadAction
from .store import StoreAction
from .modify_pte import ModifyPteAction
from .branch import CallAction
from .csr import CsrReadAction, CsrWriteAction
from .pte_actions import (
    ReadLeafPteAction,
    WriteLeafPteAction,
    ReadPteAction,
    WritePteAction,
    PteAction,
)
from .assertions.assert_equal import AssertEqualAction
from .assertions.assert_nequal import AssertNotEqualAction
from .assertions.assert_exception import AssertExceptionAction
from .memaccess import MemAccessAction
from .mappings import DEFAULT_MAPPINGS


def DefaultActionRegistry() -> ActionRegistry:
    return ActionRegistry(DEFAULT_MAPPINGS)


__all__ = [
    "Action",
    "CodeMixin",
    "ActionRegistry",
    "LabelAction",
    "LiAction",
    "LoadAction",
    "StoreAction",
    "CallAction",
    "ArithmeticAction",
    "MemoryAction",
    "CodePageAction",
    "StackPageAction",
    "ModifyPteAction",
    "CsrReadAction",
    "CsrWriteAction",
    "PteAction",
    "ReadPteAction",
    "WritePteAction",
    "ReadLeafPteAction",
    "WriteLeafPteAction",
    "AssertEqualAction",
    "AssertNotEqualAction",
    "AssertExceptionAction",
    "MemAccessAction",
    "DEFAULT_MAPPINGS",
]
