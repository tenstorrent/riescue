RVCP Print Messages
===================

RiescueD can print per-discrete-test pass/fail messages to the simulator console during execution.
This is useful for identifying which discrete tests passed or failed without inspecting memory signatures.

The messages are printed using macros defined in an ``rvmodel_macros.h`` header, which describes how to write to the console.
This follows the same convention as `riscv-arch-test <https://github.com/riscv/riscv-arch-test>`_.
RiescueD includes a default HTIF-based implementation.

.. contents:: Table of Contents
    :local:
    :depth: 2


Enabling RVCP Messages
----------------------

Two flags control which messages are printed:

``--print_rvcp_passed``
    Print a message each time a discrete test passes.

``--print_rvcp_failed``
    Print a message each time a discrete test fails.

Either or both flags can be enabled. When neither is set, no RVCP messages are generated.

.. code-block:: bash

    # Print only pass messages
    riescued run --testfile test.s --print_rvcp_passed

    # Print only fail messages
    riescued run --testfile test.s --print_rvcp_failed

    # Print both
    riescued run --testfile test.s --print_rvcp_passed --print_rvcp_failed

Message Format
--------------

Each message follows the format:

.. code-block:: text

    RVCP: Test File <testname> <discrete_test_name> PASSED
    RVCP: Test File <testname> <discrete_test_name> FAILED

Where:

- ``<testname>`` is the test elf filename
- ``<discrete_test_name>`` is the name from the ``discrete_test`` directive

For example, given a test elf ``load_store`` with a discrete test ``test01``:

.. code-block:: text

    RVCP: Test File load_store test01 PASSED

RVModel Macros
--------------

RVCP messages are printed using macros defined in an ``rvmodel_macros.h`` header file.
These macros follow the same interface defined by the `riscv-arch-test <https://github.com/riscv/riscv-arch-test>`_ framework
(see the ``rvmodel_macros.h`` convention in riscv-arch-test).
This means existing ``rvmodel_macros.h`` headers written for riscv-arch-test targets can be reused with RiescueD.

This header is automatically included in the generated assembly and provides two macros:

``RVMODEL_IO_INIT(_R1, _R2, _R3)``
    Called once during loader initialization to set up the I/O device.
    Scratch registers ``_R1``, ``_R2``, ``_R3`` are available for use.

``RVMODEL_IO_WRITE_STR(_R1, _R2, _R3, _STR_PTR)``
    Writes a null-terminated string at address ``_STR_PTR`` to the console device.
    ``_R1``, ``_R2``, ``_R3`` are scratch registers. ``_STR_PTR`` is advanced past the string.

By default, RiescueD uses an HTIF-based implementation that writes characters via the ``tohost`` interface.


Selecting a Macros Header
~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``--rvmodel_macros`` to provide a custom header:

.. code-block:: bash

    riescued run --testfile test.s --print_rvcp_passed \
        --rvmodel_macros path/to/rvmodel_macros_uart.h
