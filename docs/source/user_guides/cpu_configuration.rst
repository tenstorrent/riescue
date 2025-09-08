CPU Configuration and Memory Map Basics
=======================================

This tutorial introduces how to configure CPU settings and memory maps for RISC-V processors in the test framework. You'll learn to create and customize configuration files that define your processor's capabilities and memory layout.

Basic Configuration
-------------------

The CPU configuration system uses JSON files to define processor settings. Here's a minimal example:

.. literalinclude:: ../../../riescue/dtest_framework/tests/tutorials/cpu_config.json
    :language: json


This configuration sets:

- **reset_pc**: The program counter value when the processor starts (0x8000_0000)
- **mmap**: Memory map defining available memory regions

Memory Map Structure
^^^^^^^^^^^^^^^^^^^^

Every configuration must include a memory map with at least one DRAM region. The memory map has two main sections:

**DRAM Regions** (Required)
  Main system memory where programs and data are stored. Must include at least one region.

**IO Regions** (Optional)
  Memory-mapped peripherals and special hardware regions.

.. code-block:: json

    "mmap": {
        "dram": {
            "main_memory": {"address": "0x8000_0000", "size": "0x10_0000_0000"}
        },
        "io": {
            "uart": {"address": "0x1000_0000", "size": "0x1000"},
            "timer": {"address": "0x1001_0000", "size": "0x1000"}
        }
    }

Address and size values can be written as hexadecimal strings with underscores for readability (e.g., "0x8000_0000") or as plain integers.

Advanced Configuration
----------------------

RISC-V Feature Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The framework supports extensive RISC-V feature configuration. Each feature has three properties:

- **supported**: Whether the hardware supports this feature
- **enabled**: Whether the feature is currently active
- **randomize**: Percentage chance (0-100) this feature appears in generated tests

.. code-block:: json

    "features": {
        "v": {"supported": true, "enabled": true, "randomize": 80},
        "f": {"supported": true, "enabled": false, "randomize": 0},
        "zba": {"supported": true, "enabled": true, "randomize": 50}
    }

Common RISC-V extensions include:

- **i, m, a, f, d, c**: Base integer, multiplication, atomic, floating-point, and compressed instruction sets
- **v**: Vector extension for SIMD operations
- **h**: Hypervisor extension
- **zba, zbb, zbc, zbs**: Bit manipulation extensions
- **zfh**: Half-precision floating-point

Secure Memory Regions
^^^^^^^^^^^^^^^^^^^^^

DRAM regions can be marked as secure for testing security features:

.. code-block:: json

    "mmap": {
        "dram": {
            "normal_ram": {"address": "0x8000_0000", "size": "0x4000_0000"},
            "secure_ram": {"address": "0xC000_0000", "size": "0x4000_0000", "secure": true}
        }
    }

IO Region Access Control
^^^^^^^^^^^^^^^^^^^^^^^^

IO regions can specify test access permissions:

.. code-block:: json

    "io": {
        "restricted_device": {"address": "0x1000_0000", "size": "0x1000"},
        "test_device": {"address": "0x2000_0000", "size": "0x1000", "test_access": "available"}
    }

Test Generation Parameters
^^^^^^^^^^^^^^^^^^^^^^^^^^

The `test_generation` section controls how tests are created:

.. code-block:: json

    "test_generation": {
        "secure_access_probability": 30,
        "a_d_bit_randomization": 10,
        "pbmt_ncio_randomization": 5
    }

- **secure_access_probability**: Chance (0-100) of generating secure memory accesses
- **a_d_bit_randomization**: Frequency of testing accessed/dirty bit behavior
- **pbmt_ncio_randomization**: Frequency of testing page-based memory types

Using Configuration in Tests
----------------------------

Loading Configuration Files
^^^^^^^^^^^^^^^^^^^^^^^^^^^

In Python code, load configurations using the CpuConfig class:

.. code-block:: python

    from riescue.dtest_framework.config.cpu_config import CpuConfig

    # Load from file
    config = CpuConfig.from_json("my_config.json")

    # Access memory regions
    dram_regions = config.memory.dram_ranges
    io_regions = config.memory.io_ranges

    # Check features
    if config.features.is_feature_enabled("v"):
        print("Vector extension is enabled")

Feature Overrides
^^^^^^^^^^^^^^^^^

Override features at runtime without modifying configuration files:

.. code-block:: python

    # Enable vector extension, disable floating-point
    config = CpuConfig.from_json("config.json", "v.enable f.disable")

This is useful for testing different feature combinations with the same base configuration.

Configuration Templates
-----------------------

Create reusable configuration templates for common processor types:

**Minimal RISC-V Core**

.. code-block:: json

    {
        "reset_pc": "0x8000_0000",
        "isa": ["i"],
        "mmap": {
            "dram": {
                "ram": {"address": "0x8000_0000", "size": "0x8000_0000"}
            }
        },
        "features": {
            "rv64": {"supported": true, "enabled": true, "randomize": 100},
            "i": {"supported": true, "enabled": true, "randomize": 100}
        }
    }

**Full-Featured Application Processor**

.. code-block:: json

    {
        "reset_pc": "0x8000_0000",
        "isa": ["i", "m", "a", "f", "d", "c", "v"],
        "mmap": {
            "dram": {
                "main_ram": {"address": "0x8000_0000", "size": "0x40_0000_0000"}
            },
            "io": {
                "uart": {"address": "0x1000_0000", "size": "0x1000"},
                "timer": {"address": "0x1001_0000", "size": "0x1000"},
                "interrupt_controller": {"address": "0x1080_0000", "size": "0x10_0000"}
            }
        },
        "features": {
            "rv64": {"supported": true, "enabled": true, "randomize": 100},
            "i": {"supported": true, "enabled": true, "randomize": 100},
            "m": {"supported": true, "enabled": true, "randomize": 100},
            "a": {"supported": true, "enabled": true, "randomize": 100},
            "f": {"supported": true, "enabled": true, "randomize": 100},
            "d": {"supported": true, "enabled": true, "randomize": 100},
            "c": {"supported": true, "enabled": true, "randomize": 100},
            "v": {"supported": true, "enabled": true, "randomize": 100}
        }
    }

Validation and Debugging
-------------------------

Common Configuration Errors
^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Missing DRAM Section**
  Every configuration must include at least one DRAM region in the `mmap.dram` section.

**Invalid Address Format**
  Addresses must be valid integers or hex strings. Use "0x8000_0000" not "8000_0000".

**Conflicting Memory Ranges**
  Ensure memory regions don't overlap unless intentionally designed.

**Unsupported Feature Names**
  Feature names must match RISC-V standard extension names (case-sensitive).

Debugging Configuration Issues
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable debug logging to see how configurations are parsed:

.. code-block:: python

    import logging
    logging.basicConfig(level=logging.DEBUG)

    config = CpuConfig.from_json("config.json")

This shows detailed information about memory range parsing and feature detection.

The configuration system provides flexible control over processor simulation while maintaining compatibility with RISC-V standards. Start with simple configurations and gradually add complexity as needed for your specific testing requirements.