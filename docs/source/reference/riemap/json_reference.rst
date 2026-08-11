JSON Configuration Reference
============================

The ``riemap`` console script generates RISC-V page tables from a declarative JSON
configuration. This page documents the CLI, the complete input schema, and the
complete output schema. For a first example see the
:doc:`tutorial </tutorials/riemap/index>`.

CLI Usage
---------

.. code-block:: bash

   riemap <input.json> <output.json> [--seed N] [--log-level LEVEL] [--addrgen-log-level LEVEL]

Positional arguments:

- ``input.json`` -- path to the input configuration file.
- ``output.json`` -- path to write the output JSON (parent directories are created).

Optional arguments:

- ``--seed N`` -- random seed for reproducibility (default: 1).
- ``--log-level LEVEL`` -- one of ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``
  (default: ``WARNING``, which is silent).
- ``--addrgen-log-level LEVEL`` -- logging level for address-search details, using
  the same choices (default: ``WARNING``). This can be enabled independently because
  address-search debugging is substantially more verbose.

Example:

.. code-block:: bash

   riemap input.json pagetables.json --seed 42

Input JSON Format
-----------------

The input file has two top-level keys: ``mmap`` and ``spaces``. Any key beginning
with ``_comment`` is ignored at the config, space, page, and mmap-entry levels.
Unknown keys are rejected. Inside ``attributes``, ``_comment`` is **not** ignored --
it is treated as an unknown page attribute.

.. code-block:: text

   {
     "mmap": [ ... ],
     "spaces": { ... }
   }

Memory Map (``mmap``)
~~~~~~~~~~~~~~~~~~~~~~

An array of physical memory regions (at least one required). Each entry is either:

- An array ``[low, high]`` for normal memory.
- An object ``{"low": "0x...", "high": "0x...", "secure": true}`` for secure memory
  (``secure`` defaults to ``false``).

Addresses may be hex strings or integers. Each region must have ``low < high``,
non-negative bounds, **4 KiB-aligned** endpoints, and addresses within a 56-bit
physical space. Secure regions back page-table node placement when
``secure_pt_probability`` is set, and back pages carrying the ``secure`` attribute.
(The frontend draws physical addresses with a 52-bit width; secure output PAs may
have bit 55 set.)

.. code-block:: json

   "mmap": [
     ["0x80000000", "0x80000000000000"],
     {"low": "0x0", "high": "0x40000000", "secure": true}
   ]

Spaces (``spaces``)
~~~~~~~~~~~~~~~~~~~~

A dictionary of address-space configurations keyed by space ID (at least one
required). Each space has the following fields:

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Field
     - Default
     - Description
   * - ``paging_mode``
     - ``"sv39"``
     - VS-stage paging mode. Scalar string, uniform list, or weighted list (see :ref:`randomization <riemap-randomization-formats>`).
   * - ``gstage_paging_mode``
     - ``"sv39"``
     - G-stage paging mode (only used when ``twostage`` is true).
   * - ``twostage``
     - ``false``
     - Enable two-stage address translation.
   * - ``secure_pt_probability``
     - ``0``
     - Integer 0--100. Probability that page-table nodes are placed in secure memory. Requires a ``secure`` mmap region.
   * - ``pages``
     - (required)
     - Array of page specifications (at least one; see below).

Valid paging-mode strings: ``"sv32"``, ``"sv39"``, ``"sv48"``, ``"sv57"``,
``"disable"``.

Page Specifications (``pages``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each entry in the ``pages`` array describes a group of pages. Unknown keys are
rejected.

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Default
     - Description
   * - ``num_pages``
     - ``1``
     - Non-negative integer number of pages to generate. Zero disables this group;
       at least one group in the space must remain enabled.
   * - ``id``
     - page index
     - Identifier string for this group. Used as an output dictionary key.
   * - ``va``
     - (random)
     - Exact virtual address (hex string). Cannot combine with ``va_and``/``va_or``.
       A pinned VA allows ``num_pages`` of 1 only.
   * - ``va_and``
     - (none)
     - AND mask for random VA generation.
   * - ``va_or``
     - (none)
     - OR mask for random VA generation.
   * - ``pa``
     - (random)
     - Exact physical address (hex string). All ``num_pages`` pages map here (aliasing).
       Cannot combine with ``pa_and``/``pa_or``.
   * - ``pa_and``
     - (none)
     - AND mask for random PA generation.
   * - ``pa_or``
     - (none)
     - OR mask for random PA generation.
   * - ``attributes``
     - ``{}``
     - Page attributes (see below). Omitted ``size`` defaults to ``"4kb"``.

Use ``va``/``pa`` for exact addresses; use ``va_and``/``va_or`` and
``pa_and``/``pa_or`` for constrained random generation
(``generated_addr = random() & and_mask | or_mask``). The AND mask is combined with
the pagesize alignment mask.

Page Attributes
~~~~~~~~~~~~~~~~

The ``attributes`` object accepts the following RieMap page attributes:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Attribute
     - Description
   * - ``size``
     - Page size: ``"4kb"``, ``"64kb"``, ``"4mb"``, ``"2mb"``, ``"1gb"``, ``"512gb"``, ``"256tb"``
       (default ``"4kb"`` when omitted)
   * - ``v``, ``r``, ``w``, ``x``
     - Valid, Read, Write, Execute bits (0 or 1)
   * - ``u``
     - User-mode accessible (0 or 1)
   * - ``a``, ``d``
     - Accessed, Dirty bits (default to 1 if not specified)
   * - ``g``
     - Global bit (0 or 1)
   * - ``n``
     - NAPOT bit (auto-set to 1 for 64KB pages unless explicitly set to 0)
   * - ``pbmt``
     - Page-Based Memory Type (Svpbmt)
   * - ``rsw``
     - Reserved-for-software PTE bits (bits 9:8 of the leaf PTE). No ``_glevel`` spelling.
   * - ``reserved``
     - Reserved PTE bits (bits 60:54 of the leaf PTE). No ``_glevel`` spelling.
   * - ``secure``
     - Place the page in secure memory and set the secure PA bit. Requires a ``secure`` mmap region.
   * - ``gstage_vs_leaf_size``
     - G-stage page size for the VS leaf PTE translation (two-stage only)
   * - ``gstage_vs_nonleaf_size``
     - G-stage page size for the VS non-leaf PTE translation (two-stage only)

Level-specific attributes override the defaults at individual page-table levels:

- ``{attr}_level{n}`` -- override ``attr`` at VS/single-stage level ``n`` (for
  example ``v_level0``). Which level is the leaf follows from the page's ``size``: level
  0 for ``"4kb"`` and ``"64kb"``, 1 for ``"2mb"`` and ``"4mb"``, 2 for ``"1gb"``, 3 for
  ``"512gb"``, 4 for ``"256tb"``.
- ``{attr}_nonleaf`` -- override ``attr`` on every non-leaf level of the walk.
- ``{attr}_level{vs}_glevel{g}`` -- override it at a specific VS level under a
  specific G-stage level (for example ``u_level1_glevel2``; two-stage only).

A bare base attribute is the leaf's value and takes precedence over a level-specific key
naming the leaf level: ``{"a": 1, "a_level0": 0}`` on a 4 KiB page gives a leaf with
A=1, while ``{"a_level0": 0}`` alone gives A=0. Level-specific keys above the leaf are
unaffected either way.

Pages whose forced non-default attributes at a given level are identical share that
level's page-table PTE. Conflicting forced attributes at a level produce different
index bits and separate nodes. Sharing is determined by attribute equality; there is
no separate sharing field.

.. _riemap-gstage-forcing-attrs:

G-stage forcing attributes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In a two-stage config (``twostage: true`` with ``gstage_paging_mode`` enabled), an
attribute can be forced at the VS and G-stage leaf/non-leaf positions symbolically,
without naming numeric levels. The base attributes are ``v``, ``a``, ``d``, ``g``,
``w``, ``r``, ``x``, ``u``, ``n``, and ``pbmt``. Three forms are accepted:

- ``{base}_{leaf|nonleaf}_{gleaf|gnonleaf}`` -- fully symbolic, e.g.
  ``a_leaf_gleaf``, ``pbmt_nonleaf_gnonleaf``.
- ``{base}_level{n}_{gleaf|gnonleaf}`` -- VS numeric level, G-stage symbolic, e.g.
  ``a_level0_gleaf``.
- ``{base}_{leaf|nonleaf}_glevel{n}`` -- VS symbolic, G-stage numeric level, e.g.
  ``a_leaf_glevel0``.

These are only valid when ``twostage`` is true and ``gstage_paging_mode`` is not
``"disable"``; otherwise the config is rejected.

.. _riemap-randomization-formats:

Attribute Randomization
~~~~~~~~~~~~~~~~~~~~~~~~~

Every attribute value supports three formats:

**Scalar** -- fixed value:

.. code-block:: json

   "r": 1

**Uniform list** -- one value chosen at random with equal probability:

.. code-block:: json

   "size": ["4kb", "2mb", "1gb"]

**Weighted list** -- one value chosen with the given weights:

.. code-block:: json

   "u": [
     {"value": 1, "weight": 6},
     {"value": 0, "weight": 4}
   ]

Paging-mode fields (``paging_mode``, ``gstage_paging_mode``) support the same three
formats.

.. _riemap-page-sizes:

Valid Page Sizes per Paging Mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Mode
     - Valid Sizes
   * - Sv32
     - 4KB, 4MB
   * - Sv39
     - 4KB, 64KB, 2MB, 1GB
   * - Sv48
     - 4KB, 64KB, 2MB, 1GB, 512GB
   * - Sv57
     - 4KB, 64KB, 2MB, 1GB, 512GB, 256TB

If a size list contains entries invalid for the resolved paging mode, they are
filtered out automatically (with an info message). An error is raised if no valid
sizes remain.

Two-Stage Combinations
~~~~~~~~~~~~~~~~~~~~~~~~

The generator supports four combinations via ``twostage``, ``paging_mode``, and
``gstage_paging_mode``:

.. list-table::
   :header-rows: 1
   :widths: 20 25 55

   * - Configuration
     - PTE stages
     - Description
   * - VS + G-stage
     - ``stage=1`` and ``stage=2``
     - Full two-stage: VS-stage translates VA to GPA, G-stage translates GPA to PA. Output interleaves both walks.
   * - VS-only
     - ``stage=1``
     - ``twostage=true``, ``gstage_paging_mode="disable"``. VS-stage only.
   * - G-only
     - ``stage=2``
     - ``twostage=true``, ``paging_mode="disable"``. G-stage only.
   * - Single-stage
     - ``stage`` absent
     - ``twostage=false``. Standard single-stage translation.

In the G-only case the space's addresses are guest-physical, and they are drawn at the
**full g-stage input width** -- 39 bits for an ``sv39`` g-stage, 57 for ``sv57`` -- so a
``va_or`` naming the top bit of that input is satisfiable.

Output JSON Format
------------------

The output file has two top-level keys: ``entries`` and ``spaces``.

.. code-block:: text

   {
     "entries": { ... },
     "spaces": { ... }
   }

Entries
~~~~~~~

A flat dictionary mapping PTE addresses to PTE values (both as hex strings), across
all spaces (VS-stage and G-stage combined).

.. code-block:: json

   "entries": {
     "0x0000000080100000": "0x0000000020040801",
     "0x0000000080100008": "0x0000000020080c01"
   }

Spaces
~~~~~~

A dictionary keyed by space ID. Each space contains:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Field
     - Description
   * - ``paging_mode``
     - Resolved VS-stage paging mode string.
   * - ``twostage``
     - Boolean indicating two-stage translation.
   * - ``top_base_addr``
     - VS-stage page-table root (for SATP/VSATP). Absent if VS paging is disabled.
   * - ``gstage_paging_mode``
     - G-stage paging mode string (present when G-stage is enabled -- full two-stage or G-only -- not merely when ``twostage`` is true).
   * - ``gstage_top_base_addr``
     - G-stage page-table root (for HGATP, present when G-stage is enabled).
   * - ``pages``
     - Nested dict: page ID -> VA (hex) -> page entry.

Each page entry contains ``pa``, ``size``, an ordered ``ptes`` list, and -- for a
two-stage leaf with explicit g-stage sizes -- optional ``gstage_vs_leaf_size`` /
``gstage_vs_nonleaf_size``:

.. code-block:: json

   {
     "pa": "0x0000000090001000",
     "size": "4kb",
     "ptes": [
       {"address": "0x...", "level": 2, "stage": 1},
       {"address": "0x...", "level": 1, "stage": 1},
       {"address": "0x...", "level": 0, "stage": 1}
     ]
   }

Each PTE lists its ``address`` (hex string), ``level``, and ``stage`` -- ``1``
(VS-stage), ``2`` (G-stage), or absent for single-stage. For two-stage walks, the
G-stage PTEs translating each VS-stage PTE address are interleaved before the
VS-stage PTE itself.

Python Usage
------------

The same frontend is available programmatically via
:func:`~riescue.riemap.json_frontend.generate_page_tables`:

.. code-block:: python

   from riescue.riemap.json_frontend import PageTableConfig, generate_page_tables

   config = PageTableConfig.from_dict(
       {
           "mmap": [["0x80000000", "0x80000000000000"]],
           "spaces": {
               "os": {
                   "paging_mode": "sv39",
                   "pages": [
                       {"id": "data", "num_pages": 2, "attributes": {"v": 1, "r": 1, "w": 1}},
                   ],
               }
           },
       }
   )

   output = generate_page_tables(config, seed=1)
   print("total PTEs:", len(output.entries))
   for space_id, space in output.spaces.items():
       print(f"space {space_id}: mode={space.paging_mode} twostage={space.twostage}")
       for page_id, va_map in space.pages.items():
           for va, entry in va_map.items():
               print(f"  {page_id}: VA=0x{va:x} -> PA=0x{entry.pa:x} ({entry.size})")

This prints ``total PTEs: 6`` and one line per generated page (the exact VA/PA depend
on the seed). A :class:`~riescue.riemap.json_frontend.PageTableConfig` can also be
loaded from a file with
:meth:`~riescue.riemap.json_frontend.PageTableConfig.from_json_file`, and the
:class:`~riescue.riemap.json_frontend.PageTableOutput` saved with
:meth:`~riescue.riemap.json_frontend.PageTableOutput.to_json_file`.

For a full two-stage space the JSON frontend declares the VS space's root frame
itself (a GPA page ``SameAs`` a physical HPA page, with an identity GPA-to-HPA
leaf). Single-stage, VS-only, and G-only leave root placement to the builder.
