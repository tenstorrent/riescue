Macros
======

Generates assembly macros for the Test Environment to use.
Provides macros for multiprocessor synchronization (barriers, mutexes, semaphores, critical sections), exception handling setup, and interrupt control.

Configuration
-------------

- ``priv_mode``: Privilege mode for macro operations
- ``num_cpus``: Number of harts (enables multiprocessor macros and hart ID offset calculations)

