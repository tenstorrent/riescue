Bringup Mode Tutorial
=======================

Bringup mode provides a self-checking testing for RISC-V instructions and extensions.

This tutorial walks through generating self-checking test ELF files for RV64I and RV64G instructions.
It requires a Bringup Test JSON file, which we'll write below.
By default it runs on ``Spike`` and ``Whisper`` ISS.

Generating a RV64I Bringup Test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Write a Bringup Mode Test JSON file
----------------------------------------

To start, we'll need a Bringup Test JSON file for RV64I instructions:

.. code-block:: bash

   echo '{"arch":"rv64i","include_extensions":["i_ext"],"include_groups":[],"include_instrs":[],"exclude_groups":[],"exclude_instrs":["wfi","ebreak","mret","sret","ecall","fence","fence.i","c.ebreak"]}' > rv64i.json

This will create a new file called ``rv64i.json`` with the same content as the example file.

2. Generate the test with RiescueC
----------------------------------

Now we can run the test with RiescueC:

.. code-block:: bash

   riescuec --mode bringup --json rv64i.json


This should generate a test file in the current directory called ``rv64i_test`` along with the assembly, linker script, and disassembled output.


Generating a RV64G Bringup Test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Now we can do the same for instructions included in the ``RV64G``. We can use the same example file as above, but we'll need to include all the extensions in the ``RV64G`` extension group.

.. code-block:: bash

   echo '{"arch":"rv64","include_extensions":["i_ext","m_ext","a_ext", "f_ext", "d_ext"],"include_groups":[],"include_instrs":[],"exclude_groups":[],"exclude_instrs":["wfi","ebreak","mret","sret","ecall","fence","fence.i","c.ebreak"]}' > rv64g.json
   riescuec --mode bringup --json rv64g.json



A closer look at the Bringup JSON file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Here is an example of a Bringup Test JSON file:


.. literalinclude:: ../../../../riescue/compliance/tests/rv_i/rv64i.json
    :language: json


All fields above are mandatory, raising an error if any are misisng.

Arch can be ``rv32`` or ``rv64``, controlling the set of instructions selected for a test.

.. warning::

   Note that ``rv32`` *only* selects instructions supported by the 32-bit instruction set. ``rv64`` selects both 32-bit and 64-bit instructions.

   This doesn't mean that tests are running on an ``XLEN`` of 32. Currently only ``XLEN`` of 64 is supported. Future support will add 32-bit ``XLEN`` support.


**include_groups**: List of instruction groups to include in testing. Groups organize related instructions together for easier test configuration.

**include_instrs**: List of specific instruction names to include in the test generation. Use this for precise control over which instructions are tested.

**exclude_groups**: List of instruction groups to exclude from testing. Useful for removing entire categories of instructions.

**exclude_instrs**: List of specific instruction names to exclude from testing. Common exclusions include system instructions like ``wfi``, ``ebreak``, ``mret``, ``sret``, and ``ecall`` that do not have any architecture behavior in an ISS.



Next Steps
----------

- Learn more about CPU configuration in :doc:`../cpu_configuration`
- Learn about Test Plan Mode in :doc:`test_plan_tutorial`
