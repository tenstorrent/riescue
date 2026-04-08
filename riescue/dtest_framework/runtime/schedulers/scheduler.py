# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Tuple, Any
from abc import ABC, abstractmethod

import riescue.lib.enums as RV
from riescue.dtest_framework.lib.routines import Routines
from riescue.dtest_framework.runtime.assembly_generator import AssemblyGenerator


class Scheduler(AssemblyGenerator, ABC):
    """
    Base scheduler, provides the scheduler interface for runtime.
    This includes scheduler assembly routines that are called by other runtime modules:

    - scheduler__init
    - scheduler__dispatch
    - scheduler__finished
    - scheduler__panic

    This class contains scaffolding for the scheduler interface, including jumping to test, switching privilege for test, scheduler error handling, etc.
    Actual scheduling logic is implemented in subclasses.

    Runs in machine mode. self.xtvec and self.scratch_reg are machine mode registers set in AssemblyGenerator.
    """

    def __init__(self, dtests: list[str], **kwargs: Any):
        super().__init__(**kwargs)
        self.dtests = dtests
        self.force_alignment = self.featmgr.force_alignment  # Align all instructions on 8 byte boundary

        self.variable_manager.register_hart_variable("scheduler__saved_stvec", 0)
        if self.featmgr.fs_randomization > 0:
            self.variable_manager.register_hart_variable("fs_rr_index", 0)
        if self.featmgr.vs_randomization > 0:
            self.variable_manager.register_hart_variable("vs_rr_index", 0)

        # Register hart-local variable for current test index (for RVCP pass/fail messages)
        # Special values: 0xFFFFFFFE = test_setup, 0xFFFFFFFF = test_cleanup
        if self.featmgr.rvcp_print_enabled():
            self.current_test_index = self.variable_manager.register_hart_variable("current_test_index", value=0xFFFFFFFF)

        self.dtests_sequence: list[str]
        if self.featmgr.repeat_times == -1 or self.mp_parallel:
            self.dtests_sequence = [test for test in dtests]  # Schedule is randomized at runtime for linux mode and for parallel mp mode.
        else:
            self.dtests_sequence = [test for test in dtests for _ in range(self.featmgr.repeat_times)]
        self.rng.shuffle(self.dtests_sequence)

    @abstractmethod
    def scheduler_dispatch(self) -> str:
        """
        Logic responsible for loading t1 with the next test.

        Scheduler assumes that the Hart Context is set into the ``tp`` register at this point.
        """
        return ""

    def generate(self) -> str:
        """
        Generates scheduler assmebly code for scheduler. Subclasses should really only implement the abstract methods, scheduler_init, scheduler_dispatch, and scheduler_finished.
        """
        return f"""
.section .runtime, "ax"

# Scheduler initialization; only ran once
{self.scheduler_init_label}:
    {self._init_setup()}
    {self.scheduler_init()}

# Entry point for scheduling next test
{self.scheduler_dispatch_label}:
    {self._dispatch_setup()}
    {self.featmgr.call_hook(RV.HookPoint.PRE_DISPATCH)}
    {self.scheduler_dispatch()}
    {"jal test_execution_log_add" if self.featmgr.log_test_execution else ""}
    {self.featmgr.call_hook(RV.HookPoint.POST_DISPATCH)}

# Jump to test. Test address should be loaded into t1 before running
scheduler__execute_test:
    {self.save_hart_context()}
    {self.execute_test()}

# Exit point for scheduler; only ran when all tests are finished
{self.scheduler_finished_label}:
    {self.scheduler_finished()}

# Panic point for scheduler; If an exception occurs during scheduling, it causes the test to end immediately. Only internal errors should happen here.
# Interrupts are disabled during scheduling and resumed when the scheduler is fininished.
{self.kernel_panic(self.scheduler_panic_label)}

# Additional scheduler routines
{self.scheduler_routines()}

# scheduler-local variables
{self.scheduler_variables()}

# test pointers
.align 3
scheduler__test_setup_ptr:
    .dword test_setup
scheduler__test_cleanup_ptr:
    .dword test_cleanup

"""

    def scheduler_init(self) -> str:
        """
        default behavior is to load test_setup into t1 and jump to scheduler__execute_test.
        Schedulers shouldn't include test_setup as part of the dispatch logic.
        """
        code = ""
        # Set current test index to test_setup special value (0xFFFFFFFE)
        if self.featmgr.rvcp_print_enabled():
            code += f"""
    li t0, 0xFFFFFFFE  # test_setup special index
    {self.current_test_index.store(src_reg='t0')}
"""
        code += """
    la t1, scheduler__test_setup_ptr
    ld t1, (t1)
    j scheduler__execute_test
"""
        return code

    def scheduler_finished(self) -> str:
        """
        Done with scheduler, proceed to EOT. Override if any additional cleanup is needed
        """
        return """
    li gp, 1
    j eot__end_test
"""

    def scheduler_routines(self) -> str:
        """
        Additional scheduler routines, called by scheduler
        """
        return ""

    def _init_setup(self) -> str:
        """
        Setup for scheduler initialization.
        Disables interrupts, sets up scheduler panic handler before running scheduler__init code.
        """
        return f"""
        # Setup scheduler panic handler before running test_setup
        la t0, scheduler__panic
        csrrw t0, {self.tvec}, t0
        {self.variable_manager.get_variable("scheduler__saved_stvec").store(src_reg='t0')}
        """

    def _dispatch_setup(self) -> str:
        """
        Setup for scheduler dispatch.
        Sets up scheduler panic handler before running scheduler__dispatch code.
        """
        return f"""
        # Setup scheduler panic handler before dispatching
        la t0, scheduler__panic
        csrrw t0, {self.tvec}, t0
        {self.variable_manager.get_variable("scheduler__saved_stvec").store(src_reg='t0')}
        """

    def save_hart_context(self) -> str:
        """
        Responsible for saving hart context so test doesn't overwrite data.

        - save tp to scratch register
        - restore tvec to trap handler
        - Enable interrupts at the end of save # FIXME: Interrupts are only re-enabled if handler is in a different privilege mode.
        """
        code = f"""
    {self.variable_manager.get_variable("scheduler__saved_stvec").load(dest_reg="t0")}
    csrw {self.tvec}, t0
    {self.variable_manager.exit_hart_context(scratch=self.scratch_reg)}
    li tp, 0
"""
        return code

    def execute_test(self) -> str:
        """
        Handles jumping to test. Test address should be loaded into ``t1``

        If test is in M/S mode, handler should already be in correct privilege mode, and can jump to test.

        Scheduler should save the Hart Context before executing the test.
        """

        # set tvec
        # enable interrupts

        if self.featmgr.priv_mode == RV.RiscvPrivileges.MACHINE:
            code = ""
            # For M-mode paging, enable MPRV just before jumping to test code.
            # MPRV=1 + MPP=S makes data accesses use S-mode translation.
            if self.featmgr.paging_mode != RV.RiscvPagingModes.DISABLE:
                code += """
                li t0, ((1 << 17) | (1 << 11))    # Set MPRV=1 and MPP=01 (supervisor)
                csrrs x0, mstatus, t0
                li t0, (1<<12) # Clear mstatus[12]
                csrrc x0, mstatus, t0
                """
            code += "jr t1\n"
        else:
            # For user mode use sret to jump to test
            code = self.switch_test_privilege(
                from_priv=RV.RiscvPrivileges.SUPER,
                to_priv=self.featmgr.priv_mode,
                jump_register="t1",
                switch_to_vs=self.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED,
            )
        return code

    def scheduler_variables(self) -> str:
        """
        Default scheduler-local variables. Uses an array of test labels to store the test sequence.
        Essentially the array is``os_test_sequence = ["test_cleanup", "test01", "test02", ...]``,
        which can be indexed by the ``num_runs`` variable to get the next test to run

        .. note:: Not scalable for large repeat_times values
            This stores the self.dtests_sequence list into memory.
            Since self.dtest_sequences is the tests repeated repeat_times times, it has a size of O(MxN) for M tests and N ``repeat_times``.

            This can be optimized in the future using a static array of M tests and iterating through the array N times at runtime.

        Since test_cleanup is always ran last, it is inlcuded in ``os_test_sequence``.
        This is a bit faster than setting a flag in scheduler__finished to check if test_cleanup has been ran.
        """
        code = f"""
        .align 3
        num_runs:
            .dword {len(self.dtests_sequence)+1}

        .align 3
        os_test_sequence:
            .dword test_cleanup
        """
        for test in self.dtests_sequence:
            code += f"    .dword {test}\n"
        code += self._fs_vs_rr_tables_section()
        code += self._generate_test_descriptor_table()
        return code

    def _generate_test_descriptor_table(self) -> str:
        """Generate descriptor table mapping test indices to test name strings for RVCP messages."""
        if not self.featmgr.rvcp_print_enabled():
            return ""

        code = "\n.align 3\ndtest_descriptor_table:\n"
        for i, test in enumerate(self.dtests):
            code += f"    .dword dtest_name_{i}\n"

        # String data for each discrete test
        code += "\n# Test name strings\n"
        for i, test in enumerate(self.dtests):
            code += f'dtest_name_{i}: .asciz "{test}"\n'

        # Generate lookup table mapping os_test_sequence position to discrete test index
        # This is needed because with repeat_times > 1, the same test appears multiple times
        # in os_test_sequence, but we need the discrete test index (0-N) for dtest_descriptor_table
        code += "\n# Lookup table: os_test_sequence position -> discrete test index\n"
        code += ".align 2\ndtest_index_map:\n"
        code += "    .word 0xFFFFFFFF  # Position 0 = test_cleanup\n"

        # Create mapping from test label to discrete test index
        label_to_index = {test: i for i, test in enumerate(self.dtests)}
        for test in self.dtests_sequence:
            idx = label_to_index.get(test, 0xFFFFFFFF)
            code += f"    .word {idx}  # {test}\n"

        return code

    def _fs_vs_rr_tables_section(self) -> str:
        """Round-robin tables for FS/VS randomization (per-dispatch deterministic cycle)."""
        code = ""
        if self.featmgr.fs_randomization > 0 and self.featmgr.fs_randomization_values:
            code += "\n        .align 2\n        fs_rr_table:\n"
            for v in self.featmgr.fs_randomization_values:
                code += f"            .word {v & 0x3}\n"
            code += f"        .equ fs_rr_table_size, {len(self.featmgr.fs_randomization_values)}\n"
        if self.featmgr.vs_randomization > 0 and self.featmgr.vs_randomization_values:
            code += "\n        .align 2\n        vs_rr_table:\n"
            for v in self.featmgr.vs_randomization_values:
                code += f"            .word {v & 0x3}\n"
            code += f"        .equ vs_rr_table_size, {len(self.featmgr.vs_randomization_values)}\n"
        return code
