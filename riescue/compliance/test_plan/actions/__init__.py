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
from .hload import HLoadAction
from .hxload import HXLoadAction
from .hstore import HStoreAction
from .modify_pte import ModifyPteAction
from .branch import CallAction
from .csr import CsrReadAction, CsrWriteAction, CsrDirectAccessAction
from .pte_actions import (
    ReadPteAction,
    WritePteAction,
    PteAction,
)
from .assertions.assert_equal import AssertEqualAction
from .assertions.assert_nequal import AssertNotEqualAction
from .assertions.assert_exception import AssertExceptionAction
from .assertions.assert_fetch_exception import AssertFetchExceptionAction
from .memaccess import MemAccessAction
from .system import SystemAction
from .comment import CommentAction
from .directive import DirectiveAction
from .set_wait_timeout import SetWaitTimeoutAction
from .privilege_mode import MachineCodeAction, SupervisorCodeAction, UserCodeAction
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
    "HLoadAction",
    "HXLoadAction",
    "HStoreAction",
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
    "AssertEqualAction",
    "AssertNotEqualAction",
    "AssertExceptionAction",
    "AssertFetchExceptionAction",
    "MemAccessAction",
    "SystemAction",
    "CommentAction",
    "DirectiveAction",
    "ConditionalBlockAction",
    "SetWaitTimeoutAction",
    "MachineCodeAction",
    "SupervisorCodeAction",
    "UserCodeAction",
    "DEFAULT_MAPPINGS",
]
