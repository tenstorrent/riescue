Trap Handler
============

Implements exception and interrupt handling for the test framework.
Provides default handlers that fail tests on unexpected exceptions/interrupts.
Supports exception validation by allowing tests to configure expected trap codes and return addresses.

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

Configuration
-------------

- ``cfiles``: Enable context saving/restoring for C file integration
- ``deleg_excp_to``: Privilege level for exception delegation (MACHINE or SUPER)
- ``skip_instruction_for_unexpected``: Skip an instruction for unexpected exceptions


