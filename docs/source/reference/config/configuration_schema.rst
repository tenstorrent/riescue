Configuration Schema Reference
===============================

This reference documents the CPU configuration file schema used by RiescueD. Configuration files are JSON format and define the target system's memory map, features, and test generation parameters.

Required Configuration Elements
-------------------------------

**reset_pc**
^^^^^^^^^^^^

The program counter value when the processor starts.

**Type:** String (hexadecimal) or Integer

**Examples:**

.. code-block:: json

    "reset_pc": "0x8000_0000"
    "reset_pc": "0x80000000"
    "reset_pc": 2147483648

**mmap** - Memory Map
^^^^^^^^^^^^^^^^^^^^^

Defines the system memory layout with DRAM and I/O regions.

**Type:** Object

**Structure:**

.. code-block:: json

    {
        "mmap": {
            "dram": {  },
            "io": {  }
        }
    }

Memory Map Components
---------------------

**dram** - DRAM Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Main system memory regions where code and data are allocated.

**Type:** Object with named regions

**Region Properties:**

- ``address`` (required) - Starting memory address (string or integer)
- ``size`` (required) - Size of the memory region (string or integer)

**Examples:**

.. code-block:: json

    "dram": {
        "region0": {
            "address": "0x8000_0000",
            "size": "0x10_0000_0000_0000"
        },
        "main_ram": {
            "address": "0x80000000",
            "size": "0x40000000"
        }
    }

**io** - I/O Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^

Memory-mapped I/O regions and devices.

**Type:** Object

**Properties:**

- ``address`` (required) - I/O region base address
- ``size`` (required) - Total I/O region size
- ``items`` (optional) - Named I/O devices within the region

**I/O Item Properties:**

- ``address`` (required) - Device base address
- ``size`` (required) - Device size
- ``test_access`` (optional) - Determines if location can be used as a random memory address: ``true`` or ``false``. Defaults to ``false``

**Examples:**

.. code-block:: json

    "io": {
        "io0": {
            "address": "0x0",
            "size": "0x1_0000"
        },
        "uart": {
            "address": "0x200_c000",
            "size": "0x5ff_4000",
            "test_access": "available"
        },
        "htif": {
            "address": "0x7000_0000",
            "size": "0x10"
        }
    }

**Special I/O Devices:**

- ``htif`` - Host-Target Interface, specifies the default end-of-test address (``tohost``)

Feature Configuration
---------------------

**features** - Extension Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures RISC-V extensions and their availability.

**Type:** Object with extension names as keys

**Extension Properties:**

- ``supported`` (required) - Whether extension is supported by target (boolean)
- ``enabled`` (required) - Whether extension is enabled by default (boolean)
- ``randomize`` (required) - Percentage chance of randomization (0-100)

**Standard Extensions:**

- ``rv64`` / ``rv32`` - Architecture width
- ``i`` - Base integer instruction set
- ``m`` - Integer multiplication and division
- ``a`` - Atomic instructions
- ``f`` - Single-precision floating-point
- ``d`` - Double-precision floating-point
- ``c`` - Compressed instructions
- ``h`` - Hypervisor extension
- ``v`` - Vector extension
- ``u`` - User mode
- ``s`` - Supervisor mode

**Examples:**

.. code-block:: json

    "features": {
        "rv64": {"supported": true, "enabled": true, "randomize": 100},
        "i": {"supported": true, "enabled": true, "randomize": 100},
        "m": {"supported": true, "enabled": true, "randomize": 100},
        "a": {"supported": true, "enabled": true, "randomize": 100},
        "f": {"supported": true, "enabled": true, "randomize": 100},
        "d": {"supported": true, "enabled": true, "randomize": 100},
        "c": {"supported": true, "enabled": true, "randomize": 100},
        "h": {"supported": true, "enabled": true, "randomize": 100},
        "v": {"supported": true, "enabled": false, "randomize": 100},
        "u": {"supported": true, "enabled": true, "randomize": 100},
        "s": {"supported": true, "enabled": true, "randomize": 100}
    }

Test Generation Parameters
--------------------------

**test_generation** - Generation Settings
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Controls various aspects of test generation behavior.

**Type:** Object

**Properties:**

- ``secure_access_probability`` - Percentage chance of secure access patterns (0-100)
- ``secure_pt_probability`` - Percentage chance of secure page table generation (0-100)
- ``a_d_bit_randomization`` - Percentage chance of randomizing accessed/dirty bits (0-100)
- ``pbmt_ncio_randomization`` - Percentage chance of PBMT NCIO randomization (0-100)

**Examples:**

.. code-block:: json

    "test_generation": {
        "secure_access_probability": 30,
        "secure_pt_probability": 0,
        "a_d_bit_randomization": 0,
        "pbmt_ncio_randomization": 0
    }

Complete Configuration Example
------------------------------

Here's a complete configuration file example:

.. code-block:: json

    {
        "reset_pc": "0x8000_0000",
        "mmap": {
            "dram": {
                "region0": {
                    "address": "0x8000_0000",
                    "size": "0x10_0000_0000_0000"
                }
            },
            "io": {
                "address": "0",
                "size": "0x8000_0000",
                "items": {
                    "io0": {
                        "address": "0x0",
                        "size": "0x1_0000"
                    },
                    "io1": {
                        "address": "0x200_c000",
                        "size": "0x5ff_4000",
                        "test_access": "available"
                    },
                    "htif": {
                        "address": "0x7000_0000",
                        "size": "0x10"
                    }
                }
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
            "h": {"supported": true, "enabled": true, "randomize": 100},
            "v": {"supported": true, "enabled": false, "randomize": 100},
            "u": {"supported": true, "enabled": true, "randomize": 100},
            "s": {"supported": true, "enabled": true, "randomize": 100}
        },
        "test_generation": {
            "secure_access_probability": 30,
            "secure_pt_probability": 0,
            "a_d_bit_randomization": 0,
            "pbmt_ncio_randomization": 0
        }
    }

Validation Rules
----------------

**Address Format:**
- Addresses can use underscore separators for readability: ``"0x8000_0000"``
- Both string and integer formats are supported
- Hexadecimal strings must start with ``"0x"``

**Size Format:**
- Sizes follow the same format rules as addresses
- Must be positive values

**Memory Layout:**
- DRAM regions must not overlap
- I/O items must fit within the parent I/O region
- All addresses must be valid for the target architecture

**Feature Dependencies:**
- Some extensions have dependencies (e.g., ``d`` requires ``f``)
- Architecture width (``rv32``/``rv64``) affects address space limits
