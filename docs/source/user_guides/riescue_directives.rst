
Test Generation Directives
---------------------------

These directives control how RiescueD generates test code and data.

Random Data Generation
~~~~~~~~~~~~~~~~~~~~~~

**;#random_data** - Generate Random Values
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Creates random data values with optional constraints.

.. code-block:: asm

    ;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
    ;#random_data(name=data2, type=bits64)
    ;#random_data(name=small_val, type=bits8, and_mask=0xff)

**Parameters:**
- ``name`` - Symbol name to reference in assembly code
- ``type`` - Data width: ``bits8``, ``bits16``, ``bits32``, ``bits64``, or ``bitsN`` for arbitrary width
- ``and_mask`` - Optional mask to constrain random values
- ``or_mask`` - Optional mask to set specific bits

**Usage Example:**

.. code-block:: asm

    ;#random_data(name=test_value, type=bits32, and_mask=0xfffff000)

    test_code:
        li t0, test_value    # Load the random value
        # test_value will be replaced with actual random value during generation

Memory Address Generation
~~~~~~~~~~~~~~~~~~~~~~~~~~

**;#random_addr** - Generate Random Addresses
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Creates random memory addresses with alignment and size constraints.

.. code-block:: asm

    ;#random_addr(name=addr1, type=physical, size=0x1000, and_mask=0xfffff000)
    ;#random_addr(name=vaddr, type=linear, size=0x2000)
    ;#random_addr(name=io_addr, type=physical, io=1, size=0x100)

**Parameters:**
- ``name`` - Symbol name for the address
- ``type`` - Address type: ``physical`` (physical memory) or ``linear`` (virtual memory)
- ``size`` - Size of memory region in bytes
- ``and_mask`` - Alignment mask (e.g., ``0xfffff000`` for 4KB alignment)
- ``io`` - Set to ``1`` for I/O memory regions

**Physical Memory Attributes (PMA):**

.. code-block:: asm

    ;#random_addr(name=pma_region, type=physical, in_pma=1, pma_size=0x1000, pma_read=1, pma_write=1, pma_execute=0, pma_memtype='memory', pma_amo_type='arithmetic', pma_cacheability='cacheable')

**PMA Parameters:**
- ``in_pma`` - Include in PMA configuration (``1`` or ``0``)
- ``pma_read``, ``pma_write``, ``pma_execute`` - Access permissions
- ``pma_memtype`` - Memory type: ``'memory'``, ``'io'``, ``'ch0'``, ``'ch1'``
- ``pma_amo_type`` - Atomic operation support: ``'none'``, ``'logical'``, ``'swap'``, ``'arithmetic'``
- ``pma_cacheability`` - Cache behavior: ``'cacheable'``, ``'noncacheable'``

Virtual Memory Management
~~~~~~~~~~~~~~~~~~~~~~~~~

**;#page_mapping** - Create Virtual-to-Physical Mappings
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Establishes page table entries mapping virtual addresses to physical addresses.

.. code-block:: asm

    ;#page_mapping(lin_name=vaddr, phys_name=paddr, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

**Parameters:**
- ``lin_name`` - Virtual address symbol (from ``;#random_addr type=linear``)
- ``phys_name`` - Physical address symbol (from ``;#random_addr type=physical``)
- ``v`` - Valid bit (``1`` = valid, ``0`` = invalid)
- ``r`` - Read permission (``1`` = readable, ``0`` = not readable)
- ``w`` - Write permission (``1`` = writable, ``0`` = read-only)
- ``x`` - Execute permission (``1`` = executable, ``0`` = non-executable)
- ``a`` - Accessed bit (``1`` = set accessed, ``0`` = clear)
- ``d`` - Dirty bit (``1`` = set dirty, ``0`` = clear)
- ``pagesize`` - Page size list: ``['4kb']``, ``['2mb']``, ``['1gb']``, ``['any']``

**Page Size Options:**
- ``'4kb'`` - 4 KiB pages
- ``'2mb'`` - 2 MiB pages
- ``'1gb'`` - 1 GiB pages
- ``'512gb'`` - 512 GiB pages (use carefully)
- ``'256tb'`` - 256 TiB pages (use carefully)
- ``'any'`` - Random page size selection

**;#page_map** - Page Table Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures page table structures for different paging modes.

.. code-block:: asm

    ;#page_map(name=map1, mode=sv39)
    ;#page_map(name=map2, mode=sv48)

**Parameters:**
- ``name`` - Page map identifier
- ``mode`` - Paging mode: ``sv39``, ``sv48``, ``sv57``

Memory Initialization
~~~~~~~~~~~~~~~~~~~~~

**;#init_memory** - Initialize Memory Regions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Places data or code at specific memory addresses.

.. code-block:: asm

    ;#random_addr(name=data_region, type=physical, size=0x1000)
    ;#random_data(name=test_data, type=bits32)

    ;#init_memory @data_region
        .word test_data
        .word 0x12345678
        .ascii "test string"

**Usage Patterns:**

.. code-block:: asm

    # Initialize with random data
    ;#init_memory @addr1
        .byte random_byte_value

    # Initialize with mixed content
    ;#init_memory @addr2
        .word 0xdeadbeef
        .word random_word
        nop
        li t0, 42

Test Structure Directives
~~~~~~~~~~~~~~~~~~~~~~~~~

**;#discrete_test** - Define Test Cases
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Registers individual test cases within a test file.

.. code-block:: asm

    ;#discrete_test(test=test01)
    ;#discrete_test(test=test02, repeat_times=5)

**Parameters:**
- ``test`` - Label name of the test case
- ``repeat_times`` - Number of times to execute this test (optional)

**;#reserve_memory** - Reserve Memory Regions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Reserves specific memory regions for test use.

.. code-block:: asm

    ;#reserve_memory(name=reserved1, size=0x1000, address=0x80000000)

**Parameters:**
- ``name`` - Region identifier
- ``size`` - Size in bytes
- ``address`` - Specific address to reserve

Interrupt and Exception Handling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**;#vectored_interrupt** - Configure Interrupt Handlers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sets up vectored interrupt handling for specific interrupt sources.

.. code-block:: asm

    ;#vectored_interrupt(SSI, software_interrupt_handler)
    ;#vectored_interrupt(MTI, timer_interrupt_handler)
    ;#vectored_interrupt(13, custom_interrupt_handler)

**Parameters:**
- First parameter: Interrupt index (integer) or standard name
- Second parameter: Handler label name

**Standard Interrupt Names:**
- ``SSI`` - Supervisor Software Interrupt (index 1)
- ``MSI`` - Machine Software Interrupt (index 3)
- ``STI`` - Supervisor Timer Interrupt (index 5)
- ``MTI`` - Machine Timer Interrupt (index 7)
- ``SEI`` - Supervisor External Interrupt (index 9)
- ``MEI`` - Machine External Interrupt (index 11)
- ``COI`` - Custom/Platform Interrupt (index 13)

Complete Example
----------------

Here's a comprehensive example showing multiple directive types:

.. code-block:: asm

    # Random data generation
    ;#random_data(name=test_value, type=bits32, and_mask=0xfffff000)
    ;#random_data(name=small_data, type=bits8)

    # Memory address generation
    ;#random_addr(name=phys_addr, type=physical, size=0x2000, and_mask=0xfffff000)
    ;#random_addr(name=virt_addr, type=linear, size=0x2000, and_mask=0xfffff000)

    # Virtual memory mapping
    ;#page_mapping(lin_name=virt_addr, phys_name=phys_addr, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb'])

    # Memory initialization
    ;#init_memory @virt_addr
        .word test_value
        .byte small_data
        .ascii "test data"

    # Interrupt configuration
    ;#vectored_interrupt(MTI, timer_handler)

    # Test cases
    ;#discrete_test(test=memory_test)
    ;#discrete_test(test=interrupt_test)

    .section .code, "ax"

    test_setup:
        # Setup code
        j passed

    memory_test:
        li t0, virt_addr
        lw t1, 0(t0)
        li t2, test_value
        beq t1, t2, passed
        j failed

    interrupt_test:
        # Interrupt testing code
        j passed

    timer_handler:
        # Timer interrupt handler
        mret

    test_cleanup:
        j passed

Best Practices
--------------

**Random Data Usage**
- Use appropriate bit widths for your test requirements
- Apply masks to ensure values fit expected ranges
- Use meaningful names that describe the data purpose

**Memory Management**
- Align addresses appropriately for your access patterns
- Consider page size implications for virtual memory tests
- Reserve sufficient memory regions for test data

**Virtual Memory Testing**
- Always pair ``;#random_addr`` with ``;#page_mapping`` for virtual addresses
- Test different permission combinations systematically
- Verify page size alignment requirements

**Error Handling**
- Validate that required parameters are present
- Check address alignment matches page size requirements
- Ensure virtual/physical address pairs are properly mapped