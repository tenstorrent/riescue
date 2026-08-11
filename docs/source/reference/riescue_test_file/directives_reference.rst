RiescueD Directives
=============================

This reference documents all RiescueD directives with their complete syntax and parameters. RiescueD directives are special comments that start with ``;#`` and control test generation behavior.

Test Generation Directives
---------------------------

Random Data Generation
~~~~~~~~~~~~~~~~~~~~~~

**;#random_data** - Generate Random Values
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Creates random data values with optional constraints.

**Syntax:**

.. code-block:: asm

    ;#random_data(name=<symbol>, type=<datatype> [, and_mask=<mask>] [, or_mask=<mask>])

**Parameters:**

- ``name`` (required) - Symbol name to reference in assembly code
- ``type`` (required) - Data width: ``bits8``, ``bits16``, ``bits32``, ``bits64``, or ``bitsN`` for arbitrary width
- ``and_mask`` (optional) - Mask to constrain random values (bitwise AND)
- ``or_mask`` (optional) - Mask to set specific bits (bitwise OR)

**Examples:**

.. code-block:: asm

    ;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
    ;#random_data(name=data2, type=bits64)
    ;#random_data(name=small_val, type=bits8, and_mask=0xff)

Memory Address Generation
~~~~~~~~~~~~~~~~~~~~~~~~~

**;#random_addr** - Generate Random Addresses
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Creates random memory addresses with alignment and size constraints.

**Syntax:**

.. code-block:: asm

    ;#random_addr(name=<symbol>, type=<addrtype>, size=<size> [, and_mask=<mask>] [, io=<0|1>] [, pma_options...])

**Parameters:**

- ``name`` (required) - Symbol name for the address
- ``type`` (required) - Address type: ``physical`` or ``linear``
- ``size`` (required) - Size of memory region in bytes (hexadecimal)
- ``and_mask`` (optional) - Alignment mask (e.g., ``0xfffff000`` for 4KB alignment)
- ``io`` (optional) - Set to ``1`` for I/O memory regions, ``0`` for normal memory (default: ``0``)

**Physical Memory Attributes (PMA) Parameters:**

Setting ``in_pma=1`` on a *physical* address places it inside a PMA region with the requested
attributes (requires running with ``--needs_pma``). If the attributes match a region that already
exists — from a ``;#pma_hint`` directive or an earlier ``in_pma`` address — the existing region is
reused instead of consuming another PMA CSR entry. See :doc:`/user_guides/pma` for the full
workflow.

- ``in_pma`` - Place this address inside a PMA region (``1`` or ``0``)
- ``pma_size`` - PMA region size in bytes (defaults to the ``size`` parameter if omitted)
- ``pma_read``, ``pma_write``, ``pma_execute`` - Access permissions (``1`` or ``0``)
- ``pma_memory_type`` - Memory type: ``memory``, ``io``, ``ch0``, ``ch1``
- ``pma_amo_type`` - Atomic operation support: ``none``, ``logical``, ``swap``, ``arithmetic``
- ``pma_cacheability`` - Cache behavior for memory type: ``cacheable``, ``noncacheable``
- ``pma_combining`` - Combining behavior for io type: ``combining``, ``noncombining``
- ``pma_routing_to`` - Coherency routing: ``coherent``, ``noncoherent``
- ``pma_masked`` - Force the region to be programmed with a random ``pmamask`` value; requires
  ``in_pma=1`` and ``--enable_pma_randomization`` (``1`` or ``0``, default: ``0``)

**Examples:**

.. code-block:: asm

    ;#random_addr(name=addr1, type=physical, size=0x1000, and_mask=0xfffff000)
    ;#random_addr(name=vaddr, type=linear, size=0x2000)
    ;#random_addr(name=io_addr, type=physical, io=1, size=0x100)

    # Physical address inside a cacheable RWX PMA region
    ;#random_addr(name=phys_cacheable, type=physical, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=memory, pma_cacheability=cacheable, pma_read=1, pma_write=1, pma_execute=1)

    # Physical address inside an MMIO window, in a read/write io-type PMA region
    ;#random_addr(name=phys_io, type=physical, size=0x1000, and_mask=0xfffffffffffff000, io=1, in_pma=1, pma_size=0x1000, pma_memory_type=io, pma_read=1, pma_write=1, pma_execute=0, pma_amo_type=none, pma_routing_to=noncoherent)

**;#pma_hint** - Request Auto-Generated PMA Regions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Asks the framework to create one or more PMA regions with the requested attributes at
framework-chosen addresses. Use this when a test needs regions with particular attribute
*shapes* (e.g. "one cacheable and one noncacheable region, adjacent to each other") without
caring where they land. Requires running with ``--needs_pma``. Regions created by hints can be
targeted by ``;#random_addr(..., in_pma=1, ...)`` addresses with matching attributes.

**Syntax (attribute-list form):**

The framework generates one region for every combination in the cartesian product of the
attribute lists.

.. code-block:: asm

    ;#pma_hint(name=<hint_name>, memory_types=[...], cacheability=[...], rwx_combos=[...] [, combining=[...]] [, amo_types=[...]] [, routing=[...]] [, adjacent=<true|false>] [, min_regions=<N>] [, max_regions=<N>] [, size=<bytes>])

**Syntax (explicit combinations form):**

One region is generated per combination dict.

.. code-block:: asm

    ;#pma_hint(name=<hint_name>, combinations=[{memory_type=<type>, cacheability=<c>, rwx=<rwx>, amo_type=<amo>, routing=<r>}, ...] [, adjacent=<true|false>] [, size=<bytes>])

**Parameters:**

- ``name`` (required) - Unique hint name; generated regions are named ``pma_<hint_name>_<index>``
- ``memory_types`` - List of memory types: ``memory``, ``io``, ``ch0``, ``ch1`` (default: ``[memory]``)
- ``cacheability`` - List of cache behaviors for memory type: ``cacheable``, ``noncacheable`` (default: ``[cacheable]``)
- ``combining`` - List of combining behaviors for io type: ``combining``, ``noncombining`` (default: ``[noncombining]``)
- ``rwx_combos`` - List of permission strings, e.g. ``rwx``, ``rw``, ``r`` (default: ``[rwx]``)
- ``amo_types`` - List of atomic support levels: ``none``, ``logical``, ``swap``, ``arithmetic`` (default: ``[arithmetic]``)
- ``routing`` - List of coherency routings: ``coherent``, ``noncoherent`` (default: ``[coherent]``)
- ``combinations`` - Explicit list of attribute dicts; when given, the attribute lists above are ignored
- ``adjacent`` - Place the generated regions adjacent to each other (default: ``false``)
- ``min_regions`` / ``max_regions`` - Bound the number of generated regions
- ``size`` - Size of each generated region in bytes (hex accepted)

**Examples:**

.. code-block:: text

    # Two adjacent memory regions: one cacheable, one noncacheable, both RWX
    ;#pma_hint(name=simple_hint,
        memory_types=[memory],
        cacheability=[cacheable, noncacheable],
        rwx_combos=[rwx],
        adjacent=true
    )

    # Explicit combinations
    ;#pma_hint(name=combo_hint,
        combinations=[
            {memory_type=memory, cacheability=cacheable, rwx=rwx, amo_type=arithmetic, routing=coherent},
            {memory_type=memory, cacheability=noncacheable, rwx=rwx, amo_type=arithmetic, routing=coherent}
        ],
        adjacent=true
    )

    # A single 1MB noncacheable read/write region
    ;#pma_hint(name=custom_size_hint,
        memory_types=[memory],
        cacheability=[noncacheable],
        rwx_combos=[rw],
        size=0x100000,
        max_regions=1
    )

A complete reference test ships at ``riescue/dtest_framework/tests/test_pma_hint.s``.

CSR Read/Write/Set/Clear API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**;#csr_rw** - Generate CSR R/W Code
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Generates either a CSR read/write/set/clear code, or a CSR API call code. This API can be called from any exception level.
As an API call, all inputs and outputs will be passed through the t2 register. System jumps will clobber t1 and x31, so be careful when using this directive in a system jump.

NOTE: This directive is only valid if deleg_excp_to is set to machine

**Syntax:**

.. code-block:: asm

    ;#csr_rw(<csr_name>, <action>, <direct_access>, <force_machine_rw>)

**Parameters:**

- ``csr_name`` (required) - CSR name to access
- ``action`` (required) - Action to perform: ``read``, ``write``, ``set``, ``clear``
- ``direct_access`` (required) - Direct access to CSR: ``true``, ``false``
- ``force_machine_rw`` (required) - Force machine mode access to CSR: ``true``, ``false``

**Examples:**

.. code-block:: asm

    ;#csr_rw(mcycle, set, true)
    ;#csr_rw(senvcfg, write, false)
    ;#csr_rw(time, read, true)
    ;#csr_rw(hpmcounter3, clear, false)

**;#read_leaf_pte** - Read Leaf PTE of page
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Reads the leaf PTE of a given page and returns the PTE value in t2 register.

NOTE: This directive is only valid if deleg_excp_to is set to machine

**Syntax:**

.. code-block:: asm

    ;#read_leaf_pte(<lin_name>, <paging_mode>)

**Parameters:**

- ``lin_name`` (required) - Linear address of page to read
- ``paging_mode`` (required) - Paging mode: ``sv39``, ``sv48``, ``sv57``

**Examples:**

.. code-block:: asm

    ;#read_leaf_pte(lin1, sv39)
    ;#read_leaf_pte(lin2, sv48)
    ;#read_leaf_pte(lin3, sv57)

Virtual Memory Management
~~~~~~~~~~~~~~~~~~~~~~~~~

**;#page_mapping** - Create Virtual-to-Physical Mappings
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Establishes page table entries mapping virtual addresses to physical addresses.

**Syntax:**

.. code-block:: asm

    ;#page_mapping(lin_name=<vaddr_symbol>, phys_name=<paddr_symbol>, v=<0|1>, r=<0|1>, w=<0|1> [, x=<0|1>] [, a=<0|1>] [, d=<0|1>] [, pagesize=<sizes>] [, page_maps=<maps>] [, modify_pt=<0|1>])

**Alternative Syntax:**

.. code-block:: asm

    ;#page_mapping(lin_addr=<address>, phys_addr=<address>, ...)

**Parameters:**

- ``lin_name`` / ``lin_addr`` - Virtual address symbol or literal address
- ``phys_name`` / ``phys_addr`` - Physical address symbol, literal address, or ``&random``
- ``v`` (required) - Valid bit (``1`` = valid, ``0`` = invalid)
- ``r`` (required) - Read permission (``1`` = readable, ``0`` = not readable)
- ``w`` (required) - Write permission (``1`` = writable, ``0`` = read-only)
- ``x`` (optional) - Execute permission (``1`` = executable, ``0`` = non-executable, default: ``0``)
- ``a`` (optional) - Accessed bit (``1`` = set accessed, ``0`` = clear, default: ``0``)
- ``d`` (optional) - Dirty bit (``1`` = set dirty, ``0`` = clear, default: ``0``)
- ``pagesize`` (optional) - Page size list: ``['4kb']``, ``['2mb']``, ``['1gb']``, ``['512gb']``, ``['256tb']``, ``['any']``
- ``page_maps`` (optional) - Page map list: ``['map_os']``, ``['map_hyp']``, ``['custom_map']``
- ``modify_pt`` (optional) - Allow page table modification (``1`` or ``0``, default: ``0``)
- ``modify_leaf_pt`` (optional) - Allow modification of PTEs in the final G-stage walk (``1`` or ``0``, default: ``0``)
- ``modify_nonleaf_pt`` (optional) - Allow modification of PTEs in the leaf VS-stage PTEs' G-stage walk (``1`` or ``0``, default: ``0``)

**Non-leaf Permission Variants:**

Control permission bits specifically on non-leaf page table entries:

- ``v_nonleaf`` - Valid bit for non-leaf entries (default: ``1``)
- ``a_nonleaf`` - Accessed bit for non-leaf entries (default: ``1``)
- ``d_nonleaf`` - Dirty bit for non-leaf entries (default: ``1``)
- ``r_nonleaf`` - Read permission for non-leaf entries (default: ``1``)
- ``w_nonleaf`` - Write permission for non-leaf entries
- ``x_nonleaf`` - Execute permission for non-leaf entries
- ``u_nonleaf`` - User bit for non-leaf entries
- ``g_nonleaf`` - Global bit for non-leaf entries (default: ``0``)
- ``pbmt_nonleaf`` - PBMT value for non-leaf entries (default: ``0``)

**Level-Specific Bits:**

Set page table bits at specific levels (0 through 4):

- ``v_level0`` .. ``v_level4`` - Valid bit per level (default: ``1``)
- ``g_level0`` .. ``g_level4`` - Global bit per level (default: ``0``)
- ``rsw_level0`` .. ``rsw_level4`` - RSW (reserved for software) field per level (default: ``0``)
- ``reserved_level0`` .. ``reserved_level4`` - Reserved bits per level (default: ``0``)
- ``pbmt_level0`` .. ``pbmt_level4`` - PBMT value per level (default: ``0``)

**G-stage (Two-Stage Paging) Variants:**

For hypervisor two-stage address translation, permission bits can be set independently for each combination of VS-stage and G-stage leaf/non-leaf entries:

- ``{v,a,d,r,w,x,u,g}_nonleaf_gnonleaf`` - VS non-leaf, G-stage non-leaf
- ``{v,a,d,r,w,x,u,g}_nonleaf_gleaf`` - VS non-leaf, G-stage leaf
- ``{v,a,d,r,w,x,u,g}_leaf_gnonleaf`` - VS leaf, G-stage non-leaf
- ``{v,a,d,r,w,x,u,g}_leaf_gleaf`` - VS leaf, G-stage leaf

G-stage page size control:

- ``gstage_vs_leaf_pagesize`` - Page size list for G-stage translations of VS leaf entries
- ``gstage_vs_nonleaf_pagesize`` - Page size list for G-stage translations of VS non-leaf entries

**Other Parameters:**

- ``g`` (optional) - Global bit (default: ``0``)
- ``u`` (optional) - User-mode accessible bit
- ``n`` (optional) - Napot (Naturally Aligned Power-of-Two) bit
- ``secure`` (optional) - Secure mapping (default: ``0``)
- ``in_private_map`` (optional) - Place mapping in a private page map (default: ``0``)

**Page Size Options:**

- ``'4kb'`` - 4 KiB pages
- ``'2mb'`` - 2 MiB pages
- ``'1gb'`` - 1 GiB pages
- ``'512gb'`` - 512 GiB pages
- ``'256tb'`` - 256 TiB pages
- ``'any'`` - Random page size selection

**Special Values:**

- ``&random`` - Use a random physical address for ``phys_name``

**Examples:**

.. code-block:: asm

    ;#page_mapping(lin_name=vaddr, phys_name=paddr, v=1, r=1, w=1, x=0, pagesize=['4kb'])
    ;#page_mapping(lin_addr=0x10000000, phys_name=&random, v=1, r=1, w=1, pagesize=['4kb'])

**;#page_map** - Page Table Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures page table structures for different paging modes.

**Syntax:**

.. code-block:: asm

    ;#page_map(name=<identifier>, mode=<paging_mode>)

**Parameters:**

- ``name`` (required) - Page map identifier
- ``mode`` (required) - Paging mode: ``sv39``, ``sv48``, ``sv57``

**Examples:**

.. code-block:: asm

    ;#page_map(name=map1, mode=sv39)
    ;#page_map(name=map2, mode=sv48)

Memory Initialization
~~~~~~~~~~~~~~~~~~~~~

**;#init_memory** - Initialize Memory Regions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Places data or code at specific memory addresses.

**Syntax:**

.. code-block:: asm

    ;#init_memory @<address_symbol>

**Parameters:**

- ``address_symbol`` (required) - Symbol name from ``random_addr`` directive

**Usage:**

Must be followed by assembly data or instructions that will be placed at the specified address.

**Examples:**

.. code-block:: asm

    ;#random_addr(name=data_region, type=physical, size=0x1000)
    ;#init_memory @data_region
        .word 0x12345678
        .ascii "test string"

**;#reserve_memory** - Reserve Memory Regions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Reserves specific memory regions for test use.

**Syntax:**

.. code-block:: asm

    ;#reserve_memory(start_addr=<address>, addr_type=<type>, size=<size>)

**Parameters:**

- ``start_addr`` (required) - Starting address (hexadecimal)
- ``addr_type`` (required) - Address space: ``linear`` or ``physical``
- ``size`` (required) - Size of reserved region in bytes

**Examples:**

.. code-block:: asm

    ;#reserve_memory(start_addr=0x600000, addr_type=linear, size=0x1000)
    ;#reserve_memory(start_addr=0x500000, addr_type=physical, size=0x1000)

Test Structure Directives
~~~~~~~~~~~~~~~~~~~~~~~~~~

**;#discrete_test** - Define Test Cases
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Registers individual test cases within a test file.

**Syntax:**

.. code-block:: asm

    ;#discrete_test(test=<label> [, repeat_times=<count>])

**Parameters:**

- ``test`` (required) - Label name of the test case
- ``repeat_times`` (optional) - Number of times to execute this test

**Examples:**

.. code-block:: asm

    ;#discrete_test(test=test01)
    ;#discrete_test(test=test02, repeat_times=5)

**;#test_passed** - Define Test Passed
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Directive used to end test with a pass.
Gets replaced with correct code to end test and proceed to next test or successful end of test.
Can be placed anywhere in test code (inside ``.section .code``, ``.section .data``, etc.)

**Syntax:**

.. code-block:: asm

    ;#test_passed


**;#test_failed** - Define Test Failed
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Directive used to end test with a fail.
Gets replaced with correct code to end test immediately.
Can be placed anywhere in test code (inside ``.section .code``, ``.section .data``, etc.)

**Syntax:**

.. code-block:: asm

    ;#test_failed


Interrupt and Exception Handling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. _vectored_interrupt_directive:

**;#vectored_interrupt** - Configure Interrupt Handlers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sets up vectored interrupt handling for specific interrupt sources.

**Syntax:**

.. code-block:: asm

    ;#vectored_interrupt(<interrupt_id>, <handler_label>)

**Parameters:**

- ``interrupt_id`` (required) - Interrupt index (integer) or standard name
- ``handler_label`` (required) - Handler label name

**Standard Interrupt Names:**

- ``SSI`` - Supervisor Software Interrupt (index 1)
- ``MSI`` - Machine Software Interrupt (index 3)
- ``STI`` - Supervisor Timer Interrupt (index 5)
- ``MTI`` - Machine Timer Interrupt (index 7)
- ``SEI`` - Supervisor External Interrupt (index 9)
- ``MEI`` - Machine External Interrupt (index 11)
- ``COI`` - Custom/Platform Interrupt (index 13)

**Examples:**

.. code-block:: asm

    ;#vectored_interrupt(SSI, software_interrupt_handler)
    ;#vectored_interrupt(MTI, timer_interrupt_handler)
    ;#vectored_interrupt(13, custom_interrupt_handler)

Runtime-installed exception handlers (``OS_INSTALL_EXCP_HANDLER`` /
``OS_UNINSTALL_EXCP_HANDLER``) are macros, not ``;#`` directives — see
:ref:`install_excp_handler_macro` in the Macro Reference below.

Random Memory Breakpoint
------------------------

.. _rand_mem_breakpoint_pool_directive:

``;#rand_mem_breakpoint_pool(addresses=[label1, label2, ...])``

Supplies a pool of memory addresses to the *random memory breakpoint*
feature. When enabled via ``--rand_mem_breakpoint_pct``, RiescueD samples
``K = min(2 * --rand_mem_n_triggers, len(pool))`` distinct addresses from
the pool, arms ``--rand_mem_n_triggers`` mcontrol6 load/store watchpoints
on the first N at trigger indices 4..4+N-1, and registers a default
``BREAKPOINT`` (cause=3) handler that round-robin re-arms the firing
trigger's ``tdata2`` to the next pool address — preserving program order
via re-execute — bounded by ``--rand_mem_max_fires``.

The directive may appear **multiple times** in a test; addresses from
every instance accumulate into one pool (duplicates are filtered).

**Parameters:**

- ``addresses`` (required) — comma-separated list of address labels (e.g.
  names declared via the ``;#random_addr`` directive above, or any symbol
  resolvable at link time).

**CLI flags:**

- ``--rand_mem_breakpoint_pct N`` (0–100, default 0) — probability the
  feature is enabled this run. ``0`` = off.
- ``--rand_mem_n_triggers N`` (default 1, capped at 4) — number of
  watchpoints armed at startup. Capped at 4 because the standard
  ``whisper_config_privatecsr.json`` has 4 load/store-capable trigger
  slots (indices 4–7).
- ``--rand_mem_max_fires M`` (default 0) — number of BP fires that
  re-arm the firing trigger's ``tdata2`` to the next pool entry. The
  ``(M+1)``-th fire takes the disable-all path. ``0`` = single-shot.
- ``--rand_mem_inject_icount_pct N`` (0–100, default 0) — **inner
  gate**, rolled only after ``--rand_mem_breakpoint_pct`` rolls true.
  When this in turn rolls true, an additional icount trigger is armed
  on slot 8 (the only icount-capable slot in the standard whisper
  config) with a count drawn randomly from the density-selected range.
  The icount trigger shares the ``--rand_mem_max_fires`` re-arm budget
  with the mcontrol6 watchpoints, and on each re-arm the count is
  freshly randomized (the handler picks the next pre-baked value from a
  small inline table).
- ``--rand_mem_icount_density {often,moderate,sparse}`` (default
  ``moderate``) — random count range used when icount injection rolls
  true: ``often`` = ``[1, 100]``, ``moderate`` = ``[1, 1000]``,
  ``sparse`` = ``[1, 10000]``. Only meaningful with
  ``--rand_mem_inject_icount_pct > 0``.

**medeleg requirement.** The handler writes ``tselect``/``tdata1``/
``tdata2`` which are M-mode-only CSRs, so BREAKPOINT (cause=3) must be
handled in M-mode (``medeleg`` bit 3 clear). When the user has not
forced delegation, RiescueD auto-clears bit 3 and logs INFO. When the
user explicitly supplies ``--medeleg`` or ``--deleg_excp_to`` and the
result still keeps bit 3 set, the feature **disables itself with a
warning** rather than silently overriding the user's choice.

**Single-hart only.** The feature is skipped (with a warning) for MP
runs (``num_cpus > 1``).

**Conflict auto-disable.** If the test already contains any
``;#trigger_config`` directive — from coretp's ``--test_plan sdtrig``
stimulus, the ``sdtrig_stress`` voyager2 plugin, or hand-written
triggers — the feature **auto-disables with a warning** to avoid
arming watchpoints on the same trigger CSR slots.

**Example:**

.. code-block:: asm

    ;#random_addr(name=buf_a_lin, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
    ;#random_addr(name=buf_a_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
    ;#page_mapping(lin_name=buf_a_lin, phys_name=buf_a_phys, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

    ;#random_addr(name=buf_b_lin, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
    ;#random_addr(name=buf_b_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
    ;#page_mapping(lin_name=buf_b_lin, phys_name=buf_b_phys, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

    ;#rand_mem_breakpoint_pool(addresses=[buf_a_lin, buf_b_lin])

Run with::

    riescued.py -t my_test.s \
        --rand_mem_breakpoint_pct 100 \
        --rand_mem_n_triggers 4 \
        --rand_mem_max_fires 20

To additionally inject an icount trigger on slot 8 with a freshly
randomized count from the ``often`` range on every re-arm::

    riescued.py -t my_test.s \
        --rand_mem_breakpoint_pct 100 \
        --rand_mem_n_triggers 4 \
        --rand_mem_max_fires 20 \
        --rand_mem_inject_icount_pct 100 \
        --rand_mem_icount_density often

A reference test ships at
``riescue/dtest_framework/tests/sdtrig/rand_mem_breakpoint.s``.

.. note::

   Tests must not rely on registers ``t0`` and ``t1`` surviving across
   load/store boundaries when this feature is active — the framework's
   trap dispatch unconditionally clobbers them before any default
   exception handler override runs. Use ``s0``–``s11`` or ``t2``–``t6``
   for memory base addresses if the test code may take a BP.

Exception Types Reference
-------------------------

Common exception causes for use with ``OS_SETUP_CHECK_EXCP`` macro:

**Instruction Exceptions:**
- ``INSTRUCTION_ADDRESS_MISALIGNED`` - Misaligned instruction fetch
- ``INSTRUCTION_ACCESS_FAULT`` - Instruction access violation
- ``ILLEGAL_INSTRUCTION`` - Invalid instruction
- ``INSTRUCTION_PAGE_FAULT`` - Instruction page fault

**Load Exceptions:**
- ``LOAD_ADDRESS_MISALIGNED`` - Misaligned load operation
- ``LOAD_ACCESS_FAULT`` - Load access violation
- ``LOAD_PAGE_FAULT`` - Load page fault
- ``LOAD_GUEST_PAGE_FAULT`` - Guest load page fault (virtualization)

**Store Exceptions:**
- ``STORE_ADDRESS_MISALIGNED`` - Misaligned store operation
- ``STORE_ACCESS_FAULT`` - Store access violation
- ``STORE_PAGE_FAULT`` - Store page fault
- ``STORE_GUEST_PAGE_FAULT`` - Guest store page fault (virtualization)

**System Exceptions:**
- ``ECALL`` - Environment call (generic)
- ``ECALL_FROM_USER`` - Environment call from user mode
- ``ECALL_FROM_SUPER`` - Environment call from supervisor mode
- ``ECALL_FROM_MACHINE`` - Environment call from machine mode
- ``VIRTUAL_INSTRUCTION`` - Virtual instruction exception

Macro Reference
---------------

**OS_SETUP_CHECK_EXCP** - Exception Testing Macro
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Sets up expected exceptions and verifies that they occur with correct parameters.

**Syntax:**

.. code-block:: text

    OS_SETUP_CHECK_EXCP <expected_cause>, <expected_pc>, <return_pc> \
        [, <expected_tval> [, <expected_htval> [, <skip_pc_check> \
        [, <far_expected_pc> [, <far_return_pc> [, <gva_check> \
        [, <expected_mode> [, <re_execute> ]]]]]]]]

**Parameters:**

- ``expected_cause`` (required) - Expected exception cause code
- ``expected_pc`` (required) - Label where exception should occur
- ``return_pc`` (required) - Label where execution continues after exception.
  Ignored by the handler when ``re_execute=1``.
- ``expected_tval`` (optional) - Expected trap value (default: 0)
- ``expected_htval`` (optional) - Expected hypervisor trap value (default: 0)
- ``skip_pc_check`` (optional) - When 1, do not validate the faulting PC
  (useful for icount triggers; default: 0)
- ``far_expected_pc`` (optional) - When 1, use ``li`` instead of ``la`` for
  ``expected_pc`` (use with equate addresses; default: 0)
- ``far_return_pc`` (optional) - When 1, use ``li`` instead of ``la`` for
  ``return_pc`` (default: 0)
- ``gva_check`` (optional) - When 1, also validate and clear the ``GVA`` bit
  in ``mstatus`` / ``hstatus`` (default: 0)
- ``expected_mode`` (optional) - Require the trap to be taken in a specific
  privilege mode. Use ``CHECK_EXCP_MODE_MACHINE``, ``CHECK_EXCP_MODE_HS`` or
  ``CHECK_EXCP_MODE_VS``. 0 means any mode (default: 0).
- ``re_execute`` (optional) - When 1, the OS trap handler returns with
  ``mret`` / ``sret`` **without overwriting** ``mepc`` / ``sepc``, so the
  core re-executes the same PC that took the exception. Intended for sdtrig
  ``icount`` / ``mcontrol6`` before-stimulus use cases where the test needs
  the trigger to fire repeatedly on the same instruction, or where the
  handler disables/reconfigures the trigger before returning (default: 0).

.. warning::

    When ``re_execute=1``, the caller is responsible for forward progress —
    either disable/reconfigure the trigger from a ``;#custom_handler`` or
    rely on the trigger semantics to stop firing on the next execution.
    Otherwise the hart will trap on the same PC forever.

**Examples:**

.. code-block:: asm

    # Test ecall exception
    OS_SETUP_CHECK_EXCP ECALL, ecall_instr, after_ecall

    # Test store page fault with specific trap value
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, fault_store, after_fault, readonly_page

    # sdtrig mcontrol6 that must re-execute the same PC. The custom handler
    # is responsible for disabling the trigger so the second fetch succeeds.
    # Requires running with --excp_hooks so excp_handler_pre is invoked.
    OS_SETUP_CHECK_EXCP BREAKPOINT, bp_here, bp_after, 0, 0, 0, 0, 0, 0, 0, 1
    ;#trigger_config(index=0, type=execute, addr=bp_here, action=breakpoint)
    bp_here:
        nop
    bp_after:

    # sdtrig icount + re_execute=1. Icount naturally latches count=0 after
    # firing (it is single-shot), so no in-handler cleanup is needed — the
    # re-fetch is clean even without --excp_hooks. skip_pc_check=1 is set
    # because icount fires at a non-deterministic retirement boundary.
    OS_SETUP_CHECK_EXCP BREAKPOINT, ic_after, ic_after, 0, 0, 1, 0, 0, 0, 0, 1
    ;#trigger_config(index=0, type=icount, count=3, action=breakpoint)
    addi x10, x0, 0
    addi x10, x10, 1
    addi x10, x10, 1
    ic_after:
    ;#trigger_disable(index=0)

.. _install_excp_handler_macro:

**OS_INSTALL_EXCP_HANDLER** - Arm a Runtime Exception Handler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Stores a handler address, expected mode, and cause in the hart-local
``excp_handler_addr`` / ``excp_handler_mode`` / ``excp_handler_cause``
variables (the cause store commits the arming). On a matching exception the
trap dispatch — running before ``save_context()`` — jumps directly to the
handler label; any other cause falls back to the **original exception path**
(``FeatMgr`` overrides, ``OS_SETUP_CHECK_EXCP`` handling, then the default
fail-on-unexpected behavior). Use it for on-demand, region-scoped exception
handling without changing the test-wide default exception path.

The arming state is **hart-local**, so in MP tests each hart arms its handler
independently. It is also scoped to the current discrete test: the scheduler
disarms the slot at every dispatch. There is a single slot per hart — arming
again overwrites the previous handler.

**Syntax:**

.. code-block:: text

    OS_INSTALL_EXCP_HANDLER <cause>, <handler_label> [, <mode> [, <far_addr> \
        [, <force_machine> [, <force_supervisor> [, <force_user>]]]]

**Parameters:**

- ``cause`` (required) - Synchronous exception cause the handler responds to
  (name or number), e.g. ``BREAKPOINT``.
- ``handler_label`` (required) - Label of the handler body in the test. The
  handler runs *before* the framework's context save, so it must end with
  ``mret``/``sret`` and may only clobber ``t0``/``t1`` unless it saves and
  restores any other registers itself. The label must live in ``.code``: when
  the trap lands in the M-mode trap handler, the dispatch relocates the stored
  VA to a PA via the ``.code`` base equates before jumping (M-mode instruction
  fetches are never translated).
- ``mode`` (optional) - Expected trap-handler privilege mode:
  ``CHECK_EXCP_MODE_MACHINE``, ``CHECK_EXCP_MODE_HS``, or
  ``CHECK_EXCP_MODE_VS``. 0 means any mode (default: 0). When set, a cause
  match arriving at a different-mode trap handler (e.g. a ``medeleg``
  mismatch) falls through to the original path instead of jumping into a body
  written for another mode.
- ``far_addr`` (optional) - When 1, use ``li`` instead of ``la`` for
  ``handler_label`` (use with equate addresses; default: 0)
- ``force_machine`` / ``force_supervisor`` / ``force_user`` (optional) -
  Hart-context access override, same as ``OS_SETUP_CHECK_EXCP`` (default: 0).

**Clobbers:** ``a0``, ``tp``, ``t3``. In S/U mode also ``t0`` and ``t1``
(the hart-context syscall ABI).

**Examples:**

.. code-block:: asm

    OS_INSTALL_EXCP_HANDLER BREAKPOINT, my_bp_handler, CHECK_EXCP_MODE_MACHINE
    ebreak                       # dispatches to my_bp_handler
    OS_UNINSTALL_EXCP_HANDLER    # disarm; back to the original path

Reference tests ship at
``riescue/dtest_framework/tests/non_instr_tests/install_excp_handler.s``
(single core), ``install_excp_handler_mp.s`` (MP, per-hart arming), and
``install_excp_handler_s.s`` (paged S-mode, exercising the M-mode VA->PA
relocation).

**OS_UNINSTALL_EXCP_HANDLER** - Disarm the Runtime Exception Handler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clears the armed handler (``excp_handler_cause`` = -1), returning exception
processing to the original path. Only needed for scoping finer than a
discrete test — the scheduler disarms automatically at every test boundary.

**Syntax:**

.. code-block:: text

    OS_UNINSTALL_EXCP_HANDLER [<force_machine> [, <force_supervisor> [, <force_user>]]]

**Parameters:**

- ``force_machine`` / ``force_supervisor`` / ``force_user`` (optional) -
  Hart-context access override, same as ``OS_SETUP_CHECK_EXCP`` (default: 0).

**Clobbers:** ``a0``, ``tp``, ``t3``. In S/U mode also ``t0`` and ``t1``
(the hart-context syscall ABI).
