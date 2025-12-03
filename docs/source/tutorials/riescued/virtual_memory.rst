Virtual Memory
==============

This tutorial walks you through testing virtual memory functionality with RiescueD, covering page mapping, address translation, and memory access patterns.

Virtual memory testing helps verify that your processor correctly handles address translation, page table management, and memory protection. This tutorial shows how to create tests that exercise these critical system features.

Setting Up Virtual Memory
--------------------------

Let's start with a complete virtual memory test. Here's the example file:

.. literalinclude:: ../../../../riescue/dtest_framework/tests/tutorials/virtual_memory.s

We can run the test using:

.. code-block:: bash

   riescued --testfile virtual_memory.s --run_iss

This test creates a virtual-to-physical address mapping and verifies that data can be correctly accessed through the virtual address.

Test Configuration Headers
---------------------------

The virtual memory test introduces several important headers:

``;#test.paging`` Header
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#test.paging     sv39 sv48 sv57

This header specifies which paging modes the test supports. The test will run for each specified mode:

- ``sv39`` - 39-bit virtual addressing (3-level page table)
- ``sv48`` - 48-bit virtual addressing (4-level page table)
- ``sv57`` - 57-bit virtual addressing (5-level page table)

``;#test.priv`` - Setting Supervisor or User
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Previous tutorials have been using ``;#test.priv machine``, but this example is set to ``user super``.
Some Test Headers can be set to multiple values separated by spaces, and randomly select one of the values.

This example will randomly select either user or supervisor privilege mode for the test code.

Virtual memory operations typically [#vm_priv_note]_  require supervisor privileges for page table setup to function correctly, so it should be set for the examples.



Random Address Generation
--------------------------

The test uses two types of random addresses for comprehensive testing:

``;#random_addr`` - Physical Address
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#random_addr(name=physical_address, type=physical)

This generates a random physical address where the actual data will be stored in memory. The ``type=physical`` parameter specifies this is a physical memory location.

``;#random_addr`` - Virtual Address
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#random_addr(name=virtual_address, type=linear)

This generates a random virtual address that will be mapped to the physical address. The ``type=linear`` parameter indicates this is a linear (virtual) address that requires translation.

Page Mapping Configuration
---------------------------

The core of virtual memory testing is the page mapping directive:

``;#page_mapping`` - Creating Address Mappings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#page_mapping(lin_name=virtual_address, phys_name=physical_address, v=1, r=1, w=1, a=1, d=1, pagesize=['any'])

This creates a page table entry that maps the virtual address to the physical address. The parameters control page permissions:

- ``lin_name=virtual_address`` - The virtual address symbol to map from
- ``phys_name=physical_address`` - The physical address symbol to map to
- ``v=1`` - Valid bit (page is present)
- ``r=1`` - Read permission
- ``w=1`` - Write permission
- ``a=1`` - Accessed bit
- ``d=1`` - Dirty bit
- ``pagesize=['any']`` - Allow any supported page size

Memory Initialization
---------------------

To test the mapping, we need to place data at the virtual address:

``;#init_memory`` - Populating Virtual Memory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#init_memory @virtual_address
       .word my_word

This directive places the random word value at the virtual address location. The framework handles the address translation to store the data at the correct physical location.

Virtual Memory Test Patterns
-----------------------------

The test demonstrates a common virtual memory verification pattern:

.. code-block:: asm

   test_paging:
       li t0, virtual_address      # Load virtual address
       lw t1, 0(t0)               # Read through virtual address
       li t2, my_word             # Load expected value
       beq t1, t2, paging_passed  # Verify data matches
       ;#test_failed()

This pattern:

1. Loads the virtual address into a register
2. Performs a memory access through the virtual address
3. Compares the loaded value with the expected data
4. Passes if the virtual-to-physical translation worked correctly

Advanced Features
-----------------

The framework supports additional virtual memory testing features:

Multiple Page Sizes
~~~~~~~~~~~~~~~~~~~

You can specify exact page sizes instead of just ``['any']``:

.. code-block:: asm

   ;#page_mapping(lin_name=vaddr, phys_name=paddr, v=1, r=1, w=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])


Any will pick a random page size from the supported page sizes, with larger page sizes having a lower chance of being picked.

.. note::
    both 512 GiB (``'512gb'``) and 256 TiB (``'256tb'``) are supported, but they are execluded from being randomly selected.

Currently supported page sizes are:

- 4kb
- 2mb
- 1gb
- 512gb
- 256tb


.. note::

    Page sizes are required to be naturally aligned to the page size.
    For testing purposes, test writers may want to use large values to check for page size alignment issues.


.. warning::

    However, this assumes that the memory map supports very large page sizes in the first place.
    If a 512 GiB page is requested and the memory map cannot allocate that, it will fail address generation.

    For this reason, it's recommended to use that only if the memory map supports very large page sizes. Additionally, 256tb TiB is disabled by default.

Permission Testing
~~~~~~~~~~~~~~~~~~

Test different permission combinations:

.. code-block:: asm

   ;#page_mapping(lin_name=readonly_addr, phys_name=phys_addr, v=1, r=1, w=0)


For complete documentation of all available directives, see the :doc:`RiESCUE Directives Reference <../../reference/riescue_test_file/directives_reference>` and :doc:`Test Headers Reference <../../reference/riescue_test_file/test_headers_reference>`.



.. [#vm_priv_note] Typically RISC-V uses user mode or supervisor mode to enable page table translation. However, the ``mstatus.MPRV`` bit can be set to a 1 to enable machine mode to translate virtual addresses. For the sake of simplicity, this tutorial uses the "typical" behavior of user/supervisor mode for virtual memory operations.


