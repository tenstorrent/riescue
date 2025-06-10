RiescueD User Guide
==================

Welcome to RiescueD, the **RiESCUE Directed Test Framework** - a powerful Python library for generating RISC-V directed tests with randomization capabilities, memory management, and comprehensive environment simulation.

.. contents:: Table of Contents
   :local:
   :depth: 2

What is RiescueD?
-----------------

RiescueD is a directed test framework that allows you to write assembly tests with special directives that provide:

- **Randomization**: Generate random data and addresses
- **Memory Management**: Automatic page table generation and memory mapping
- **Environment Simulation**: Support for various privilege modes, paging modes, and virtualization
- **OS Code Simulation**: Pseudo OS for scheduling and exception handling

Key Features
~~~~~~~~~~~~

- Random address and data generation with constraints
- Automatic page table creation for virtual memory testing
- Support for multiple paging modes (sv39, sv48, sv57, bare)
- Multiple privilege levels (machine, supervisor, user)
- Virtualization support (bare metal, virtualized)
- Exception handling and pass/fail conditions
- RISC-V extension support (RVA23 and more)

Getting Started
---------------

Basic Workflow
~~~~~~~~~~~~~~

1. **Write Test**: Create a `.s` file with assembly code and RiescueD directives
2. **Configure System**: Provide CPU configuration in a JSON file
3. **Generate**: Run RiescueD to produce ELF binary, assembly file, and linker script
4. **Simulate**: Run the generated test on your target simulator

.. code-block:: bash

   # Basic usage
   python3 riescued.py run --testname test.s --cpuconfig cpu_config.json

   # With specific environment settings
   python3 riescued.py run --testname test.s \
     --cpuconfig cpu_config.json \
     --test_paging_mode sv39 \
     --test_privilege_mode supervisor \
     --test_env bare_metal

RiescueD Directives Reference
-----------------------------

RiescueD directives are special comments that start with ``;#`` and provide powerful test generation capabilities.

Test Header Directives
~~~~~~~~~~~~~~~~~~~~~~

Test header directives define the overall test configuration and are placed at the beginning of your test file.

.. code-block:: asm

   ;#test.name       my_test
   ;#test.author     your.email@company.com
   ;#test.arch       rv64
   ;#test.priv       machine super user any
   ;#test.env        virtualized bare_metal any
   ;#test.cpus       1
   ;#test.paging     sv39 sv48 sv57 disable any
   ;#test.category   arch
   ;#test.class      vector
   ;#test.features   ext_v.enable ext_fp.disable
   ;#test.tags       vectors load_store

**Available Test Header Options:**

- ``test.name``: Unique test identifier
- ``test.author``: Author email address
- ``test.arch``: Target architecture (rv32, rv64)
- ``test.priv``: Privilege modes (machine, super, user, any)
- ``test.env``: Test environment (virtualized, bare_metal)
- ``test.paging``: Paging modes (sv39, sv48, sv57, disable, any)
- ``test.features``: Extension configuration (ext_name.enable/disable)
- ``test.tags``: Descriptive tags for categorization

Random Data Generation
~~~~~~~~~~~~~~~~~~~~~~

Generate random data values with optional constraints:

.. code-block:: asm

   ;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
   ;#random_data(name=data2, type=bits64, and_mask=0xffffffffffffffff)
   ;#random_data(name=data3, type=bits20)

**Parameters:**

- ``name``: Variable name to reference in your code
- ``type``: Data width (bits8, bits16, bits32, bits64, bits20, bits22, etc.)
- ``and_mask``: Optional mask to constrain random values

**Usage in Assembly:**

.. code-block:: asm

   .section .data
   my_data:
       .dword data1    # Uses the random value generated
       .dword data2

Random Address Generation
~~~~~~~~~~~~~~~~~~~~~~~~~

Generate random addresses for memory operations:

.. code-block:: asm

   ;#random_addr(name=lin1, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
   ;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
   ;#random_addr(name=io_addr, type=physical, io=1, size=0x1000, and_mask=0xfffffffffffff000)

**Parameters:**

- ``name``: Address variable name
- ``type``: Address space type

  - ``linear``: Virtual/linear address space
  - ``physical``: Physical address space

- ``size``: Size of the memory region
- ``and_mask``: Address alignment mask
- ``io``: Mark as I/O region (optional, default=0)

Memory Reservation
~~~~~~~~~~~~~~~~~~

Reserve specific memory regions:

.. code-block:: asm

   ;#reserve_memory(start_addr=0x600000, addr_type=linear, size=0x1000)
   ;#reserve_memory(start_addr=0x500000, addr_type=physical, size=0x1000)

**Parameters:**

- ``start_addr``: Starting address (hexadecimal)
- ``addr_type``: Address space (linear, physical)
- ``size``: Size of reserved region

Page Table Generation
~~~~~~~~~~~~~~~~~~~~~

Automatically generate page table entries:

.. code-block:: asm

   ;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])
   ;#page_mapping(lin_addr=0x5000000, phys_addr=0x5000000, v=1, r=1, w=1, pagesize=['4kb'])
   ;#page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1, pagesize=['2mb'])

**Parameters:**

- ``lin_name`` / ``lin_addr``: Linear (virtual) address or variable name
- ``phys_name`` / ``phys_addr``: Physical address or variable name
- ``v``: Valid bit (0 or 1)
- ``r``: Read permission (0 or 1)
- ``w``: Write permission (0 or 1)
- ``x``: Execute permission (0 or 1)
- ``a``: Accessed bit (0 or 1)
- ``d``: Dirty bit (0 or 1)
- ``pagesize``: Page size options

  - ``'4kb'``: 4KB pages
  - ``'2mb'``: 2MB pages
  - ``'1gb'``: 1GB pages
  - ``'512gb'``: 512GB pages
  - ``'256tb'``: 256TB pages
  - ``'any'``: Let RiescueD choose

**Special Values:**

- ``&random``: Use a random physical address
- ``modify_pt=1``: Allow modification of page table entry during test. Creats page pointing to each level of the page table. These pages can be used to read the page table entries to do read modified write to pagetables.

Memory Initialization
~~~~~~~~~~~~~~~~~~~~~

Initialize memory sections with data:

.. code-block:: asm

   ;#init_memory @section_name
   .section .section_name, "aw"
       .dword data1
       .dword data2

This directive initializes the memory region with the specified data.

Example Test Structure
----------------------

Here's a complete example showing how to structure a RiescueD test:

.. code-block:: asm

   ;#test.name       load_store_test
   ;#test.author     developer@company.com
   ;#test.arch       rv64
   ;#test.priv       supervisor
   ;#test.env        virtualized
   ;#test.cpus       1
   ;#test.paging     sv39
   ;#test.category   memory
   ;#test.class      load_store
   ;#test.features   ext_i.enable
   ;#test.tags       load store virtual_memory
   ;#test.summary    Test load/store operations with virtual memory

   #####################
   # Random Data Generation
   #####################
   ;#random_data(name=test_data1, type=bits64, and_mask=0xffffffffffffffff)
   ;#random_data(name=test_data2, type=bits32, and_mask=0xfffffff0)

   #####################
   # Address Generation and Page Mapping
   #####################
   ;#random_addr(name=data_region, type=linear, size=0x2000, and_mask=0xfffffffffffff000)
   ;#random_addr(name=data_phys, type=physical, size=0x2000, and_mask=0xfffffffffffff000)
   ;#page_mapping(lin_name=data_region, phys_name=data_phys, v=1, r=1, w=1, pagesize=['4kb'])

   ;#reserve_memory(start_addr=0x10000000, addr_type=linear, size=0x1000)
   ;#page_mapping(lin_addr=0x10000000, phys_name=&random, v=1, r=1, w=1, pagesize=['4kb'])

   .section .text

   #####################
   # Test Setup
   #####################
   test_setup:
       # Executed before each test, exactly once
       li t0, 0x12345678
       j test01

   #####################
   # Discrete Tests
   #####################
   ;#discrete_test(test=test01)
   test01:
       # Load from virtual address
       la t1, data_region
       ld t2, 0(t1)

       # Store to virtual address
       sd t0, 8(t1)

       # Verify the store
       ld t3, 8(t1)
       beq t0, t3, test01_pass
       j failed

   test01_pass:
       j passed

   #####################
   # Test Cleanup
   #####################
   test_cleanup:
       # Executed after all tests are run, exactly once
       li t0, 0x12345678
       j passed

   #####################
   # Memory Sections
   #####################
   ;#init_memory @data_region
   .section .data_region, "aw"
       .dword test_data1
       .dword test_data2

Advanced Features
-----------------

Exception Handling with OS_SETUP_CHECK_EXCP
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

RiescueD provides powerful exception testing capabilities through the ``OS_SETUP_CHECK_EXCP`` macro. This macro allows you to set up expected exceptions and verify that they occur with the correct parameters.

**Macro Syntax:**

.. code-block:: asm

   OS_SETUP_CHECK_EXCP expected_cause, expected_pc, return_pc, expected_tval=0

**Parameters:**

- ``expected_cause``: The expected exception cause code (e.g., ``LOAD_PAGE_FAULT``, ``STORE_PAGE_FAULT``, ``ECALL``)
- ``expected_pc``: Label where the exception should occur
- ``return_pc``: Label where execution should continue after exception handling
- ``expected_tval``: Expected trap value (optional, defaults to 0)

**Exception Types:**

Common exception causes that can be tested:

- ``INSTRUCTION_ADDRESS_MISALIGNED``: Misaligned instruction fetch
- ``INSTRUCTION_ACCESS_FAULT``: Instruction access violation
- ``ILLEGAL_INSTRUCTION``: Invalid instruction
- ``LOAD_ADDRESS_MISALIGNED``: Misaligned load operation
- ``LOAD_ACCESS_FAULT``: Load access violation
- ``STORE_ADDRESS_MISALIGNED``: Misaligned store operation
- ``STORE_ACCESS_FAULT``: Store access violation
- ``ECALL``: Environment call from various privilege modes
- ``LOAD_PAGE_FAULT``: Load page fault
- ``STORE_PAGE_FAULT``: Store page fault
- ``INSTRUCTION_PAGE_FAULT``: Instruction page fault
- ``LOAD_GUEST_PAGE_FAULT``: Guest load page fault (virtualization)
- ``STORE_GUEST_PAGE_FAULT``: Guest store page fault (virtualization)
- ``VIRTUAL_INSTRUCTION``: Virtual instruction exception
- ``ECALL_FROM_USER``, ``ECALL_FROM_SUPER``, ``ECALL_FROM_MACHINE``: Privilege-specific ecalls

**Basic Exception Testing Example:**

.. code-block:: asm

   # Test ecall exception
   OS_SETUP_CHECK_EXCP ECALL, ecall_instr, after_ecall

   ecall_instr:
       ecall          # This instruction will cause an exception
       j failed       # Should never reach here

   after_ecall:
       # Continue test execution here
       j passed

**Page Fault Testing Example:**

.. code-block:: asm

   # Test store page fault on a non-writable page
   ;#page_mapping(lin_name=readonly_page, phys_name=readonly_phys, v=1, r=1, w=0, pagesize=['4kb'])

   # Setup expected page fault
   OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, fault_store, after_fault, readonly_page

   fault_store:
       li t1, readonly_page
       sw t0, 0(t1)    # This will cause a store page fault
       j failed        # Should never reach here

   after_fault:
       # Exception was handled correctly
       j passed

Page Map Feature
~~~~~~~~~~~~~~~~

The ``page_maps`` parameter in ``page_mapping`` directives allows you to specify which page table map(s) a page should belong to. This is essential for advanced virtual memory testing, especially in virtualized environments.

**Default Page Maps:**

- ``map_os``: Operating system page map (default for all pages)
- ``map_hyp``: Hypervisor page map (used in virtualized environments)

**Custom Page Maps:**

You can define custom page maps for specialized testing scenarios:

.. code-block:: asm

   # Page belongs to custom map
   ;#page_mapping(lin_name=custom_page, phys_name=custom_phys, v=1, r=1, w=1, pagesize=['4kb'], page_maps=['custom_map'])

   # Page belongs to multiple maps
   ;#page_mapping(lin_name=shared_page, phys_name=shared_phys, v=1, r=1, w=1, pagesize=['4kb'], page_maps=['map_os', 'custom_map'])

**Use Cases for Page Maps:**

1. **Process Isolation Testing:**

.. code-block:: asm

   # Process 1 pages
   ;#page_mapping(lin_name=proc1_stack, phys_name=proc1_stack_phys, v=1, r=1, w=1, pagesize=['4kb'], page_maps=['proc1_map'])
   ;#page_mapping(lin_name=proc1_heap, phys_name=proc1_heap_phys, v=1, r=1, w=1, pagesize=['4kb'], page_maps=['proc1_map'])

   # Process 2 pages
   ;#page_mapping(lin_name=proc2_stack, phys_name=proc2_stack_phys, v=1, r=1, w=1, pagesize=['4kb'], page_maps=['proc2_map'])
   ;#page_mapping(lin_name=proc2_heap, phys_name=proc2_heap_phys, v=1, r=1, w=1, pagesize=['4kb'], page_maps=['proc2_map'])

2. **Virtualization Testing:**

.. code-block:: asm

   # Guest OS pages
   ;#page_mapping(lin_name=guest_kernel, phys_name=guest_kernel_phys, v=1, r=1, w=1, x=1, pagesize=['4kb'], page_maps=['map_os'])

   # Hypervisor pages
   ;#page_mapping(lin_name=hyp_pages, phys_name=hyp_phys, v=1, r=1, w=1, x=1, pagesize=['4kb'], page_maps=['map_hyp'])

3. **Shared Memory Testing:**

.. code-block:: asm

   # Shared between processes
   ;#page_mapping(lin_name=shared_mem, phys_name=shared_phys, v=1, r=1, w=1, pagesize=['4kb'], page_maps=['proc1_map', 'proc2_map'])

**Advanced Page Map Features:**

- **Automatic Map Selection**: RiescueD automatically adds appropriate maps based on test environment
- **Map Inheritance**: Pages can inherit properties from their parent maps
- **Cross-Map References**: Pages in different maps can reference each other for complex scenarios

**Debugging Page Maps:**

When debugging page map issues, RiescueD generates detailed page table information in the output files:

- ``.ld`` file contains memory layout for all maps
- ``.dis`` file shows the final page table entries
- Log files detail which pages belong to which maps

Environment Randomization
~~~~~~~~~~~~~~~~~~~~~~~~~

RiescueD can randomize various aspects of the test environment:

- **Privilege Modes**: Automatically switch between machine, supervisor, and user modes
- **Paging Modes**: Test different virtual memory configurations
- **Extension Configuration**: Enable/disable RISC-V extensions randomly

Best Practices
--------------

Test Organization
~~~~~~~~~~~~~~~~~

1. **Use Clear Headers**: Always include comprehensive test headers
2. **Group Directives**: Organize random data, addresses, and page mappings in sections
3. **Document Tests**: Use ``test.summary`` to explain test intent
4. **Use Meaningful Names**: Choose descriptive names for variables and tests

Memory Management
~~~~~~~~~~~~~~~~~

1. **Align Addresses**: Use appropriate ``and_mask`` values for alignment
2. **Size Appropriately**: Ensure memory regions are sized correctly
3. **Test Boundaries**: Include tests for page boundaries and edge cases
4. **Consider Caching**: Be aware of cache line effects in your tests

Randomization Strategy
~~~~~~~~~~~~~~~~~~~~~~

1. **Constrain Wisely**: Use masks to ensure valid address ranges
2. **Test Multiple Scenarios**: Use ``any`` options to test various configurations
3. **Verify Assumptions**: Don't assume specific random values
4. **Handle Edge Cases**: Consider what happens with extreme random values

Debugging Tips
--------------

Common Issues
~~~~~~~~~~~~~

1. **Address Alignment**: Ensure addresses are properly aligned for their access size
2. **Page Permissions**: Verify page mappings have correct read/write/execute permissions
3. **Address Space Conflicts**: Avoid overlapping memory regions
4. **Missing Mappings**: Ensure all accessed addresses have corresponding page mappings

Debug Output
~~~~~~~~~~~~

RiescueD generates several helpful files:

- ``.S`` file: Final assembly with all substitutions
- ``.ld`` file: Linker script with memory layout
- ``.dis`` file: Disassembly for verification
- Log files: Detailed generation information

Performance Considerations
--------------------------

Test Generation Speed
~~~~~~~~~~~~~~~~~~~~~

- **Minimize Complex Mappings**: Large page tables slow generation
- **Use Appropriate Page Sizes**: Larger pages reduce table complexity
- **Limit Random Iterations**: Don't over-randomize in tight loops

Simulation Performance
~~~~~~~~~~~~~~~~~~~~~~

- **Optimize Hot Paths**: Keep frequently executed code efficient
- **Consider Memory Hierarchy**: Be aware of cache and TLB effects
- **Use Appropriate Test Lengths**: Balance coverage with simulation time

Configuration Files
-------------------

CPU Configuration
~~~~~~~~~~~~~~~~~

Create a ``cpu_config.json`` file to specify your target system:

.. code-block:: json

   {
       "memory_map": {
           "ram": {
               "start": "0x80000000",
               "size": "0x10000000"
           },
           "io": {
               "start": "0x10000000",
               "size": "0x1000000"
           }
       }
   }

Integration with Simulators
---------------------------

RiescueD works with popular RISC-V simulators:

- **Spike**: RISC-V ISA simulator
- **Whisper**: TenstorrentTT's RISC-V simulator
- **QEMU**: Full system emulation (coming soon)
- **Custom RTL**: Integration with RTL simulators

Further Resources
-----------------

- :doc:`Getting Started </user_guides/getting_started>` - Installation and setup
- :doc:`API Reference </api/RiescueD>` - Complete API documentation
- `GitHub Repository <https://github.com/tenstorrent/riescue>`_ - Source code and examples
- `Example Tests <https://github.com/tenstorrent/riescue/tree/main/riescue/dtest_framework/tests>`_ - Sample test cases

Need Help?
----------

- Check the `GitHub Issues <https://github.com/tenstorrent/riescue/issues>`_ for known problems
- Browse `GitHub Discussions <https://github.com/tenstorrent/riescue/discussions>`_ for community support
- Refer to the :doc:`Internal API </internal/internal_api>` for advanced usage

Happy testing with RiescueD! 🚀
