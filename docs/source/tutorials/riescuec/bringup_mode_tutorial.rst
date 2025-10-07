Bringup Mode Tutorial
=====================

Bringup mode provides a self-checking testing for RISC-V instructions and extensions. It requires a Bringup Test JSON file and a CPU configuration file.


Configuring Bringup Mode
-------------------------

To run in bringup mode, write a Bringup Test JSON file, like this one:

.. literalinclude:: ../../../../riescue/compliance/tests/rv_i/rv64i.json
    :language: json

Running a bringup test
----------------------
.. code-block:: bash

   python -m riescuec --mode bringup --json riescue/compliance/tests/rv_i/rv64i.json



All fields above are mandatory and it'll raise an error if any are missing.

Arch can be ``rv32`` or ``rv64`` and controls the set of instructions selected for a test.

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
