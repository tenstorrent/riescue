Physical Memory Attributes (PMA)
================================

This guide explains how RiescueD models Physical Memory Attributes, how to give your test
memory with specific attributes, and how to use PMA randomization to stress a design's PMA
checking logic. It assumes you know roughly what a PMA is in RISC-V, but nothing about how
RiescueD handles them.

Background: PMAs in thirty seconds
----------------------------------

In a RISC-V system, every physical address range has *attributes*: is it normal cacheable
RAM? Uncached memory? A memory-mapped device? Can you fetch instructions from it? Can you
run atomic (AMO) instructions against it? The hardware checks every access against these
attributes, and an access that violates them raises an access-fault exception
(``LOAD_ACCESS_FAULT``, ``STORE_ACCESS_FAULT``, or ``INSTRUCTION_ACCESS_FAULT``).

The RISC-V privileged spec leaves *how* PMAs are configured up to the platform. RiescueD
targets a platform scheme where PMAs are software-programmable through a bank of CSR pairs.

How RiescueD models PMAs
------------------------

The target implements up to **64 PMA entries**. Each entry is a pair of registers:

- ``pmacfg`` — encodes the region's base address, size, and attributes (memory type,
  cacheability, permissions, AMO support, coherency routing)
- ``pmamask`` — an optional address-compare mask over physical address bits 51:12. A zero
  mask means "match the whole naturally-aligned region"; a nonzero mask makes the entry
  match a scattered pattern of pages instead of one contiguous range

The **first 16 entries are direct CSRs** (``pmacfg`` at ``0x7E0 + i``, ``pmamask`` at
``0x7F0 + i``). Entries 16 and above have no direct CSR addresses and are reached
*indirectly*: write the entry number to ``miselect``, then access the entry's ``pmacfg``
through ``mireg`` and its ``pmamask`` through ``mireg2``.

Two rules matter for everything below:

1. **First match wins.** When an access matches several entries, the lowest-numbered entry
   decides the attributes. Entry 0 has the highest priority.
2. **The top two entries are catch-alls.** RiescueD always keeps the last two entries
   (``num_pmas - 2`` and ``num_pmas - 1``) programmed as broad default regions — an
   IO/noncacheable region covering low addresses and a cacheable-coherent RWX region
   covering all of memory — so that any address not covered by a more specific entry still
   has sane attributes and the test can always run.

The number of implemented entries is configured with ``num_pmas`` (cpu config
``mmap.pma.num_pmas`` or the ``--num_pmas`` CLI flag, default 64) and **must match the ISS
configuration** — the generated boot code physically writes entry ``num_pmas - 1``, so a
mismatch means writing CSRs the simulator doesn't implement.

Enabling PMAs in a test
-----------------------

PMA support is off by default. Run with ``--needs_pma`` and the generated boot code
(the *loader*) will program one PMA entry per defined region before your test starts.
Where do regions come from? Three places, usable together:

1. **The cpu config** — fixed regions and hints under ``mmap.pma`` in the JSON
   configuration file. Use this for regions the platform always has. See the
   :doc:`/reference/config/configuration_schema` for the schema.

2. **The** ``;#pma_hint`` **directive** — asks the framework to create regions with the
   attributes you want at addresses it picks:

   .. code-block:: text

       ;#pma_hint(name=my_hint,
           memory_types=[memory],
           cacheability=[cacheable, noncacheable],
           rwx_combos=[rwx],
           adjacent=true
       )

   This generates one region per attribute combination (here: two adjacent RWX memory
   regions, one cacheable and one noncacheable).

3. **The** ``;#random_addr`` **directive with** ``in_pma=1`` — the most common way in
   practice. It gives you a random physical address *and* guarantees the address sits
   inside a PMA region with the attributes you asked for:

   .. code-block:: asm

       ;#random_addr(name=nc_buf, type=physical, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=memory, pma_cacheability=noncacheable, pma_read=1, pma_write=1, pma_execute=0)

   Your test code can then load ``nc_buf`` into a register and know every access through it
   hits noncacheable memory.

**Region reuse.** PMA entries are a scarce resource. When an ``in_pma=1`` address requests
attributes that exactly match an existing region — one made by a ``;#pma_hint``, or by an
earlier ``in_pma`` address — the framework places the address inside that existing region
instead of creating a new one. Ten addresses with identical attributes cost one PMA entry,
not ten.

Full syntax for both directives is in the
:doc:`/reference/riescue_test_file/directives_reference`, and a complete reference test
ships at ``riescue/dtest_framework/tests/test_pma_hint.s``.

PMA randomization
-----------------

Everything above is *directed*: you say what you want and get exactly that. The
``--enable_pma_randomization`` flag adds a *random* layer on top, designed to stress the
design's PMA matching logic rather than your test's logic:

.. code-block:: bash

    riescued -t my_test.s --needs_pma \
        --enable_pma_randomization \
        --pma_random_regions 8 \
        --pma_random_mask_pct 25

Three things happen:

**1. Decoy regions.** The framework carves ``--pma_random_regions`` (default 8) extra PMA
regions — called *decoys* — at random naturally-aligned power-of-two locations in the
physical address space, sized between 4KB and 1GB. Each decoy gets random but *legal*
attributes (memory/io/ch0/ch1 types, permission combinations, AMO support, and so on),
including faulting flavors like no-access memory or read-only IO. Attribute draws are biased
toward real workloads: about 78% cacheable memory, 10% noncacheable, 10% IO, and 2% channel
types. Decoys are placed only in
*holes*: they never overlap your test's memory, the reset vector, IO devices such as the
HTIF or interrupt files, or page tables. Test address generation likewise avoids landing
inside decoys. The result is a PMA CSR bank full of live, randomly-shaped entries that the
hardware must correctly match (or correctly *not* match) on every single access your test
makes — while the test still passes.

**2. Random masks on decoys.** Each decoy has a ``--pma_random_mask_pct`` (default 25)
percent chance of receiving a nonzero ``pmamask``, turning it from one contiguous region
into a scattered match pattern. Mask shapes are drawn from several weighted strategies
(contiguous runs, single bits, dense and sparse patterns) and are constrained so the
scattered match windows never cover memory the test actually uses.

**3. Random masks on your regions.** Named regions created by your test's hints and
``in_pma`` addresses (internally called *carve-outs*) can also receive random masks with
``--pma_carveout_mask_pct`` (default 0 — off). A masked carve-out still matches its own
base window, so addresses placed inside it keep their attributes, while the extra scattered
match windows exercise masked comparison in the design. To *force* a mask onto one specific
region, use ``pma_masked=1`` on the ``in_pma`` address.

All randomness is drawn from the test's seed: the same ``--seed`` reproduces the same
decoys, attributes, and masks.

.. _pma-fixed-windows:

Region tags and fixed windows
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Two independent keys on a cpuconfig memory-map region control how randomization treats it and how
a test reaches it:

.. code-block:: json

   "dram": {
       "dram":          {"address": "0x8000_0000",   "size": "0xf_ffff_8000_0000"},
       "poison_window": {"address": "0x1_0000_0000", "size": "0x1_0000",
                         "tags": ["derr"], "pma_randomization": false},
       "scrub_window":  {"address": "0x1_1000_0000", "size": "0x1_0000",
                         "tags": ["nderr"], "pma_randomization": false},
       "tee_window":    {"address": "0x1_2000_0000", "size": "0x1_0000",
                         "tags": ["stee"], "pma_randomization": false},
       "spare_window":  {"address": "0x1_3000_0000", "size": "0x1_0000",
                         "tags": ["derr", "spare"], "pma_randomization": false},
       "low_bank":      {"address": "0x2_0000_0000", "size": "0x1000_0000", "tags": ["bank0"]}
   }

``tags`` is a list of free-form string labels. RiescueD attaches no meaning to a tag beyond making
the region selectable by it — see *Reaching a region from a test* below. Tags are also accepted on
``io`` and ``custom`` regions.

``pma_randomization: false`` makes the region a **fixed window**: something nothing random touches.

**What a fixed window buys you:**

- a named PMA region with default cacheable-RWX memory attributes, programmed in the high-priority
  carve-out tier so randomized decoy regions never shadow it
- skipped by decoy placement and by scattered random ``pmamask`` windows
- exempt from carve-out mask stress (``--pma_carveout_mask_pct``)
- kept out of the general DRAM pool, so address generation never lands test content there by chance

**The default.** ``pma_randomization`` defaults to ``true``, so a region is a fixed window only when
the config asks for one. No tag implies it: ``{"tags": ["derr"]}`` on its own leaves the region in
the general pool. RiescueD models no derr/nderr/stee behavior of its own; what such a label *means*
(poisoned data, scrub-on-read, a TEE window) is a contract between the DUT and the testbench. Note
that the ``secure`` *tag* does not make a region secure — the ``secure`` key, or a ``secure*`` region
name, does that.

Because the two keys are independent, ``low_bank`` above is selectable by its ``bank0`` tag while
remaining ordinary allocatable DRAM.

**Reaching a fixed window from a test.** Each one is published as ``pma_<region name>_base`` /
``_size`` / ``_end`` equates. Because nothing else can be placed there, the addresses are stable
across seeds:

.. code-block:: asm

   li t0, pma_poison_window_base       # 0x1_0000_0000
   li t1, pma_poison_window_end

**Reaching a region from a test.** To allocate an address *inside* a region on purpose, name it in
``custom_region=`` — the same mechanism used for ``custom`` memory-map regions. A ``custom_region``
spec may be a region name or a tag:

.. code-block:: asm

   ;#random_addr(name=phys_in_poison, type=physical, custom_region=poison_window, size=0x1000)
   ;#random_addr(name=phys_in_spare,  type=physical, custom_region=spare,         size=0x1000)
   ;#random_addr(name=phys_in_bank0,  type=physical, custom_region=bank0,         size=0x1000)

An exact region name always wins over a tag, so naming a region reaches that one region. When a tag
matches several regions, one is picked per address — so a handful of addresses naming ``derr`` spread
across every ``derr``-tagged window rather than piling into one. An unknown spec is an error listing
every accepted name and tag.

Without an explicit request nothing lands in a fixed window, which is the point: a test touches
these windows only when it means to.

A complete worked example lives in ``riescue/dtest_framework/tests/pma_carveout.s``, with its
memory map in ``riescue/dtest_framework/tests/cpu_config_pma_carveout.json``:

.. code-block:: bash

   riescued --testfile riescue/dtest_framework/tests/pma_carveout.s \
            --cpuconfig riescue/dtest_framework/tests/cpu_config_pma_carveout.json \
            --enable_pma_randomization --run_iss

Reserving entries for the test to program
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A test that programs pmacfg entries itself needs some left free. ``;#test.user_programmable_pmacfg``
states that requirement in the test file, where it belongs — a given ``.s`` needs a fixed number of
entries and cannot adapt to a smaller one:

.. code-block:: asm

   ;#test.user_programmable_pmacfg 2

Entries ``[0..N)`` are then reserved: RiescueD programs no region into them. Under
``--enable_pma_randomization`` it zeroes them once at boot (so a stale boot ``pmacfg14``/``15``
catchall cannot outrank every real entry) and never touches them again; with randomization off it
does not emit them at all.

The same number can come from ``mmap.pma.user_programmable_pmacfg`` in the cpuconfig or from
``--user_programmable_pmacfg``. The test header is a *floor*, not an override: when the sources
disagree the larger value wins, since a test can tolerate extra free entries but not fewer.

Flag summary
^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 34 12 54

   * - Flag
     - Default
     - Effect
   * - ``--needs_pma``
     - off
     - Program PMA entries for all defined regions at boot
   * - ``--num_pmas <N>``
     - 64
     - Implemented PMA entries (2-64); must match the ISS config
   * - ``--enable_pma_randomization``
     - off
     - Generate decoy regions and random masks (implies ``--needs_pma``)
   * - ``--pma_random_regions <N>``
     - 8
     - Number of decoy regions
   * - ``--pma_random_mask_pct <P>``
     - 25
     - Percent of decoys that get a nonzero ``pmamask``
   * - ``--pma_carveout_mask_pct <P>``
     - 0
     - Percent of named test regions that get a random ``pmamask``
   * - ``--pma_indirect_access_pct <P>``
     - 50 when randomizing, else 0
     - Percent of entries 0-15 programmed via ``miselect``/``mireg``/``mireg2`` instead of direct CSRs

What the loader emits
---------------------

With ``--needs_pma``, the generated assembly contains a ``loader__setup_pma:`` block that
runs in machine mode at boot. Each entry is a commented CSR write sequence — direct
``csrw 0x7e0+i`` / ``csrw 0x7f0+i`` pairs for entries below 16, and
``miselect``/``mireg``/``mireg2`` sequences for entries 16 and above. With
``--pma_indirect_access_pct``, a random share of the low entries also uses the indirect
sequence, exercising both access paths. Because writing an
entry's ``pmacfg`` resets its mask, the ``pmamask`` write always follows the ``pmacfg``
write.

Entries are laid out from index 0 upward:

1. **User-reserved entries** (``user_programmable_pmacfg`` of them, default 0) — carry no
   region; the test programs them at runtime (see below). With randomization on they are
   zeroed once at boot, then left alone
2. **Your named regions** (carve-outs from hints and ``in_pma`` addresses)
3. **Decoy regions** (randomization only)
4. **Invalidated entries** — zeroed so no stale boot values linger (randomization only)
5. **The two catch-alls** at the very top

With randomization on, the loader writes the top catch-all entries *first* — the boot-time
catch-alls at entries 14/15 still cover memory at that moment, so there is never a window
where an access has no matching entry. Only then are the reserved entries zeroed, which
matters when more than 14 are reserved: entries 14/15 fall inside the reserved block and
their boot catch-all values would otherwise outrank every region below. Named regions come
before decoys, and decoys before broad memory-map regions, so the specific entries win
first-match-wins priority.

.. note::

   With randomization **off**, named carve-out regions are emitted after memory-map regions
   and can be shadowed by them (a known quirk of the legacy emission order). If your test
   depends on carve-out regions winning priority, run with ``--enable_pma_randomization``.

Runtime PMA programming by the test
-----------------------------------

Setting ``user_programmable_pmacfg: N`` in the cpu config's ``mmap.pma`` section reserves
entries ``0`` to ``N-1`` for the *test itself*. The loader puts no region there — with
randomization on it zeroes them at boot and never writes them again, with randomization off
it leaves them alone entirely. Since entry 0 outranks everything, a machine-mode test can
write its own ``pmacfg`` value there at runtime to override the attributes of any page — for
example, flipping a cacheable RWX page to read-only IO, verifying that stores now fault, then
writing 0 to the entry to restore normal behavior. The reserved count must leave room for the
two catch-alls (``N <= max_regions - 2``).

Capacity and gotchas
--------------------

- Everything must fit: ``user_programmable_pmacfg`` + named regions + memory-map regions +
  2 catch-alls must be at most ``num_pmas``, or generation fails with a clear error. Decoys
  are the flexible part — they are truncated (with a warning) to whatever space remains.
- ``--num_pmas`` and the ISS configuration must agree.
- ``pma_masked=1`` requires ``in_pma=1`` on the same ``;#random_addr``.
- Reuse regions where you can: request the same attribute combination rather than a new one
  per address.

Checking the output
-------------------

Useful signs of life when debugging a PMA test:

- The generation log prints one line per region
  (``Generated PMA region: <name> at 0x..., size 0x..., type=...``), the decoy summary
  (``Generated N randomized decoy PMA regions``), and each masked region
  (``Randomized PMA region: ... mask=0x...`` / ``Masked carve-out region <name>: ...``).
- The generated ``.S`` file's ``loader__setup_pma:`` block lists every entry with a comment
  naming the region and its attributes — this is the ground truth for what got programmed
  where.
