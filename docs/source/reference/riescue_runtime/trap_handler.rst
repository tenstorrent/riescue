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

Runtime-installed exception handlers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tests can arm an on-demand exception handler at runtime with the
:ref:`install_excp_handler_macro` macro, which stores the handler address,
expected mode, and cause in the hart-local ``excp_handler_addr`` /
``excp_handler_mode`` / ``excp_handler_cause`` variables.

The dispatch is a fixed block at the start of the exception path, **before**
context save: the slot is read through the scratch CSR (``mscratch`` /
``sscratch`` still holds the hart-context pointer at that point), so only
``t0``/``t1`` are clobbered — the same register contract as
``FeatMgr.register_default_exception_handler`` overrides. When the exception
cause matches ``excp_handler_cause`` (and ``excp_handler_mode`` is 0 or matches
the handler's own mode), control jumps to the runtime-loaded
``excp_handler_addr`` — no link-time jump table, so the ``jr`` reaches anywhere
in the image regardless of how far the handler is from ``.runtime``. On any
mismatch, execution continues into the original exception path unchanged
(FeatMgr overrides, then ``check_excp``, then the default fail-on-unexpected
behavior).

Address translation: the install macro stores a *virtual* address (``la`` in
test code), and HS/VS-mode trap handlers execute with translation enabled, so
they jump to the stored VA directly. The M-mode trap handler fetches bare —
M-mode instruction fetches are never translated — so before jumping it
relocates the VA to a physical address assuming the handler lives in ``.code``:
``pa = va - align4k(code) + code_pa`` (the ``code``/``code_pa`` section equates,
same idiom as exception-hook dispatch). When paging is disabled the relocation
is an identity (``code_pa == align4k(code)``). Handler bodies must therefore
live in ``.code``.

The expected-mode gate guards against ``medeleg`` randomization, mid-test
``medeleg`` writes, and trap origin (an M-mode ``ebreak`` lands in the M-mode
handler even with ``medeleg[3]=1``): a cause match arriving at the wrong-mode
handler falls through instead of jumping into a body written for another mode
(wrong ``xret``, wrong CSR view, VA-vs-bare addressing). The mode reported by
each trap handler matches ``OS_SETUP_CHECK_EXCP``: 1 = MACHINE, 2 = HS, 3 = VS
(the ``trap_handler_s__`` handler reports 3 in a virtualized environment, where
it is the VS handler, and 2 in a bare-metal environment, where it is the HS
handler).

The arming state is hart-local, so in MP tests each hart arms its handler
independently. The scheduler clears the slot at every dispatch, so arming is
scoped to a single discrete test by default; use
``OS_UNINSTALL_EXCP_HANDLER`` only for scoping finer than a discrete test.
There is a single slot per hart — arming again overwrites the previous handler.

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


