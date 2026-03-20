Trap Handler
============

Implements exception and interrupt handling for the test framework.
Provides default handlers that fail tests on unexpected exceptions/interrupts.
Supports exception validation by allowing tests to configure expected trap codes and return addresses.

Architecture
------------

The trap handling system uses multiple entry points managed from Machine mode:

**Machine Mode Trap Handler**
   - Entry at ``mtvec``
   - Handles all syscalls (ecalls) from any privilege mode
   - Handles non-delegated exceptions and interrupts

**Supervisor Mode Trap Handler** (non-virtualized)
   - Entry at ``stvec``
   - Handles exceptions delegated via ``medeleg``

**HS-mode and VS-mode Trap Handlers** (when ``test_env`` is virtualized)
   - HS-mode handler at ``stvec`` handles exceptions delegated via ``medeleg``
   - VS-mode handler at ``vstvec`` handles exceptions delegated via ``hedeleg``

Delegation registers (``medeleg``/``mideleg``/``hedeleg``/``hideleg``) control
how non-ecall traps get delegated. By default, these are set randomly.

Interrupts
-----------
By default nested interrupts are not supported.
Interrupts are cleared and then return to code.
To override an interrupt handler, a test can register a custom interrupt handler with :ref:`vectored_interrupt_directive`.

Exceptions
----------
Exceptions return to the `Runtime Environment` and run the exception handler.
Expeceted exceptions can be configured using ``OS_SETUP_CHECK_EXCP`` macro.
This informs the exception handler what the exception PC and cause are, and where to return after an exception.
This is useful for testing expected exceptions.

Unexpected exceptions will cause the test to fail, unless ``FeatMgr.skip_instruction_for_unexpected`` is set.

Hart Context
-------------

The Hart Context is managed through the :doc:`Variables <variables>` module.

A the start of a trap, the Test's context is swapped with the Hart Context.
This is done by swapping the scratch register with tp, swapping the ``sp`` register with the Hart Context's ``sp`` variable, and pushing temporary registers to the stack.

If the test needs to return control to the Test Environment, the Hart Context is swapped back when the trap is exited.

.. image:: /common/images/hart_context.png




Configuration
-------------

- ``cfiles``: Enable context saving/restoring for C file integration
- ``medeleg``/``mideleg``: Control which exceptions are delegated to S-mode handlers
- ``skip_instruction_for_unexpected``: Skip an instruction for unexpected exceptions

.. note::
   The ``deleg_excp_to`` option is a convenience that sets delegation registers
   so that all non-ecall exceptions are delegated to the specified mode.
   By default, delegation registers are set randomly.


