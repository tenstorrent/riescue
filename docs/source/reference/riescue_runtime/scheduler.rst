Scheduler
=========

Generates test scheduler assembly code for coordinating test execution.
Manages test sequencing, randomization, and synchronization across single or multiple harts.
Supports barriers, mutexes, and both sequential and parallel execution modes.

Scheduler privilege mode depends on the Test's privilege mode.
- If test's privilege mode is MACHINE, scheduler runs in MACHINE mode
- If test's privilege mode is SUPER, scheduler runs in SUPER mode
- If test's privilege mode is USER, scheduler runs in MACHINE mode

Configuration
-------------

- ``force_alignment``: Align all instructions on 8-byte boundary
- ``num_cpus``: Number of harts for multiprocessor mode
- ``priv_mode``: Privilege mode for test execution
- ``repeat_times``: Number of times to repeat each test (-1 for Linux mode runtime randomization)
- ``linux_mode``: Enable Linux scheduler mode with runtime randomization
- ``parallel_scheduling_mode``: Scheduling mode for parallel MP (ROUND_ROBIN or EXHAUSTIVE)
