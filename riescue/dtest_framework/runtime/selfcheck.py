# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

"""
Selfcheck module for checking architectural state at the end of each test.

This module provides a self-checking mechanism that computes a 128-bit Fletcher
checksum over the architectural state (variables, CSRs, FP regs, vector regs)
at the end of each test phase. Two 64-bit accumulators (sum1 in t5, sum2 in t6)
process each 8-byte state value directly using add. Both accumulators are stored
via sd (16 bytes total per test phase). This dramatically reduces selfcheck data
size while still detecting state mismatches.

Memory Layout (per-hart):
+0x00: used_bytes (8 bytes) - how many bytes written so far
+0x08: checksum data (16 bytes per test phase: sum1 then sum2)
"""

import logging
from abc import ABC, abstractmethod

import riescue.lib.enums as RV
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator, RuntimeContext
from riescue.dtest_framework.runtime.variable.variable import Variable

log = logging.getLogger(__name__)

# Size in bytes of each stored checksum (sum1 + sum2, each 8 bytes)
SELFCHECK_CHECKSUM_SIZE = 16


class CheckResource(ABC):
    """
    Abstract base for a single checkable resource.

    Each CheckResource instance represents ONE resource to be checksummed.
    Subclasses must implement generate_checksum_update_code().

    Register convention:
      t0       - scratch (load value here before folding)
      t1, t2   - scratch
      t5       - Fletcher sum1 (accumulator, must be preserved across calls)
      t6       - Fletcher sum2 (accumulator, must be preserved across calls)
      t3, t4   - must NOT be clobbered
    """

    @abstractmethod
    def generate_checksum_update_code(self) -> str:
        """
        Generate asm that loads this resource's values into t0 and folds
        each into the running Fletcher checksum (t5=sum1, t6=sum2).

        For each 8-byte value:
          load value into t0
          sum1 += value (mod 2^64 - 1)
          sum2 += sum1 (mod 2^64 - 1)

        May use t0, t1, t2 as scratch. Must NOT clobber t3, t4.
        """
        pass

    def _fletcher_update(self) -> str:
        """Return the Fletcher fold sequence with mod 2^64-1 reduction (value already in t0)."""
        return """
            add t5, t5, t0
            sltu t1, t5, t0
            add t5, t5, t1
            add t6, t6, t5
            sltu t1, t6, t5
            add t6, t6, t1
        """


class VariableResource(CheckResource):
    """Folds a single variable (including arrays) into the Fletcher checksum."""

    def __init__(self, variable: Variable):
        self._variable = variable

    def generate_checksum_update_code(self) -> str:
        """
        Generate assembly to fold variable value(s) into the checksum.

        For arrays, folds all elements sequentially.
        Uses Variable.load() to generate proper load code via tp + offset.
        """
        code = f"\n    # Checksum variable: {self._variable.name}"

        for i in range(self._variable.element_count):
            code += f"""
            {self._variable.load('t0', index=i)}
            {self._fletcher_update()}
            """

        code += "\n"
        return code


class CsrResource(CheckResource):
    """Folds a single CSR value into the Fletcher checksum."""

    def __init__(self, csr_name: str):
        self._csr_name = csr_name

    def generate_checksum_update_code(self) -> str:
        """Generate assembly to read a CSR and fold it into the checksum."""
        return f"""
        # Checksum CSR: {self._csr_name}
        csrr t0, {self._csr_name}
        {self._fletcher_update()}
        """


class FpRegisterResource(CheckResource):
    """Folds a single floating-point register into the Fletcher checksum."""

    def __init__(self, reg_num: int, has_d_extension: bool):
        self._reg_num = reg_num
        self._has_d_extension = has_d_extension

    def generate_checksum_update_code(self) -> str:
        """Generate assembly to move FP register bits to t0 and fold into checksum."""
        move_instr = "fmv.x.d" if self._has_d_extension else "fmv.x.w"
        return f"""
        # Checksum FP register: f{self._reg_num}
        {move_instr} t0, f{self._reg_num}
        {self._fletcher_update()}
        """


class VectorRegisterResource(CheckResource):
    """Folds a single vector register into the Fletcher checksum via scratch memory."""

    def __init__(self, reg_num: int, vlen_bytes: int, scratch_var: Variable):
        """
        Initialize vector register resource.

        :param reg_num: Vector register number (0-31)
        :param vlen_bytes: Size of each vector register in bytes (from vlenb CSR)
        :param scratch_var: Hart-local scratch variable for memory-based access
        """
        self._reg_num = reg_num
        self._vlen_bytes = vlen_bytes
        self._scratch_var = scratch_var

    def generate_checksum_update_code(self) -> str:
        """
        Generate assembly to store vector register to scratch memory and fold
        each 8-byte chunk into the checksum.

        Uses vs1r.v to store without modifying vtype, then loads 8-byte chunks.
        """
        num_chunks = self._vlen_bytes // 8

        chunk_code = ""
        for i in range(num_chunks):
            chunk_code += f"""
            ld t0, {i * 8}(t2)
            {self._fletcher_update()}"""

        return f"""
        # Checksum vector register: v{self._reg_num}
        {self._scratch_var.load_immediate('t2')}
        vs1r.v v{self._reg_num}, (t2)
        {chunk_code}
        """


class Selfcheck(AssemblyGenerator):
    """
    Generates assembly code necessary for self-checking support.

    Generates code for computing a 128-bit Fletcher checksum over architectural
    state at the end of each discrete test. The checksum is saved to per-hart
    memory regions (16 bytes per phase) that can be compared against expected
    "golden" values.

    Interface:
    - ``selfcheck__decide_save_or_check``: Called once at boot; sets ``selfcheck_mode`` to
      0 (save) or 1 (check) based on whether selfcheck data is compiled in.
    - ``selfcheck__save_or_check``: Called at each test boundary; computes the Fletcher
      checksum over all resources, then branches to save or check based on ``selfcheck_mode``.

    Memory Layout (per-hart):
    +0x00: used_bytes (8 bytes) - how many bytes written so far
    +0x08: checksum data (SELFCHECK_CHECKSUM_SIZE bytes per phase: sum1 then sum2)
    """

    def __init__(self, ctx: RuntimeContext):
        super().__init__(ctx=ctx)

        if self.featmgr.mp != RV.RiscvMPEnablement.MP_OFF:
            raise Exception("Selfcheck does not support MP")

        self.mode = self.variable_manager.register_hart_variable("selfcheck_mode")
        self.check_offset = self.variable_manager.register_hart_variable("selfcheck_check_offset")

        self._resources: list[CheckResource] = []
        self._resources.append(VariableResource(self.variable_manager.get_variable("gpr_save_area")))

        # Register FP registers if F extension is enabled
        if self.featmgr.is_feature_enabled("f"):
            has_d_extension = self.featmgr.is_feature_enabled("d")
            for i in range(32):
                self._resources.append(FpRegisterResource(i, has_d_extension))

        # Register Vector registers if V extension is enabled
        if self.featmgr.is_feature_enabled("v"):
            # Default VLEN is 256 bits = 32 bytes (matches whisper_config.json)
            vlen_bytes = 32

            # Add vector CSRs
            self._resources.append(CsrResource("vtype"))
            self._resources.append(CsrResource("vl"))
            self._resources.append(CsrResource("vstart"))

            # Register scratch area for memory-based vector store (vlen_bytes / 8 elements of 8 bytes each)
            selfcheck_vec_scratch = self.variable_manager.register_hart_variable("selfcheck_vec_scratch", value=0, element_count=vlen_bytes // 8)

            # Add vector registers v0-v31
            for i in range(32):
                self._resources.append(VectorRegisterResource(i, vlen_bytes, selfcheck_vec_scratch))

        # Register equate for per-hart size (8 byte header + 16 bytes per checkpoint)
        per_hart_size = 8 + SELFCHECK_CHECKSUM_SIZE * self.featmgr.repeat_times * (len(self.pool.discrete_tests) + 2)
        self.register_equate("selfcheck_per_hart_size", f"0x{per_hart_size:x}")

    def generate(self) -> str:
        """
        Generate selfcheck assembly code.

        Generates two routines:
        1. selfcheck__decide_save_or_check - sets selfcheck_mode (0=save, 1=check)
        2. selfcheck__save_or_check - computes checksum, then branches to save or check
        """
        code_parts: list[str] = [
            '.section .runtime, "ax"',
            ".balign 4, 0",
            self._generate_decide_save_or_check(),
            self._generate_save_or_check_routine(),
        ]

        return "\n".join(code_parts)

    def _generate_decide_save_or_check(self) -> str:
        """
        Generate the selfcheck__decide_save_or_check routine.

        Called once at boot. Sets selfcheck_mode to 0 (save) or 1 (check)
        based on whether selfcheck data already exists (used_bytes > 0).
        """
        return f"""
# Selfcheck decide whether to use save or check mode
# Sets selfcheck_mode: 0 = save, 1 = check
# Uses: t0-t4 (caller-saved)
selfcheck__decide_save_or_check:
    # hart_offset = hart_id * per_hart_size
    csrr t0, mhartid                # Read hart ID directly (always M-mode)
    li t1, selfcheck_per_hart_size  # Per-hart size (from equate)
    mul t0, t0, t1
    li t1, selfcheck_data_pa        # Base PA address from equate
    add t4, t0, t1                  # t4 = this hart's state_save base

    ld t0, 0(t4)                    # t0 = used_bytes
    beqz t0, selfcheck__decided_save
selfcheck__decided_check:
    li t0, 1
    {self.mode.store('t0')}
    ret
selfcheck__decided_save:
    li t0, 0
    {self.mode.store('t0')}
    ret
"""

    def _generate_save_or_check_routine(self) -> str:
        """
        Generate the selfcheck__save_or_check routine.

        This single routine:
        1. Gets hart ID and calculates state_save base address (t3, preserved)
        2. Initializes Fletcher accumulators t5=0, t6=0
        3. Folds each resource into the checksum
        4. Branches on selfcheck_mode: save or check
        """
        resource_checksum_code = "\n".join(r.generate_checksum_update_code() for r in self._resources)

        return f"""
# Selfcheck save-or-check routine
# Computes Fletcher checksum over architectural state, then saves or checks
# Uses: t0-t2, t5, t6 (caller-saved); t3 = hart state_save base (preserved); t4 = scratch
selfcheck__save_or_check:
    # 1. Get hart ID and calculate state_save base (preserved in t3)
    csrr t0, mhartid                # Read hart ID directly (always M-mode)
    li t1, selfcheck_per_hart_size  # Per-hart size (from equate)
    mul t0, t0, t1
    li t1, selfcheck_data_pa        # Base PA address from equate
    add t3, t0, t1                  # t3 = this hart's state_save base

    # 2. Init Fletcher accumulators
    li t5, 0                        # sum1 = 0
    li t6, 0                        # sum2 = 0

    # 3. Fold each resource into the checksum
    {resource_checksum_code}

    # 4. Branch based on mode
    {self.mode.load('t0')}
    bnez t0, selfcheck__do_check

selfcheck__do_save:
    # Save: store checksum at base + 8 + used_bytes, then update used_bytes
    ld t0, 0(t3)                    # t0 = used_bytes
    addi t4, t3, 8                  # skip header
    add t4, t4, t0                  # t4 = write position
    sd t5, 0(t4)                    # store sum1
    sd t6, 8(t4)                    # store sum2
    addi t0, t0, {SELFCHECK_CHECKSUM_SIZE}
    sd t0, 0(t3)                    # update used_bytes
    ret

selfcheck__do_check:
    # Check: compare checksum at base + 8 + check_offset, then update check_offset
    {self.check_offset.load('t0')}
    li t1, (selfcheck_per_hart_size-8) # check region size less header
    bge t0, t1, eot__failed         # check_offset out of bounds
    addi t4, t3, 8                  # skip header
    add t4, t4, t0                  # t4 = check position
    ld t1, 0(t4)                    # load stored sum1
    bne t5, t1, eot__failed
    ld t1, 8(t4)                    # load stored sum2
    bne t6, t1, eot__failed
    addi t0, t0, {SELFCHECK_CHECKSUM_SIZE}
    {self.check_offset.store('t0')}
    ret
"""
