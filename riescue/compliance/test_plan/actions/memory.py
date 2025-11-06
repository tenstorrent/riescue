# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Any, Optional

from coretp import Instruction, StepIR
from coretp.isa import Label
from coretp.step import Memory
from coretp.rv_enums import PageSize, PageFlags, PagingMode
from riescue.compliance.test_plan.actions import Action, CodeMixin
from riescue.compliance.test_plan.context import LoweringContext


class MemoryAction(Action):
    """
    Reserve Memory Action.
    Currently allocates a MemoryBlock
    """

    register_fields: list[str] = []

    def __init__(self, size: int, page_size: PageSize, flags: PageFlags, modify: bool = False, page_cross_en: bool = False, num_pages: int = 1, **kwargs):
        super().__init__(**kwargs)
        self.constraints = {}
        self.size = size
        self.page_size = page_size
        self.flags = flags
        self.num_pages = num_pages
        self.page_cross_en = page_cross_en
        self.data: list[Any] = [".dword 0xc001c0de"]
        self.modify = modify

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "MemoryAction":
        """
        Create a MemoryAction from a StepIR object.
        """
        if TYPE_CHECKING:
            assert isinstance(step.step, Memory)
        if step.step.num_pages is None:
            num_pages = 1
        else:
            num_pages = step.step.num_pages
        return cls(
            step_id=step_id,
            size=step.step.size,
            page_size=step.step.page_size,
            flags=step.step.flags,
            page_cross_en=step.step.page_cross_en,
            num_pages=num_pages,
            modify=step.step.modify,
            **kwargs,
        )

    def repr_info(self) -> str:
        return f"[size=0x{self.size:x}]"

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        "Not used here, instead this is handled by MemoryRegistry.allocate_data"
        raise RuntimeError("MemoryAction should not be used directly. Use MemoryRegistry.allocate_data instead.")


class StackPageAction(MemoryAction):
    """
    Stack Page Action.
    Defaults to 4KiB stack page with read/write permissions.
    """

    register_fields: list[str] = []

    def __init__(self, name: str, size: int = 0x1000, **kwargs):
        super().__init__(
            step_id=name,
            size=size,
            page_size=PageSize.SIZE_4K,
            flags=PageFlags.READ | PageFlags.WRITE,
            page_cross_en=False,
            **kwargs,
        )


class ReturnAction(Action):
    """tiny class that just emits `ret`"""

    register_fields = []

    def repr_info(self) -> str:
        return ""

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        return ctx.instruction_catalog.get_instruction("ret")


class CodePageAction(MemoryAction, CodeMixin):
    """
    Code Page Action. Used to generate a page in memory that will be jumped to and from.

    Full code:

    .. code-block:: asm

        la rd, remote_page
        addi sp, sp, -4
        lw ra, 0(sp)
        jalr ra, 0(rd)

        <remote page>
        ret

        <return_addr>
        addi sp, sp, 4

    This requires a stack page to be setup for each test and the stack to be respected.

    code with no stack suport (just overwrites ra)

    .. code-block:: asm

        la rd, remote_page
        jalr ra, 0(rd)

        <remote page>
        ret


    """

    register_fields: list[str] = []
    defines_code: bool = True

    def __init__(self, code: list[Action], **kwargs):
        super().__init__(**kwargs)
        self._code = code
        self.expanded = False

    @property
    def code(self) -> list[Action]:
        return self._code

    def update_code(self, code: list[Action]):
        self._code = code

    def repr_info(self) -> str:
        if self.code:
            return f"[size=0x{self.size:x}, {len(self.code)} instrs]"
        else:
            return f"[size=0x{self.size:x}]"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        """
        Expands code page to a list of instructions, followed by return instruction
        """

        if self.expanded:
            return None
        self.expanded = True

        actions = [*self.code, ReturnAction(step_id=ctx.new_value_id())]
        self.update_code([])  # flatten code
        return actions

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        "Load the address into a register"

        label = Label(self.step_id)
        return label
