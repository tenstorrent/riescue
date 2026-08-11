Loader
======

Generates loader assembly code for initializing the test runtime environment.
Boots at ``_start`` in machine mode, initializes registers (integer, floating-point, vector if supported) and CSRs, sets up paging and interrupts, and hands control to the scheduler.

Interface
-----------

The loader interface defines the following routines:


``_start``
__________

Entry point required by the linker. Sets up integer GPRs and initial panic.
Any traps that occur before the loader sets up the trap handler will jump to ``eot__failed`` to end the test.


``loader__initialize_runtime``
______________________________

Initializes runtime environment by setting up CSRs, paging, trap delegation, and privilege mode.
Any traps that occurs after ``mtvec`` has been set will jump to ``eot__test_end`` to end the test.
Includes any ``PRE_LOADER`` hooks that have been registered.



``loader__done``
______________________________

Starts scheduler by jumping to ``scheduler__init``.
Includes any ``POST_LOADER`` hooks that have been registered.




``loader_panic``
______________________________

Panic routine to jump directly to ``eot__failed`` and end the test as a fail.
This assumes that the current privilege is M mode and bypasses ``eot__end_test`` sequence.

.. note::
    Traps that occur after ``mtvec`` has been set will jump through ``trap_handler`` instead, and finish with ``eot__end_test``.



.. mermaid::

    graph LR
        start[_start]
        panic[loader__panic]
        init[loader__initialize_runtime]
        done[loader__done]
        scheduler[scheduler__init]
        eot[eot__fail]


        start --> init
        start -->|trap| panic
        init --> done
        init -->|trap| panic
        done --> scheduler

        panic --> eot



        classDef mMode fill:#5164e0,stroke:#5164e0,stroke-width:3px,color:#fff
        classDef handlerMode fill:#fa512e,stroke:#fa512e,stroke-width:3px,color:#000
        classDef handlerBlock fill:#FEC3A4,stroke:#FEC3A4,stroke-width:3px,color:#0
        classDef testMode fill:#7584e6,stroke:#7584e6,stroke-width:3px,color:#0
        classDef testBlock fill:#C0C0E0,stroke:#C0C0E0,stroke-width:3px,color:#0

        class start,init,eot,panic mMode
        class done,scheduler handlerMode
        class syscall_f0000002,syscall_f0000003,failed,end_test handlerBlock




Linux Mode
-----------

The linux loader targets application code is enabled with the ``linux_mode`` feature.
It is used to run tests in linux mode and skips all setup code to jump to ``scheduler__init``.


``wysiwyg`` mode
-----------------

Returns the loader used for `What You See Is What You Get` (wysiwyg) mode.

Since wysiwyg mode doesn't have a scheduler, the loader just initializes the GPRs
and any init_csr_code and continues to test.


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
- ``needs_pma``: Setup PMA regions (see `PMA Setup`_ below)
- ``num_pmas``: Number of implemented PMA CSR entries (2-64, default 64); must match the ISS config
- ``enable_pma_randomization``: Also program randomized decoy PMA regions (implies ``needs_pma``)
- ``user_programmable_pmacfg``: Leave the first N PMA entries untouched for the test to program at runtime
- ``setup_pmp``: Setup PMP regions
- ``secure_mode``: Enable secure mode
- ``env``: Test environment (TEST_ENV_VIRTUALIZED or standard)
- ``senvcfg``: Value to write to senvcfg
- ``trap_handler_label``: Label for trap handler entry point
- ``interrupts_enabled``: Enable interrupts
- ``deleg_excp_to``: Sets ``medeleg``/``mideleg`` for exception delegation (does not change runtime privilege)
- ``pbmt_ncio``: Enable SVPBMT NCIO
- ``svadu``: Enable SVADU extension
- ``menvcfg``: Value to OR with default menvcfg
- ``medeleg``: Custom medeleg value


PMA Setup
----------

When ``needs_pma`` is set, ``loader__initialize_runtime`` includes a ``loader__setup_pma`` block
that programs one PMA CSR entry per defined region, in machine mode, before any test code runs.

Each PMA entry is a ``pmacfg``/``pmamask`` register pair. The first 16 entries are direct CSRs
(``pmacfg`` at ``0x7E0 + i``, ``pmamask`` at ``0x7F0 + i``); entries 16 and above are accessed
indirectly by writing ``miselect = 0x8000000000000000 | entry`` and then accessing ``mireg``
(``pmacfg``) and ``mireg2`` (``pmamask``). Writing an entry's ``pmacfg`` resets its mask, so the
``pmamask`` write is always emitted after the ``pmacfg`` write.

Entry layout, from index 0 (highest match priority) upward:

1. ``user_programmable_pmacfg`` untouched entries reserved for the test's own runtime programming
2. Named test regions (from ``;#pma_hint`` and ``in_pma`` directives), then randomized decoy
   regions when ``enable_pma_randomization`` is set, then memory-map regions
3. Invalidated (zeroed) entries up to the catch-alls, when randomization is set
4. Two catch-all entries at ``num_pmas - 2`` / ``num_pmas - 1``: an IO/noncacheable region
   covering 0-2GB and a cacheable-coherent RWX region covering all of memory, matching the
   boot reset values of ``pmacfg14``/``pmacfg15``

With randomization on, the catch-alls are written *first* (the boot entries 14/15 still provide
default coverage at that point, so no access ever lacks a matching entry), then the stale boot
entries are invalidated at the end.

See :doc:`/user_guides/pma` for a full guide to PMA configuration and randomization.
