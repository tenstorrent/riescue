Test Headers Reference
======================

This reference documents all test header directives that configure the overall test environment. Test headers must be placed at the beginning of your test file and start with ``;#test.``.

Basic Test Configuration
------------------------

**;#test.arch** - Target Architecture
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Specifies the RISC-V architecture width for the test.

**Syntax:**

.. code-block:: asm

    ;#test.arch <architecture_list>

**Available Values:**

- ``rv32`` - 32-bit RISC-V architecture
- ``rv64`` - 64-bit RISC-V architecture
- ``any`` - Framework chooses architecture randomly

**Examples:**

.. code-block:: asm

    ;#test.arch       rv64        # 64-bit RISC-V only
    ;#test.arch       rv32        # 32-bit RISC-V only
    ;#test.arch       rv32 rv64   # Support both architectures

**;#test.priv** - Privilege Mode
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sets the privilege level where test code executes.

**Syntax:**

.. code-block:: asm

    ;#test.priv <privilege_list>

**Available Values:**

- ``machine`` - Machine mode (M-mode)
- ``super`` - Supervisor mode (S-mode)
- ``user`` - User mode (U-mode)
- ``any`` - Framework chooses randomly

**Examples:**

.. code-block:: asm

    ;#test.priv       machine           # Machine mode only
    ;#test.priv       super             # Supervisor mode only
    ;#test.priv       user              # User mode only
    ;#test.priv       machine super     # Randomly select machine or supervisor
    ;#test.priv       any               # Any privilege mode

**;#test.env** - Test Environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures the execution environment and hypervisor usage.

**Syntax:**

.. code-block:: asm

    ;#test.env <environment_list>

**Available Values:**

- ``bare_metal`` - Direct hardware execution
- ``virtualized`` - Hypervisor-based execution
- ``any`` - Framework chooses randomly

**Examples:**

.. code-block:: asm

    ;#test.env        bare_metal        # No hypervisor
    ;#test.env        virtualized       # With hypervisor
    ;#test.env        bare_metal virtualized  # Randomly select

**;#test.paging** - Memory Management
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Specifies virtual memory paging modes supported by the test.

**Syntax:**

.. code-block:: asm

    ;#test.paging <paging_mode_list>

**Available Values:**

- ``disable`` - No paging/virtual memory
- ``sv39`` - 3-level page table (39-bit virtual addresses)
- ``sv48`` - 4-level page table (48-bit virtual addresses)
- ``sv57`` - 5-level page table (57-bit virtual addresses)
- ``any`` - Framework chooses randomly

**Examples:**

.. code-block:: asm

    ;#test.paging     disable           # No virtual memory
    ;#test.paging     sv39              # 39-bit virtual addressing
    ;#test.paging     sv48              # 48-bit virtual addressing
    ;#test.paging     sv57              # 57-bit virtual addressing
    ;#test.paging     sv39 sv48 sv57    # Support multiple modes
    ;#test.paging     any               # Any paging mode

Multiprocessor Configuration
----------------------------

**;#test.cpus** - CPU Count
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sets the number of processor cores for multiprocessor testing.

**Syntax:**

.. code-block:: asm

    ;#test.cpus <cpu_count>

**Available Values:**

- Any positive integer
- ``N+`` format for "N or more" processors

**Examples:**

.. code-block:: asm

    ;#test.cpus       1             # Single processor
    ;#test.cpus       4             # Four processors
    ;#test.cpus       2+            # Two or more processors

**;#test.mp_mode** - Multiprocessor Mode
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures multiprocessor test behavior.

**Syntax:**

.. code-block:: asm

    ;#test.mp_mode <mode>

**Available Values:**

- ``enable`` - Enable MP features
- ``disable`` - Single processor mode

**Examples:**

.. code-block:: asm

    ;#test.mp_mode    enable        # Enable MP features
    ;#test.mp_mode    disable       # Single processor mode

Test Metadata
-------------

**;#test.name** - Test Identifier
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Unique identifier for the test case.

**Syntax:**

.. code-block:: asm

    ;#test.name <identifier>

**Examples:**

.. code-block:: asm

    ;#test.name       my_vector_test

**;#test.author** - Author Information
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Contact information for the test author.

**Syntax:**

.. code-block:: asm

    ;#test.author <email>

**Examples:**

.. code-block:: asm

    ;#test.author     engineer@company.com

**;#test.category** - Test Category
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

High-level categorization for test organization.

**Syntax:**

.. code-block:: asm

    ;#test.category <category>

**Common Categories:**

- ``arch`` - Architecture tests
- ``compliance`` - Compliance tests
- ``performance`` - Performance tests

**Examples:**

.. code-block:: asm

    ;#test.category   arch          # Architecture tests
    ;#test.category   compliance    # Compliance tests
    ;#test.category   performance   # Performance tests

**;#test.class** - Test Class
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Specific test classification within a category.

**Syntax:**

.. code-block:: asm

    ;#test.class <class>

**Common Classes:**

- ``vector`` - Vector extension tests
- ``memory`` - Memory system tests
- ``interrupt`` - Interrupt handling tests

**Examples:**

.. code-block:: asm

    ;#test.class      vector        # Vector extension tests
    ;#test.class      memory        # Memory system tests
    ;#test.class      interrupt     # Interrupt handling tests

**;#test.tags** - Descriptive Tags
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Space-separated tags for test filtering and organization.

**Syntax:**

.. code-block:: asm

    ;#test.tags <tag1> <tag2> <tag3> ...

**Examples:**

.. code-block:: asm

    ;#test.tags       vectors load_store simd

**;#test.summary** - Test Documentation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Multi-line test description and documentation.

**Syntax:**

.. code-block:: asm

    ;#test.summary
    ;#test.summary    <description_line_1>
    ;#test.summary    <description_line_2>
    ;#test.summary    ...

**Examples:**

.. code-block:: asm

    ;#test.summary
    ;#test.summary    This test verifies vector load/store operations
    ;#test.summary    with various data types and alignment patterns.
    ;#test.summary
    ;#test.summary    test01: Basic vector loads
    ;#test.summary    test02: Misaligned vector stores
    ;#test.summary

Extension Configuration
-----------------------

**;#test.features** - Extension Control
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enables or disables RISC-V extensions for the test.

**Syntax:**

.. code-block:: asm

    ;#test.features <ext_config1> <ext_config2> ...

**Format:** ``ext_<name>.enable`` or ``ext_<name>.disable``

**Common Extensions:**

- ``ext_v`` - Vector extension
- ``ext_f`` - Single-precision floating-point
- ``ext_d`` - Double-precision floating-point
- ``ext_c`` - Compressed instructions
- ``ext_zba``, ``ext_zbb``, ``ext_zbc``, ``ext_zbs`` - Bit manipulation
- ``ext_h`` - Hypervisor extension

**Examples:**

.. code-block:: asm

    ;#test.features   ext_v.enable          # Enable vector extension
    ;#test.features   ext_f.disable         # Disable float extension
    ;#test.features   ext_v.enable ext_f.disable ext_zba.enable

Complete Example
----------------

Here's a comprehensive example showing all test header options:

.. code-block:: asm

    ;#test.name       comprehensive_test
    ;#test.author     engineer@company.com
    ;#test.arch       rv64
    ;#test.priv       machine super
    ;#test.env        bare_metal
    ;#test.cpus       1
    ;#test.paging     sv39 sv48
    ;#test.category   arch
    ;#test.class      memory
    ;#test.features   ext_v.enable
    ;#test.tags       virtual_memory randomization
    ;#test.summary
    ;#test.summary    Comprehensive test demonstrating multiple RiescueD features
    ;#test.summary    including virtual memory, random data, and interrupt handling
    ;#test.summary
