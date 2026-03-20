Selfcheck: Self-Checking Tests
===================================================

Selfcheck compiles golden checksums of architectural state into the test binary so it can verify its own correctness on any DUT without requiring an external reference model at runtime.

When ``--selfcheck`` is enabled, RiescueD builds the test twice: once normally to capture golden checksums via Whisper, and again with that golden data linked in. The resulting binary can then run on any target and compare its live checksum against the compiled-in expected values at the end of each discrete test.


What is Selfcheck?
-------------------

Selfcheck enables *self-checking* directed tests. At the end of each discrete test (including ``test_setup`` and ``test_cleanup``), the runtime computes a 128-bit Fletcher checksum over the architectural state and compares it against compiled-in golden checksums. If the checksum mismatches, the test jumps to ``eot__failed``.

This allows a single test binary to be validated on any DUT or ISS without requiring a separate reference model to be running alongside it at runtime.


How Selfcheck Works
-------------------

Build and Link Flow
~~~~~~~~~~~~~~~~~~~

The selfcheck flow extends the normal RiescueD build pipeline with additional steps:

1. **Generate and build normally** -- RiescueD generates the test assembly from the test file and compiles it into an ELF binary, just like a standard run.

2. **Run in Whisper with** ``--dumpmem`` -- The initial binary is executed in the Whisper ISS. Whisper dumps the ``selfcheck_data`` memory region (which contains the computed checksums) to a hex file (``<testname>_selfcheck_dump.hex``).

3. **Convert hex dump to** ``.byte`` **assembly** -- The hex dump is converted into a ``.byte``-per-byte assembly file (``<testname>_selfcheck_dump.s``) placed in the ``.selfcheck_data`` section.

4. **Relink with golden data** -- The golden data object is linked into the binary, populating the ``selfcheck_data`` region with the expected values. This produces the final self-checking ELF.

5. **Optionally run on target ISS** -- The relinked binary can be run on any ISS or DUT using ``--run_iss``. The selfcheck runtime detects the compiled-in data and switches to check mode automatically.

.. mermaid::

    graph LR
        A[Generate assembly] --> B[Build - compile + link]
        B --> C[Simulate in Whisper w/ --dumpmem]
        C --> D[Convert hex dump to .byte asm]
        D --> E[Relink with golden data]
        E --> F[Run on target ISS / DUT]


Runtime Behavior
~~~~~~~~~~~~~~~~

The selfcheck runtime has two modes, selected automatically based on whether golden data is present:

- **Save mode** (first run): No golden data exists yet. At the end of each discrete test, the runtime computes a 128-bit Fletcher checksum over the architectural state and stores it to the per-hart ``selfcheck_data`` region. This is the mode used during the initial Whisper simulation.

- **Check mode** (second run): Golden data has been compiled in. At the end of each discrete test, the runtime recomputes the checksum and compares it against the stored golden values. Any mismatch causes the test to fail.

The runtime automatically detects whether golden data is present and selects the appropriate mode.


What Gets Checked
~~~~~~~~~~~~~~~~~

The resources folded into the checksum depend on which ISA extensions are enabled in the CPU configuration:

- **GPRs** -- General-purpose registers are always included.
- **FP registers** (f0-f31) -- Included when the F or D extension is enabled. Uses ``fmv.x.w`` for F-only or ``fmv.x.d`` when D is enabled to move bits into an integer register for checksumming.
- **Vector registers** (v0-v31) -- Included when the V extension is enabled. Each register is stored to a scratch buffer via ``vs1r.v`` and then checksummed in 8-byte chunks.
- **Vector CSRs** (``vtype``, ``vl``, ``vstart``) -- Included when the V extension is enabled.


Using Selfcheck
-------------------

Command-Line Usage
~~~~~~~~~~~~~~~~~~

To generate a self-checking test binary:

.. code-block:: bash

    riescued run --testfile test.s --cpuconfig cpu.json --selfcheck

This runs the full selfcheck flow: generate, build, simulate in Whisper, convert dump, and relink.

To additionally run the relinked binary on the configured ISS:

.. code-block:: bash

    riescued run --testfile test.s --cpuconfig cpu.json --selfcheck --run_iss


Generated Files
-------------------

When ``--selfcheck`` is enabled, three additional files are produced alongside the standard output:

- ``<testname>_selfcheck_dump.hex`` -- Raw hex dump of the ``selfcheck_data`` region from the Whisper simulation.
- ``<testname>_selfcheck_dump.s`` -- Assembly file containing ``.byte`` directives generated from the hex dump.
- ``<testname>_selfcheck_dump.o`` -- Compiled object file linked into the final self-checking ELF.


Constraints and Limitations
----------------------------

- **Single core only** -- Selfcheck does not support multiprocessor (MP) mode. The test must use a single hart.
- **Requires Whisper ISS** -- Golden data generation depends on the Whisper ISS for the initial simulation with ``--dumpmem``. Whisper must be available in the toolchain configuration.
- **Extension-dependent checking** -- Only resources corresponding to enabled extensions are checked. If F/D is not enabled, FP registers are not checked. If V is not enabled, vector registers and vector CSRs are not checked.
