# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Optional

from coretp.isa import Register, RISCV_REGISTERS
from coretp.rv_enums import RegisterClass, OperandType

from riescue.compliance.test_plan.context import LoweringContext


class RegisterPool:
    """
    Container for available registers. Abstracts free vs allocated registers.
    :var free_registers: List of registers to be used for allocation
    :var ignore_registers: List of registers to be ignored in allocation/freeing. Attempting to free these registers will pass instead of raising an error.


    :param ignore_registers_filters:
    :raises ValueError: If no registers are left for the given type
    """

    def __init__(self, ignore_registers_filters: tuple[RegisterClass, ...], ctx: LoweringContext, spill_register: Optional[str] = None):
        self.ignore_registers_filters = ignore_registers_filters
        if spill_register is not None:
            self.free_regs = [r for r in RISCV_REGISTERS if r.reg_class not in self.ignore_registers_filters and spill_register not in r.name]
        else:
            self.free_regs = [r for r in RISCV_REGISTERS if r.reg_class not in self.ignore_registers_filters]
        self.ignore_regs = [r for r in RISCV_REGISTERS if r.reg_class in self.ignore_registers_filters]

        self.available_register_size = len(self.free_regs)  # number of possible available registers
        self.allocated: dict[str, Register] = {}  # Maps nodes to registers. Used to track allocated registers for each node
        self.rng = ctx.rng

    def reserve_register(self, name: str):
        """
        Blacklist a register from being allocated.
        """
        for reg in self.free_regs:
            if reg.name == name:
                self.free_regs.remove(reg)
                break
        else:
            raise Exception(f"Error no register named {name}")

    def candidate_registers(
        self,
        reg_type: OperandType,
        register_class: Optional[RegisterClass] = None,
        exclude_registers: Optional[list[str]] = None,
    ) -> list[Register]:
        candidates = [r for r in self.free_regs if r.reg_type == reg_type]
        if register_class:
            candidates = [r for r in candidates if r.reg_class == register_class]
        if exclude_registers:
            candidates = [r for r in candidates if r.name not in exclude_registers]
        return candidates

    def allocate(
        self,
        reg_type: OperandType,
        temp_reg_name: str,
        register_class: Optional[RegisterClass] = None,
        exclude_registers: Optional[list[str]] = None,
        hard_coded_register: Optional[Register] = None,
    ) -> Register:
        """
        Allocate a register of the given type.
        Throws ValueError if no registers are left for the given type.
        Throws ValueError if node_id has already allocated a register.

        :param reg_type: The type of register to allocate (GPR, FPR, VEC)
        :param temp_reg_name: The temporary register name that needs to be allocated
        :param register_class: The class of register to allocate (GPR, FPR, VEC, callee_saved)
        :param exclude_registers: List of registers to exclude from allocation
        """

        if temp_reg_name in self.allocated:
            raise ValueError(f"Node {temp_reg_name} has already allocated a register")

        candidates = self.candidate_registers(reg_type, register_class, exclude_registers)
        if not candidates:
            raise ValueError(f"No registers left for type {reg_type}")

        if hard_coded_register is not None:
            for candidate in candidates:
                if candidate.name == hard_coded_register.name:
                    reg = candidate
                    break
            else:
                raise ValueError(f"Hard-coded register {hard_coded_register.name} not found in candidates; it was already allocated")
        else:
            reg = self.rng.choice(candidates)
        self.free_regs.remove(reg)
        self.allocated[temp_reg_name] = reg
        return reg

    def free(self, temp_reg_name: str):
        """
        Free allocated register by node ID. Throws KeyError if no register is allocated for the given node ID.

        :param temp_reg_name: The temporary register name that is freeing the register
        :raises KeyError: If no register is allocated for the given node ID
        """

        if temp_reg_name not in self.allocated:
            raise KeyError(f"No allocated register with id {temp_reg_name}. {self.allocated=}")
        reg_to_free = self.allocated.pop(temp_reg_name)
        self.free_regs.append(reg_to_free)

    def get_reg_from_temp_name(self, temp_reg_name: str) -> Optional[Register]:
        """
        Gets the register that was allocated for the given temporary register name
        """
        return self.allocated.get(temp_reg_name)

    def is_ignored_register(self, register_name: str) -> bool:
        """
        Check if a register is in the ignore list
        """
        return any(r.name == register_name for r in self.ignore_regs)

    def __str__(self):
        return f"RegisterPool(free={self.free_regs}, allocated={self.allocated})"
