# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, NamedTuple, Union, TYPE_CHECKING

from coretp.isa import Instruction, Label, Register, get_register, Operand
from coretp.rv_enums import RegisterClass, OperandType, Xlen, Extension, Category

from riescue.compliance.test_plan.context import LoweringContext
from .register_pool import RegisterPool
from .types import BasicBlock


class Interval(NamedTuple):
    start: int
    end: int
    reg_name: str
    reg_type: OperandType


class StackEntry(NamedTuple):
    offset: int
    name: str


class VariableRecord(NamedTuple):
    """
    Record of a virtual register / variable's life in the program
    """

    start: int
    end: int
    slot: Union[Register, StackEntry]


class SpillManager:
    def __init__(self):
        self.counter = 0
        self.var_to_slot: dict[str, StackEntry] = {}

    def get_slot(self, var) -> StackEntry:
        if var not in self.var_to_slot:
            self.var_to_slot[var] = StackEntry(offset=self.counter, name=f"stack{self.counter}")
            self.counter += 1
        return self.var_to_slot[var]


class LinearScan:
    """
    Linear scan register allocator
    """

    def __init__(self, instructions: list[Instruction], ctx: LoweringContext):
        self.instructions = instructions
        self.live_intervals = self._analyze(self.instructions)
        self.reg_pool = RegisterPool(ignore_registers_filters=(RegisterClass.special,), ctx=ctx, spill_register="t0")
        self.spill_manager = SpillManager()
        self.ctx = ctx
        self.bytes_per_spill = self.ctx.env.reg_width // 8
        self.xlen = Xlen.XLEN32 if self.ctx.env.reg_width == 32 else Xlen.XLEN64

    def allocate(self) -> list[Instruction]:
        """
        Allocate registers for a list of instructions.

        :return: List of instructions with registers allocated.
        :rtype: list[Instruction]
        """
        register_map = self._linear_scan()
        return self._apply_linear_scan(register_map)

    def _analyze(self, instructions: list[Instruction]) -> dict[str, Interval]:
        """
        Compute live intervals: start = first definition, end = last use.
        """
        live_intervals: dict[str, Interval] = {}

        for i, instr in enumerate(instructions):
            if instr.destination and instr.destination.is_register() and isinstance(instr.destination.val, str):
                name = instr.destination.val
                if name not in live_intervals:
                    live_intervals[name] = Interval(i, i, name, instr.destination.type)
                else:
                    # If variable is written more than once, update start if earlier
                    live_intervals[name] = live_intervals[name]._replace(start=min(live_intervals[name].start, i))

        for i, instr in enumerate(instructions):
            for src in instr.source:
                if src.is_register() and isinstance(src.val, str):
                    name = src.val
                    if name not in live_intervals:
                        # Case: used before defined
                        live_intervals[name] = Interval(i, i, name, src.type)
                    live_intervals[name] = live_intervals[name]._replace(end=max(live_intervals[name].end, i))

        return live_intervals

    def _clobber_map(self):
        clobbered_registers: dict[int, list[str]] = {}
        # Also need to check for clobbered registers from function calls

        # check for jalr instructions? see what they point to?
        instr_map = {instr.instruction_id: instr for instr in self.instructions}
        for i, instr in enumerate(self.instructions):

            if instr.name == "jalr_ra":
                rs1 = instr.rs1()
                if rs1 is None:
                    raise Exception(f"jalr instruction {instr} has no rs1")
                if not isinstance(rs1.val, str):
                    raise Exception(f"jalr's rs1 is not a register {instr}")
                li = instr_map[rs1.val]
                call_label = li.immediate_operand()
                if not call_label or not isinstance(call_label.val, str):
                    raise Exception(f"jalr's load immediate is not a label {instr}")

                clobbered_registers[i] = [r.name for r in self.ctx.global_function_clobbers[call_label.val]]
            elif instr.clobbers:
                clobbered_registers[i] = instr.clobbers

        return clobbered_registers

    def _linear_scan(self):
        """
        Performs linear scan register allocation, returns a map of {temp_reg_name: register}
        """

        live_interval_list = sorted(self.live_intervals.values(), key=lambda x: x.start)
        clobbered_registers = self._clobber_map()
        register_map: dict[str, VariableRecord] = {}
        active: list[Interval] = []

        for i, interval in enumerate(live_interval_list):
            active = sorted(active, key=lambda x: x.end)

            # expire old intervals
            expired = []
            for a in active:
                if a.end < interval.start:
                    expired.append(a)

            for e in expired:
                active.remove(e)
                self.reg_pool.free(e.reg_name)

            # handle clobbered registers, e.g. macros, call instructions
            # checks if value lives across a clobber and excludes clobbered registers from possible registers
            clobbered_instruction_idx = [idx for idx in clobbered_registers if interval.start < idx < interval.end]
            exclude_registers = []
            for c in clobbered_instruction_idx:
                exclude_registers.extend(clobbered_registers[c])

            # allocate register
            if len(active) >= len(self.reg_pool.candidate_registers(interval.reg_type, exclude_registers=exclude_registers)):
                # get last active interval of the same type
                revered_active = reversed(active)
                last_active_interval = next(revered_active)
                while last_active_interval.reg_type != interval.reg_type:
                    last_active_interval = next(revered_active)

                # spill register that lives longer
                stack_slot = self.spill_manager.get_slot(interval)
                if last_active_interval.end > interval.end:
                    register_map[interval.reg_name] = register_map[last_active_interval.reg_name]
                    register_map[last_active_interval.reg_name] = VariableRecord(last_active_interval.start, last_active_interval.end, stack_slot)
                    active.remove(last_active_interval)
                else:
                    register_map[interval.reg_name] = VariableRecord(interval.start, interval.end, stack_slot)
            else:
                allocated_reg = self.reg_pool.allocate(interval.reg_type, interval.reg_name, exclude_registers=exclude_registers)
                active.append(interval)
                register_map[interval.reg_name] = VariableRecord(interval.start, interval.end, allocated_reg)

        return register_map

    def _apply_linear_scan(self, register_map: dict[str, VariableRecord]) -> list[Instruction]:
        """
        Apply the linear scan register allocation to the instructions
        """
        new_instructions: list[Instruction] = []
        num_spills = len(self.spill_manager.var_to_slot)
        if num_spills > 0:
            self._allocate_stack(num_spills)
        t0 = get_register("t0")
        for i, instr in enumerate(self.instructions):
            spill = []
            # update instruction
            if instr.destination and instr.destination.is_register() and isinstance(instr.destination.val, str):
                records = register_map[instr.destination.val]
                if isinstance(records.slot, Register):
                    instr.destination.val = records.slot
                elif isinstance(records.slot, StackEntry):
                    temp_reg, store_instr = self._push_stack(instr.destination.type, records.slot.offset)
                    spill.append(store_instr)
                    instr.destination.val = temp_reg

            for src in instr.source:
                spilled = False
                if src.is_register() and isinstance(src.val, str):
                    records = register_map[src.val]
                    if isinstance(records.slot, Register):
                        src.val = records.slot
                    elif isinstance(records.slot, StackEntry):
                        if spilled:
                            continue
                        temp_reg, load_instr = self._pop_stack(src.type, records.slot.offset)
                        spill.append(load_instr)
                        src.val = temp_reg
                        spilled = True

            new_instructions.extend(spill)
            new_instructions.append(instr)
        return new_instructions

    def _allocate_stack(self, num_spills: int) -> Instruction:
        """
        Allocates stack space for a spill
        """
        if num_spills == 0:
            raise Exception("No spills, no need to advance stack")
        else:
            sp = get_register("sp")
            addi = self.ctx.instruction_catalog.get_instruction("addi")

            addi.destination = Operand("rd", OperandType.GPR, sp)
            rs1 = addi.rs1()
            imm = addi.immediate_operand()
            if rs1 is None or imm is None:
                raise Exception("addi instruction does not have a destination or immediate")
            rs1.val = sp
            imm.val = num_spills * self.bytes_per_spill
            return addi

    def _push_stack(self, reg_type: OperandType, offset: int) -> tuple[Register, Instruction]:
        """
        Push the stack pointer num_spills times.
        """
        if reg_type == OperandType.FPR:
            if self.xlen == Xlen.XLEN32:
                store = self.ctx.instruction_catalog.get_instruction("fsw")
            else:
                store = self.ctx.instruction_catalog.get_instruction("fsd")

        else:
            if self.xlen == Xlen.XLEN32:
                store = self.ctx.instruction_catalog.get_instruction("sw")
            else:
                store = self.ctx.instruction_catalog.get_instruction("sd")
        if reg_type == OperandType.FPR:
            temp_reg = get_register("ft0")
        else:
            temp_reg = get_register("t0")
        sp = get_register("sp")
        rs1 = store.rs1()
        rs2 = store.rs2()
        imm = store.immediate_operand()
        if rs1 is None or rs2 is None or imm is None:
            raise Exception("store instruction does not have a destination or immediate")
        rs1.val = sp
        rs2.val = temp_reg
        imm.val = offset * self.bytes_per_spill
        return temp_reg, store

    def _pop_stack(self, reg_type: OperandType, offset: int) -> tuple[Register, Instruction]:
        """
        Pop the stack pointer num_spills times
        """
        if reg_type == OperandType.FPR:
            if self.xlen == Xlen.XLEN32:
                load = self.ctx.instruction_catalog.get_instruction("flw")
            else:
                load = self.ctx.instruction_catalog.get_instruction("fld")

        else:
            if self.xlen == Xlen.XLEN32:
                load = self.ctx.instruction_catalog.get_instruction("lw")
            else:
                load = self.ctx.instruction_catalog.get_instruction("ld")
        sp = get_register("sp")
        if reg_type == OperandType.FPR:
            temp_reg = get_register("ft0")
        else:
            temp_reg = get_register("t0")
        rd = load.destination
        rs1 = load.rs1()
        imm = load.immediate_operand()
        if rd is None or rs1 is None or imm is None:
            raise Exception("load instruction does not have a destination or immediate")
        rd.val = temp_reg
        rs1.val = sp
        imm.val = offset * self.bytes_per_spill
        return temp_reg, load
