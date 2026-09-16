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

Trap instruction register check (``mtinst`` / ``htinst``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With ``--check_xtinst``, the M-mode and HS-mode handlers check the value the
hardware wrote into their trap instruction register against the values the RISC-V
privileged spec permits. The flag is off by default. It additionally requires the
H extension, since ``mtinst`` and ``htinst`` only exist with it, and is never
emitted for the VS handler, which has no such CSR.

On an interrupt the only permitted value is zero. On a synchronous exception, a
nonzero value must be one of the shapes the reported cause permits:

* a **transformation of the trapping instruction** -- permitted only for faults
  on explicit memory accesses. Two chapters define transformations: the
  hypervisor extension covers basic loads and stores, atomics and the
  virtual-machine load/stores, and the CMO extension covers the cache-block
  instructions. The handler also re-fetches the instruction at ``xepc`` and
  requires every field the transformation keeps (``funct3``/``rd``/opcode for a
  basic load, ``rs2``/``funct3``/opcode for a basic store, everything but
  ``rs1`` for an atomic or HLV/HLVX/HSV, opcode/``funct3``/``operation`` for a
  cache-block operation) to match, so a value that is a legal shape but describes
  some other instruction is caught. Bits[1:0] of the value must also agree with
  the instruction on being compressed.
* a **custom value** -- a ``custom-0``..``custom-3`` major opcode, permitted only
  for the causes whose spec table row allows one. A *reserved* major opcode is
  never a legal custom value.
* a **guest-page-fault pseudoinstruction** -- ``0x3000`` or ``0x3020``
  (VSXLEN=64 is assumed), permitted only for a guest-page fault. Deliberately not
  also conditioned on a nonzero faulting guest physical address: the allowed-value
  list permits a pseudoinstruction on a guest-page fault outright, and the rule
  about ``htval``/``mtval2`` runs the other way -- an implicit VS-stage access
  *with* a nonzero faulting address must write one -- so requiring a nonzero
  ``htval`` would fail a conforming DUT whose WARL ``htval`` reads zero.
* **zero**, which is a legal value for every cause. The one case the spec forbids it
  requires the implementation to have written a nonzero faulting guest physical address
  to ``htval``/``mtval2``, which is its own choice to make -- both are WARL and may hold
  only zero -- so no state of these registers makes zero illegal on its own.

Anything else fails the test, including a nonzero value above bit 31, bits[1:0]
equal to ``0b10``, a nonzero field that the transformation must zero, a
read/write direction that disagrees with the cause, and a standard instruction
for which no transformation is defined -- for which the spec requires zero.

Every rejection jumps to one shared failure path,
``trap_handler_<mode>__xtinst_fail``, with the reason as a comment at the branch
site in the generated assembly.

The spec tables are emitted as actual lookup tables, as data inline in the
handler's own section: a byte per cause code holding which options that cause
permits and which access direction it implies, a byte per major opcode holding
its custom/reserved classification and transformation kind, and a pair of words
per kind holding the fields that transformation must zero and must preserve.
Each is read with one ``la``/``add``/``lbu`` -- a single load regardless of how
many causes or opcodes the table covers -- in place of rebuilding a bitmask per
test. They live alongside the code rather than in ``.rodata`` because the
section the check itself executes from is already readable wherever it is
mapped, so a PC-relative ``la`` reaches them with none of the ``code``/
``code_pa`` relocation math a cross-section data reference would need. The
tables sit between the failure path and the exit label, both of which are
reached only by a jump, so nothing falls through into the data.

What stays a branch is only dispatch too small to be worth a table: the two
pseudoinstruction literals, and the kind-to-direction dispatch over five kinds.

The check touches ``t0``, ``t1`` and ``t2`` only, and spills nothing. Those are
the three registers the exception path already clobbers, and generated tests do
keep live values in ``t3``-``t6`` across a trap -- riescuec's hypervisor paging
tests hold a branch target in ``t4`` while taking a fault -- so a check that used
them would corrupt the test. Three registers are enough because everything the
check needs is either a re-readable CSR (``mtinst``/``htinst``, ``xcause``,
``xepc``, ``htval``/``mtval2``) or a cheap re-lookup in one of the tables above,
so nothing has to stay live across a branch: ``t0`` holds the trap value during
the legality checks and the fetched instruction during the comparison. ``t1`` is
reloaded with ``xcause`` on the way out, since the code that follows expects it
there.

The re-read of the trapping instruction is always done as halfword loads, two of
them combined for a 32-bit instruction. With RVC enabled a 32-bit instruction can
sit at an address ≡ 2 (mod 4), so a word load would be misaligned, and on a DUT
that does not implement misaligned loads that raises a nested exception while
``xtvec`` still points at ``trap_panic`` -- turning a passing test into a panic.

A compressed trapping instruction gets the bits[1:0] agreement check but no field
comparison, because comparing one means expanding it to its 32-bit equivalent
first. Traps that never reach the exception entry --
``OS_INSTALL_EXCP_HANDLER`` bodies and
``FeatMgr.register_default_exception_handler`` overrides, both dispatched before
context save -- are not checked, and neither is an ecall: the check sits behind
the ecall dispatch, because the syscall path carries its arguments in
``t2``-``t6`` and the spec defines no transformation for an ecall anyway.

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
- ``check_xtinst``: Check ``mtinst``/``htinst`` against the spec on every trap into
  M or HS mode (``--check_xtinst``, off by default; requires the H extension)

.. note::
   The ``deleg_excp_to`` option is a convenience that sets delegation registers
   so that all non-ecall exceptions are delegated to the specified mode.
   By default, delegation registers are set randomly.


