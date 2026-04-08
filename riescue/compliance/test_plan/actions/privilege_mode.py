# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, TYPE_CHECKING

from coretp import Instruction, StepIR
from coretp.isa import Extension
from coretp.rv_enums import Xlen, Category
from coretp.step import MachineCode, SupervisorCode, UserCode
from riescue.compliance.test_plan.actions import Action, CodeMixin
from riescue.compliance.test_plan.context import LoweringContext


class PrivilegeBlockMarkerInstruction(Instruction):
    """
    Marker instruction for privilege block boundaries.

    This instruction doesn't emit any actual assembly code - it's used
    internally to track where privilege block code starts and ends during
    transformation.

    When marker_type="end", this instruction clobbers t0, t1, and t6 because
    the return syscall sequence uses these registers:
        li x31, 0xf0001004
        ecall
    This prevents the allocator from assigning values to these registers
    that would be lost when returning from privilege mode.
    """

    # Registers clobbered when privilege mode returns (end marker)
    END_CLOBBERED_REGISTERS = ["t0", "t1", "t6"]

    def __init__(self, marker_type: str, block_index: int, mode: str, instruction_id: str):
        clobbers = self.END_CLOBBERED_REGISTERS if marker_type == "end" else []

        super().__init__(
            name=f"privilege_block_{marker_type}",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,
            source=[],
            clobbers=clobbers,
        )
        self.marker_type = marker_type  # "start" or "end"
        self.block_index = block_index
        self.mode = mode  # "machine" or "supervisor"
        self.instruction_id = instruction_id

    def format(self) -> str:
        """Marker instructions don't emit assembly."""
        return f"# {self.mode} block {self.block_index} {self.marker_type}"


class PrivilegeBlockMarkerAction(Action):
    """Marker action for privilege block boundaries (start or end)."""

    register_fields: list[str] = []

    def __init__(self, step_id: str, block_index: int, mode: str, marker_type: str):
        super().__init__(step_id=step_id)
        self.block_index = block_index
        self.mode = mode  # "machine" or "supervisor"
        self.marker_type = marker_type  # "start" or "end"

    def repr_info(self) -> str:
        return f"block_index={self.block_index}, mode={self.mode}, marker_type={self.marker_type}"

    def pick_instruction(self, ctx: "LoweringContext") -> Instruction:
        return PrivilegeBlockMarkerInstruction(
            marker_type=self.marker_type,
            block_index=self.block_index,
            mode=self.mode,
            instruction_id=self.step_id,
        )


# Convenience aliases for backward compatibility and clarity
def PrivilegeBlockStartAction(step_id: str, block_index: int, mode: str) -> PrivilegeBlockMarkerAction:
    """Create a start marker action for a privilege block."""
    return PrivilegeBlockMarkerAction(step_id, block_index, mode, marker_type="start")


def PrivilegeBlockEndAction(step_id: str, block_index: int, mode: str) -> PrivilegeBlockMarkerAction:
    """Create an end marker action for a privilege block."""
    return PrivilegeBlockMarkerAction(step_id, block_index, mode, marker_type="end")


class PrivilegeModeInstruction(Instruction):
    """
    Custom instruction for privilege mode syscall invocation.

    This generates the assembly sequence to:
    1. Load the block index into s11 (callee-saved)
    2. Load the syscall number into x31
    3. Execute ecall to switch privilege mode

    Clobbers: t0, t1, t6, s11 (used by syscall handlers in syscalls.py)
    """

    # Registers clobbered by the syscall handlers (from syscalls.py)
    CLOBBERED_REGISTERS = ["t0", "t1", "t6", "s11"]

    def __init__(self, syscall_num: int, block_index: int, instruction_id: str):
        super().__init__(
            name="privilege_mode_syscall",
            extension=Extension.I,
            xlen=Xlen.XLEN64,
            category=Category.SYSTEM,
            destination=None,
            source=[],
            clobbers=self.CLOBBERED_REGISTERS,
        )
        self.syscall_num = syscall_num
        self.block_index = block_index
        self.instruction_id = instruction_id

    def format(self) -> str:
        """Generate assembly for the privilege mode switch."""
        return f"# Privilege mode switch (block {self.block_index})\n" f"    li s11, {self.block_index}\n" f"    li x31, {hex(self.syscall_num)}\n" f"    ecall"


class PrivilegeCodeAction(Action, CodeMixin):
    """
    Base action for executing code in a privileged mode.

    Subclasses specify the mode name and syscall number.
    """

    register_fields: list[str] = []
    _block_counter: int = 0

    # Subclasses must override these
    MODE: str = ""
    SYSCALL_NUM: int = 0
    STEP_TYPE: type = type(None)

    def __init__(self, step_id: str, code: Optional[list[Action]] = None, block_index: int = -1, **kwargs):
        super().__init__(step_id=step_id)
        self._code = code if code is not None else []
        self.block_index = block_index
        self.expanded = False

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "PrivilegeCodeAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, cls.STEP_TYPE)
        block_index = cls._block_counter
        cls._block_counter += 1
        code = kwargs.pop("code", [])
        return cls(step_id=step_id, code=code, block_index=block_index, **kwargs)

    @classmethod
    def reset_counter(cls):
        """Reset the block counter. Useful for testing."""
        cls._block_counter = 0

    @property
    def code(self) -> list[Action]:
        return self._code

    def update_code(self, code: list[Action]):
        self._code = code

    def repr_info(self) -> str:
        return f"block_index={self.block_index}, code_len={len(self._code)}"

    def expand(self, ctx: LoweringContext) -> Optional[list[Action]]:
        """
        Expand to inline nested code with markers.

        Returns: start marker + nested code actions + end marker + self (syscall)
        """
        if self.expanded:
            return None
        self.expanded = True

        expanded_code: list[Action] = []
        for action in self._code:
            result = action.expand(ctx)
            if result is None:
                expanded_code.append(action)
            else:
                expanded_code.extend(result)

        start_marker = PrivilegeBlockStartAction(
            step_id=ctx.new_value_id(),
            block_index=self.block_index,
            mode=self.MODE,
        )
        end_marker = PrivilegeBlockEndAction(
            step_id=ctx.new_value_id(),
            block_index=self.block_index,
            mode=self.MODE,
        )

        return [self, start_marker] + expanded_code + [end_marker]

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        """Generate the syscall instruction to switch privilege mode."""
        return PrivilegeModeInstruction(
            syscall_num=self.SYSCALL_NUM,
            block_index=self.block_index,
            instruction_id=self.step_id,
        )


class MachineCodeAction(PrivilegeCodeAction):
    """
    Action for executing code in machine mode.

    Invokes syscall 0xf0001001 to switch to machine mode.
    The actual code execution happens in the machine mode code page (.code_machine_0).
    """

    register_fields: list[str] = []
    MODE = "machine"
    SYSCALL_NUM = 0xF0001001
    STEP_TYPE = MachineCode
    _block_counter: int = 0


class SupervisorCodeAction(PrivilegeCodeAction):
    """
    Action for executing code in supervisor mode.

    Invokes syscall 0xf0001002 to switch to supervisor mode.
    The actual code execution happens in the supervisor mode code page (.code_super_0).
    """

    register_fields: list[str] = []
    MODE = "supervisor"
    SYSCALL_NUM = 0xF0001002
    STEP_TYPE = SupervisorCode
    _block_counter: int = 0


class UserCodeAction(PrivilegeCodeAction):
    """
    Action for executing code in user mode.

    Invokes syscall 0xf0001003 to switch to user mode.
    The actual code execution happens in the user mode code page (.code_user_0).
    """

    register_fields: list[str] = []
    MODE = "user"
    SYSCALL_NUM = 0xF0001003
    STEP_TYPE = UserCode
    _block_counter: int = 0
