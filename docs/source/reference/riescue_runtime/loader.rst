Loader
======

Generates loader assembly code for initializing the test runtime environment.
Boots at ``_start`` in machine mode, initializes registers (integer, floating-point, vector if supported) and CSRs, sets up paging and interrupts, and hands control to the scheduler.

Configuration
-------------

- ``priv_mode``: Target privilege mode
- ``paging_mode``: Paging mode to enable (SV39, SV48, SV57)
- ``bringup_pagetables``: Skip scheduler and jump directly to first test
- ``linux_mode``: Enable Linux loader mode
- ``wysiwyg``: Minimal loader without runtime setup
- ``big_endian``: Enable big-endian mode
- ``csr_init``: List of CSRs to initialize (format: ``csr=value``)
- ``csr_init_mask``: List of CSRs to initialize with mask (format: ``csr=mask=value``)
- ``counter_event_path``: Path to event file for enabling performance counters
- ``disable_wfi_wait``: Disable WFI waiting
- ``needs_pma``: Setup PMA regions
- ``setup_pmp``: Setup PMP regions
- ``secure_mode``: Enable secure mode
- ``setup_stateen``: Setup mstateen registers
- ``env``: Test environment (TEST_ENV_VIRTUALIZED or standard)
- ``senvcfg``: Value to write to senvcfg
- ``trap_handler_label``: Label for trap handler entry point
- ``interrupts_enabled``: Enable interrupts
- ``deleg_excp_to``: Delegate exceptions to privilege level
- ``pbmt_ncio``: Enable SVPBMT NCIO
- ``svadu``: Enable SVADU extension
- ``menvcfg``: Value to OR with default menvcfg
- ``medeleg``: Custom medeleg value
- ``user_interrupt_table``: Use user-defined interrupt table
