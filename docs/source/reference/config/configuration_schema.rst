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

The Memory Map is configured with the ``mmap`` key, using the Memory class:

.. autoclass:: riescue.dtest_framework.config.memory.Memory
   :noindex:



Memory Map Components
---------------------

**dram** - DRAM Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: riescue.dtest_framework.config.memory.DramRange
   :members: from_dict
   :noindex:

**DramRange Fields:**

- ``permissions`` (optional) - PMP access permissions as a string. Values: ``"rwx"`` (read/write/execute, default), ``"rw"`` (read/write), ``"r"`` (read-only), ``"none"`` (no access).
- ``cacheable`` (optional) - Whether the region is cacheable (boolean, default: ``false``). **Note:** the correct spelling is ``cacheable``, not ``cachable``.
- ``secure`` (optional) - Whether the region is a secure region (boolean, default: ``false``).
- ``configurable`` (optional) - Whether the region can be split/reconfigured during test generation (boolean, default: ``false``).

**io** - I/O Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^

Memory-mapped I/O regions and devices.

.. autoclass:: riescue.dtest_framework.config.memory.IoRange
   :members: from_dict
   :noindex:

**IoRange Fields:**

- ``permissions`` (optional) - PMP access permissions as a string. Values: ``"rw"`` (read/write, default), ``"r"`` (read-only), ``"none"`` (no access).
- ``test_access`` (optional) - Whether the I/O region is available for test access (boolean, default: ``false``).

**Special I/O Devices:**

- ``htif`` - Host-Target Interface, specifies the default end-of-test address (``tohost``)
- ``debug_rom`` - Debug ROM region for RISC-V Debug support. Configured with ``address`` and ``size`` fields under ``mmap.io.debug_rom``. Required when the ``debug`` feature is enabled.

PMA Configuration
-----------------

Physical Memory Attributes (PMA) define hardware-enforced memory properties per region. PMA configuration is specified under ``mmap.pma``.

**Top-Level PMA Fields:**

- ``max_regions`` (optional) - Maximum number of PMA regions (integer)

**regions** - Predefined PMA Regions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A dictionary of named PMA regions. Each region has:

- ``base`` (optional) - Base address (hex string or integer). Auto-generated if not specified.
- ``size`` (optional) - Region size in bytes (hex string or integer). Defaults to 16MB if not specified.
- ``attributes`` - PMA attribute object (see below)
- ``adjacent_to`` (optional) - Name of another region this should be adjacent to
- ``auto_generate`` (optional) - If ``true``, generate automatically from hints (boolean, default: ``false``)

**PMA Attributes:**

- ``memory_type`` - Memory type: ``"memory"``, ``"io"``, ``"ch0"``, or ``"ch1"`` (default: ``"memory"``)
- ``cacheability`` - Cache behavior for memory type: ``"cacheable"`` or ``"noncacheable"`` (default: ``"cacheable"``)
- ``combining`` - Combining behavior for IO type: ``"combining"`` or ``"noncombining"`` (default: ``"noncombining"``)
- ``read`` - Read permission (boolean, default: ``true``)
- ``write`` - Write permission (boolean, default: ``true``)
- ``execute`` - Execute permission (boolean, default: ``true``)
- ``amo_type`` - Atomic operation type: ``"none"``, ``"logical"``, ``"swap"``, or ``"arithmetic"`` (default: ``"arithmetic"``)
- ``routing`` - Coherency routing: ``"coherent"`` or ``"noncoherent"`` (default: ``"coherent"``)

**hints** - PMA Generation Hints
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A dictionary of named hints that guide automatic PMA region generation. Each hint has:

- ``name`` - Unique hint name
- ``combinations`` (optional) - List of specific PMA attribute combination dicts
- ``adjacent`` (optional) - Request adjacent regions (boolean, default: ``false``)
- ``min_regions`` (optional) - Minimum number of regions to generate
- ``max_regions`` (optional) - Maximum number of regions to generate
- ``size`` (optional) - Size of generated PMA regions in bytes (hex string or integer)

**PMA Example:**

.. code-block:: json

    "pma": {
        "max_regions": 15,
        "regions": {
            "predefined_region1": {
                "base": "0x90000000",
                "size": "0x1000000",
                "attributes": {
                    "memory_type": "memory",
                    "cacheability": "cacheable",
                    "read": true,
                    "write": true,
                    "execute": true,
                    "amo_type": "arithmetic",
                    "routing": "coherent"
                }
            }
        },
        "hints": {
            "config_hint1": {
                "memory_types": ["memory"],
                "cacheability": ["noncacheable"],
                "rwx_combos": ["rw"],
                "size": 524288,
                "adjacent": false
            }
        }
    }

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

- ``secure_access_probability`` - Percentage chance of secure access patterns (0-100, default: 30)
- ``secure_pt_probability`` - Percentage chance of secure page table generation (0-100, default: 0)
- ``a_d_bit_randomization`` - Percentage chance of randomizing accessed/dirty bits (0-100, default: 0)
- ``pbmt_ncio_randomization`` - Percentage chance of PBMT NCIO randomization (0-100, default: 0)
- ``fs_randomization`` - Percentage chance of randomizing the FS (floating-point status) field in mstatus/sstatus (0-100, default: 0)
- ``fs_randomization_values`` - List of allowed FS field values when randomized. Values: ``0`` (Off), ``1`` (Initial), ``2`` (Clean), ``3`` (Dirty). Default: ``[2]``
- ``vs_randomization`` - Percentage chance of randomizing the VS (vector status) field in mstatus/sstatus (0-100, default: 0)
- ``vs_randomization_values`` - List of allowed VS field values when randomized. Values: ``0`` (Off), ``1`` (Initial), ``2`` (Clean), ``3`` (Dirty). Default: ``[2]``

**Examples:**

.. code-block:: json

    "test_generation": {
        "secure_access_probability": 30,
        "secure_pt_probability": 0,
        "a_d_bit_randomization": 0,
        "pbmt_ncio_randomization": 0,
        "fs_randomization": 100,
        "fs_randomization_values": [1, 2, 3],
        "vs_randomization": 100,
        "vs_randomization_values": [1, 2, 3]
    }

CSR Randomization CLI Flags
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The ``test_generation`` parameters above can also be set via CLI flags (e.g. ``--fs_randomization``, ``--vs_randomization``). Additionally, CSR read randomization during OS scheduler code is controlled by these CLI-only flags:

- ``--no_random_csr_reads`` - Disable random CSR read randomization entirely
- ``--max_random_csr_reads <N>`` - Maximum number of CSR reads to inject (default: 16, minimum: 3)
- ``--random_machine_csr_list <csrs>`` - Comma-separated list of CSR names to include when in machine mode (e.g., ``mstatus,mcause``)
- ``--random_supervisor_csr_list <csrs>`` - Comma-separated list of CSR names to include when in supervisor/machine mode (e.g., ``sstatus,scause``)
- ``--random_user_csr_list <csrs>`` - Comma-separated list of CSR names to include when in user/supervisor/machine mode (e.g., ``fcsr,time``)

See :doc:`cli` for the complete list of all command-line flags.

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
