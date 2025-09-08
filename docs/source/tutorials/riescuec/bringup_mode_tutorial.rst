Bringup Mode Tutorial
=====================

Bringup mode provides a simplified testing approach for initial RISC-V core validation and feature enablement.

What is Bringup Mode?
---------------------

Bringup mode is designed for early-stage RISC-V implementation validation. It focuses on:

* Basic instruction execution verification
* Progressive feature enablement
* Simplified test generation with minimal configuration
* Quick validation cycles for hardware bringup

Key characteristics:

* Reduced complexity compared to full compliance mode
* Targeted instruction subset testing
* Streamlined configuration requirements
* Fast iteration for debugging

Configuring Bringup Mode
-------------------------

To run in bringup mode, specify ``--mode bringup`` in your command line:

.. code-block:: bash

   riescue_c.py --mode bringup --json basic_test.json

Basic JSON configuration for bringup mode:

.. code-block:: json

   {
       "arch": "rv64",
       "include_extensions": ["i_ext"],
       "include_instrs": ["addi", "add", "sub", "lui", "auipc"],
       "exclude_instrs": ["wfi", "ebreak", "mret", "sret", "ecall"],
       "max_instructions": 100
   }

Bringup Test Workflow
----------------------

The bringup workflow follows these steps:

1. **Minimal Configuration**: Start with basic integer instructions
2. **Test Generation**: Generate small, focused test cases
3. **Execution**: Run tests on target simulator or hardware
4. **Validation**: Verify expected behavior
5. **Incremental Expansion**: Add more instructions/features

Example workflow:

.. code-block:: bash

   # Start with basic integer instructions
   riescue_c.py --mode bringup --json rv64i_basic.json

   # Add memory operations
   riescue_c.py --mode bringup --json rv64i_memory.json

   # Include control flow
   riescue_c.py --mode bringup --json rv64i_branches.json

