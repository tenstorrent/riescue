Variables
=========


..
    _comment: Generated using Opus 4.5


The Variable module manages runtime environment variables used by the test runtime.
It handles both hart-local (per-hart) and shared (cross-hart) variable storage, providing
assembly code generation for loading, storing, and manipulating these variables.

Architecture
------------

The variable system consists of three memory containers managed by a central ``VariableManager``:

.. mermaid::

    graph TD
        VM[VariableManager]
        HC[HartContext<br/>Hart-local variables]
        HS[HartStack<br/>Hart-local stack]
        SM[SharedMemory<br/>Cross-hart variables]

        VM --> HC
        VM --> HS
        VM --> SM

        classDef manager fill:#5164e0,stroke:#5164e0,stroke-width:3px,color:#fff
        classDef memory fill:#fa512e,stroke:#fa512e,stroke-width:3px,color:#000

        class VM manager
        class HC,HS,SM memory


Hart-Local vs Shared Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Hart-local variables are stored in a per-hart context accessed via the ``tp`` register.
Each hart has its own copy of these variables, making them safe for concurrent access without synchronization.

Shared variables are stored in a global ``os_data`` section accessible by all harts.
These require atomic operations (AMO) when accessed concurrently in multiprocessor configurations.


Variable
--------

The :py:class:`riescue.dtest_framework.runtime.variable.variable.Variable` class represents a single runtime variable.

Each variable tracks:

- ``name``: Symbol name for the variable
- ``value``: Initial value
- ``size``: Size in bytes (1, 2, 4, or 8)
- ``offset``: Byte offset from the base pointer
- ``hart_variable``: Whether the variable is hart-local (uses ``tp``) or shared

Code Generation Methods
~~~~~~~~~~~~~~~~~~~~~~~

``load(dest_reg)``
    Generates assembly to load the variable value into the destination register.

``store(src_reg, temp_reg)``
    Generates assembly to store a register value into the variable.

``load_immediate(dest_reg)``
    Generates assembly to load the variable address into a register.

``increment(dest_reg, addr_reg)``
    Loads, increments by 1, and stores. Uses ``amoadd`` if AMO is enabled.

``load_and_clear(dest_reg)``
    Atomically loads the value and clears it. Uses ``amoswap`` if AMO is enabled.


VariableManager
---------------

The :py:class:`riescue.dtest_framework.runtime.variable.manager.VariableManager` class coordinates all variable storage.

API
~~~

``register_hart_variable(name, value, **kwargs)``
    Register a new hart-local variable.

``register_shared_variable(name, value, **kwargs)``
    Register a new shared variable.

``get_variable(name)``
    Retrieve a variable by name from any memory container.

``initialize(scratch_regs)``
    Generate code to initialize the hart context pointer into scratch CSRs.

``enter_hart_context(scratch)``
    Generate code to enter the runtime context by swapping ``tp`` with a scratch CSR.

``exit_hart_context(scratch)``
    Generate code to exit the runtime context and restore test context.

``allocate()``
    Generate assembly directives to allocate all variable storage sections.

``equates(offset)``
    Generate ``.equ`` directives for shared variables.


HartContext
-----------

The :py:class:`riescue.dtest_framework.runtime.variable.hart_memory.HartContext` class manages per-hart context storage.

Each hart context contains:

- Hart stack pointer
- Test stack pointer swap space
- ``mhartid`` (automatically set per-hart)
- User-registered variables

Contexts are aligned to 64-byte boundaries.

For multi-hart configurations, a ``hart_context_table`` is generated containing pointers to each hart's context.
The correct context is loaded at runtime based on ``mhartid``.


HartStack
---------

The :py:class:`riescue.dtest_framework.runtime.variable.hart_memory.HartStack` class allocates per-hart stack space.

Follows RISC-V ABI 2.1:

- Aligned to 128-bit (16 bytes)
- Stack grows downward (initialized with ``{stack_name}_end``)
- Default size: 4096 bytes


SharedMemory
------------

The :py:class:`riescue.dtest_framework.runtime.variable.shared_memory.SharedMemory` class manages cross-hart shared variables.

Variables are allocated in the ``os_data`` section with ``.equ`` directives generated for symbol access.
Variable names in memory are suffixed with ``_mem`` to avoid conflicts with the equate symbols.


Configuration
-------------

- ``xlen``: Register width (32 or 64 bits)
- ``hart_count``: Number of harts
- ``amo_enabled``: Enable atomic memory operations for shared variable access
- ``hart_stack_size``: Size of per-hart stack (default 0x1000)


Example Usage
-------------

.. code-block:: python

    from riescue.dtest_framework.runtime.variable import VariableManager
    import riescue.lib.enums as RV

    vm = VariableManager(
        data_section_name="runtime_data",
        xlen=RV.Xlen.XLEN64,
        hart_count=4,
        amo_enabled=True
    )

    # Register variables
    vm.register_hart_variable("trap_count", value=0, description="Number of traps")
    vm.register_shared_variable("global_lock", value=0)

    # Generate allocation code
    alloc_code = vm.allocate()

    # Get variable and generate load code
    trap_var = vm.get_variable("trap_count")
    load_code = trap_var.load("t0")