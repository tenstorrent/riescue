RiescueC Framework Overview
~~~~~~~~~~~~~~~~~~~~~~~~~~~
RiescueC generates RISC-V compliance tests automatically. You provide a JSON configuration file specifying which instructions you want to test, and it creates self-checking assembly tests that run on instruction set simulators.

What RiescueC Does
==================

RiescueC creates randomized RISC-V assembly tests for compliance verification. It provides different avenues for verification, through instruction and architectural compliance scenarios. RiescueC provides differnt modes for each of these avenues.

RiescueC Modes
==============

RiescueC operates in three distinct modes, each designed for different verification scenarios.

1. Bringup Mode

**Purpose**: Quick test generation for instruction and extension bringup.

**How to run**:

.. code-block:: bash

   python riescuec --mode bringup --json my_config.json

**What you get**: Focused tests for specific instructions or extensions with minimal overhead.


2. Test Plan Mode

**Purpose**: Generate tests from ``coretp`` test plans through self-checking architectural scenarios.


3. Compliance Mode

**Purpose**: Full compliance test suite generation with comprehensive coverage.



Getting Started
===============

Bringup Mode
--------------------------------

Run RiescueC Bringup mode with a Bringup Test JSON file:

.. code-block:: bash

   python riesceuc --mode bringup --json my_test.json -o my_test

Example JSON configuration:

.. literalinclude:: ../../../../riescue/compliance/tests/rv_i/rv64i.json
    :language: json


Configuration
=============

- ``arch``: Architecture (rv32/rv64)
- ``include_extensions``: Extensions to test
- ``include_groups``: Instruction groups to include
- ``include_instrs``: Specific instructions to include
- ``exclude_groups``: Instruction groups to exclude
- ``exclude_instrs``: Specific instructions to exclude

Here
Example bringup test files are in ``riescue/compliance/tests/``.


Two-Pass Generation
===================

RiescueC uses a two-pass approach:

1. **First Pass**: Generates initial test and runs it on Spike simulator to gather execution data
2. **Second Pass**: Uses first pass results to create enhanced self-checking tests

What You Get
============

RiescueC produces a a handful of files as part of the generation flow. Use ``--run_dir`` to specify a directory to place output files.
The final test ELF will have the name of the Bringup Test JSON file combined with ``_1`` and ``_2`` for their respective passes. ``_2`` includes the self-checking instructions.

You can also use ``-o`` to specify an output filename. E.g. ``-o my_test`` will produce a test ELF file ``my_test``. Along with the ELF you can find a handful of other files.

- ``.s`` files: Generated assembly tests
- ``.log`` files: Simulation results
- ``.dis`` files: Disassembled output for debugging


Next Steps
==========

- Try the bringup mode tutorial for hands-on examples
- Check the configuration examples for your target extensions
- Use test plan mode for advanced verification scenarios