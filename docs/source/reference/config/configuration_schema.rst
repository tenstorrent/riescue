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

.. autoclass:: riescue.riemap.memory.Memory
   :noindex:



Memory Map Components
---------------------

**dram** - DRAM Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: riescue.riemap.memory.DramRange
   :members: from_dict
   :noindex:

**DramRange Fields:**

- ``permissions`` (optional) - PMP access permissions as a string. Values: ``"rwx"`` (read/write/execute, default), ``"rw"`` (read/write), ``"r"`` (read-only), ``"none"`` (no access).
- ``cacheable`` (optional) - Whether the region is cacheable (boolean, default: ``false``). **Note:** the correct spelling is ``cacheable``, not ``cachable``.
- ``secure`` (optional) - Marks the region as a Trusted Execution Environment (TEE) zone, matching Whisper's TEE implementation; TEE is not standard RISC-V, so this can be ignored unless targeting TEE (boolean, default: ``false``).
- ``configurable`` (optional) - Whether the region can be split/reconfigured during test generation (boolean, default: ``false``).

- ``tags`` (optional) - Free-form string labels for the region (list of strings, default: ``[]``). Also accepted on ``io`` and ``custom`` regions.
- ``pma_randomization`` (optional) - Whether PMA randomization may touch the region (boolean, default: ``true``). ``dram`` only.

These two keys are independent. ``tags`` makes a region *selectable*: a ``;#random_addr`` can name any
of its tags in ``custom_region=``, exactly as it can name the region itself. RiescueD attaches no other
meaning to a tag.

``pma_randomization: false`` makes a region a *fixed window*: it is split out of ``dram_ranges``, given
default cacheable-RWX PMA attributes at high priority, excluded from decoy placement and mask stress,
kept out of the general DRAM pool, and published to the test as ``pma_<region name>_base`` / ``_size`` /
``_end`` equates.

A tag never implies ``pma_randomization: false``; only the key itself makes a region a fixed window.
Note that the ``secure`` *tag* does not make a region secure — the ``secure`` key (or a ``secure*``
name) does that. See :ref:`pma-fixed-windows`.

**io** - I/O Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^

Memory-mapped I/O regions and devices.

.. autoclass:: riescue.riemap.memory.IoRange
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

Physical Memory Attributes (PMA) define hardware-enforced memory properties per region — whether a range of physical addresses behaves like normal cacheable RAM, uncached memory, or a memory-mapped device, and which kinds of accesses (reads, writes, fetches, atomics) it accepts. PMA configuration is specified under ``mmap.pma``.

For a walkthrough of how PMAs work in RiescueD — including the runtime CSR programming and the ``--enable_pma_randomization`` feature — see :doc:`/user_guides/pma`.

**Top-Level PMA Fields:**

- ``max_regions`` (optional) - Maximum number of PMA regions test generation may use (integer, 1-64, default: 64)
- ``num_pmas`` (optional) - Number of PMA CSR entries implemented by the target (integer, 2-64, default: 64). The first 16 entries are direct CSRs; the rest are reached indirectly through ``miselect``/``mireg``/``mireg2``. **Must match the ISS (whisper) configuration** — changing this requires a matching whisper config. Can also be set with the ``--num_pmas`` CLI flag.
- ``user_programmable_pmacfg`` (optional) - Reserve the first N ``pmacfg`` entries (indices ``0`` to ``N-1``) for the test's own runtime programming; the framework puts no region there (integer, default: 0). Because entry 0 has the highest match priority, a test can claim a reserved entry at runtime and override the attributes of any page. With ``--enable_pma_randomization`` the reserved entries are zeroed once at boot so a count above 14 cannot leave the ISS boot catch-alls at entries 14/15 shadowing every region below. Maximum value is ``max_regions - 2`` (the top two entries are always kept as catch-all regions).

**regions** - Predefined PMA Regions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Explicit PMA regions with known attributes. Accepts either a dictionary keyed by region name or a list of objects with a ``name`` field. Each region has:

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

Hints ask the framework to auto-generate PMA regions with the requested attributes but framework-chosen addresses. Accepts either a dictionary keyed by hint name or a list of objects with a ``name`` field. Each hint has:

- ``name`` - Unique hint name
- ``combinations`` - List of PMA attribute combination dicts; one region is generated per combination. Each combination may contain ``memory_type``, ``cacheability`` (memory) or ``combining`` (io), ``rwx`` (a string like ``"rwx"`` or ``"rw"``), ``amo_type``, and ``routing``.
- ``adjacent`` (optional) - Place the generated regions adjacent to each other (boolean, default: ``false``)
- ``min_regions`` (optional) - Minimum number of regions to generate
- ``max_regions`` (optional) - Maximum number of regions to generate
- ``size`` (optional) - Size of generated PMA regions in bytes (hex string or integer)

.. note::

   In the cpu config JSON, hints must use the ``combinations`` form. The attribute-list style
   (``memory_types=[...]``, ``rwx_combos=[...]``, etc.) is only supported by the ``;#pma_hint``
   test-file directive — see :doc:`/reference/riescue_test_file/directives_reference`.

**PMA Example:**

.. code-block:: json

    "pma": {
        "max_regions": 15,
        "num_pmas": 16,
        "user_programmable_pmacfg": 0,
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
                "combinations": [
                    {"memory_type": "memory", "cacheability": "noncacheable", "rwx": "rw"}
                ],
                "size": 524288,
                "adjacent": false
            }
        }
    }

A complete working example ships at ``riescue/dtest_framework/tests/cpu_config_pma.json``.

PMA CLI Flags
^^^^^^^^^^^^^^

PMA behavior can also be controlled from the command line:

- ``--needs_pma`` - Enable PMA support: the loader programs a PMA CSR entry for every defined region at boot
- ``--num_pmas <N>`` - Number of implemented PMA CSR entries (2-64); overrides the cpu config value and requires a matching whisper config
- ``--enable_pma_randomization`` - Program randomized *decoy* PMA regions with legal random ``pmacfg``/``pmamask`` values (implies ``--needs_pma``)
- ``--pma_random_regions <N>`` - Number of randomized decoy regions when randomization is enabled (default: 8)
- ``--pma_random_mask_pct <P>`` - Percent (0-100) of decoy regions that get a nonzero ``pmamask`` (default: 25)
- ``--pma_carveout_mask_pct <P>`` - Percent (0-100) of named test-defined PMA regions that get a random ``pmamask``; requires ``--enable_pma_randomization`` (default: 0)

See :doc:`/user_guides/pma` for what each of these means in practice.

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

- ``secure_access_probability`` - Percentage chance that memory accesses target secure (TEE) regions; no effect unless a ``secure`` region exists in the memory map (0-100, default: 30)
- ``secure_pt_probability`` - Percentage chance that page tables are placed in secure (TEE) regions; no effect unless a ``secure`` region exists in the memory map (0-100, default: 0)
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
