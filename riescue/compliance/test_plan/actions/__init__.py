# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


from .action import Action, CodeMixin
from .registry import ActionRegistry
from .label import LabelAction
from .li import LiAction
from .conditional_block import ConditionalBlockAction
from .memory import MemoryAction, CodePageAction, StackPageAction, RequestPmpAction
from .arithmetic import ArithmeticAction
from .load import LoadAction
from .store import StoreAction
from .modify_pte import ModifyPteAction
from .branch import CallAction
from .csr import CsrReadAction, CsrWriteAction, CsrDirectAccessAction
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
from .system import SystemAction
from .comment import CommentAction
from .directive import DirectiveAction
from .set_wait_timeout import SetWaitTimeoutAction
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
    "ConditionalBlockAction",
    "ArithmeticAction",
    "MemoryAction",
    "CodePageAction",
    "StackPageAction",
    "ModifyPteAction",
    "CsrReadAction",
    "CsrWriteAction",
    "CsrDirectAccessAction",
    "PteAction",
    "ReadPteAction",
    "WritePteAction",
    "ReadLeafPteAction",
    "WriteLeafPteAction",
    "AssertEqualAction",
    "AssertNotEqualAction",
    "AssertExceptionAction",
    "MemAccessAction",
    "SystemAction",
    "CommentAction",
    "DirectiveAction",
    "ConditionalBlockAction",
    "SetWaitTimeoutAction",
    "DEFAULT_MAPPINGS",
]
