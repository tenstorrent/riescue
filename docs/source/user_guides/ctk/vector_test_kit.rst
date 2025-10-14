Generating A Vector Test Kit
==================================

This guide explains how to generate RISC-V Vector (RVV) extension tests using ``CTK``.

Prerequisites
-------------

Before generating vector tests, ensure you have:

- Whisper ISS with vector extension support (built with ``--enable-zvec``)
- RISC-V toolchain with vector extension support
- Basic understanding of RISC-V vector extension parameters (VLEN, LMUL, SEW)

See :doc:`/tutorials/install` for installation instructions.

Quick Start
-----------

Generate vector extension tests with CTK:

.. code-block:: bash

   ctk --run_dir vector_tests --seed 42 --test_count 50 --isa rv64v

Tests are generated when the ISA includes ``v`` and vector is enabled in CPU configuration.


Complete Example
----------------

**Step 1: Create CPU Configuration** (``cpu_vector.json``):

.. code-block:: json

   {
       "reset_pc": "0x8000_0000",
        "mmap": {
            "dram": {
                "dram0": {"address": "0x0", "size": "0x8000_0000"},
                "dram1": {"address": "0x1_8000_0000", "size": "0xF_8000_0000"}
            },
            "io": {
                "htif": {"address": "0x9000_0000", "size": "0x10"},
                "pcie_sub0_mmio": {"address": "0x100_0000_0000", "size": "0x100_0000_0000", "test_access": true},
                "pcie_sub1_mmio": {"address": "0x200_0000_0000", "size": "0x100_0000_0000", "test_access": true},
                "pcie_sub2_mmio": {"address": "0x300_0000_0000", "size": "0x100_0000_0000", "test_access": true}
            }
        },
       "features": {
           "rv64": {"supported": true, "enabled": true, "randomize": 100},
           "i": {"supported": true, "enabled": true, "randomize": 100},
           "m": {"supported": true, "enabled": true, "randomize": 100},
           "f": {"supported": true, "enabled": true, "randomize": 100},
           "d": {"supported": true, "enabled": true, "randomize": 100},
           "v": {"supported": true, "enabled": true, "randomize": 100}
       }
   }

**Step 2: Generate Vector Tests**:

.. code-block:: bash

   python -m riescue.ctk \
       --run_dir rv64imfdv_tests \
       --seed 12345 \
       --test_count 100 \
       --whisper_path /path/to/whisper

CTK generates tests covering vector arithmetic, load/store, mask operations, and reductions.


Vector Instruction Generation
-----------------------------

More documentation coming soon on how to modify the default vector instruction generation.


Next Steps
----------

- Understand CPU configuration details: :doc:`/tutorials/cpu_configuration`

- Explore the CTK Python API: :doc:`/reference/python_api/CTK`

- Review configuration schema: :doc:`/reference/config/configuration_schema`