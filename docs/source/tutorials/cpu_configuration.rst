Configuration & Memory Map Basics
=======================================

This tutorial covers the essential CPU configuration settings needed to get started with the test framework. You'll learn the basic structure of configuration files and the minimum required settings.

Basic Configuration
-------------------

CPU configurations are defined in JSON files with three essential components:

.. literalinclude:: ../../../riescue/dtest_framework/tests/tutorials/cpu_config.json
    :language: json


Required Settings
^^^^^^^^^^^^^^^^^

**reset_pc**
  The program counter value when the processor starts. Typically set to ``0x8000_0000`` for RISC-V systems.

**mmap.dram**
  Memory map defining at least one DRAM region where programs and data are stored. Each region needs:

  - ``address``: Starting memory address
  - ``size``: Size of the memory region

Memory Map Structure
^^^^^^^^^^^^^^^^^^^^

The memory map has two main sections:

**DRAM** (Required)
  Main system memory. Must include at least one region:

.. code-block:: json

    "dram": {
        "main_ram": {"address": "0x8000_0000", "size": "0x40_0000_0000"}
    }

**IO** (Optional)
  Memory-mapped peripherals:

.. code-block:: json

    "io": {
        "uart": {"address": "0x1000_0000", "size": "0x1000"},
        "timer": {"address": "0x1001_0000", "size": "0x1000"}

    }

Address Format
^^^^^^^^^^^^^^

Addresses can be written as:

- Hex strings with underscores: ``"0x8000_0000"``
- Plain hex strings: ``"0x80000000"``
- Integers: ``2147483648``


Using Configuration in Tests
----------------------------

Load your configuration file on the command-line with the ``--cpuconfig`` flag:

.. code-block:: bash

    --cpuconfig my_config.json

Configuration Templates
-----------------------

**Minimal Configuration**

.. code-block:: json

    {
        "reset_pc": "0x8000_0000",
        "mmap": {
            "dram": {
                "ram": {"address": "0x8000_0000", "size": "0x8000_0000"}
            }
        }
    }

Validation and Debugging
-------------------------

Common errors to avoid:

- Missing ``dram`` section in memory map
- Invalid address formats (must include ``0x`` prefix for hex)
- Empty configuration sections

If your configuration fails to load, check that all required fields are present and addresses are properly formatted.


Advanced Configuration
----------------------

For more complex configurations including secure memory, test generation parameters, and extensive RISC-V feature sets, see the :doc:`Configuration Schema Reference <../reference/config/configuration_schema>`.