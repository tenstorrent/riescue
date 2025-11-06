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

    OS_SETUP_CHECK_EXCP <expected_cause>, <expected_pc>, <return_pc> [, <expected_tval>]

**Parameters:**

- ``expected_cause`` (required) - Expected exception cause code
- ``expected_pc`` (required) - Label where exception should occur
- ``return_pc`` (required) - Label where execution continues after exception
- ``expected_tval`` (optional) - Expected trap value (default: 0)

**Examples:**

.. code-block:: asm

    # Test ecall exception
    OS_SETUP_CHECK_EXCP ECALL, ecall_instr, after_ecall

    # Test store page fault with specific trap value
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, fault_store, after_fault, readonly_page
