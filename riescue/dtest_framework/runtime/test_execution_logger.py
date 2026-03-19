# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

"""
Test execution logger module.

Assembly generator that provides test_execution_log_add for logging test
execution events. Per-hart data structure (test_execution_log):

  uint64_t test_counter
  uint64_t current_test_ptr
  struct { uint64_t test_ptr; uint64_t mtime; } entries[128]

Each entry stores full 64-bit test pointer and 64-bit mtime.
test_counter & 0x7f is the index for the next entry.
Caller must have current test pointer in t1 (e.g. from scheduler).
"""

from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator, RuntimeContext

# Per-hart layout: test_counter (8) + current_test_ptr (8) + 128 * (test_ptr, mtime) (128*16)
# Making it page aligned to simplify debugging/dumping of the test execution data.
TEST_EXECUTION_DATA_PER_HART_SIZE = (((8 + 8 + 128 * 16) + 0xFFF) // 0x1000) * 0x1000


class TestExecutionLogger(AssemblyGenerator):
    """
    Generates assembly for test execution logging.

    Interface:
    - ``test_execution_log_add``: Called from scheduler with t1 = current test pointer.
      Sets s1 = mhartid, a0 = test_execution_data + s1*per_hart_size (hart base).
      Appends (t1, mtime) to this hart's log using index test_counter & 0x7f.
      Uses: t0, t2 (caller-saved). Writes s1 (mhartid), a0 (hart log base).
    """

    def __init__(self, ctx: RuntimeContext):
        super().__init__(ctx=ctx)
        self.register_equate("test_execution_data_per_hart_size", f"0x{TEST_EXECUTION_DATA_PER_HART_SIZE:x}")

    def generate(self) -> str:
        """
        Generate test execution logger assembly code.

        Emits the test_execution_log_add routine in .runtime section.
        """
        code_parts: list[str] = [
            '.section .runtime, "ax"',
            ".align 2",
            self._generate_test_execution_log_add(),
        ]
        return "\n".join(code_parts)

    def _generate_test_execution_log_add(self) -> str:
        """
        Generate the test_execution_log_add routine.

        On entry: t1 = current test pointer (from scheduler).
        Gets mhartid into s1, loads a0 with test_execution_log base for this hart,
        then stores (t1, mtime) at entries[test_counter & 0x7f].
        """
        return """
test_execution_log_init:
    csrr s1, mhartid
    li t0, test_execution_data_per_hart_size
    mul t0, s1, t0
    li a0, test_execution_data # a0 is the base of the hart's test execution data
    add a0, a0, t0
    sd x0, 0(a0)    # clear test_counter
    ret

# Test execution log add. runs in m mode from scheduler
# Per-hart: test_counter(8) current_test_ptr(8) (test_ptr, mtime)[128] (16 bytes/entry)
# Index = test_counter & 0x7f. On entry t1 = current test pointer.
# Uses: t0, t2 (caller-saved). Sets s1=mhartid, a0=hart base.
# t1 is the current test pointer. Stays unchanged.
test_execution_log_add:
    csrr s1, mhartid
    li t0, test_execution_data_per_hart_size
    mul t0, s1, t0
    li a0, test_execution_data
    add a0, a0, t0 # a0 is now the base of the hart's test execution data

    ld t0, 0(a0) # t0 is the test_counter
    andi t2, t0, 0x7f
    slli t2, t2, 4 # t2 is the offset of the next entry
    add t2, a0, t2
    addi t2, t2, 16 # t2 is the address of the next entry in test circular buffer

    sd t1, 0(t2) # store the test pointer in the next entry
    csrr t0, time
    sd t0, 8(t2) # store the current time in the next entry

    sd t1, 8(a0) # store the test pointer in the current entry
    ld t0, 0(a0) # t0 is the test_counter
    addi t0, t0, 1 # increment the test_counter
    sd t0, 0(a0) # store the test_counter in the current entry
    ret
"""
