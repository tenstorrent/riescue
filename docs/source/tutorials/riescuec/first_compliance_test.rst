First Compliance Test
=====================

This tutorial guides you through running your first compliance test using RiESCUE-C framework.

What is Compliance Testing?
---------------------------

Compliance testing verifies that a RISC-V implementation correctly executes instructions according to the RISC-V specification. RiESCUE-C generates self-checking tests that:

* Execute instruction sequences with known inputs
* Compare actual results against expected outcomes
* Validate behavior across multiple instruction set simulators
* Identify implementation bugs and specification violations

Key benefits:

* **Comprehensive Coverage**: Tests all instruction variants and edge cases
* **Self-Checking**: Tests include expected results for automatic validation
* **Cross-Validation**: Compares results between Spike and Whisper simulators
* **Randomized Testing**: Generates diverse test scenarios to catch corner cases

Setting Up Compliance Testing
-----------------------------

**Prerequisites**

* RiESCUE-C framework installed
* Container environment configured
* Target instruction set simulators (Spike, Whisper)

**Basic Configuration**

Create a test configuration file ``my_first_test.json``:

.. code-block:: json

   {
       "arch": "rv64",
       "include_extensions": ["i_ext"],
       "exclude_instrs": ["wfi", "ebreak", "mret", "sret", "ecall", "fence", "fence.i"],
   }

This configuration:

* Targets RV64I architecture
* Includes basic integer extension
* Excludes system/fence instructions
* Limits test size to 50 instructions

Running Your First Test
-----------------------

Execute the compliance test using the container:

.. code-block:: bash

   python3 -m riescuec --bringup --json my_first_test.json"

