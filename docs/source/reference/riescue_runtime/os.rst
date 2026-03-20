OS
==

Generates OS code for test execution.
Produces barrier synchronization, test pass/fail handling, and utility functions for discrete test coordination.
Manages shared and per-hart OS variables.

Configuration
-------------

- ``linux_mode``: Enable Linux OS mode
- ``num_cpus``: Number of harts (enables multiprocessor variables)
- ``xlen``: Register width (32 or 64 bits)
- ``excp_hooks``: Enable pre/post exception handler hooks
- ``wysiwyg``: Minimal OS with only tohost/fromhost
- ``fe_tb``: Frontend testbench mode
- ``eot_pass_value``: Value to write to tohost on test pass
- ``eot_fail_value``: Value to write to tohost on test fail
- ``big_endian``: Enable big-endian mode for tohost writes

