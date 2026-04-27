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

- ``in_pma`` - Include in PMA configuration (``1`` or ``0``)
- ``pma_size`` - PMA region size in bytes
- ``pma_read``, ``pma_write``, ``pma_execute`` - Access permissions (``1`` or ``0``)
- ``pma_mem_type`` - Memory type: ``'memory'``, ``'io'``, ``'ch0'``, ``'ch1'``
- ``pma_amo_type`` - Atomic operation support: ``'none'``, ``'logical'``, ``'swap'``, ``'arithmetic'``
- ``pma_cacheability`` - Cache behavior: ``'cacheable'``, ``'noncacheable'``

**Examples:**

.. code-block:: asm

    ;#random_addr(name=addr1, type=physical, size=0x1000, and_mask=0xfffff000)
    ;#random_addr(name=vaddr, type=linear, size=0x2000)
    ;#random_addr(name=io_addr, type=physical, io=1, size=0x100)

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
